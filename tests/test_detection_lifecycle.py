"""Consent, concurrent publication and the host's real session-end cleanup."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from test_decision_detection import build, add_turn, insight_ops, RECORD_VERDICT, Client
import amplifier_module_hooks_teamwork as teamwork


class AcceptedClient(Client):
    def request(self, endpoint, body, key=None):
        super().request(endpoint, body, key)
        if endpoint == "publish":
            return {"results": [{"id": op["id"], "version": 1} for op in body["operations"]]}
        return {"items": [], "next_cursor": None, "has_more": False,
                "truncated": False, "message_status": []}


class DetectionLifecycle(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        hook, _, journal = build(self)
        hook.client = client = AcceptedClient()
        add_turn(hook, "Synthetic project A prompt", "Synthetic project A conclusion")
        return hook, client, journal

    async def test_rebinding_during_judgment_never_publishes_the_old_window(self):
        hook, old_client, _ = self.fixture()
        started, release = asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Even a provider returning a verdict after cancellation cannot
                # cross the captured binding boundary.
                pass
            return dict(RECORD_VERDICT)

        new_client = AcceptedClient()
        with patch.object(teamwork.decision_judge, "judge_window", judge):
            task = hook.detect_decision()
            await started.wait()
            with patch.object(teamwork, "HTTPClient", return_value=new_client):
                hook.rebind("project-b", {"token": "synthetic-project-b-token"})
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(insight_ops(old_client), [])
        self.assertEqual(insight_ops(new_client), [])

    async def test_rebinding_while_source_registration_awaits_never_crosses_projects(self):
        hook, old_client, _ = self.fixture()
        started, release = asyncio.Event(), asyncio.Event()

        async def flush():
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

        new_client = AcceptedClient()
        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))), \
                patch.object(hook, "flush", flush):
            task = hook.detect_decision()
            await started.wait()
            with patch.object(teamwork, "HTTPClient", return_value=new_client):
                hook.rebind("project-b", {"token": "synthetic-project-b-token"})
            release.set()
            await task
        self.assertEqual(insight_ops(old_client), [])
        self.assertEqual(insight_ops(new_client), [])

    async def test_concurrent_identical_verdicts_make_one_request(self):
        hook, client, journal = self.fixture()
        calls, ready, first_started = 0, asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            nonlocal calls
            calls += 1
            first_started.set()
            if calls == 2:
                ready.set()
            await ready.wait()
            return dict(RECORD_VERDICT)

        with patch.object(teamwork.decision_judge, "judge_window", judge):
            first = hook.detect_decision(tool_calls=[{"name": "delegate", "target": "a"}])
            await asyncio.wait_for(first_started.wait(), 1)
            add_turn(hook, "Second completed window", "The same decision restated")
            second = hook.detect_decision(tool_calls=[{"name": "delegate", "target": "b"}])
            await asyncio.gather(first, second)
        self.assertEqual(calls, 2)
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_cancelled_publish_keeps_reservation_and_cannot_duplicate(self):
        hook, _, journal = self.fixture()
        started = asyncio.Event()

        async def uncertain_write(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))), \
                patch.object(teamwork.RecordInsightTool, "execute", uncertain_write):
            task = hook.detect_decision()
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        fingerprint = teamwork.sha(" ".join(RECORD_VERDICT["claim"].lower().split()))
        self.assertTrue(journal.decision_seen(hook.sid, fingerprint))
        add_turn(hook, "again", "same conclusion")
        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))), \
                patch.object(teamwork.RecordInsightTool, "execute", AsyncMock()) as publish:
            await hook.detect_decision()
        publish.assert_not_called()

    async def test_definite_refusal_releases_reservation_for_a_later_window(self):
        hook, client, journal = self.fixture()
        from amplifier_core.models import ToolResult
        refusal = ToolResult(success=False, error={"message": "Not recorded: permission denied"})
        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))), \
                patch.object(teamwork.RecordInsightTool, "execute", AsyncMock(return_value=refusal)):
            await hook.detect_decision()
        fingerprint = teamwork.sha(" ".join(RECORD_VERDICT["claim"].lower().split()))
        self.assertFalse(journal.decision_seen(hook.sid, fingerprint))
        add_turn(hook, "retry with permission", "same conclusion")
        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            await hook.detect_decision()
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_session_end_drains_a_non_delegated_decision(self):
        hook, client, _ = self.fixture()

        async def judge(*args, **kwargs):
            await asyncio.sleep(.01)
            return dict(RECORD_VERDICT)

        with patch.object(teamwork.decision_judge, "judge_window", judge):
            await hook.on_end("session:end", {})
            self.assertEqual(len(insight_ops(client)), 1)
            self.assertEqual(hook._decision_tasks, set())

    async def test_shutdown_deadline_cancels_and_awaits_owned_judge(self):
        hook, client, _ = self.fixture()
        cleaned = asyncio.Event()

        async def judge(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with patch.object(teamwork.decision_judge, "judge_window", judge), \
                patch.object(teamwork, "DETECTION_DRAIN_SECONDS", .01, create=True):
            await asyncio.wait_for(hook.on_end("session:end", {}), 1)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(hook._decision_tasks, set())
        self.assertEqual(insight_ops(client), [])
        self.assertIsNone(hook.detect_decision(tool_calls=[{"name": "delegate"}]))

    async def test_real_stock_core_cleanup_owns_returned_hook_cleanup(self):
        from amplifier_core import AmplifierSession
        hook, client, journal = self.fixture()
        session = AmplifierSession(config={
            "session": {"orchestrator": "loop-streaming", "context": "context-simple"},
            "providers": [], "tools": [], "hooks": [],
        })
        # Real stock-Core init/cleanup with inert local module mounts. No module
        # downloads or model invocation are needed to exercise the host lifecycle.
        from amplifier_core.loader import ModuleLoader

        async def load_fixture(loader, module_id, *args, **kwargs):
            async def mount_fixture(coordinator):
                point = "orchestrator" if module_id == "loop-streaming" else "context"
                await coordinator.mount(point, SimpleNamespace())
            return mount_fixture

        with patch.object(ModuleLoader, "load", load_fixture):
            await session.initialize()
        try:
            with patch.object(teamwork, "HTTPClient", return_value=client):
                cleanup = await teamwork.mount(session.coordinator, {
                    "share_visible_turns": True, "detect_decisions": True,
                    "base_url": "https://team.example.invalid", "project_id": "project-a",
                    "token": "synthetic-token", "journal_path": journal.path,
                    "file_inbound_reports": False,
                })
            self.assertTrue(callable(cleanup))
            # This is the same public contract used by Core's module loader.
            session.coordinator.register_cleanup(cleanup)
            mounted_hook = cleanup.__self__
            add_turn(mounted_hook, "No delegation", "Completed synthetic conclusion")
            with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
                await session.cleanup()
            self.assertEqual(len(insight_ops(client)), 1)
            self.assertEqual(mounted_hook._decision_tasks, set())
        finally:
            await session.cleanup()

    async def test_empty_delegations_do_not_repeat_paid_judgments(self):
        hook, _, _ = self.fixture()
        with patch.object(teamwork.decision_judge, "judge_window", AsyncMock(return_value={"record": False})) as judge:
            await hook.detect_decision()
            for _ in range(10):
                self.assertIsNone(hook.detect_decision(tool_calls=[{"name": "delegate"}]))
        self.assertEqual(judge.await_count, 1)

    async def test_task_limit_retains_unexamined_turns_without_spawning_more_work(self):
        hook, _, journal = self.fixture()
        release = asyncio.Event()
        started = asyncio.Queue()

        async def judge(*args, **kwargs):
            started.put_nowait(True)
            await release.wait()
            return {"record": False}

        with patch.object(teamwork.decision_judge, "judge_window", judge):
            first = hook.detect_decision()
            await asyncio.wait_for(started.get(), 1)
            add_turn(hook, "second", "second")
            second = hook.detect_decision()
            await asyncio.wait_for(started.get(), 1)
            add_turn(hook, "third", "third")
            self.assertIsNone(hook.detect_decision())
            self.assertEqual(journal.decision_watermark(hook.sid), 2)
            self.assertEqual([t["turn_index"] for t in hook.state["decision_turns"]], [3])
            release.set()
            await asyncio.gather(first, second)

    async def test_judge_deadline_cleans_up_without_publishing(self):
        hook, client, _ = self.fixture()
        cleaned = asyncio.Event()

        async def judge(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with patch.object(teamwork.decision_judge, "judge_window", judge), \
                patch.object(teamwork, "DETECTION_JUDGE_SECONDS", .01):
            await asyncio.wait_for(hook.detect_decision(), 1)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(insight_ops(client), [])

    async def test_rebinding_requests_owned_judge_cancellation(self):
        hook, _, _ = self.fixture()
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def judge(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with patch.object(teamwork.decision_judge, "judge_window", judge):
            task = hook.detect_decision()
            await started.wait()
            with patch.object(teamwork, "HTTPClient", return_value=AcceptedClient()):
                hook.rebind("project-b", {"token": "synthetic-project-b-token"})
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(cleaned.is_set())


if __name__ == "__main__":
    unittest.main()
