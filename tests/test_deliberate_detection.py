"""Offline publication races: deliberate tool calls and automatic detectors."""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_lesson_detection import Coordinator, RECORD_VERDICT, add_turn, insight_ops
from amplifier_module_hooks_teamwork import (
    Journal,
    RecordInsightTool,
    SyncError,
    TeamworkHook,
    decision_judge,
)
import amplifier_module_hooks_teamwork as teamwork


class AcceptedClient:
    """The service's actual successful publish shape, not an unknown outcome."""

    def __init__(self, manual_outcome=None):
        self.requests = []
        self.manual_outcome = manual_outcome

    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, json.loads(json.dumps(body)), key))
        if endpoint == "context":
            return {
                "items": [],
                "has_more": False,
                "next_cursor": None,
                "truncated": False,
                "message_status": [],
            }
        operations = body.get("operations", [])
        if (
            any(op["op"] == "insight.upsert" for op in operations)
            and self.manual_outcome
        ):
            outcome, self.manual_outcome = self.manual_outcome, None
            if outcome == "unknown":
                return {}
            raise SyncError(status=403, body={"error": "synthetic refusal"})
        return {"results": [{"id": op["id"], "version": 1} for op in operations]}


MANUAL = {
    "claim": "A deliberate statement about this synthetic finding.",
    "basis": "observation",
    "confidence": "high",
    "limitations": "Fixture only.",
    "evidence": [{"kind": "external", "uri": "fixture://manual"}],
}


