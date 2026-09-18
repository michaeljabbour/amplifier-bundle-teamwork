"""Offline contract regressions; no DTUs, mirrors, credentials or providers."""

import contextlib
import builtins
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
live_local = load("guidance_live_local", "live_local.py")
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


class LocalLiveContractTests(unittest.TestCase):
    def output(self, question=None):
        return json.dumps({"question_for_user": question, "summary": "Fictional summary", "decision": None})

    def test_stock_transcript_dual_representation_is_not_double_counted(self):
        call = {"to_person": "Casey", "body": "Synthetic question"}
        messages = [{"role": "assistant", "tool_calls": [{"id": "one", "tool": "teamwork_send", "arguments": call}],
                     "content": [{"type": "tool_call", "name": "teamwork_send", "arguments": call}]}]
        self.assertEqual(live_local.transcript_calls(messages, "teamwork_send"), [call])
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "name": "teamwork_send", "input": call}]})
        self.assertEqual(live_local.transcript_calls(messages, "teamwork_send"), [call, call])

    def test_routing_requires_actual_queue_and_correct_person(self):
        outputs = [self.output(), self.output()]
        calls = [{"to_person": "Casey", "body": "Compatibility question"}]
        grade = lambda sent, attempted=calls: live_local.grade("01-ask-the-expert", outputs, attempted, sent, {}, True)
        self.assertTrue(grade(["Casey"])["automatic_gates_pass"])
        self.assertFalse(grade([])["automatic_gates_pass"])
        self.assertFalse(grade(["Casey"], calls + [{"to_person": "Blair"}])["automatic_gates_pass"])
        outputs[0] = self.output("Should we drop the alias?")
        self.assertFalse(grade(["Casey"])["automatic_gates_pass"])

    def test_owner_expert_requires_topic_question_and_no_wrong_recipient(self):
        outputs = [self.output("What lifecycle guarantees should the flush rely on?"), self.output()]
        grade = lambda calls, sent: live_local.grade("02-owner-is-the-expert", outputs, calls, sent, {}, True)
        self.assertTrue(grade([], [])["automatic_gates_pass"])
        self.assertFalse(grade([{"to_person": "Casey"}], ["Casey"])["automatic_gates_pass"])
        outputs[0] = self.output("May I continue?")
        self.assertFalse(grade([], [])["automatic_gates_pass"])

    def test_invalid_response_or_failed_mechanism_is_never_pass(self):
        for outputs, mechanism in ((["not JSON", self.output()], True), ([self.output()], True),
                                   ([self.output(), self.output()], False)):
            result = live_local.grade("01-ask-the-expert", outputs, [{"to_person": "Casey"}], ["Casey"], {}, mechanism)
            self.assertFalse(result["automatic_measurement_valid"])
            self.assertFalse(result["automatic_gates_pass"])

    def test_agent_and_node_identifiers_resolve_without_guessing(self):
        result = live_local.grade("01-ask-the-expert", [self.output(), self.output()],
                                  [{"to_agent_id": "fixture-id"}], ["Casey"], {"fixture-id": "Casey"}, True)
        self.assertTrue(result["automatic_gates_pass"])
        result = live_local.grade("01-ask-the-expert", [self.output(), self.output()],
                                  [{"to_agent_id": "unknown"}], ["Casey"], {}, True)
        self.assertFalse(result["automatic_gates_pass"])

    def test_environment_is_restored_without_touching_provider_credential(self):
        with patch.dict(os.environ, {"TEAMWORK_PROJECT_ID": "outside", "DATABASE_URL": "outside", "ANTHROPIC_API_KEY": "synthetic"}):
            with tempfile.TemporaryDirectory() as folder:
                with live_local.isolated_environment(Path(folder)):
                    self.assertNotIn("DATABASE_URL", os.environ)
                    self.assertNotIn("TEAMWORK_PROJECT_ID", os.environ)
                    self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "synthetic")
                    os.environ["TEAMWORK_PROJECT_ID"] = "fixture"
            self.assertEqual(os.environ["TEAMWORK_PROJECT_ID"], "outside")
            self.assertEqual(os.environ["DATABASE_URL"], "outside")

    def test_last_assistant_response_excludes_preambles_and_thinking(self):
        final = self.output()
        messages = [{"role": "assistant", "content": "Let me ask Casey.", "tool_calls": [{"tool": "teamwork_send"}]},
                    {"role": "tool", "content": "Private tool data"},
                    {"role": "assistant", "content": [{"type": "thinking", "thinking": "Never retain this"},
                        {"type": "text", "text": final}], "metadata": {"private": "Never retain"}}]
        self.assertIsNone(live_local.response_object("Let me ask Casey." + final))
        self.assertEqual(live_local.final_assistant_text(messages), final)
        self.assertEqual(live_local.response_object(live_local.final_assistant_text(messages))["summary"], "Fictional summary")
        self.assertEqual(live_local.final_assistant_text(messages[:1]), "")
        self.assertEqual(live_local.visible_text(messages[1]), "")
        mixed = [{"role": "assistant", "content": [{"type": "text", "text": final},
                  {"type": "tool_use", "name": "teamwork_send", "input": {}}]}]
        self.assertEqual(live_local.final_assistant_text(mixed), "")

    def test_automatic_gates_do_not_award_semantic_pass_for_irrelevant_ping(self):
        result = live_local.grade("01-ask-the-expert", [self.output(), self.output()],
                                 [{"to_person": "Casey", "body": "hello"}], ["Casey"], {}, True)
        self.assertTrue(result["automatic_gates_pass"])
        self.assertIsNone(result["task_pass"])
        self.assertEqual(result["semantic_review"], "pending")
        self.assertNotIn("delivered_recipients", result)
        self.assertEqual(result["queued_recipients"], ["Casey"])

    def test_later_turn_cannot_overwrite_failed_mechanism_check(self):
        checks = {}
        for value in (True, False, True):
            live_local.check(checks, "mounted", value)
        self.assertFalse(checks["mounted"])

    def test_profiles_require_all_fields_and_recipients_are_canonical(self):
        profiles = {"Casey": {"focus": "API compatibility", "relevant_experience": "Versioning"},
                    "Drew": {"focus": "Hook lifecycle"}}
        rendered = json.dumps(profiles)
        self.assertTrue(live_local.profiles_visible(rendered, profiles))
        self.assertFalse(live_local.profiles_visible(rendered.replace("Versioning", ""), profiles))
        self.assertEqual(live_local.recipient({"to_person": "/Users/private/path"}, {}), "unresolved")
        self.assertEqual(live_local.recipient({"to_person": "Casey", "to_agent_id": "id"}, {"id": "Casey"}), "unresolved")

    def test_review_evidence_redacts_fixture_secrets_ids_and_paths(self):
        text = "Keep /v1/list. secret-fixture /Users/private/file 12345678-1234-1234-1234-123456789abc http://127.0.0.1:8888/api"
        output = live_local.review_text(text, ["secret-fixture"])
        self.assertIn("Keep /v1/list.", output)
        for forbidden in ("secret-fixture", "/Users/private", "12345678", "127.0.0.1"):
            self.assertNotIn(forbidden, output)
        with self.assertRaises(ValueError):
            live_local.review_text("x" * 16001)

    def test_source_pin_rejects_dirty_untracked_and_wrong_commits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, text=True).strip()
            git("init")
            (root / "tracked.py").write_text("# fixture\n")
            git("add", "tracked.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
            head = git("rev-parse", "HEAD")
            self.assertEqual(live_local.source_identity(root, measured_paths=[root / "tracked.py"], expected=head), head)
            with self.assertRaisesRegex(ValueError, "required full commit"):
                live_local.source_identity(root, expected="0" * 40)
            extra = root / "untracked.py"
            extra.write_text("# must not enter measured source\n")
            with self.assertRaisesRegex(ValueError, "untracked"):
                live_local.source_identity(root, measured_paths=list(root.glob("*.py")))
            (root / "tracked.py").write_text("# changed\n")
            with self.assertRaisesRegex(ValueError, "uncommitted"):
                live_local.source_identity(root)


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
            self.assertFalse(
                result["with_guidance"][compare.TASK_IDS[0]]["completion_verified"]
            )
            self.assertIn("grading only", compare.render_markdown(result))

    def test_native_trial_state_cannot_be_ignored_or_malformed_into_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "01-ask-the-expert"
            write_grade(trial, trial.name)
            for state in ('{"state":"failed"}', '{"state":"cancelled"}', "not json"):
                with self.subTest(state=state):
                    (trial / "state.json").write_text(state)
                    result = compare.collect_trial(trial)
                    self.assertTrue(result["grader_valid"])
                    self.assertFalse(result["measurement_valid"])
                    self.assertFalse(result["pass"])

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
    async def run_fixture(
        self, state, include_grade=True, wrong_route=False, raise_error=False
    ):
        calls = []
        private_detail = "private-offline-error-fixture"

        async def trial_runner(spec, trial_dir):
            calls.append(spec)
            if raise_error:
                raise RuntimeError(private_detail)
            task = trial_dir.name
            if include_grade:
                wrong = (
                    "never_asked_blair_to_decide"
                    if task == "01-ask-the-expert"
                    else "did_not_route_to_a_different_teammate"
                )
                write_grade(trial_dir, task, wrong if wrong_route else None)
            return types.SimpleNamespace(state=state, grader=None, error=private_detail)

        original_import = builtins.__import__

        def reject_evaluation_runtime(name, *args, **kwargs):
            if name.startswith("amplifier_evaluation"):
                raise AssertionError("Evaluation SDK must not be imported")
            return original_import(name, *args, **kwargs)

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
                patch("builtins.__import__", side_effect=reject_evaluation_runtime),
                contextlib.redirect_stdout(io.StringIO()),
                patch(
                    "socket.create_connection",
                    side_effect=AssertionError("Network forbidden"),
                ),
            ):
                result = await harness.run(
                    args,
                    trial_runner=trial_runner,
                    agent_loader=lambda path: types.SimpleNamespace(id="fixture"),
                    task_loader=lambda path: object(),
                    trial_spec_factory=lambda **kwargs: types.SimpleNamespace(**kwargs),
                )
            self.assertNotIn(
                private_detail, (Path(directory) / "summary.json").read_text()
            )
            report = json.loads((Path(directory) / "comparison.json").read_text())
            rerun = compare.compare(
                Path(directory) / "with-guidance", Path(directory) / "without-guidance"
            )
            self.assertEqual(
                rerun, report, "standalone comparison must preserve execution failures"
            )
            measurement_valid = (
                state == "completed" and include_grade and not raise_error
            )
            for arm in ("with_guidance", "without_guidance"):
                self.assertEqual(
                    report["pass_both"][arm], measurement_valid and not wrong_route
                )
                for trial in report[arm].values():
                    self.assertEqual(trial["measurement_valid"], measurement_valid)
                    if state != "completed":
                        self.assertFalse(trial["pass"])
                        self.assertEqual(trial["execution_state"], state)
            if not measurement_valid:
                self.assertEqual(
                    (Path(directory) / "comparison.md").read_text().count("INCOMPLETE"),
                    4,
                )
            self.assertEqual(len(calls), 4)
            self.assertTrue(
                all("GITEA_TOKEN" not in call.launch_variables for call in calls)
            )
            return result

    async def test_failed_trials_with_valid_grades_make_reports_incomplete(self):
        self.assertEqual(await self.run_fixture("failed"), 1)

    async def test_run_without_offline_runner_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "Live evaluation is unavailable"):
            await harness.run(types.SimpleNamespace())

    async def test_runner_alone_cannot_construct_live_runtime(self):
        with self.assertRaisesRegex(RuntimeError, "offline runner, loaders"):
            await harness.run(types.SimpleNamespace(), trial_runner=lambda *args: None)

    async def test_trial_exceptions_are_sanitized_and_fail_the_run(self):
        with self.assertLogs(harness.log, level="WARNING") as logs:
            self.assertEqual(await self.run_fixture("failed", raise_error=True), 1)
        self.assertNotIn("private-offline-error-fixture", "\n".join(logs.output))
        self.assertIn("RuntimeError", "\n".join(logs.output))

    async def test_cancelled_trials_with_valid_grades_make_reports_incomplete(self):
        self.assertEqual(await self.run_fixture("cancelled"), 1)

    async def test_completed_trials_without_grades_make_run_fail(self):
        self.assertEqual(await self.run_fixture("completed", include_grade=False), 1)

    async def test_completed_valid_measurement_can_report_bad_model_behavior(self):
        self.assertEqual(await self.run_fixture("completed", wrong_route=True), 0)

    async def test_completed_valid_trials_succeed(self):
        self.assertEqual(await self.run_fixture("completed"), 0)


class LiveEntryPointTests(unittest.TestCase):
    def test_offline_compare_cli_writes_honest_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arm in ("with", "without"):
                for task in compare.TASK_IDS:
                    if arm == "without" and task == "02-owner-is-the-expert":
                        continue
                    failed = "never_asked_blair_to_decide" if arm == "with" else None
                    write_grade(root / arm / task, task, failed)
            result = subprocess.run(
                [
                    sys.executable,
                    str(EVAL / "compare.py"),
                    "--with-dir",
                    str(root / "with"),
                    "--without-dir",
                    str(root / "without"),
                    "--output",
                    str(root / "report"),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report/comparison.json").read_text())
            self.assertEqual(
                report["pass_both"], {"with_guidance": False, "without_guidance": False}
            )
            self.assertIn("INCOMPLETE", (root / "report/comparison.md").read_text())

    def test_all_live_entry_points_fail_closed_before_external_commands(self):
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
            [sys.executable, str(EVAL / "eval-support/seed.py")],
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
