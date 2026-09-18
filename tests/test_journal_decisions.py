"""The journal keeps what was decided, and the recall follows the binding.

A fingerprint answers "have we said this already?" and cannot be read back. So a
decision this machine reached was legible only on the service or in the
transcript -- neither reachable as local context, which means a session could not
be reminded of what it had already decided.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
import amplifier_module_hooks_teamwork as teamwork_module
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, mount


class Context:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages(self):
        return list(self.messages)


class Hooks:
    def __init__(self):
        self.handlers = []

    def register(self, event, handler, **kwargs):
        self.handlers.append(event)

    async def emit(self, event, payload):
        return None


class Coordinator:
    parent_id = None
    session_id = "journal-fixture"

    def __init__(self):
        self.hooks = Hooks()
        self.mount_points = {}
        self.context = Context()
        self.capabilities = {}
        self.contributors = []

    def get(self, name):
        return self.context if name == "context" else None

    def get_capability(self, name):
        return self.capabilities.get(name)

    def register_capability(self, name, value):
        self.capabilities[name] = value

    def register_contributor(self, channel, name, supplier):
        self.contributors.append((channel, name, supplier))

    async def mount(self, point, value, name=None):
        self.mount_points.setdefault(point, {})[name] = value


def hook(directory=None):
    directory = directory or Path(tempfile.mkdtemp(prefix="teamwork-journal-"))
    return TeamworkHook(Coordinator(),
                        {"base_url": "https://team.example.invalid", "project_id": "p",
                         "token": "fixture-harness-credential-0123456789"},
                        Journal(directory / "j.sqlite3"), object())


class TheDecisionItselfIsKept(unittest.TestCase):
    def test_a_recorded_decision_is_readable_back_in_its_own_words(self):
        h = hook()
        h.journal.record_decision_body(
            h.sid, "fp-1", "decision", "We ship single-tenant first.",
            "inference", "high", "One customer; revisit at three.", "rec-9")
        found = h.recall_decisions()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["claim"], "We ship single-tenant first.")
        self.assertEqual(found[0]["basis"], "inference")
        self.assertEqual(found[0]["limitations"], "One customer; revisit at three.")
        self.assertEqual(found[0]["record_id"], "rec-9")

    def test_the_same_decision_twice_is_one_row_not_two(self):
        """Same contract as the fingerprint beside it: a decision is identified
        by its fingerprint, and re-recording one must not grow the store."""
        h = hook()
        for record_id in ("rec-1", "rec-2"):
            h.journal.record_decision_body(h.sid, "fp-1", "decision", "Same call.",
                                           record_id=record_id)
        self.assertEqual(len(h.recall_decisions()), 1)

    def test_newest_first_because_recall_is_bounded(self):
        h = hook()
        for index in range(3):
            h.journal.record_decision_body(h.sid, "fp-%d" % index, "decision", "call %d" % index)
        self.assertEqual([d["claim"] for d in h.recall_decisions()][0], "call 2")

    def test_recall_is_bounded_at_the_journal_not_at_the_caller(self):
        """An unbounded local store becomes a per-turn tax the moment anything
        injects from it, so a caller cannot ask for everything."""
        h = hook()
        for index in range(teamwork_module.DECISION_RECALL_MAX + 10):
            h.journal.record_decision_body(h.sid, "fp-%d" % index, "decision", "call %d" % index)
        self.assertEqual(len(h.recall_decisions()), teamwork_module.DECISION_RECALL)
        self.assertEqual(len(h.recall_decisions(limit=10_000)), teamwork_module.DECISION_RECALL_MAX)

    def test_a_long_claim_is_truncated_rather_than_served_whole(self):
        h = hook()
        h.journal.record_decision_body(h.sid, "fp-1", "decision", "x" * 40_000)
        entry = h.recall_decisions()[0]
        self.assertLessEqual(len(entry["claim"]), teamwork_module.DECISION_RECALL_CLAIM)
        self.assertTrue(entry["truncated"])

    def test_keeping_a_copy_can_never_turn_a_recorded_decision_into_a_failed_one(self):
        h = hook()
        h.journal.path = "/nonexistent/directory/j.sqlite3"
        h.journal.record_decision_body(h.sid, "fp-1", "decision", "still recorded")
        self.assertEqual(h.recall_decisions(), [])


class TheRecallFollowsTheBinding(unittest.TestCase):
    """THE HAZARD THIS IS DESIGNED AROUND.

    One journal file serves every binding a credential has had, with rows keyed
    by shared session id. A consumer handed the Journal -- or one that captured
    `hook.sid` once -- would serve the PREVIOUS project's decisions after a
    rebind. The capability is an accessor that resolves the id on every call.
    """

    def test_a_rebound_session_does_not_read_the_previous_projects_decisions(self):
        h = hook()
        h.journal.record_decision_body(h.sid, "fp-1", "decision", "belongs to project one")
        before = h.sid
        h.rebind("another-project")
        self.assertNotEqual(h.sid, before)
        self.assertEqual(h.recall_decisions(), [])
        h.journal.record_decision_body(h.sid, "fp-2", "decision", "belongs to project two")
        self.assertEqual([d["claim"] for d in h.recall_decisions()], ["belongs to project two"])

    def test_the_capability_is_the_accessor_and_not_the_store(self):
        h = hook()
        recall = h.recall_decisions           # what mount() registers
        h.journal.record_decision_body(h.sid, "fp-1", "decision", "project one")
        h.rebind("another-project")
        h.journal.record_decision_body(h.sid, "fp-2", "decision", "project two")
        # The SAME captured callable now serves the new binding, because it
        # reads the id from the hook rather than closing over it.
        self.assertEqual([d["claim"] for d in recall()], ["project two"])


class TheCapabilityIsRegistered(unittest.IsolatedAsyncioTestCase):
    async def test_mount_registers_a_decisions_capability(self):
        directory = Path(tempfile.mkdtemp(prefix="teamwork-journal-mount-"))
        connection = directory / "connection.json"
        connection.write_text('{"base_url": "https://team.example.invalid", '
                              '"project_id": "p", "token": "fixture-credential-0123456789"}')
        connection.chmod(0o600)
        coordinator = Coordinator()
        await mount(coordinator, {"connection_file": str(connection),
                                  "share_visible_turns": True,
                                  "journal_path": str(directory / "j.sqlite3"),
                                  "file_inbound_reports": False})
        recall = coordinator.get_capability("teamwork.decisions")
        self.assertTrue(callable(recall), sorted(coordinator.capabilities))
        self.assertEqual(recall(), [])


if __name__ == "__main__":
    unittest.main()
