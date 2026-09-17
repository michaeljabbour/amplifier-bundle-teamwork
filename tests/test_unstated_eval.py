"""Evaluation outages must not become successful classifier results."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


class UnstatedEvaluationAvailability(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "evals/02-decision-detection/run_unstated.py"
        spec = importlib.util.spec_from_file_location("unstated_eval_under_test", path)
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_unavailable_and_malformed_verdicts_never_count_as_correct(self):
        for expected in ("RECORD", "SKIP"):
            case = {"id": "fixture", "expected": expected, "note": "fixture"}
            for verdict in (None, {}, {"available": False, "record": False}):
                result = self.runner.assess_case(case, verdict)
                self.assertEqual(result["judged"], "UNAVAILABLE")
                self.assertFalse(result["correct"])
        result = self.runner.assess_case(case, {"available": True, "record": False})
        self.assertEqual(result["judged"], "SKIP")
        self.assertTrue(result["correct"])

    async def test_all_unavailable_run_exits_nonzero_and_reports_no_correct_skips(self):
        runner = self.runner
        root = SimpleNamespace(coordinator=object(), cleanup=AsyncMock())
        verdict = runner.decision_judge.no_verdict("offline fixture")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "__file__", str(Path(directory) / "run.py")), \
                patch.object(runner, "_load_keys"), \
                patch.dict(os.environ, {"ANTHROPIC_API_KEY": "offline-fixture-not-a-credential"}), \
                patch.object(runner, "_build_root_session", AsyncMock(return_value=root)), \
                patch.object(runner.decision_judge, "judge_window", AsyncMock(return_value=verdict)), \
                contextlib.redirect_stdout(io.StringIO()):
            code = await runner.run()
            files = list((Path(directory) / "results").glob("*.json"))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text())
        self.assertEqual(code, 1)
        self.assertEqual(report["correct"], 0)
        self.assertEqual(report["unavailable_count"], len(runner.CASES_UNSTATED))
        self.assertEqual(report["miss_count"], 0)
        self.assertEqual(report["false_positive_count"], 0)
        root.cleanup.assert_awaited_once()
