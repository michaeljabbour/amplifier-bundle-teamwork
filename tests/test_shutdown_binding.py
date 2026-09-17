"""A shutdown belongs to the project selected when shutdown began."""
import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from test_decision_detection import Client, add_turn, build
import amplifier_module_hooks_teamwork as teamwork


class ShutdownBinding(unittest.IsolatedAsyncioTestCase):
    async def rebind_during_flush(self, *, detectors=False, callback_first=False, concurrent=False):
        hook, old_client, journal = build(self, decision_detection=detectors)
        hook.lesson_detection = detectors
        add_turn(hook, "Synthetic old-project prompt", "Synthetic response")
        old_sid = hook.sid
        entered, release = threading.Event(), threading.Event()
        original_flush = journal.flush
        flushed_bindings = []

        def paused_flush(sid, client):
            # Pause inside the real to_thread boundary: flush arguments have
            # already captured the old project and client before rebinding.
            flushed_bindings.append((sid, client))
            if len(flushed_bindings) == 1:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("Synthetic flush barrier timed out")
            original_flush(sid, client)

        journal.flush = paused_flush
        new_client = Client()
        with patch.object(teamwork, "HTTPClient", return_value=new_client), \
                patch.object(teamwork.decision_judge, "judge_window", AsyncMock()) as decision, \
                patch.object(teamwork.decision_judge, "judge_lesson_window", AsyncMock()) as lesson:
            task = asyncio.create_task(hook.cleanup() if callback_first else hook.on_end("session:end", {}))
            other = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                if concurrent:
                    other = asyncio.create_task(hook.cleanup())
                    await asyncio.sleep(0)
                new_sid = hook.rebind("new-synthetic-project", {"token": "new-synthetic-credential"})
            finally:
                release.set()
            await asyncio.wait_for(task, 3)
            if other:
                await asyncio.wait_for(other, 3)
            # Released Core invokes the callback before session:end. Other
            # hosts reverse that order, and repeated cleanup must be harmless.
            await hook.on_end("session:end", {})
            await hook.cleanup()
            decision.assert_not_awaited()
            lesson.assert_not_awaited()

        self.assertNotEqual(new_sid, old_sid)
        self.assertEqual(hook.state["version"], 0)
        self.assertFalse(hook.entered)
        self.assertEqual(new_client.requests, [])
        self.assertTrue(flushed_bindings)
        self.assertTrue(all(sid == old_sid and client is old_client for sid, client in flushed_bindings))
        old_statuses = [op["data"].get("status") for endpoint, body, _ in old_client.requests
                        if endpoint == "publish" for op in body["operations"]
                        if op["op"] == "session.upsert"]
        self.assertEqual(old_statuses, ["active", "completed"])
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE session=?", (old_sid,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE session=?", (new_sid,)).fetchone()[0], 0)
        self.assertFalse(hook._decision_tasks | hook._lesson_tasks)

    async def test_default_off_shutdown_does_not_end_a_new_project(self):
        await self.rebind_during_flush()

    async def test_both_detectors_keep_shutdown_and_flush_on_the_old_project(self):
        await self.rebind_during_flush(detectors=True)

    async def test_cleanup_callback_before_end_does_not_finalize_twice_after_rebind(self):
        await self.rebind_during_flush(detectors=True, callback_first=True)

    async def test_concurrent_cleanup_keeps_the_first_binding(self):
        await self.rebind_during_flush(detectors=True, callback_first=True, concurrent=True)

    async def test_rebind_while_cleanup_waits_for_lock_still_drains_old_tasks(self):
        hook, _client, _journal = build(self)
        entered = asyncio.Event()

        async def old_judge():
            entered.set()
            await asyncio.Event().wait()

        old_task = asyncio.create_task(old_judge())
        hook._decision_tasks.add(old_task)
        await entered.wait()
        new_client = Client()
        await hook.lock.acquire()
        try:
            cleanup = asyncio.create_task(hook.cleanup())
            await asyncio.sleep(0)
            with patch.object(teamwork, "HTTPClient", return_value=new_client):
                hook.rebind("new-synthetic-project", {"token": "new-synthetic-credential"})
        finally:
            hook.lock.release()
        await asyncio.wait_for(cleanup, 2)
        self.assertTrue(old_task.cancelled())
        self.assertFalse(hook._decision_tasks | hook._lesson_tasks)
        self.assertTrue(hook._detection_publish_closed)
        self.assertEqual(new_client.requests, [])
        self.assertEqual(hook.state["version"], 0)


if __name__ == "__main__":
    unittest.main()
