"""The detection surface is observable, and cannot silently stop being.

Detection's only observable result used to be a successful publish. The eleven
other outcomes went to a local SQLite table with no subscriber, which is why
"does detection work?" had no answer -- a mechanism whose failures are invisible
can only be said to have shipped.
"""
import ast
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, mount
from amplifier_module_hooks_teamwork import events as detection_events

# BOTH modules, because the outcomes are reached from both now: the detector
# reports what it could not judge, and the recorder reports what it could not
# write. A guard that walked only one of them would pass while half the call
# sites went unchecked -- and it caught exactly that when the recorder moved out.
PACKAGE = (Path(__file__).resolve().parents[1]
           / "modules/hooks-teamwork/amplifier_module_hooks_teamwork")
MODULES = [PACKAGE / "__init__.py", PACKAGE / "recording.py"]


class Hooks:
    def __init__(self):
        self.emitted = []

    async def emit(self, name, payload):
        self.emitted.append((name, payload))

    def register(self, *a, **k):
        return None


class Context:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages(self):
        return list(self.messages)


class Coordinator:
    parent_id = None
    session_id = "events-fixture"

    def __init__(self):
        self.hooks = Hooks()
        self.mount_points = {}
        self.context = Context()
        self.contributors = []
        self.capabilities = {}

    def get(self, name):
        return self.context if name == "context" else None

    def get_capability(self, name):
        return None

    def register_capability(self, name, value):
        self.capabilities[name] = value

    def register_contributor(self, channel, name, supplier):
        self.contributors.append((channel, name, supplier))

    async def mount(self, point, value, name):
        self.mount_points.setdefault(point, {})[name] = value


def hook():
    directory = tempfile.mkdtemp(prefix="teamwork-events-")
    return TeamworkHook(Coordinator(),
                        {"base_url": "https://team.example.invalid", "project_id": "p",
                         "token": "fixture-harness-credential-0123456789"},
                        Journal(Path(directory) / "j.sqlite3"), object())


