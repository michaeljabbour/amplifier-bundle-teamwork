"""Fresh-process resume keeps the same own card addressable without blind replay."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_hook import Client, Context, Coordinator
from amplifier_module_hooks_teamwork import Journal, SyncError, TeamworkHook, mount


class VersionedCards(Client):
    """App's single-operation version_conflict contract, with real version CAS."""
    def __init__(self):
        super().__init__([])
        self.versions = {}
        self.card_calls = []

    def request(self, endpoint, body, key=None):
        operations = body.get('operations', [])
        if endpoint == 'publish' and len(operations) == 1 and operations[0]['op'] in ('agent.upsert', 'presence.upsert'):
            op = operations[0]
            self.card_calls.append((copy.deepcopy(op), key))
            identity = (op['op'], op['id'])
            actual = self.versions.get(identity, 0)
            if op['expected_version'] != actual:
                raise SyncError(409, {'error':{'code':'version_conflict', 'current_version':actual, 'operation_index':0}})
            self.versions[identity] = actual + 1
        return super().request(endpoint, body, key)


class ResumeRegistration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.connection = {'base_url':'https://fixture.invalid', 'project_id':'fixture-project', 'token':'fixture-only-token'}
        self.journal = Journal(Path(self.tmp.name) / 'journal.sqlite3')
        self.client = VersionedCards()
        self.context = Context([])
        self.hook = self.fresh_hook()

    def fresh_hook(self):
        return TeamworkHook(Coordinator(self.context), self.connection, self.journal, self.client)

    async def test_resume_event_registers_before_first_prompt(self):
        class Hooks:
            def __init__(self): self.handlers = {}
            def register(self, event, handler, **kw): self.handlers[event] = handler
        class Host(Coordinator):
            parent_id = None
            def __init__(self, context): super().__init__(context); self.hooks = Hooks()
            def register_capability(self, *args): pass
        host = Host(self.context)
        with patch('amplifier_module_hooks_teamwork.resolve_connection', return_value=(self.connection, Path(self.tmp.name))), patch('amplifier_module_hooks_teamwork.HTTPClient', return_value=self.client):
            await mount(host, {'share_visible_turns':True, 'file_inbound_reports':False})
        self.assertIn('session:resume', host.hooks.handlers)
        await host.hooks.handlers['session:resume']('session:resume', {})
        self.assertEqual(len(self.client.card_calls), 1)
        self.assertEqual(self.client.card_calls[0][0]['op'], 'agent.upsert')
        self.assertFalse(any(o['op'] == 'turn.upsert' for _, b, _ in self.client.requests for o in b.get('operations', [])))

    async def test_fresh_process_rebases_existing_agent_and_presence_once(self):
        await self.hook.on_start('session:start', {})
        self.hook.report_presence('idle')
        resumed = self.fresh_hook()
        await resumed.on_start('session:resume', {})
        resumed.report_presence('idle')
        self.assertEqual(resumed.agent_status, 'registered')
        self.assertEqual(resumed.presence_status, 'idle')
        self.assertEqual((resumed.agent_version, resumed.presence_version), (2, 2))
        for kind in ('agent.upsert', 'presence.upsert'):
            calls = [(op, key) for op, key in self.client.card_calls if op['op'] == kind]
            self.assertEqual([op['expected_version'] for op, _ in calls], [0, 0, 1])
            self.assertEqual(len({key for _, key in calls}), 3)
            self.assertEqual({op['id'] for op, _ in calls}, {self.hook.sid})

    async def test_resume_explicitly_clears_prior_end_timestamp(self):
        await self.hook.on_start('session:start', {})
        await self.hook.on_end('session:end', {})
        resumed = self.fresh_hook()
        await resumed.on_start('session:resume', {})
        sessions = [op for _, body, _ in self.client.requests for op in body.get('operations', []) if op['op'] == 'session.upsert']
        self.assertEqual(sessions[-2]['data']['status'], 'completed')
        self.assertTrue(sessions[-2]['data']['ended_at'])
        self.assertEqual(sessions[-1]['data']['status'], 'active')
        self.assertIn('ended_at', sessions[-1]['data'])
        self.assertIsNone(sessions[-1]['data']['ended_at'])

    async def test_only_exact_definite_conflict_can_retry(self):
        cases = [(0,None), (503,None), (403,None), (409,None),
                 (409,{'error':{'code':'idempotency_mismatch','current_version':2,'operation_index':0}}),
                 (409,{'error':{'code':'version_conflict','current_version':True,'operation_index':0}}),
                 (409,{'error':{'code':'version_conflict','current_version':-1,'operation_index':0}}),
                 (409,{'error':{'code':'version_conflict','current_version':'2','operation_index':0}}),
                 (409,{'error':{'code':'version_conflict','current_version':0,'operation_index':0}}),
                 (409,{'error':{'code':'version_conflict','current_version':2,'operation_index':False}}),
                 (409,{'error':{'code':'version_conflict','current_version':2}}),
                 (409,{'error':{'code':'version_conflict','current_version':2,'operation_index':1}})]
        for status, body in cases:
            with self.subTest(status=status, body=body):
                hook = self.fresh_hook(); hook.entered = True
                with patch.object(self.client, 'request', side_effect=SyncError(status, body)) as call:
                    hook.register_agent(); hook.register_agent()
                self.assertEqual(call.call_count, 1)
                self.assertEqual(hook.agent_status, 'unavailable')

    async def test_successful_rebase_retains_payload_and_updates_only_expected_version(self):
        self.hook.entered = True
        self.client.versions[('agent.upsert', self.hook.sid)] = 7
        self.hook.register_agent()
        first, second = self.client.card_calls
        self.assertEqual(first[0] | {'expected_version':7}, second[0])
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(self.hook.agent_version, 8)
        self.assertEqual(self.hook.agent_status, 'registered')

    async def test_continuing_conflict_is_bounded_to_one_rebase(self):
        self.hook.entered = True
        error = SyncError(409, {'error':{'code':'version_conflict','current_version':3,'operation_index':0}})
        with patch.object(self.client, 'request', side_effect=error) as call:
            self.hook.register_agent()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(self.hook.agent_status, 'unavailable')

    async def test_objective_fallback_cannot_start_a_second_conflict_rebase(self):
        self.hook.entered = True
        conflict = lambda version: SyncError(409, {'error':{'code':'version_conflict','current_version':version,'operation_index':0}})
        unsupported = SyncError(422, {'error':{'code':'invalid_request','message':'Unknown queue observation field'}})
        with patch.object(self.hook, 'queue_observation', return_value={'queue_status':'ready','objectives':[]}):
            with patch.object(self.client, 'request', side_effect=[conflict(3), unsupported, conflict(4), {'stored':True}]) as call:
                self.hook.register_agent()
        self.assertEqual(call.call_count, 3)
        self.assertEqual(self.hook.agent_status, 'unavailable')

    async def test_objective_fallback_still_allows_its_first_definite_rebase(self):
        self.hook.entered = True
        unsupported = SyncError(422, {'error':{'code':'invalid_request','message':'Unknown queue observation field'}})
        conflict = SyncError(409, {'error':{'code':'version_conflict','current_version':3,'operation_index':0}})
        with patch.object(self.hook, 'queue_observation', return_value={'queue_status':'ready','objectives':[]}):
            with patch.object(self.client, 'request', side_effect=[unsupported, conflict, {'stored':True}]) as call:
                self.hook.register_agent()
        self.assertEqual(call.call_count, 3)
        self.assertEqual(self.hook.agent_status, 'registered')
        self.assertEqual(self.hook.agent_version, 4)

    async def test_rebind_during_conflict_cannot_publish_in_new_project(self):
        self.hook.entered = True
        def changed_binding(*args):
            self.hook.rebind('other-project', {'token':'other-fixture-token'})
            raise SyncError(409, {'error':{'code':'version_conflict','current_version':3,'operation_index':0}})
        with patch.object(self.client, 'request', side_effect=changed_binding) as call:
            self.hook.register_agent()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.hook.agent_status, 'unregistered')
        self.assertEqual(self.hook.agent_version, 0)


if __name__ == '__main__': unittest.main()
