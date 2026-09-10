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
                                             mount, resolve_connection, describe, PERSON_FIELDS, verbosity)


class Context:
    def __init__(self, events, fail=False): self.events, self.fail = events, fail
    async def add_message(self, message):
        if self.fail: raise RuntimeError("Input rejected")
        self.events.append(("input_accepted", message))


class Coordinator:
    session_id = "native-session-fixture"
    def __init__(self, context): self.context = context
    def get(self, name): return self.context if name == "context" else None


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

    async def test_offline_outbox_reuses_key_after_restart(self):
        self.client.fail = True
        await self.hook.on_start("session:start", {})
        first = self.client.requests[0]
        self.client.fail = False
        resumed = TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)
        await resumed.on_start("session:start", {})
        self.assertEqual(first, self.client.requests[1])
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


class RedirectTests(unittest.TestCase):
    def test_harness_bearer_redirect_is_refused(self):
        request = urllib.request.Request('https://team.example.invalid/api/v1/projects/test/context', headers={'Authorization': 'Bearer fixture'})
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 307, 'Moved', {}, 'https://other.example.invalid/')


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
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}
            def register_capability(self, name, value): self.capabilities[name] = value

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
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}
            def register_capability(self, name, value): self.capabilities[name] = value

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
            def __init__(self): self.hooks = Hooks(); self.capabilities = {}
            def register_capability(self, name, value): self.capabilities[name] = value

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


if __name__ == '__main__':
    unittest.main()
