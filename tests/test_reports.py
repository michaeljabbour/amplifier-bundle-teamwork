"""An inbound message becomes a report in the receiving side's own queue."""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))

from amplifier_module_hooks_teamwork import Journal, TeamworkHook
from amplifier_module_hooks_teamwork.reports import (BODY_LIMIT, FilingUnknown, Queue, QueueUnavailable,
                                                     description, mirror_operation, sanitize_outbound,
                                                     sender, title)

BODY = "Can you look at the relay timeouts before Thursday? We saw three drops."


def message(mid="msg-1", to="agent-me", body=BODY, person="person-alex", sent="2026-09-10T10:00:00Z"):
    content = {"id": mid, "to_agent_id": to, "body": body, "from_person_id": person,
               "from_harness_id": "harness-7", "sent_at": sent, "created_by": person}
    return {"key": "message:%s:1" % mid, "id": mid, "version": 1, "record_type": "message",
            "change": "upsert", "content": content, "content_sha256": "hash-" + mid}


def stub(directory, script):
    """A real executable on PATH, so the subprocess seam itself is exercised."""
    path = Path(directory) / "fake-work-tracker"
    path.write_text("#!/usr/bin/env python3\nimport json, sys\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class Words(unittest.TestCase):
    def test_the_title_is_derived_and_the_body_is_not(self):
        # The title is a label for a queue view and may be shortened; the words
        # themselves are carried whole, and the report says which is which.
        record = message(body="x" * 400)
        self.assertLessEqual(len(title(record, "Alex")), 121)
        self.assertIn("x" * 400, description(record, "teamwork", "agent-me", "Alex"))

    def test_the_senders_words_are_fenced_and_unedited(self):
        text = description(message(), "teamwork", "agent-me", "Alex")
        fenced = text.split("----- begin message -----\n")[1].split("\n----- end message -----")[0]
        self.assertEqual(fenced, BODY)

    def test_the_report_carries_the_message_id_that_proves_it_was_filed(self):
        # This marker is what a read-back looks for after an ambiguous write, so
        # losing it would turn every ambiguous write into a duplicate.
        self.assertIn("msg-1", description(message(), "teamwork", "agent-me", "Alex"))

    def test_the_report_names_itself_a_report_and_the_triage_path(self):
        text = description(message(), "teamwork", "agent-me", "Alex")
        self.assertIn("This is a REPORT, not an issue", text)
        self.assertIn("discovered-from", text)

    def test_an_unnamed_sender_is_identified_by_id_rather_than_invented(self):
        name, person, harness = sender(message(), {})
        self.assertIsNone(name)
        self.assertEqual((person, harness), ("person-alex", "harness-7"))
        self.assertIn("person-alex", title(message(), name))

    def test_a_known_sender_is_named_from_the_cached_person_record(self):
        name, _, _ = sender(message(), {"person-alex": {"id": "person-alex", "name": "Alex Stone"}})
        self.assertEqual(name, "Alex Stone")


class Mirror(unittest.TestCase):
    def test_mirror_payload_shape(self):
        record = message()
        op = mirror_operation(record, "Alex Stone", "person-jamie", "teamwork", "https://team.example.invalid")
        self.assertEqual(op["op"], "request.upsert")
        self.assertEqual(op["id"], "inbound-msg-1")
        self.assertEqual(op["expected_version"], 0)
        data = op["data"]
        self.assertEqual(data["title"], "Inbound message from Alex Stone")
        self.assertEqual(data["requested_person_id"], "person-jamie")
        self.assertEqual(data["status"], "requested")
        self.assertEqual(data["evidence_refs"],
                         [{"kind": "record", "record_type": "message", "record_id": "msg-1", "version": 1}])
        self.assertIn(BODY, data["description"])

    def test_mirror_description_truncated_to_500_with_pointer(self):
        record = message(body="z" * 900)
        op = mirror_operation(record, "Alex", "person-jamie", "teamwork", "https://team.example.invalid")
        description_text = op["data"]["description"]
        head = description_text.split("\n\n")[0]
        self.assertEqual(head, "z" * 500)
        self.assertIn("teamwork", description_text)
        self.assertIn("https://team.example.invalid", description_text)
        self.assertIn("msg-1", description_text)


class QueueBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "queues.json"

    def queue(self, script, project="teamwork"):
        return Queue(project, self.registry, command=stub(self.tmp.name, script))

    def test_no_tracker_installed_is_an_unavailable_queue_not_an_error(self):
        queue = Queue("teamwork", self.registry, command=str(Path(self.tmp.name) / "absent"))
        with self.assertRaises(QueueUnavailable) as caught:
            queue.ready()
        self.assertIn("installed", str(caught.exception))

    def test_a_missing_project_reports_the_command_that_creates_it(self):
        queue = self.queue("sys.stderr.write(\"project 'teamwork' not found. "
                           "Create it first: amplifier-work-tracker new teamwork\\n\"); sys.exit(1)\n")
        with self.assertRaises(QueueUnavailable) as caught:
            queue.ready()
        self.assertIn("new teamwork", str(caught.exception))

    def test_two_projects_that_would_share_a_queue_are_refused_before_filing(self):
        # The refusal is what stops one team's inbound requests landing in another
        # team's backlog, so it must happen before anything is written.
        ok = "print(json.dumps({'items': [], 'truncated': False, 'added': 'x-1'}))\n"
        self.assertEqual(self.queue(ok, "design-review").ready(), "design_review")
        with self.assertRaises(QueueUnavailable) as caught:
            self.queue(ok, "design_review").ready()
        self.assertIn("already bound", str(caught.exception))

    def test_a_filed_report_returns_the_item_id(self):
        queue = self.queue("print(json.dumps({'items': [], 'truncated': False, 'added': 'tw-42'}))\n")
        queue.ready()
        self.assertEqual(queue.file("msg-1", "t", "d"), "tw-42")

    def test_an_ambiguous_write_that_landed_is_found_rather_than_repeated(self):
        # The tracker's own contract: a failed write may still have landed. Filing
        # the same words twice is worse than the failure that caused it.
        script = ("verb = sys.argv[1]\n"
                  "if verb == 'add':\n"
                  "    sys.stderr.write('still conflicting after 8 retries\\n'); sys.exit(1)\n"
                  "print(json.dumps({'items': [{'id': 'tw-9', 'description': 'Message id: msg-1'}],"
                  " 'truncated': False}))\n")
        queue = self.queue(script)
        queue.ready()
        self.assertEqual(queue.file("msg-1", "t", "d"), "tw-9")

    def test_an_ambiguous_write_that_did_not_land_stays_retryable(self):
        script = ("verb = sys.argv[1]\n"
                  "if verb == 'add':\n"
                  "    sys.stderr.write('conflict\\n'); sys.exit(1)\n"
                  "print(json.dumps({'items': [], 'truncated': False}))\n")
        queue = self.queue(script)
        queue.ready()
        with self.assertRaises(QueueUnavailable):
            queue.file("msg-1", "t", "d")

    def test_a_truncated_listing_cannot_prove_absence(self):
        # A listing that was cut short says nothing about what it did not show.
        # Reading that silence as "not filed" is exactly how a duplicate appears.
        script = ("verb = sys.argv[1]\n"
                  "if verb == 'add':\n"
                  "    sys.stderr.write('conflict\\n'); sys.exit(1)\n"
                  "print(json.dumps({'items': [], 'truncated': True}))\n")
        queue = self.queue(script)
        queue.ready()
        with self.assertRaises(FilingUnknown):
            queue.file("msg-1", "t", "d")

    def test_a_landed_write_with_an_unreadable_id_is_not_retried(self):
        script = ("verb = sys.argv[1]\n"
                  "print('' if verb == 'add' else json.dumps({'items': [], 'truncated': False}))\n")
        queue = self.queue(script)
        queue.ready()
        self.assertIn("filed", queue.file("msg-1", "t", "d"))


class Context:
    def __init__(self, events): self.events = events

    async def add_message(self, message):
        self.events.append(("input_accepted", message))


class Coordinator:
    session_id = "native-session-fixture"

    def __init__(self, context): self.context = context

    def get(self, name): return self.context if name == "context" else None


class Client:
    """Returns one inbound message, addressed to whichever session asks."""

    def __init__(self, records):
        self.records, self.requests = records, []

    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, json.loads(json.dumps(body)), key))
        if endpoint == "context":
            return {"next_cursor": "c", "delivery_id": "d", "has_more": False, "truncated": False,
                    "items": self.records}
        return {"stored": True}


