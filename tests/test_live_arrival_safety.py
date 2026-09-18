"""Versioned delivery, retries and consent boundaries of the optional live host."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_hook import Client, Context, Coordinator, Journal, TeamworkHook, teamwork_module as tw


def source(number=1, version=1):
    return {"record": {"record_type": "request", "id": str(number), "version": version,
        "key": "request:%s:%s" % (number, version), "change": "upsert",
        "content": {"title": "Fixture question", "response": "context" if version > 1 else None,
                    "progress_note": "Fixture reply" if version > 1 else None}}}


class Runtime:
    def __init__(self): self.commands = []; self.fail = False
    async def submit(self, command):
        if self.fail: raise RuntimeError("PRIVATE-EXCEPTION-CANARY")
        self.commands.append(command)


class LiveSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.runtime = Runtime()
        host = Coordinator(Context([]))
        host.get_capability = lambda name: self.runtime if name == "live.runtime" else None
        self.hook = TeamworkHook(host,
            {"base_url": "https://fixture.invalid", "project_id": "a", "token": "fixture-only"},
            Journal(Path(tmp.name) / "journal.db"), Client([]))
        async def noop(): pass
        self.hook.retrieve = noop
        self.seen = {}

    async def asyncTearDown(self):
        await self.hook.stop_live_watch()

    async def poll(self):
        await self.hook.live_poll(self.hook.detection_binding(), self.seen)

    async def test_answer_edit_of_cached_request_is_delivered_once(self):
        key = "request:1"
        self.hook.state["cache"][key] = source()
        self.seen[key] = self.hook.arrival_version(source())
        self.hook.state["cache"][key] = source(version=2)
        await self.poll(); await self.poll()
        self.assertEqual(len(self.runtime.commands), 1)
        self.assertIn("Fixture reply", self.runtime.commands[0].text)
        self.assertEqual(self.runtime.commands[0].kind, "service")
        self.assertEqual(self.runtime.commands[0].source, "teamwork")

    async def test_batch_overflow_remains_pending_until_next_poll(self):
        self.hook.state["cache"] = {"request:" + str(n): source(n) for n in range(8)}
        await self.poll()
        self.assertEqual(len(self.runtime.commands), 5)
        await self.poll(); await self.poll()
        self.assertEqual(len(self.runtime.commands), 8)
        self.assertEqual(len({c.id for c in self.runtime.commands}), 8)

    async def test_failed_submit_is_retried_without_logging_exception_content(self):
        self.hook.state["cache"]["request:1"] = source()
        self.runtime.fail = True
        with self.assertLogs(tw.logger, level="WARNING") as logs:
            await self.poll()
        self.assertNotIn("PRIVATE-EXCEPTION-CANARY", " ".join(logs.output))
        self.assertEqual(self.seen, {})
        self.runtime.fail = False
        await self.poll(); await self.poll()
        self.assertEqual(len(self.runtime.commands), 1)

    async def test_rebind_during_retrieval_submits_nothing_from_old_binding(self):
        async def rebind():
            self.hook.rebind("b", {"token": "different-fixture"})
            self.hook.state["cache"]["request:1"] = source()
        self.hook.retrieve = rebind
        await self.poll()
        self.assertEqual(self.runtime.commands, [])
        self.assertEqual(self.seen, {})

    async def test_superseded_snapshot_is_skipped_during_batch_delivery(self):
        self.hook.state["cache"] = {"request:" + str(n): source(n) for n in range(2)}
        original = self.runtime.submit
        async def submit(command):
            await original(command)
            self.hook.state["cache"].pop("request:1", None)
        self.runtime.submit = submit
        await self.poll()
        self.assertEqual(len(self.runtime.commands), 1)

    async def test_timeout_and_shutdown_bound_a_cancellation_resistant_host(self):
        release = asyncio.Event()
        accepted = []
        async def stubborn(command):
            while not release.is_set():
                try: await release.wait()
                except asyncio.CancelledError: pass
            self.runtime.commands.append(command)
        self.runtime.submit = stubborn
        with patch.object(tw, "LIVE_SUBMIT_SECONDS", .02), patch.object(tw, "LIVE_STOP_SECONDS", .02):
            start = time.monotonic()
            try:
                self.assertFalse(await self.hook.deliver_live("fixture", "a", accepted=lambda: accepted.append(True)))
                self.assertFalse(await self.hook.deliver_live("fixture", "b"))
                self.assertEqual(len(self.hook._live_submissions), 1)
                with self.assertLogs(tw.logger, level="WARNING"):
                    await self.hook.stop_live_watch()
                self.assertLess(time.monotonic() - start, .3)
                self.assertFalse(await self.hook.deliver_live("fixture", "c"))
            finally:
                release.set()
                await asyncio.gather(*tuple(self.hook._live_submissions), return_exceptions=True)
            self.assertEqual(accepted, [])  # late host completion cannot acknowledge after shutdown

    async def test_late_success_before_shutdown_counts_once(self):
        release = asyncio.Event()
        async def stubborn(command):
            try: await release.wait()
            except asyncio.CancelledError: await release.wait()
            self.runtime.commands.append(command)
        self.runtime.submit = stubborn
        self.hook.state["cache"]["request:1"] = source()
        with patch.object(tw, "LIVE_SUBMIT_SECONDS", .01):
            await self.poll()
            self.assertEqual(self.seen, {})
            release.set()
            await asyncio.gather(*tuple(self.hook._live_submissions))
            await self.poll()
        self.assertEqual(len(self.runtime.commands), 1)

    async def test_absent_capability_starts_no_watcher(self):
        self.hook.coordinator.get_capability = lambda name: None
        self.hook.start_live_watch()
        self.assertIsNone(self.hook._live_task)
        self.assertEqual(self.hook._live_submissions, set())

    async def test_watch_error_logs_never_include_private_exception_text(self):
        async def bad(*args):
            self.hook._live_closing = True
            raise RuntimeError("PRIVATE-EXCEPTION-CANARY")
        self.hook.live_poll = bad
        with patch.object(tw, "LIVE_POLL_SECONDS", 0), self.assertLogs(tw.logger) as logs:
            await self.hook.live_watch()
        self.assertNotIn("PRIVATE-EXCEPTION-CANARY", " ".join(logs.output))

    async def test_mount_returns_cleanup_when_optional_detectors_are_off(self):
        class Hooks:
            def register(self, *args, **kwargs): pass
        self.hook.coordinator.hooks = Hooks()
        self.hook.coordinator.register_capability = lambda *args: None
        with patch.object(tw, "resolve_connection", return_value=(self.hook.connection, Path(self.hook.journal.path).parent)):
            cleanup = await tw.mount(self.hook.coordinator, {"share_visible_turns": True, "file_inbound_reports": False})
        self.assertTrue(callable(cleanup))
        mounted = cleanup.__self__
        mounted.client = Client([])
        mounted.start_live_watch()
        await cleanup()
        self.assertIsNone(mounted._live_task)
        self.assertTrue(mounted._live_closing)
        self.assertTrue(mounted._session_ended)