class EveryOutcomeIsAnnounced(unittest.IsolatedAsyncioTestCase):
    async def announce(self, h, *args):
        await h.detection_outcome(*args)
        await asyncio.gather(*tuple(h._detection_event_tasks))

    async def test_a_recorded_decision_is_announced(self):
        h = hook()
        await self.announce(h, "s1", "decision", "recorded", 4, 2)
        self.assertEqual(h.coordinator.hooks.emitted, [(
            "teamwork:decision_recorded",
            {"session_id": "s1", "kind": "decision", "outcome": "recorded",
             "tally_size": 4, "link_count": 2})])

    async def test_a_failure_is_announced_as_a_failure_and_names_which(self):
        """The category is what a consumer acts on; the raw outcome is what an
        audit needs. Losing either would make the surface useless to one of them."""
        h = hook()
        await self.announce(h, "s1", "decision", "judge_failed", 3)
        name, payload = h.coordinator.hooks.emitted[0]
        self.assertEqual(name, "teamwork:decision_failed")
        self.assertEqual(payload["outcome"], "judge_failed")

    async def test_a_deliberate_skip_is_not_reported_as_a_failure(self):
        h = hook()
        await self.announce(h, "s1", "lesson", "no_claim", 3)
        self.assertEqual(h.coordinator.hooks.emitted[0][0], "teamwork:lesson_skipped")

    async def test_the_outcome_is_still_journalled(self):
        """Announcing must ADD a channel, never replace the durable one."""
        h = hook()
        await self.announce(h, "s1", "decision", "recorded", 1, 0)
        with h.journal.connect() as conn:
            rows = conn.execute("SELECT kind, outcome FROM detection_outcome").fetchall()
        self.assertEqual(rows, [("decision", "recorded")])

    async def test_every_outcome_the_module_records_has_an_event(self):
        """THE GUARD. The one way this surface can silently lose a signal is an
        outcome the code reaches and the table does not name, so this reads the
        module's own call sites rather than trusting the table to be complete.

        Parsed, not grepped: a regex over source text crosses statement
        boundaries and reported an `agent_status` value as a detection outcome
        the first time it ran. An approximate guard that cries wolf gets
        loosened until it guards nothing.
        """
        def reached_in(module):
            found = set()
            for node in ast.walk(ast.parse(module.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name not in ("detection_outcome", "record_detection_outcome", "_reported"):
                    continue
                def outcomes(value):
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return {value.value}
                    if isinstance(value, ast.IfExp):
                        return outcomes(value.body) | outcomes(value.orelse)
                    return set()
                if len(node.args) >= 3:
                    found.update(outcomes(node.args[2]))
            return found

        per_module = {module.name: reached_in(module) for module in MODULES}
        # ANTI-VACUITY, and it is not a magic number: EVERY module that reports
        # outcomes must have contributed call sites. A count would have passed
        # while one whole module went unread -- which is exactly what happened
        # when the recorder moved out of the hook and this test found it.
        for name, found in per_module.items():
            self.assertTrue(found, "no outcome call sites found in %s; the guard is not reading it" % name)
        reached = set().union(*per_module.values())
        unmapped = reached - set(detection_events.OUTCOMES)
        self.assertEqual(unmapped, set(),
                         "outcomes reached by the code but not announced: %s" % sorted(unmapped))

    async def test_nothing_is_emitted_that_was_never_advertised(self):
        for kind in ("decision", "lesson"):
            for outcome in detection_events.OUTCOMES:
                name = detection_events.event_for(kind, outcome)
                self.assertIn(name, detection_events.ALL_EVENTS, outcome)

    async def test_an_unknown_outcome_is_not_guessed_into_a_category(self):
        h = hook()
        await self.announce(h, "s1", "decision", "something_new_nobody_mapped")
        self.assertEqual(h.coordinator.hooks.emitted, [])

    async def test_a_host_without_a_hook_bus_still_detects(self):
        h = hook()
        h.coordinator.hooks = None
        await self.announce(h, "s1", "decision", "recorded")
        with h.journal.connect() as conn:
            self.assertEqual(len(conn.execute("SELECT 1 FROM detection_outcome").fetchall()), 1)

    async def test_an_emit_that_raises_cannot_break_detection(self):
        """Observation must never break the thing observed."""
        h = hook()

        async def explode(name, payload):
            raise RuntimeError("subscriber blew up")

        h.coordinator.hooks.emit = explode
        await self.announce(h, "s1", "decision", "recorded")
        with h.journal.connect() as conn:
            self.assertEqual(len(conn.execute("SELECT 1 FROM detection_outcome").fetchall()), 1)

    async def test_conditional_negative_and_unavailable_outcomes_are_announced(self):
        h = hook()
        await self.announce(h, "s1", "decision", "skip_verdict")
        await self.announce(h, "s1", "lesson", "unavailable")
        self.assertEqual([e[0] for e in h.coordinator.hooks.emitted],
                         ["teamwork:decision_skipped", "teamwork:lesson_failed"])

    async def test_observer_may_acquire_the_publication_lock(self):
        h = hook()
        observed = asyncio.Event()
        async def observer(name, payload):
            async with h.lock:
                observed.set()
        h.coordinator.hooks.emit = observer
        async def publish():
            async with h.lock:
                await h.detection_outcome("s1", "decision", "recorded")
        await asyncio.wait_for(publish(), 0.5)
        await asyncio.wait_for(observed.wait(), 0.5)
        await h.drain_detection_events()

    async def test_observer_exception_does_not_log_private_text(self):
        h = hook()
        async def observer(name, payload):
            raise RuntimeError("synthetic-private-observer-canary")
        h.coordinator.hooks.emit = observer
        with self.assertLogs("amplifier_module_hooks_teamwork", level="DEBUG") as logs:
            await self.announce(h, "s1", "decision", "recorded")
        self.assertNotIn("synthetic-private-observer-canary", "\n".join(logs.output))

    async def test_slow_observation_is_bounded_and_cleanup_closes_the_queue(self):
        from unittest.mock import patch
        import amplifier_module_hooks_teamwork as module
        h = hook()
        stopped = asyncio.Event()
        async def observer(name, payload):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        h.coordinator.hooks.emit = observer
        with patch.object(module, "DETECTION_EVENT_SECONDS", 0.03):
            for _ in range(module.MAX_DETECTION_EVENTS + 4):
                await h.detection_outcome("s1", "decision", "recorded")
            self.assertLessEqual(len(h._detection_event_tasks), module.MAX_DETECTION_EVENTS)
            await asyncio.wait_for(h.drain_detection_events(), 0.5)
            await asyncio.wait_for(stopped.wait(), 0.5)
            await asyncio.gather(*tuple(h._detection_event_tasks), return_exceptions=True)
            h.queue_detection_event("teamwork:decision_recorded", {})
            self.assertFalse(h._detection_event_tasks)


class TheCatalogueIsDiscoverable(unittest.IsolatedAsyncioTestCase):
    async def test_mount_contributes_the_catalogue_to_observability_events(self):
        """Consumers discover names via collect_contributions rather than
        hard-coding them -- which is how hook-context-intelligence already picks
        up every bundle that contributes to this channel."""
        directory = Path(tempfile.mkdtemp(prefix="teamwork-events-mount-"))
        connection = directory / "connection.json"
        connection.write_text('{"base_url": "https://team.example.invalid", '
                              '"project_id": "p", "token": "fixture-credential-0123456789"}')
        connection.chmod(0o600)
        coordinator = Coordinator()
        await mount(coordinator, {"connection_file": str(connection),
                                  "share_visible_turns": True,
                                  "journal_path": str(directory / "j.sqlite3"),
                                  "file_inbound_reports": False})
        channels = [c for c in coordinator.contributors if c[0] == "observability.events"]
        self.assertEqual(len(channels), 1, coordinator.contributors)
        self.assertEqual(sorted(channels[0][2]()), sorted(detection_events.ALL_EVENTS))
        self.assertTrue(all(n.startswith("teamwork:") for n in channels[0][2]()))

    async def test_real_core_discovers_and_delivers_an_outcome(self):
        from amplifier_core import AmplifierSession
        from amplifier_core.models import HookResult
        session = AmplifierSession({"session": {"orchestrator": "loop-streaming", "context": "context-simple"},
                                    "providers": [], "tools": [], "hooks": []})
        await session.initialize()
        try:
            with tempfile.TemporaryDirectory(prefix="teamwork-real-events-") as directory:
                cleanup = await mount(session.coordinator, {
                    "base_url": "https://team.example.invalid", "project_id": "p",
                    "token": "synthetic-event-credential", "share_visible_turns": True,
                    "journal_path": str(Path(directory)/"j.db"), "file_inbound_reports": False})
                h = cleanup.__self__
                contributions = await session.coordinator.collect_contributions("observability.events")
                self.assertIn("teamwork:decision_recorded", [n for names in contributions for n in names])
                received = []
                async def observer(event, payload):
                    received.append(payload)
                    return HookResult(action="continue")
                session.coordinator.hooks.register("teamwork:decision_recorded", observer, name="fixture-observer")
                await h.detection_outcome(h.sid, "decision", "recorded", 2, 0)
                await asyncio.gather(*tuple(h._detection_event_tasks))
                self.assertEqual([p["outcome"] for p in received], ["recorded"])
                await h.drain_detection_events()
        finally:
            await session.cleanup()

    async def test_a_host_without_a_contributor_registry_still_mounts(self):
        directory = Path(tempfile.mkdtemp(prefix="teamwork-events-bare-"))
        connection = directory / "connection.json"
        connection.write_text('{"base_url": "https://team.example.invalid", '
                              '"project_id": "p", "token": "fixture-credential-0123456789"}')
        connection.chmod(0o600)

        class Bare(Coordinator):
            register_contributor = None

        coordinator = Bare()
        await mount(coordinator, {"connection_file": str(connection),
                                  "share_visible_turns": True,
                                  "journal_path": str(directory / "j.sqlite3"),
                                  "file_inbound_reports": False})


if __name__ == "__main__":
    unittest.main()