class Filing(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.events = []
        self.context = Context(self.events)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork",
                           "token": "fixture-token"}
        self.journal = Journal(Path(self.tmp.name) / "queue.db")
        self.registry = Path(self.tmp.name) / "queues.json"
        self.log = Path(self.tmp.name) / "calls.log"

    def build(self, records, script=None, command=None):
        client = Client(records)
        queue = None
        if script is not None or command is not None:
            queue = Queue("teamwork", self.registry,
                          command=command or stub(self.tmp.name, script))
        hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, client, filing=queue)
        # The service addresses a message to the session id it computed for us.
        for record in records:
            if record["record_type"] == "message":
                record["content"]["to_agent_id"] = hook.sid
        return hook, client

    def recorder(self):
        return ("open(%r, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                "print(json.dumps({'items': [], 'truncated': False, 'added': 'tw-42'}))\n" % str(self.log))

    async def test_an_inbound_message_is_filed_once_as_a_report(self):
        hook, _ = self.build([message()], self.recorder())
        await hook.on_submit("prompt:submit", {"prompt": "hello"})
        await hook.on_complete("prompt:complete", {"response": "hi"})
        calls = self.log.read_text().splitlines()
        self.assertEqual([c for c in calls if c.startswith("add")].__len__(), 1)
        added = [c for c in calls if c.startswith("add")][0]
        self.assertIn("--project teamwork", added)
        self.assertEqual(hook.state["filed"], {"msg-1": "tw-42"})
        # A second turn must not file the same words again.
        await hook.on_submit("prompt:submit", {"prompt": "again"})
        self.assertEqual(len([c for c in self.log.read_text().splitlines() if c.startswith("add")]), 1)

    async def test_filing_never_precedes_the_observed_acceptance(self):
        # The receipt contract is the load-bearing one; filing is downstream of it
        # and must not be able to reorder or substitute for it.
        hook, client = self.build([message()], self.recorder())
        await hook.on_submit("prompt:submit", {"prompt": "hello"})
        endpoints = [name for name, _, _ in client.requests]
        self.assertIn("acknowledgements", endpoints)
        self.assertLess(endpoints.index("context"), endpoints.index("acknowledgements"))
        self.assertEqual([name for name, _ in self.events], ["input_accepted"])

    async def test_without_a_tracker_the_message_still_arrives_and_the_absence_is_named(self):
        hook, _ = self.build([message()], command=str(Path(self.tmp.name) / "absent"))
        result = await hook.on_submit("prompt:submit", {"prompt": "hello"})
        self.assertEqual([name for name, _ in self.events], ["input_accepted"])
        self.assertIn("no local work queue took them", result.user_message)
        self.assertEqual(hook.state.get("filed"), {})

    async def test_the_absence_is_said_once_rather_than_at_every_turn(self):
        hook, _ = self.build([message()], command=str(Path(self.tmp.name) / "absent"))
        await hook.on_submit("prompt:submit", {"prompt": "one"})
        second = await hook.on_submit("prompt:submit", {"prompt": "two"})
        self.assertNotIn("no local work queue", second.user_message or "")

    async def test_no_queue_configured_at_all_files_nothing_and_says_nothing(self):
        hook, _ = self.build([message()])
        result = await hook.on_submit("prompt:submit", {"prompt": "hello"})
        self.assertNotIn("queue", (result.user_message or ""))

    async def test_a_message_for_another_agent_is_never_filed_here(self):
        record = message(to="somebody-else")
        client = Client([record])
        queue = Queue("teamwork", self.registry, command=stub(self.tmp.name, self.recorder()))
        hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, client, filing=queue)
        await hook.on_submit("prompt:submit", {"prompt": "hello"})
        self.assertFalse(self.log.exists())

    async def test_an_oversized_body_is_refused_rather_than_shortened(self):
        hook, _ = self.build([message(body="y" * (BODY_LIMIT + 1))], self.recorder())
        result = await hook.on_submit("prompt:submit", {"prompt": "hello"})
        self.assertIn("not shortened", result.user_message)
        self.assertNotIn("add", self.log.read_text())

    async def test_an_ambiguous_filing_is_carried_as_unknown_and_not_retried(self):
        script = ("verb = sys.argv[1]\n"
                  "if verb == 'add':\n"
                  "    sys.stderr.write('conflict\\n'); sys.exit(1)\n"
                  "print(json.dumps({'items': [], 'truncated': True}))\n")
        hook, _ = self.build([message()], script)
        result = await hook.on_submit("prompt:submit", {"prompt": "hello"})
        self.assertIn("may or may not have been filed", result.user_message)
        self.assertTrue(hook.state["filed"]["msg-1"].startswith("acceptance_unknown"))

    async def test_a_message_the_excerpt_had_no_room_for_is_still_filed(self):
        # Filing reads the cache, not the rendered excerpt. A message that lost the
        # byte budget is exactly as much a teammate's request as one that won it,
        # and filing only what was displayed would drop teammates' work silently.
        crowd = [message(mid="msg-%d" % i, body="y" * 2400) for i in range(8)]
        hook, _ = self.build(crowd, self.recorder())
        await hook.on_submit("prompt:submit", {"prompt": "hello"})
        rendered = self.events[0][1]["content"]
        seated = [record["id"] for record in crowd if record["key"] in rendered]
        self.assertLess(len(seated), len(crowd), "the excerpt was not actually crowded")
        # Filing is also bounded per turn, so the remainder lands on the next one.
        await hook.on_submit("prompt:submit", {"prompt": "again"})
        self.assertEqual(sorted(hook.state["filed"]), sorted(record["id"] for record in crowd))