class DeliberateSuppression(unittest.IsolatedAsyncioTestCase):
    def build(self, manual_outcome=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = AcceptedClient(manual_outcome)
        hook = TeamworkHook(
            Coordinator(),
            {
                "base_url": "https://team.example.invalid",
                "project_id": "fixture",
                "token": "synthetic-unused-token",
            },
            Journal(Path(temp.name) / "journal.db"),
            client,
            decision_detection=True,
            lesson_detection=True,
        )
        return hook, client

    async def test_delayed_automatic_write_does_not_suppress_new_turn_or_other_detector(
        self,
    ):
        hook, client = self.build()
        add_turn(hook, "first synthetic turn", "first finding")
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def judge(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 1:
                started.set()
                await release.wait()
                return dict(RECORD_VERDICT)
            return decision_judge.no_verdict("synthetic follow-up")

        with patch.object(decision_judge, "judge_window", judge):
            first = hook.detect_decision()
            await asyncio.wait_for(started.wait(), 1)
            add_turn(hook, "second synthetic turn", "an independent finding")
            release.set()
            await first
            self.assertEqual(hook.state.get("deliberate_record_turn", 0), 0)
            second = hook.detect_decision()
            if second:
                await second
        lesson = AsyncMock(return_value=decision_judge.no_verdict("synthetic lesson"))
        with patch.object(decision_judge, "judge_lesson_window", lesson):
            task = hook.detect_lesson()
            if task:
                await task
        self.assertEqual(len(calls), 2)
        self.assertEqual(lesson.await_count, 1)
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_manual_record_during_judgment_prevents_returning_auto_write(self):
        hook, client = self.build()
        add_turn(hook, "synthetic turn", "finding")
        started, release = asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.set()
            await release.wait()
            return dict(RECORD_VERDICT)

        with patch.object(decision_judge, "judge_lesson_window", judge):
            automatic = hook.detect_lesson()
            await asyncio.wait_for(started.wait(), 1)
            result = await RecordInsightTool(hook).execute(dict(MANUAL))
            self.assertTrue(result.success)
            release.set()
            await automatic
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_unknown_manual_outcome_suppresses_a_potential_duplicate(self):
        hook, client = self.build("unknown")
        add_turn(hook, "synthetic turn", "finding")
        result = await RecordInsightTool(hook).execute(dict(MANUAL))
        self.assertEqual(result.error["outcome"], "unknown")
        judge = AsyncMock(return_value=dict(RECORD_VERDICT))
        with patch.object(decision_judge, "judge_lesson_window", judge):
            task = hook.detect_lesson()
            if task:
                await task
        self.assertEqual(judge.await_count, 0)
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_manual_stamp_uses_call_origin_turn_before_lock_wait(self):
        hook, _client = self.build()
        add_turn(hook, "first synthetic turn", "first finding")
        await hook.lock.acquire()
        task = asyncio.create_task(RecordInsightTool(hook).execute(dict(MANUAL)))
        await asyncio.sleep(0)
        add_turn(hook, "second synthetic turn", "second finding")
        hook.lock.release()
        result = await task
        self.assertTrue(result.success)
        self.assertEqual(hook.state["deliberate_record_turn"], 1)

    async def test_definite_manual_refusal_does_not_suppress_detection(self):
        hook, client = self.build("refused")
        add_turn(hook, "synthetic turn", "finding")
        result = await RecordInsightTool(hook).execute(dict(MANUAL))
        self.assertFalse(result.success)
        judge = AsyncMock(return_value=dict(RECORD_VERDICT))
        with patch.object(decision_judge, "judge_lesson_window", judge):
            task = hook.detect_lesson()
            if task:
                await task
        self.assertEqual(judge.await_count, 1)
        self.assertEqual(len(insight_ops(client)), 2)

    async def test_model_input_cannot_disable_deliberate_tracking(self):
        hook, _client = self.build()
        add_turn(hook, "synthetic turn", "finding")
        result = await RecordInsightTool(hook).execute(dict(MANUAL, automatic=True))
        self.assertTrue(result.success)
        self.assertEqual(hook.state["deliberate_record_turn"], 1)

    async def test_refused_manual_attempt_during_judgment_does_not_discard_verdict(
        self,
    ):
        hook, client = self.build("refused")
        add_turn(hook, "synthetic turn", "finding")
        started, release = asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.set()
            await release.wait()
            return dict(RECORD_VERDICT)

        with patch.object(decision_judge, "judge_lesson_window", judge):
            automatic = hook.detect_lesson()
            await asyncio.wait_for(started.wait(), 1)
            result = await RecordInsightTool(hook).execute(dict(MANUAL))
            self.assertFalse(result.success)
            release.set()
            await automatic
        self.assertEqual(len(insight_ops(client)), 2)
        self.assertEqual(hook.state.get("deliberate_record_turn", 0), 0)

    async def test_cancelled_manual_submission_keeps_deliberate_reservation(self):
        hook, client = self.build()
        add_turn(hook, "synthetic turn", "finding")
        started, release = threading.Event(), threading.Event()
        request = client.request

        def stalled_request(endpoint, body, key=None):
            if any(op["op"] == "insight.upsert" for op in body.get("operations", [])):
                started.set()
                release.wait(2)
            return request(endpoint, body, key)

        with patch.object(client, "request", stalled_request):
            task = asyncio.create_task(RecordInsightTool(hook).execute(dict(MANUAL)))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(hook.state["deliberate_record_turn"], 1)
                self.assertEqual(
                    hook.journal.load(hook.sid)["deliberate_record_generation"], 1
                )
            finally:
                release.set()

    async def test_lesson_rebind_discards_old_window_even_if_judge_resists_cancel(self):
        hook, old_client = self.build()
        add_turn(hook, "synthetic project A turn", "finding")
        started, release = asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
            return dict(RECORD_VERDICT)

        new_client = AcceptedClient()
        with patch.object(decision_judge, "judge_lesson_window", judge):
            task = hook.detect_lesson()
            await asyncio.wait_for(started.wait(), 1)
            with patch.object(teamwork, "HTTPClient", return_value=new_client):
                hook.rebind("project-b", {"token": "synthetic-project-b-token"})
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(insight_ops(old_client), [])
        self.assertEqual(insight_ops(new_client), [])

    async def test_concurrent_lesson_verdicts_reserve_one_publication(self):
        hook, client = self.build()
        add_turn(hook, "first turn", "finding")
        started, release = asyncio.Queue(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.put_nowait(True)
            await release.wait()
            return dict(RECORD_VERDICT)

        with patch.object(decision_judge, "judge_lesson_window", judge):
            first = hook.detect_lesson()
            await asyncio.wait_for(started.get(), 1)
            add_turn(hook, "second turn", "same finding")
            second = hook.detect_lesson()
            await asyncio.wait_for(started.get(), 1)
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_lesson_only_shutdown_drains_owned_task(self):
        hook, client = self.build()
        hook.decision_detection = False
        add_turn(hook, "synthetic turn", "finding")
        with patch.object(
            decision_judge,
            "judge_lesson_window",
            AsyncMock(return_value=dict(RECORD_VERDICT)),
        ):
            await asyncio.wait_for(hook.cleanup(), 1)
        self.assertEqual(len(insight_ops(client)), 1)
        self.assertEqual(hook._lesson_tasks, set())

    async def test_lesson_shutdown_timeout_cancels_owned_judge(self):
        hook, client = self.build()
        hook.decision_detection = False
        add_turn(hook, "synthetic turn", "finding")
        cleaned = asyncio.Event()

        async def judge(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with (
            patch.object(decision_judge, "judge_lesson_window", judge),
            patch.object(teamwork, "DETECTION_DRAIN_SECONDS", 0.01),
        ):
            await asyncio.wait_for(hook.cleanup(), 1)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(hook._lesson_tasks, set())
        self.assertEqual(insight_ops(client), [])


if __name__ == "__main__":
    unittest.main()
