import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
import urllib.error
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import (HTTPClient, Journal, TeamworkHook, SyncError, sha, NoRedirect,
                                             mount, resolve_connection, describe, PERSON_FIELDS, verbosity,
                                             PRESENCE_SUMMARY, TasksTool, ClaimTool, ProgressTool,
                                             RETIRED_MESSAGE)


class Context:
    def __init__(self, events, fail=False): self.events, self.fail = events, fail
    async def add_message(self, message):
        if self.fail: raise RuntimeError("Input rejected")
        self.events.append(("input_accepted", message))


class Coordinator:
    session_id = "native-session-fixture"
    def __init__(self, context): self.context = context; self.tools = {}
    def get(self, name): return self.context if name == "context" else None
    async def mount(self, point, value, name): self.tools[name] = value


class Client:
    def __init__(self, events): self.events, self.fail, self.requests = events, False, []
    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, json.loads(json.dumps(body)), key))
        if self.fail: raise SyncError(503)
        self.events.append((endpoint, body))
        if endpoint == "context":
            record = {"id": "teamwork", "version": 1, "goal": "Fixture goal"}
            return {"next_cursor": "cursor", "delivery_id": "manifest", "has_more": False, "truncated": False, "items": [{"key": "project:teamwork:1", "id": "teamwork", "version": 1, "record_type": "project", "change": "upsert", "content": record, "content_sha256": "fixturehash"}]}
        return {"stored": True}


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.events = []; self.client = Client(self.events)
        self.context = Context(self.events)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork", "token": "test-credential-no-real-secret"}
        self.journal = Journal(Path(self.tmp.name) / "queue.db")
        self.hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)

    async def test_observed_input_precedes_receipt_and_visible_turn_is_paired(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        await self.hook.on_complete("prompt:complete", {"response": "Visible response"})
        await self.hook.on_end("session:end", {})
        names = [e[0] for e in self.events]
        self.assertLess(names.index("input_accepted"), names.index("acknowledgements"))
        receipts = [body for endpoint, body, _ in self.client.requests if endpoint == "acknowledgements"]
        self.assertEqual(receipts[0]["delivery_method"], "harness_input_accepted")
        self.assertEqual(receipts[0]["items"][0]["representation"], "derived")
        turns = [op for endpoint, body, _ in self.client.requests if endpoint == "publish" for op in body["operations"] if op["op"] == "turn.upsert"]
        self.assertEqual(turns[0]["data"]["user_prompt"], "My prompt")
        self.assertEqual(turns[0]["data"]["agent_responses"][0]["text"], "Visible response")
        self.assertEqual(turns[0]["data"]["hook_injections"][0], receipts[0]["injection"])

    async def test_failed_input_never_acknowledged(self):
        self.context.fail = True
        await self.hook.on_submit("prompt:submit", {"prompt": "Prompt"})
        self.assertFalse(any(e == "acknowledgements" for e, _, _ in self.client.requests))
        self.assertEqual(self.journal.load(self.hook.sid)["turn"]["boundary"], "prepared")

    def outbox_publishes(self, op="session.upsert"):
        """Only the durable-outbox traffic: registration rides a separate path."""
        return [r for r in self.client.requests
                if r[0] == "publish" and any(o["op"] == op for o in r[1]["operations"])]

    async def test_a_declared_card_is_carried_and_marked_by_its_field(self):
        hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client,
                            responsibility="Reviews deploy failures for the billing service",
                            skills=["read logs", "explain a rollback"])
        await hook.on_start("session:start", {})
        data = [r for r in self.client.requests
                if r[0] == "publish" and any(o["op"] == "agent.upsert" for o in r[1]["operations"])
                ][0][1]["operations"][0]["data"]
        self.assertEqual(data["responsibility"], "Reviews deploy failures for the billing service")
        self.assertEqual(data["skills"], ["read logs", "explain a rollback"])

    async def test_an_undeclared_purpose_stays_absent_rather_than_invented(self):
        # An invented purpose reads exactly like a declared one, and no reader
        # could tell them apart -- so absence must survive to the server.
        await self.hook.on_start("session:start", {})
        data = [r for r in self.client.requests
                if r[0] == "publish" and any(o["op"] == "agent.upsert" for o in r[1]["operations"])
                ][0][1]["operations"][0]["data"]
        self.assertNotIn("responsibility", data)
        self.assertNotIn("skills", data)

    async def test_capabilities_are_read_from_what_is_actually_mounted(self):
        class Mounted(Coordinator):
            mount_points = {"tools": {"teamwork_send": object(), "bash": object()}}

        hook = TeamworkHook(Mounted(self.context), self.connection, self.journal, self.client)
        self.assertEqual(hook.observed_capabilities(), ["bash", "teamwork_send"])

    async def test_an_unreadable_coordinator_reports_nothing_rather_than_guessing(self):
        class Odd(Coordinator):
            @property
            def mount_points(self):
                raise RuntimeError("no mount points here")

        hook = TeamworkHook(Odd(self.context), self.connection, self.journal, self.client)
        self.assertEqual(hook.observed_capabilities(), [])
        # And registration still happens -- the card is poorer, the session is fine.
        await hook.on_start("session:start", {})
        self.assertEqual(hook.agent_status, "registered")

    def presences(self):
        return [r for r in self.client.requests
                if r[0] == "publish" and any(o["op"] == "presence.upsert" for o in r[1]["operations"])]

    async def test_a_running_turn_reports_itself_active(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "Look at the deploy failure"})
        sent = self.presences()
        self.assertEqual(len(sent), 1)
        operation = sent[0][1]["operations"][0]
        self.assertEqual(operation["id"], self.hook.sid)
        self.assertEqual(operation["data"]["state"], "active")
        self.assertIn("deploy failure", operation["data"]["summary"])

    async def test_a_finished_turn_reports_idle_rather_than_staying_active(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "Look at the deploy failure"})
        await self.hook.on_complete("prompt:complete", {"response": "Done"})
        states = [r[1]["operations"][0]["data"]["state"] for r in self.presences()]
        self.assertEqual(states, ["active", "idle"])
        # One record, advancing -- not a second session appearing.
        self.assertEqual({r[1]["operations"][0]["id"] for r in self.presences()}, {self.hook.sid})
        self.assertEqual([r[1]["operations"][0]["expected_version"] for r in self.presences()], [0, 1])

    async def test_going_idle_keeps_what_the_session_was_last_working_on(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "Look at the deploy failure"})
        await self.hook.on_complete("prompt:complete", {"response": "Done"})
        idle = self.presences()[-1][1]["operations"][0]["data"]
        self.assertEqual(idle["state"], "idle")
        self.assertIn("deploy failure", idle["summary"])

    async def test_the_reported_summary_is_bounded(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "x" * 5000})
        summary = self.presences()[0][1]["operations"][0]["data"]["summary"]
        self.assertLessEqual(len(summary), PRESENCE_SUMMARY)

    async def test_a_server_that_rejects_presence_does_not_disturb_the_session(self):
        class Selective(Client):
            def request(self, endpoint, body, key=None):
                operations = body.get("operations") if isinstance(body, dict) else None
                if operations and any(o["op"] == "presence.upsert" for o in operations):
                    self.requests.append((endpoint, json.loads(json.dumps(body)), key))
                    raise SyncError(400)
                return super().request(endpoint, body, key)

        self.client = Selective(self.events)
        hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        await hook.on_start("session:start", {})
        await hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        await hook.on_complete("prompt:complete", {"response": "Visible response"})
        self.assertEqual(hook.presence_status, "unavailable")
        # Tried once, then stopped -- a refusal does not accumulate.
        self.assertEqual(len(self.presences()), 1)
        # And the turn was still shared.
        self.assertTrue(any(o["op"] == "turn.upsert"
                            for r in self.client.requests if r[0] == "publish"
                            for o in r[1]["operations"]))

    def registrations(self):
        return [r for r in self.client.requests
                if r[0] == "publish" and any(o["op"] == "agent.upsert" for o in r[1]["operations"])]

    async def test_the_session_registers_itself_as_an_addressable_agent(self):
        await self.hook.on_start("session:start", {})
        sent = self.registrations()
        self.assertEqual(len(sent), 1)
        operation = sent[0][1]["operations"][0]
        self.assertEqual(operation["id"], self.hook.sid)
        self.assertEqual(operation["data"]["session_id"], self.hook.sid)
        self.assertEqual(operation["expected_version"], 0)
        self.assertEqual(self.hook.agent_status, "registered")

    async def test_a_completed_turn_refreshes_liveness_rather_than_adding_an_agent(self):
        await self.hook.on_start("session:start", {})
        await self.hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        await self.hook.on_complete("prompt:complete", {"response": "Visible response"})
        sent = self.registrations()
        self.assertEqual(len(sent), 2)
        # Same record, next version: a heartbeat, not a second agent.
        self.assertEqual({op[1]["operations"][0]["id"] for op in sent}, {self.hook.sid})
        self.assertEqual([op[1]["operations"][0]["expected_version"] for op in sent], [0, 1])

    async def test_a_configured_node_label_is_sent_and_an_absent_one_reveals_nothing(self):
        await self.hook.on_start("session:start", {})
        self.assertNotIn("node_label", self.registrations()[0][1]["operations"][0]["data"])
        labelled = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client,
                                node_label="laptop-2")
        await labelled.on_start("session:start", {})
        self.assertEqual(self.registrations()[-1][1]["operations"][0]["data"]["node_label"], "laptop-2")

    async def test_a_server_that_rejects_registration_does_not_disturb_the_session(self):
        # The durable outbox stops at its first failure, so registration must never
        # ride it: an older server that does not know this record kind would
        # otherwise wedge every later publish behind a record nobody needs.
        class Selective(Client):
            def request(self, endpoint, body, key=None):
                operations = body.get("operations") if isinstance(body, dict) else None
                if operations and any(o["op"] == "agent.upsert" for o in operations):
                    self.requests.append((endpoint, json.loads(json.dumps(body)), key))
                    raise SyncError(400)
                return super().request(endpoint, body, key)

        self.client = Selective(self.events)
        hook = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        await hook.on_start("session:start", {})
        await hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        await hook.on_complete("prompt:complete", {"response": "Visible response"})
        self.assertEqual(hook.agent_status, "unavailable")
        # Tried once, then stopped -- a refusal does not accumulate.
        self.assertEqual(len(self.registrations()), 1)
        # And the session shared everything it would have shared anyway.
        self.assertTrue(any(o["op"] == "turn.upsert"
                            for r in self.client.requests if r[0] == "publish"
                            for o in r[1]["operations"]))

    async def test_offline_outbox_reuses_key_after_restart(self):
        self.client.fail = True
        await self.hook.on_start("session:start", {})
        first = self.outbox_publishes()[0]
        self.client.fail = False
        resumed = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        await resumed.on_start("session:start", {})
        self.assertEqual(first, self.outbox_publishes()[1])
        self.assertEqual(resumed.sid, self.hook.sid)

    async def test_exact_connection_credential_redacted_and_internal_events_unhandled(self):
        await self.hook.on_submit("prompt:submit", {"prompt": "Secret " + self.connection["token"]})
        await self.hook.on_complete("prompt:complete", {"response": "Bearer abcdefghijklmnopqrstuvwxyz"})
        published = json.dumps([b for e, b, _ in self.client.requests if e == "publish"])
        self.assertNotIn(self.connection["token"], published)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", published)
        self.assertIn("REDACTED CREDENTIAL", published)

    async def test_nested_context_content_is_redacted_before_cache_persistence(self):
        secret = self.connection["token"]
        self.client.request = lambda endpoint, body, key=None: {
            "next_cursor": "cursor",
            "delivery_id": "manifest",
            "has_more": False,
            "truncated": False,
            "items": [{
                "key": "secret-" + secret,
                "id": "teamwork",
                "record_type": "project",
                "change": "upsert",
                "content": {"nested-" + secret: {"credential": secret}},
                "content_sha256": "source-hash-must-remain",
            }],
        }
        await self.hook.retrieve()
        saved = self.journal.load(self.hook.sid)["cache"]
        payload = json.dumps(saved)
        self.assertNotIn(secret, payload)
        record = next(iter(saved.values()))["record"]
        self.assertEqual(record["content_sha256"], "source-hash-must-remain")

    async def test_legacy_cache_is_scrubbed_when_a_session_reopens(self):
        secret = self.connection["token"]
        self.hook.state["cache"] = {
            "project:secret-" + secret: {
                "record": {
                    "key": "secret-" + secret,
                    "id": "item",
                    "record_type": "project",
                    "content": {"credential": secret},
                    "content_sha256": "original-source-hash",
                },
                "delivery_id": "delivery",
            }
        }
        self.journal.save(self.hook.sid, self.hook.state)
        reopened = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        saved = reopened.journal.load(reopened.sid)["cache"]
        self.assertNotIn(secret, json.dumps(saved))
        self.assertEqual(
            next(iter(saved.values()))["record"]["content_sha256"],
            "original-source-hash",
        )



