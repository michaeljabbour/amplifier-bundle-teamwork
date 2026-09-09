import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, SyncError, sha, NoRedirect, mount


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

    async def test_root_requires_explicit_sharing_opt_in(self):
        with self.assertRaisesRegex(ValueError, 'explicit'):
            await mount(object(), {})


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


if __name__ == '__main__':
    unittest.main()
