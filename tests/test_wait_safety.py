"""Wait results must belong to the exact request, consent and observed window."""
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_hook import Client, Context, Coordinator, Journal, TeamworkHook, WaitTool, teamwork_module as tw


def answer(request_id="req-1"):
    return {"record_type": "request", "id": request_id, "change": "upsert",
            "content": {"response": "context", "progress_note": "Verified fixture answer"}}


class WaitSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hook = TeamworkHook(Coordinator(Context([])),
            {"base_url": "https://fixture.invalid", "project_id": "a", "token": "fixture-only"},
            Journal(Path(tmp.name) / "journal.db"), Client([]))
        self.hook.entered = True
        async def noop(): pass
        self.hook.flush = noop
        self.hook.retrieve = noop
        def presence(*args, **kwargs): self.hook.presence_status = "waiting"
        self.hook.report_presence = presence

    async def wait(self):
        with patch.object(tw, "WAIT_MAX_SECONDS", 0.08), patch.object(tw, "WAIT_POLL_SECONDS", 0):
            return await WaitTool(self.hook).execute({"reason": "fixture", "request_id": "req-1"})

    async def test_project_switch_is_not_an_answer(self):
        async def rebind(): self.hook.rebind("b", {"token": "other-fixture"})
        self.hook.retrieve = rebind
        result = await self.wait()
        self.assertFalse(result.success)
        self.assertIn("project changed", result.error["message"])
        self.assertIsNone(self.hook.answer)

    async def test_replacement_wait_cannot_supply_another_requests_answer(self):
        async def replace():
            await self.hook.declare_wait("another", "req-2")
            self.hook.note_answer(answer("req-2"))
        self.hook.retrieve = replace
        result = await self.wait()
        self.assertFalse(result.success)
        self.assertEqual(self.hook.answer["request_id"], "req-2")

    async def test_same_request_replacement_still_owns_a_separate_observation(self):
        async def replace():
            await self.hook.declare_wait("another", "req-1")
            self.hook.note_answer(answer())
        self.hook.retrieve = replace
        self.assertFalse((await self.wait()).success)

    async def test_cleared_wait_without_answer_does_not_report_answered(self):
        async def clear(): self.hook.waiting_on = None
        self.hook.retrieve = clear
        result = await self.wait()
        self.assertEqual(result.output["state"], "waiting")
        self.assertNotIn("answer", result.output)

    async def test_answer_cached_before_wait_is_returned_without_a_delta(self):
        self.hook.state["cache"]["request:req-1"] = {"record": answer()}
        result = await self.wait()
        self.assertEqual(result.output["answer"]["note"], "Verified fixture answer")
        self.assertEqual(result.output["state"], "answered")

    async def test_cached_other_request_does_not_answer_this_one(self):
        self.hook.state["cache"]["request:req-2"] = {"record": answer("req-2")}
        result = await self.wait()
        self.assertEqual(result.output["state"], "waiting")

    async def test_deadline_includes_slow_context_and_lock_contention(self):
        async def slow(): await asyncio.sleep(1)
        self.hook.retrieve = slow
        start = time.monotonic()
        result = await self.wait()
        self.assertLess(time.monotonic() - start, .4)
        self.assertFalse(result.output["observed"])
        self.assertNotIn("answer", result.output)
        await self.hook.lock.acquire()
        try:
            start = time.monotonic()
            self.assertEqual((await self.wait()).output["state"], "waiting")
            self.assertLess(time.monotonic() - start, .4)
        finally:
            self.hook.lock.release()

    async def test_late_threaded_reply_cannot_mutate_new_project(self):
        started, release = threading.Event(), threading.Event()
        original = Client([])
        class Slow:
            def request(self, endpoint, body, key=None):
                started.set()
                release.wait(2)
                return original.request(endpoint, body, key)
        self.hook.client = Slow()
        pending = asyncio.create_task(TeamworkHook.retrieve(self.hook))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            self.hook.rebind("b", {"token": "other-fixture"})
            release.set()
            await pending
            self.assertEqual(self.hook.state["cache"], {})
            self.assertIsNone(self.hook.state["cursor"])
        finally:
            release.set()
            await pending

    async def test_cancelled_http_read_does_not_apply_late_reply(self):
        started, release = threading.Event(), threading.Event()
        original = Client([])
        class Slow:
            def request(self, endpoint, body, key=None):
                started.set()
                release.wait(2)
                return original.request(endpoint, body, key)
        self.hook.client = Slow()
        self.hook.retrieve = lambda: TeamworkHook.retrieve(self.hook)
        try:
            result = await self.wait()
            self.assertTrue(started.is_set())
            self.assertEqual(result.output["state"], "waiting")
            release.set()
            await asyncio.sleep(.02)
            self.assertEqual(self.hook.state["cache"], {})
        finally:
            release.set()

    async def test_exact_answer_positive_control(self):
        async def receive(): self.hook.note_answer(answer())
        self.hook.retrieve = receive
        result = await self.wait()
        self.assertTrue(result.success)
        self.assertEqual(result.output["request_id"], result.output["answer"]["request_id"])