class RebindTests(unittest.IsolatedAsyncioTestCase):
    """Moving a session between projects must not carry credentials or turns across."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.events = []; self.client = Client(self.events)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "alpha",
                           "token": "alpha-credential-no-real-secret", "harness_id": "harness-alpha"}
        self.hook = TeamworkHook(Coordinator(Context(self.events)), self.connection,
                                 Journal(Path(self.tmp.name) / "queue.db"), self.client)

    def test_a_fresh_enrollment_replaces_the_previous_project_credential(self):
        minted = {"base_url": "https://team.example.invalid", "project_id": "beta",
                  "token": "beta-credential-no-real-secret", "harness_id": "harness-beta"}
        before = self.hook.sid
        self.hook.rebind("beta", minted)
        self.assertEqual(self.hook.connection["token"], minted["token"])
        self.assertEqual(self.hook.connection["harness_id"], "harness-beta")
        self.assertNotEqual(self.hook.sid, before)
        # The credential the client will actually present is the new one.
        self.assertEqual(self.hook.client.token, minted["token"])
        # The superseded credential is no longer what redaction protects.
        self.assertIn("[REDACTED CREDENTIAL]", self.hook.clean("x " + minted["token"]))

    def test_binding_without_a_credential_keeps_the_configured_one(self):
        self.hook.rebind("beta")
        self.assertEqual(self.hook.connection["token"], "alpha-credential-no-real-secret")
        self.assertEqual(self.hook.connection["project_id"], "beta")

    async def test_an_open_turn_is_closed_under_the_project_it_started_in(self):
        await self.hook.on_submit("prompt:submit", {"prompt": "Asked while on alpha"})
        alpha = self.hook.sid
        self.hook.rebind("beta", {"token": "beta-credential-no-real-secret"})
        self.assertIsNone(self.hook.state.get("turn"))
        # Known limit: flushing is session-scoped, so the closed turn stays queued
        # under alpha for the replay helper rather than following the session to beta.
        await self.hook.flush()
        sent = [op for endpoint, body, _ in self.client.requests
                if endpoint == "publish" for op in body["operations"] if op["op"] == "turn.upsert"]
        self.assertFalse(sent, "the previous project's turn must not be sent by the rebound session")
        with self.hook.journal.connect() as conn:
            queued = [json.loads(row[0]) for row in
                      conn.execute("SELECT body FROM outbox WHERE session=?", (alpha,)).fetchall()]
        turns = [op for body in queued for op in body["operations"] if op["op"] == "turn.upsert"]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["data"]["session_id"], alpha)
        self.assertEqual(turns[0]["data"]["user_prompt"], "Asked while on alpha")
        # No response was produced before the move, so none is claimed.
        self.assertEqual(turns[0]["data"]["agent_responses"], [])
        # And nothing about that turn is attributed to beta.
        with self.hook.journal.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM outbox WHERE session=?",
                                          (self.hook.sid,)).fetchone()[0], 0)

    def test_a_blank_project_is_refused(self):
        for bad in ("", "   ", None):
            with self.assertRaises(ValueError): self.hook.rebind(bad)

    def test_a_rebind_service_url_is_validated(self):
        with self.assertRaises(ValueError):
            self.hook.rebind("beta", {"base_url": "http://not-loopback.invalid", "token": "t"})


class InfluenceTests(unittest.IsolatedAsyncioTestCase):
    """Received teammate context is named to the user, once, and never over-shares."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.events = []; self.client = Client(self.events)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork", "token": "test-credential-no-real-secret"}
        self.hook = TeamworkHook(Coordinator(Context(self.events)), self.connection,
                                 Journal(Path(self.tmp.name) / "queue.db"), self.client)

    def source(self, kind, rid, content, digest=None):
        return {"record": {"key": kind + ":" + rid + ":1", "id": rid, "record_type": kind,
                           "content": content, "content_sha256": digest or (kind + rid)},
                "delivery_id": "manifest"}

    def cache(self, sources):
        self.hook.state["cache"] = {s["record"]["record_type"] + ":" + s["record"]["id"]: s for s in sources}

    def test_a_numerous_kind_no_longer_starves_the_others(self):
        # One person record is worth more to a reader than a twentieth task.
        crowd = [self.source("work", "t%d" % i, {"title": "Task %d" % i, "detail": "x" * 700}) for i in range(19)]
        self.cache(crowd + [
            self.source("person", "p1", {"name": "Fixture", "focus": "Routing work to the right human"}),
            self.source("insight", "i1", {"summary": "Only one insight exists"}),
            self.source("plan_step", "s1", {"text": "Agree the goal"}),
        ])
        rendered, chosen = self.hook.render()
        kinds = {source["record"]["record_type"] for source in chosen}
        self.assertEqual(kinds, {"work", "person", "insight", "plan_step"})
        self.assertLessEqual(len(rendered.encode()), 10000)
        self.assertIn("Routing work to the right human", rendered)
        self.assertIn("Only one insight exists", rendered)

    def test_the_excerpt_reads_in_type_order_not_seating_order(self):
        self.cache([
            self.source("work", "t1", {"title": "A task"}),
            self.source("person", "p1", {"name": "Fixture"}),
            self.source("work", "t2", {"title": "Another task"}),
        ])
        rendered, chosen = self.hook.render()
        self.assertEqual([s["record"]["record_type"] for s in chosen], ["work", "work", "person"])
        self.assertLess(rendered.index("work:t1"), rendered.index("person:p1"))

    def test_everything_that_fits_is_still_included_unchanged(self):
        sources = [self.source("work", "t1", {"title": "A task"}),
                   self.source("person", "p1", {"name": "Fixture"}),
                   self.source("insight", "i1", {"summary": "Small"})]
        self.cache(sources)
        rendered, chosen = self.hook.render()
        self.assertEqual(len(chosen), 3)
        for source in sources:
            self.assertIn(source["record"]["key"], rendered)

    def test_a_long_record_is_truncated_rather_than_dropped(self):
        # The 1600-character fragment cap means no single record can overflow the
        # budget by itself; skipping only happens once the budget is nearly spent.
        self.cache([
            self.source("work", "t1", {"title": "Long", "detail": "x" * 40000}),
            self.source("person", "p1", {"name": "Fixture"}),
        ])
        rendered, chosen = self.hook.render()
        self.assertEqual([s["record"]["record_type"] for s in chosen], ["work", "person"])
        self.assertIn("[excerpt truncated;", rendered)
        self.assertLessEqual(len(rendered.encode()), 10000)

    def test_new_records_are_named_with_their_author(self):
        notice = self.hook.influence([
            self.source("insight", "i1", {"author": {"name": "Dana Cole"}, "summary": "Run the server yourself"}),
            self.source("work", "t1", {"title": "Connect a session", "status": "accepted"}),
        ])
        self.assertIn("teamwork", notice)
        self.assertIn("not instructions", notice)
        self.assertIn("Insight", notice)
        self.assertIn("Dana Cole", notice)
        self.assertIn("Run the server yourself", notice)
        self.assertIn("Connect a session", notice)
        # Insights lead; incidental records follow.
        self.assertLess(notice.index("Insight"), notice.index("Work"))

    def test_unchanged_records_are_not_re_announced_but_edits_are(self):
        first = self.source("insight", "i1", {"summary": "Original"}, "hash-1")
        self.assertIsNotNone(self.hook.influence([first]))
        self.assertIsNone(self.hook.influence([first]))
        edited = self.source("insight", "i1", {"summary": "Revised"}, "hash-2")
        notice = self.hook.influence([edited])
        self.assertIn("Revised", notice)

    def test_a_record_dropped_from_cache_is_announced_again_when_it_returns(self):
        record = self.source("idea", "d1", {"text": "Try a local host"})
        self.assertIn("Try a local host", self.hook.influence([record]))
        # A later turn no longer carries it: the service evicted it.
        self.hook.state["cache"] = {}
        self.assertIsNone(self.hook.influence([]))
        self.assertFalse(self.hook.state["announced"])
        # It comes back, so it is influence the user has not been told about.
        self.assertIn("Try a local host", self.hook.influence([record]))

    def test_a_record_still_cached_is_not_forgotten_between_turns(self):
        record = self.source("idea", "d1", {"text": "Try a local host"})
        self.hook.state["cache"] = {"idea:d1": {"record": record["record"], "delivery_id": "manifest"}}
        self.assertIsNotNone(self.hook.influence([record]))
        self.assertIsNone(self.hook.influence([]))
        self.assertIsNone(self.hook.influence([record]))

    def test_a_complaint_is_said_once_even_when_the_level_is_silent(self):
        self.hook.level, self.hook.complaint = "silent", "Teamwork verbosity 'loud' is not usable."
        record = self.source("insight", "i1", {"summary": "Delivered"})
        self.assertEqual(self.hook.influence([record]), "Teamwork verbosity 'loud' is not usable.")
        self.assertIsNone(self.hook.influence([record]))

    def test_a_complaint_leads_the_first_spoken_notice_then_stops(self):
        self.hook.complaint = "Teamwork verbosity 'loud' is not usable."
        notice = self.hook.influence([self.source("insight", "i1", {"summary": "Delivered"})])
        self.assertTrue(notice.startswith("Teamwork verbosity"))
        self.assertIn("Delivered", notice)
        later = self.hook.influence([self.source("idea", "d1", {"text": "Second"})])
        self.assertNotIn("verbosity", later)

    def test_silent_suppresses_the_notice_without_replaying_it_later(self):
        record = self.source("insight", "i1", {"summary": "Quietly delivered"})
        self.hook.level = "silent"
        self.assertIsNone(self.hook.influence([record]))
        # Tracking still advanced, so speaking again does not replay old influence.
        self.hook.level = "summary"
        self.assertIsNone(self.hook.influence([record]))
        edited = self.source("insight", "i1", {"summary": "Changed since"}, "hash-changed")
        self.assertIn("Changed since", self.hook.influence([edited]))

    def test_detail_names_every_record_where_summary_abbreviates(self):
        many = [self.source("work", "t%d" % index, {"title": "Item %d" % index}) for index in range(9)]
        self.hook.level = "detail"
        notice = self.hook.influence(many)
        self.assertEqual(len(notice.splitlines()), 10)         # header + all nine
        self.assertNotIn("more (", notice)
        self.assertIn("Item 8", notice)

    def test_long_notices_are_bounded_and_summarised(self):
        many = [self.source("work", "t%d" % index, {"title": "Item %d" % index}) for index in range(9)]
        notice = self.hook.influence(many)
        self.assertEqual(len(notice.splitlines()), 7)          # header + 5 shown + the remainder
        self.assertIn("+4 more (work)", notice)

    def test_notice_never_exposes_unprojected_person_fields_or_credentials(self):
        secret = self.connection["token"]
        notice = self.hook.influence([
            self.source("person", "p1", {"name": "Fixture Person", "summary": "UNPROJECTED", "private_note": "LEAK"}),
            self.source("insight", "i1", {"summary": "Token is " + secret}),
        ])
        self.assertIn("Fixture Person", notice)
        for hidden in ("UNPROJECTED", "LEAK", secret):
            self.assertNotIn(hidden, notice)
        self.assertIn("[REDACTED CREDENTIAL]", notice)
        # The projection the notice uses is the one the excerpt uses.
        self.assertNotIn("summary", PERSON_FIELDS)

    def test_titles_are_truncated_and_unknown_types_still_render(self):
        line = describe({"record_type": "insight", "id": "i", "key": "insight:i:1",
                         "content": {"summary": "x" * 500}})
        self.assertLessEqual(len(line), 170)
        self.assertTrue(line.endswith("\u2026"))
        unknown = describe({"record_type": "brand_new", "id": "n", "key": "brand_new:n:1", "content": {}})
        self.assertIn("Brand new", unknown)
        self.assertIn("brand_new:n:1", unknown)

    async def test_the_notice_reaches_the_user_through_the_hook_result(self):
        result = await self.hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        self.assertEqual(result.user_message_source, "teamwork")
        self.assertEqual(result.user_message_level, "info")
        self.assertIn("Fixture goal", result.user_message)
        # The same context on the next turn is not re-announced as new influence.
        again = await self.hook.on_submit("prompt:submit", {"prompt": "Another prompt"})
        self.assertIsNone(again.user_message)

    async def test_no_notice_when_nothing_was_accepted(self):
        self.hook.coordinator = Coordinator(Context(self.events, fail=True))
        result = await self.hook.on_submit("prompt:submit", {"prompt": "My prompt"})
        self.assertIsNone(result.user_message)
        self.assertFalse(self.hook.state.get("announced"))