class Outbound(unittest.TestCase):
    """One chokepoint, allow-listed. A field nobody thought about is dropped."""

    def test_only_allow_listed_keys_survive(self):
        payload = {"queue_status": "ready", "ready_count": 3, "observed_at": "2026-09-11T10:00:00Z",
                   "queue_path": "/private/work/queue.db",
                   "command": "amplifier-work-tracker list --project secret-codename",
                   "hostname": "someones-laptop.local", "token": "tok-abc123"}
        self.assertEqual(sanitize_outbound(payload),
                         {"queue_status": "ready", "ready_count": 3,
                          "observed_at": "2026-09-11T10:00:00Z"})

    def test_values_are_bounded_and_wrong_types_are_dropped(self):
        result = sanitize_outbound({"queue_status": "x" * 200, "ready_count": "seven",
                                    "integration": None, "reason_code": "y" * 400})
        self.assertEqual(result, {"queue_status": "x" * 40, "reason_code": "y" * 120})

    def test_a_negative_or_boolean_count_is_not_a_count(self):
        self.assertEqual(sanitize_outbound({"ready_count": -1}), {})
        self.assertEqual(sanitize_outbound({"ready_count": True}), {})

    def test_a_non_dict_payload_publishes_nothing(self):
        self.assertEqual(sanitize_outbound("ready"), {})
        self.assertEqual(sanitize_outbound(None), {})


class QueueStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "queues.json"

    def queue(self, script=None, command=None):
        return Queue("teamwork", self.registry, command=command or stub(self.tmp.name, script))

    def test_a_readable_queue_is_ready_with_a_count_and_a_time(self):
        script = ("print(json.dumps({'items': [{'id': 'tw-1', 'status': 'open'},"
                  " {'id': 'tw-2', 'status': 'closed'}], 'truncated': False}))\n")
        result = self.queue(script).status()
        self.assertEqual(result["queue_status"], "ready")
        self.assertEqual(result["ready_count"], 1)
        self.assertEqual(result["integration"], "amplifier-work-tracker")
        self.assertTrue(result["observed_at"])

    def test_no_tracker_is_unavailable_with_a_reason_and_no_count(self):
        result = self.queue(command=str(Path(self.tmp.name) / "absent")).status()
        self.assertEqual(result["queue_status"], "unavailable")
        self.assertNotIn("ready_count", result)
        self.assertIn("installed", result["reason_code"])

    def test_a_failed_probe_after_a_good_one_is_stale_not_ready(self):
        # A queue that answered once and cannot be reached now is not "connected"
        # and not "gone": it is a last-known reading, carrying the time it was read.
        marker = Path(self.tmp.name) / "broken"
        script = ("import os\n"
                  "if os.path.exists(%r):\n"
                  "    sys.stderr.write('the tracker is busy\\n'); sys.exit(1)\n"
                  "print(json.dumps({'items': [{'id': 'tw-1', 'status': 'open'}], 'truncated': False}))\n"
                  % str(marker))
        queue = self.queue(script)
        first = queue.status()
        marker.write_text("x")
        second = queue.status()
        self.assertEqual(first["queue_status"], "ready")
        self.assertEqual(second["queue_status"], "stale")
        self.assertEqual(second["observed_at"], first["observed_at"])
        self.assertEqual(second["ready_count"], 1)

    def test_unreadable_output_is_unavailable_rather_than_invented(self):
        result = self.queue("print('not json at all')\n").status()
        self.assertEqual(result["queue_status"], "unavailable")
        self.assertIn("could not read", result["reason_code"])


if __name__ == "__main__":
    unittest.main()
