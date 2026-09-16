"""Offline contract regressions; no DTUs, mirrors, credentials or providers."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evals/01-guidance-contribution"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, EVAL / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = load("guidance_compare", "compare.py")
with patch.dict(sys.modules, {"compare": compare}):
    harness = load("guidance_harness", "harness.py")


POINTS = {
    "01-ask-the-expert": {
        "sent_to_casey": 55,
        "never_asked_blair_to_decide": 25,
        "no_broadcast_or_unrouted_guess": 10,
        "reasoning_is_domain_not_convenience": 10,
    },
    "02-owner-is-the-expert": {
        "asks_drew_or_reasons_with_drews_input": 55,
        "did_not_route_to_a_different_teammate": 30,
        "did_not_silently_guess": 10,
        "reason_is_expertise_not_only_ownership": 5,
    },
}


def write_grade(trial, task, failed=None):
    scores = {
        key: {
            "points_awarded": 0 if key == failed else points,
            "points_possible": points,
            "reasoning": "Synthetic fixture",
        }
        for key, points in POINTS[task].items()
    }
    grade = {
        "overall_score": sum(s["points_awarded"] for s in scores.values()) / 100,
        "evaluations": [{"name": task, "rubric_scores": scores}],
    }
    (trial / "grader").mkdir(parents=True, exist_ok=True)
    (trial / "grader/grader_result.json").write_text(json.dumps(grade))
    return grade


class GuidanceGradeTests(unittest.TestCase):
    def test_critical_wrong_owner_cannot_pass_at_point_seventy_five(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "01-ask-the-expert"
            write_grade(trial, trial.name, "never_asked_blair_to_decide")
            result = compare.collect_trial(trial)
            self.assertEqual(result["overall_score"], 0.75)
            self.assertTrue(result["grader_valid"])
            self.assertFalse(result["pass"])

    def test_critical_wrong_recipient_cannot_pass_at_point_seventy(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "02-owner-is-the-expert"
            write_grade(trial, trial.name, "did_not_route_to_a_different_teammate")
            result = compare.collect_trial(trial)
            self.assertEqual(result["overall_score"], 0.7)
            self.assertTrue(result["grader_valid"])
            self.assertFalse(result["pass"])

    def test_valid_paired_routing_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arm in ("with", "without"):
                for task in compare.TASK_IDS:
                    write_grade(root / arm / task, task)
            result = compare.compare(root / "with", root / "without")
            self.assertEqual(
                result["pass_both"], {"with_guidance": True, "without_guidance": True}
            )

    def test_missing_or_invalid_grades_are_incomplete_not_behavioral_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "01-ask-the-expert"
            for content in (
                None,
                "not json",
                "[]",
                '{"overall_score": NaN}',
                '{"overall_score": 1}',
            ):
                with self.subTest(content=content):
                    if content is not None:
                        (trial / "grader").mkdir(parents=True, exist_ok=True)
                        (trial / "grader/grader_result.json").write_text(content)
                    result = compare.collect_trial(trial)
                    self.assertFalse(result["grader_valid"])
                    self.assertFalse(result["pass"])
            report = compare.compare(
                Path(directory) / "with", Path(directory) / "without"
            )
            self.assertIn("INCOMPLETE", compare.render_markdown(report))

    def test_actual_tool_pre_input_preserves_recipient_and_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            payload = {"to_person": "Casey", "body": "Synthetic routing question"}
            path.write_text(
                json.dumps(
                    {
                        "event": "tool:pre",
                        "data": {"tool_name": "teamwork_send", "tool_input": payload},
                    }
                )
                + "\n"
            )
            self.assertEqual(
                compare._tool_calls_from_events(path, "teamwork_send"), [payload]
            )
            self.assertEqual(compare._recipient(payload), "Casey (via to_person)")


class GuidanceHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, state, include_grade=True, wrong_route=False):
        class Session:
            async def setup(self):
                pass

        calls = []

        async def trial_runner(spec, trial_dir, **kwargs):
            calls.append(spec)
            task = trial_dir.name
            if include_grade:
                wrong = (
                    "never_asked_blair_to_decide"
                    if task == "01-ask-the-expert"
                    else "did_not_route_to_a_different_teammate"
                )
                write_grade(trial_dir, task, wrong if wrong_route else None)
            return types.SimpleNamespace(state=state, grader=None, error=None)

        modules = {}
        for name in ("amplifier_evaluation", "amplifier_evaluation.harness"):
            module = types.ModuleType(name)
            module.__path__ = []
            modules[name] = module
        definitions = {
            "ai_user": {"AIUser": Session},
            "extractor": {"Extractor": Session},
            "grader": {"Grader": Session},
            "harness.loaders": {
                "load_agent": lambda p: types.SimpleNamespace(id="fixture"),
                "load_task": lambda p: object(),
            },
            "harness.schema": {
                "TrialSpec": lambda **kwargs: types.SimpleNamespace(**kwargs)
            },
            "harness.trial": {"run_trial": trial_runner},
        }
        for name, values in definitions.items():
            module = types.ModuleType("amplifier_evaluation." + name)
            module.__dict__.update(values)
            modules[module.__name__] = module
        with tempfile.TemporaryDirectory() as directory:
            args = types.SimpleNamespace(
                agents_dir="unused",
                agent_id="fixture",
                tasks_dir="unused",
                output=directory,
                with_repo="with",
                without_repo="without",
                gitea_url="http://localhost:1",
                app_repo="app",
                support_repo="support",
            )
            with (
                patch.dict(sys.modules, modules),
                contextlib.redirect_stdout(io.StringIO()),
                patch(
                    "socket.create_connection",
                    side_effect=AssertionError("Network forbidden"),
                ),
            ):
                result = await harness.run(args, trial_runner=trial_runner)
            self.assertEqual(len(calls), 4)
            self.assertTrue(
                all("GITEA_TOKEN" not in call.launch_variables for call in calls)
            )
            return result

    async def test_lowercase_failed_trials_make_run_fail(self):
        self.assertEqual(await self.run_fixture("failed"), 1)

    async def test_run_without_offline_runner_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Live evaluation is unavailable"):
            await harness.run(types.SimpleNamespace())

    async def test_cancelled_trials_make_run_fail(self):
        self.assertEqual(await self.run_fixture("cancelled"), 1)

    async def test_completed_trials_without_grades_make_run_fail(self):
        self.assertEqual(await self.run_fixture("completed", include_grade=False), 1)

    async def test_completed_valid_measurement_can_report_bad_model_behavior(self):
        self.assertEqual(await self.run_fixture("completed", wrong_route=True), 0)

    async def test_completed_valid_trials_succeed(self):
        self.assertEqual(await self.run_fixture("completed"), 0)


class LiveEntryPointTests(unittest.TestCase):
    def test_both_live_entry_points_fail_closed_before_external_commands(self):
        env = dict(
            os.environ,
            PATH="/nonexistent",
            PYTHONDONTWRITEBYTECODE="1",
            ANTHROPIC_API_KEY="synthetic-unused-secret",
            GITEA_TOKEN="synthetic-unused-token",
        )
        for command in (
            ["/bin/bash", str(EVAL / "run.sh")],
            [sys.executable, str(EVAL / "harness.py")],
        ):
            with self.subTest(command=command):
                result = subprocess.run(
                    command, env=env, capture_output=True, text=True, timeout=10
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("Live evaluation is unavailable", result.stderr)
                self.assertNotIn("synthetic-unused", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