class ExcerptTests(unittest.TestCase):
    """The injected excerpt says what it omits and spends its budget on content, not audit trail."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork", "token": "fixture-token"}
        self.hook = TeamworkHook(Coordinator(Context([])), self.connection,
                                 Journal(Path(self.tmp.name) / "queue.db"), Client([]))

    def cache(self, *records):
        self.hook.state["cache"] = {r["record"]["record_type"] + ":" + r["record"]["id"]: r for r in records}

    def source(self, kind, rid, content):
        return {"record": {"key": kind + ":" + rid + ":1", "id": rid, "record_type": kind,
                           "content": content, "content_sha256": kind + rid}, "delivery_id": "manifest"}

    def audited(self, **content):
        actor = "141280cb-0000-5000-8000-000000000000"
        via = {"harness_id": None, "kind": "member", "person_id": actor}
        content.update(created_at="2026-09-09T20:56:00+00:00", created_by=actor, created_via=via,
                       updated_at="2026-09-09T20:56:00+00:00", updated_by=actor, updated_via=via)
        return content

    def test_header_counts_shown_records_against_the_synchronized_cache(self):
        self.cache(*[self.source("work", "t%d" % index, {"text": "x" * 2000}) for index in range(8)])
        output, chosen = self.hook.render()
        self.assertLess(len(chosen), 8)                                # the byte budget omitted some
        self.assertIn("showing %d of 8 synchronized records" % len(chosen), output)
        self.assertLessEqual(len(output.encode()), 10000)
        self.assertNotIn("The synchronized baseline is partial.", output)
        self.hook.state["partial"] = True
        self.assertIn("The synchronized baseline is partial.", self.hook.render()[0])

    def test_audit_trail_is_projected_out_so_a_real_project_record_fits_whole(self):
        repositories = [self.audited(id="repo-%d" % index, label="Repository %d" % index, url="https://example.invalid/%d" % index,
                                     description="A description long enough to matter for the excerpt budget of this record.",
                                     access="private", access_verification="Member supplied; not independently checked",
                                     attribution_source="authenticated", ownership="shared", status="active", version=1)
                        for index in range(3)]
        project = self.audited(id="teamwork", goal="Fixture goal", accepted_plan_id=None, repositories=repositories)
        self.assertGreater(len(json.dumps(project)), 1600)          # would have been cut mid-JSON before
        self.cache(self.source("project", "teamwork", project))
        output, chosen = self.hook.render()
        fragment = output.split("project:teamwork:1\n", 1)[1].splitlines()[0]
        parsed = json.loads(fragment)                                # whole and valid, not truncated
        self.assertNotIn("[excerpt truncated", output)
        self.assertEqual(parsed["goal"], "Fixture goal")
        self.assertEqual([repo["label"] for repo in parsed["repositories"]], ["Repository 0", "Repository 1", "Repository 2"])
        for kept in ("attribution_source", "access_verification", "ownership", "status", "url"):
            self.assertIn(kept, parsed["repositories"][0])           # uncertainty markers survive
        for dropped in ("created_at", "created_by", "created_via", "updated_at", "updated_by", "updated_via"):
            self.assertNotIn(dropped, output)
        self.assertNotIn("141280cb-0000-5000-8000-000000000000", output)
        self.assertEqual(len(chosen), 1)

    def test_a_record_still_too_large_keeps_its_title_after_the_cut(self):
        self.cache(self.source("work", "big", {"detail": "d" * 3000, "owner": "MJ", "title": "Needle title"}))
        output, chosen = self.hook.render()
        self.assertIn("[excerpt truncated", output)
        self.assertIn("Needle title", output)                        # sorted-key JSON cut before "title"
        self.assertEqual(len(chosen), 1)

    def test_person_records_still_use_the_person_projection(self):
        self.cache(self.source("person", "p1", {"name": "Fixture Person", "summary": "UNPROJECTED", "created_by": "actor"}))
        output, _ = self.hook.render()
        self.assertIn("Fixture Person", output)
        self.assertNotIn("UNPROJECTED", output)
        self.assertNotIn("created_by", output)


class RedirectTests(unittest.TestCase):
    def test_harness_bearer_redirect_is_refused(self):
        request = urllib.request.Request('https://team.example.invalid/api/v1/projects/test/context', headers={'Authorization': 'Bearer fixture'})
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 307, 'Moved', {}, 'https://other.example.invalid/')


class RetiredHostTests(unittest.IsolatedAsyncioTestCase):
    """A retired base_url must never spin: mount stays inert and says so once."""

    class Hooks:
        def __init__(self):
            self.handlers = {}

        def register(self, event, handler, priority=0, name=None):
            self.handlers[name] = (event, handler)

        def unregister(self, name):
            self.handlers.pop(name, None)

    class Root:
        parent_id = None
        session_id = "retired-session"

        def __init__(self):
            self.hooks = RetiredHostTests.Hooks()
            self.capabilities = {}

        def register_capability(self, name, value):
            self.capabilities[name] = value

    async def test_retired_host_connection_stays_inert_and_says_so_once(self):
        root = self.Root()
        result = await mount(root, {
            "share_visible_turns": True,
            "base_url": "https://team.amplifier.run",
            "token": "fixture-token",
        })
        self.assertIsNone(result)
        self.assertNotIn("teamwork.session_id", root.capabilities)
        self.assertEqual(len(root.hooks.handlers), 1)
        event, handler = next(iter(root.hooks.handlers.values()))
        self.assertEqual(event, "prompt:submit")
        first = await handler("prompt:submit", {})
        self.assertEqual(first.user_message, RETIRED_MESSAGE)
        self.assertEqual(len(root.hooks.handlers), 0)
        # A retired-host result never performs a second HTTP call: the handler
        # unregistered itself, so a second submit reaches no Teamwork handler at all.

    async def test_retired_host_bind_returns_the_update_hint(self):
        # Same retired base_url, but with a project_id set -- the "bind" shape.
        root = self.Root()
        result = await mount(root, {
            "share_visible_turns": True,
            "base_url": "https://team.amplifier.run",
            "token": "fixture-token",
            "project_id": "some-project",
        })
        self.assertIsNone(result)
        self.assertNotIn("teamwork.session_id", root.capabilities)
        event, handler = next(iter(root.hooks.handlers.values()))
        notice = await handler("prompt:submit", {})
        self.assertEqual(notice.user_message, RETIRED_MESSAGE)


class MountTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_session_does_not_read_credentials_or_register_hooks(self):
        class Child:
            parent_id = 'synthetic-parent'
        self.assertIsNone(await mount(Child(), {'share_visible_turns': True, 'connection_file': '/nonexistent/fixture.json'}))

    async def test_explicitly_disabled_hook_is_a_successful_noop(self):
        self.assertIsNone(await mount(object(), {"share_visible_turns": False}))

    async def test_missing_opt_in_is_inert(self):
        self.assertIsNone(await mount(object(), {}))

    async def test_invalid_opt_in_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'explicit'):
            await mount(object(), {'share_visible_turns': 'true'})

    async def test_enabled_mount_returns_no_cleanup_metadata(self):
        class Hooks:
            def __init__(self): self.handlers = []
            def register(self, *args, **kwargs): self.handlers.append((args, kwargs))

        class Root:
            parent_id = None
            session_id = "root-session"
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}; self.tools = {}
            def register_capability(self, name, value): self.capabilities[name] = value
            async def mount(self, point, value, name): self.tools[name] = value

        with tempfile.TemporaryDirectory() as directory:
            connection = Path(directory) / "connection.json"
            connection.write_text(
                json.dumps({"base_url": "https://team.example.invalid", "project_id": "project", "token": "token"}),
                encoding="utf-8",
            )
            connection.chmod(0o600)
            root = Root()
            result = await mount(root, {"share_visible_turns": True, "connection_file": str(connection)})
        self.assertIsNone(result)
        self.assertEqual(len(root.hooks.handlers), 4)
        self.assertIn("teamwork.session_id", root.capabilities)

    async def test_host_configuration_supplies_the_connection_without_a_private_file(self):
        class Hooks:
            def __init__(self): self.handlers = []
            def register(self, *args, **kwargs): self.handlers.append((args, kwargs))

        class Root:
            parent_id = None
            session_id = "configured-session"
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}; self.tools = {}
            def register_capability(self, name, value): self.capabilities[name] = value
            async def mount(self, point, value, name): self.tools[name] = value

        root = Root()
        with tempfile.TemporaryDirectory() as directory:
            result = await mount(root, {
                "share_visible_turns": True,
                "base_url": "https://team.example.invalid",
                "project_id": "configured-project",
                "token": "[REDACTED:SECRET]",
                "journal_path": str(Path(directory) / "queue.sqlite3"),
            })
        self.assertIsNone(result)
        self.assertEqual(len(root.hooks.handlers), 4)
        self.assertIn("teamwork.session_id", root.capabilities)

    def test_configured_project_overrides_the_enrolled_file(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = Path(directory) / "connection.json"
            connection.write_text(
                json.dumps({"base_url": "https://team.example.invalid", "project_id": "enrolled", "token": "file-token"}),
                encoding="utf-8",
            )
            connection.chmod(0o600)
            resolved, home = resolve_connection({"connection_file": str(connection), "project_id": "session-bound"})
        self.assertEqual(resolved["project_id"], "session-bound")
        self.assertEqual(resolved["token"], "file-token")
        self.assertEqual(home, connection.parent)

    def test_blank_configuration_value_is_refused_rather_than_sent(self):
        # An unset ${VAR} expands to an empty string; that must not reach the service.
        with self.assertRaisesRegex(ValueError, "empty"):
            resolve_connection({
                "base_url": "https://team.example.invalid",
                "project_id": "project",
                "token": "   ",
            })

    def test_configuration_without_a_credential_still_requires_one(self):
        with self.assertRaises((ValueError, OSError, FileNotFoundError)):
            resolve_connection({"base_url": "https://team.example.invalid", "project_id": "project",
                                "connection_file": "/nonexistent/fixture.json"})

    def test_configured_recipient_is_validated(self):
        with self.assertRaises(ValueError):
            resolve_connection({"base_url": "http://team.example.invalid", "project_id": "p", "token": "t"})

    def test_an_unusable_level_degrades_and_complains_rather_than_disabling_sharing(self):
        self.assertEqual(verbosity({}), ("summary", None))
        self.assertEqual(verbosity({"verbosity": "silent"}), ("silent", None))
        level, complaint = verbosity({"verbosity": "loud"})
        self.assertEqual(level, "summary")
        self.assertIn("silent, summary, detail", complaint)
        self.assertEqual(verbosity({"verbosity": 2})[0], "summary")

    async def test_configured_level_reaches_the_mounted_hook(self):
        class Hooks:
            def __init__(self): self.handlers = []
            def register(self, *args, **kwargs): self.handlers.append((args, kwargs))

        class Root:
            parent_id = None
            session_id = "verbosity-session"
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}; self.tools = {}
            def register_capability(self, name, value): self.capabilities[name] = value
            async def mount(self, point, value, name): self.tools[name] = value

        root = Root()
        with tempfile.TemporaryDirectory() as directory:
            await mount(root, {
                "share_visible_turns": True,
                "base_url": "https://team.example.invalid",
                "project_id": "configured",
                "token": "[REDACTED:SECRET]",
                "verbosity": "detail",
                "journal_path": str(Path(directory) / "queue.sqlite3"),
            })
            hook = root.hooks.handlers[0][0][1].__self__
            self.assertEqual(hook.level, "detail")

    def test_http_client_validates_recipient_before_reading_token(self):
        class TokenTrap(dict):
            def __getitem__(self, key):
                if key == "token":
                    raise AssertionError("token was read before recipient validation")
                return super().__getitem__(key)

        with self.assertRaises(ValueError):
            HTTPClient(TokenTrap({
                "base_url": "https://team.example.invalid@evil.example",
                "token": "must-not-be-read",
            }))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = HookTests.asyncSetUp
    async def test_restart_after_input_acceptance_before_receipt_commit_preserves_unknown(self):
        original_save = self.journal.save
        def fail_receipt_commit(sid, state, mutations=()):
            if mutations and mutations[0][0] == 'acknowledgements':
                raise OSError('synthetic disk interruption')
            return original_save(sid, state, mutations)
        self.journal.save = fail_receipt_commit
        await self.hook.on_submit('prompt:submit', {'prompt': 'Synthetic prompt'})
        self.assertIn('input_accepted', [event[0] for event in self.events])
        durable = self.journal.load(self.hook.sid)
        prepared = durable['turn']['prepared_injection']
        self.assertEqual(durable['turn']['boundary'], 'prepared')
        self.journal.save = original_save
        resumed = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        await resumed.on_end('session:end', {})
        recovered = self.journal.load(self.hook.sid)
        unknown = next(iter(recovered['acceptance_unknown'].values()))
        self.assertEqual(unknown, {'outcome': 'acceptance_unknown', 'injection': prepared})
        self.assertIsNone(recovered['turn'])
        self.assertFalse(any(endpoint == 'acknowledgements' for endpoint, _, _ in self.client.requests))
        turns = [op for endpoint, body, _ in self.client.requests if endpoint == 'publish' for op in body['operations'] if op['op'] == 'turn.upsert']
        self.assertEqual(turns[0]['data']['hook_injections'], [])


class StandaloneRecoveryTests(unittest.TestCase):
    def test_replay_help_does_not_require_amplifier_core(self):
        replay = Path(__file__).resolve().parents[1] / "replay_pending.py"
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(replay), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Replay this connection", result.stdout)



class WorkToolTests(unittest.IsolatedAsyncioTestCase):
    """The tools a session uses to see and move work its own person holds."""

    OWNER = "person-alex"

    class Work:
        def __init__(self, sid, items, fail=None):
            self.sid, self.items, self.fail, self.writes = sid, items, fail, []

        def request(self, endpoint, body, key=None):
            if endpoint == "context":
                records = [{"record_type": "agent", "content": {"id": self.sid,
                                                                "owner_person_id": WorkToolTests.OWNER}}]
                records += [{"record_type": "work", "content": item} for item in self.items]
                return {"items": records, "next_cursor": None, "has_more": False}
            if self.fail:
                raise SyncError(self.fail)
            self.writes.append(body)
            return {"results": [{"id": body["operations"][0]["id"], "version": 2}]}

    def build(self, items, fail=None):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork", "token": "t"}
        hook = TeamworkHook(Coordinator(Context([])), connection, Journal(Path(tmp.name) / "q.db"),
                            self.Work("placeholder", items, fail))
        hook.client.sid = hook.sid
        return hook

    async def test_it_lists_my_work_and_reads_the_older_status_spelling(self):
        # Stored records still carry UI labels from before the portal normalised
        # them on write; a tool that only knew canonical values would report half
        # the board as unknown.
        hook = self.build([{"id": "w1", "title": "Ship it", "status": "In progress", "version": 1,
                            "requested_person_id": self.OWNER}])
        result = await TasksTool(hook).execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.output["assigned"][0]["status"], "in_progress")

    async def test_claiming_writes_the_canonical_status(self):
        hook = self.build([{"id": "w1", "title": "Ship it", "status": "Ready", "version": 3,
                            "requested_person_id": self.OWNER}])
        result = await ClaimTool(hook).execute({"task_id": "w1"})
        self.assertTrue(result.success)
        operation = hook.client.writes[0]["operations"][0]
        self.assertEqual(operation["data"], {"status": "accepted"})
        self.assertEqual(operation["expected_version"], 3)

    async def test_progress_touches_only_the_allowed_fields(self):
        hook = self.build([{"id": "w1", "title": "Ship it", "status": "accepted", "version": 1,
                            "requested_person_id": self.OWNER}])
        result = await ProgressTool(hook).execute({"task_id": "w1", "note": "Rolled back the bad deploy",
                                                   "status": "in_progress"})
        self.assertTrue(result.success)
        self.assertEqual(set(hook.client.writes[0]["operations"][0]["data"]), {"progress_note", "status"})

    async def test_somebody_elses_work_is_refused_without_confirming_it_exists(self):
        hook = self.build([{"id": "w1", "title": "Not yours", "status": "requested", "version": 1,
                            "requested_person_id": "person-blair"}])
        result = await ClaimTool(hook).execute({"task_id": "w1"})
        self.assertFalse(result.success)
        self.assertIn("not assigned to you, or does not exist", result.error["message"])
        self.assertEqual(hook.client.writes, [])

    async def test_a_server_without_the_narrow_permission_says_so_plainly(self):
        hook = self.build([{"id": "w1", "title": "Ship it", "status": "requested", "version": 1,
                            "requested_person_id": self.OWNER}], fail=403)
        result = await ClaimTool(hook).execute({"task_id": "w1"})
        self.assertFalse(result.success)
        self.assertIn("does not yet allow", result.error["message"])

    async def test_the_mounted_tools_can_move_work_but_never_create_it(self):
        # A session must not invent tasks for anyone, including its own owner:
        # that is a person's decision, made in the portal.
        hook = self.build([])
        mounted = set()

        class Recording(Coordinator):
            def __init__(self, context): super().__init__(context); self.hooks = Hooks(); self.capabilities = {}
            def register_capability(self, name, value): self.capabilities[name] = value
            async def mount(self, point, value, name): mounted.add(name)

        for tool in (TasksTool(hook), ClaimTool(hook), ProgressTool(hook)):
            mounted.add(tool.name)
        self.assertEqual(mounted, {"teamwork_tasks", "teamwork_claim", "teamwork_progress"})
        self.assertFalse(any("add" in n or "create" in n or "new" in n for n in mounted))


def mirror_message(mid="msg-1", body="Please look at the relay timeouts.", person="person-alex"):
    content = {"id": mid, "body": body, "from_person_id": person, "from_harness_id": "harness-7",
               "sent_at": "2026-09-10T10:00:00Z", "created_by": person}
    return {"key": "message:%s:1" % mid, "id": mid, "version": 1, "record_type": "message",
            "change": "upsert", "content": content, "content_sha256": "hash-" + mid}


def mirror_agent(sid, owner="person-jamie"):
    return {"key": "agent:%s:1" % sid, "id": sid, "version": 1, "record_type": "agent",
            "change": "upsert", "content": {"id": sid, "owner_person_id": owner}, "content_sha256": "agent-" + sid}


class FakeFiling:
    """An in-memory local queue: files land immediately, no subprocess involved."""

    def __init__(self):
        self.name = "teamwork"
        self.filed = {}

    def ready(self):
        return self.name

    def file(self, message_id, item_title, item_description):
        self.filed[message_id] = item_title
        return "tw-" + message_id


class MirrorClient:
    """Serves one inbound message plus its sender's agent record; `publish` of a
    `request.upsert` op can be made to fail with a chosen HTTP status."""

    def __init__(self, records, fail_status=None):
        self.records, self.fail_status, self.requests = records, fail_status, []

    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, json.loads(json.dumps(body)), key))
        if endpoint == "context":
            return {"next_cursor": "c", "delivery_id": "d", "has_more": False, "truncated": False,
                    "items": self.records}
        if endpoint == "publish":
            operations = body.get("operations") or []
            if self.fail_status and any(o.get("op") == "request.upsert" for o in operations):
                raise SyncError(self.fail_status)
        return {"stored": True}


class Mirror(unittest.IsolatedAsyncioTestCase):
    """A filed report is best-effort mirrored to the shared project as a request."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork",
                           "token": "test-credential-no-real-secret"}
        self.journal_path = Path(self.tmp.name) / "queue.db"

    def build(self, fail_status=None, owner="person-jamie", mid="msg-1", body=None, journal=None):
        journal = journal or Journal(self.journal_path)
        client = MirrorClient([], fail_status)
        hook = TeamworkHook(Coordinator(Context([])), self.connection, journal, client, filing=FakeFiling())
        message = mirror_message(mid=mid, body=body) if body else mirror_message(mid=mid)
        message["content"]["to_agent_id"] = hook.sid
        client.records = [message, mirror_agent(hook.sid, owner)]
        return hook, client

    def mirror_ops(self, client):
        return [op for endpoint, body, _ in client.requests if endpoint == "publish"
                for op in body["operations"] if op["op"] == "request.upsert"]

    async def test_filing_publishes_request_upsert(self):
        hook, client = self.build()
        await hook.on_submit("prompt:submit", {"prompt": "hi"})
        ops = self.mirror_ops(client)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["id"], "inbound-msg-1")

    async def test_mirror_uses_own_person_id_from_agent_cache(self):
        hook, client = self.build(owner="person-jamie")
        await hook.on_submit("prompt:submit", {"prompt": "hi"})
        self.assertEqual(self.mirror_ops(client)[0]["data"]["requested_person_id"], "person-jamie")

    async def test_mirror_403_degrades_to_one_notice(self):
        hook, client = self.build(fail_status=403)
        result = await hook.on_submit("prompt:submit", {"prompt": "hi"})
        self.assertIn("does not yet allow mirroring", result.user_message)
        second_message = mirror_message(mid="msg-2")
        second_message["content"]["to_agent_id"] = hook.sid
        client.records.append(second_message)
        second = await hook.on_submit("prompt:submit", {"prompt": "again"})
        self.assertNotIn("does not yet allow mirroring", second.user_message or "")
        # Disabled for the rest of the session, not just the notice: one attempt only.
        self.assertEqual(len(self.mirror_ops(client)), 1)

    async def test_mirror_409_counts_as_mirrored(self):
        hook, client = self.build(fail_status=409)
        await hook.on_submit("prompt:submit", {"prompt": "hi"})
        self.assertEqual(hook.state["mirrored"]["msg-1"], "inbound-msg-1")
        await hook.on_submit("prompt:submit", {"prompt": "again"})
        self.assertEqual(len(self.mirror_ops(client)), 1)

    async def test_mirror_failure_never_blocks_local_filing(self):
        hook, client = self.build(fail_status=500)
        result = await hook.on_submit("prompt:submit", {"prompt": "hi"})
        self.assertEqual(hook.state["filed"]["msg-1"], "tw-msg-1")
        self.assertNotIn("msg-1", hook.state.get("mirrored", {}))
        self.assertIn("Filed into the local queue", result.user_message)

    async def test_mirror_is_idempotent_across_turns(self):
        hook, client = self.build(fail_status=None)
        await hook.on_submit("prompt:submit", {"prompt": "hi"})
        self.assertEqual(len(self.mirror_ops(client)), 1)
        resumed = TeamworkHook(Coordinator(Context([])), self.connection, Journal(self.journal_path), client,
                               filing=FakeFiling())
        self.assertEqual(resumed.sid, hook.sid)
        await resumed.on_submit("prompt:submit", {"prompt": "again"})
        self.assertEqual(len(self.mirror_ops(client)), 1)
        self.assertEqual(resumed.state["mirrored"]["msg-1"], "inbound-msg-1")


if __name__ == '__main__':
    unittest.main()
