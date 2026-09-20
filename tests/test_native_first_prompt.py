"""A hook attached by native consent starts after the host's session:start."""
from pathlib import Path
import tempfile
import unittest

from test_hook import Client, Context, Coordinator
from amplifier_module_hooks_teamwork import Journal, SyncError, TeamworkHook, WaitTool


class ParentRequiredClient(Client):
    """Presence needs a server-owned session, as in the app's own_session gate."""
    def __init__(self):
        super().__init__([])
        self.sessions = set()
        self.presence_attempts = []

    def request(self, endpoint, body, key=None):
        operations = body.get('operations', []) if endpoint == 'publish' else []
        for operation in operations:
            if operation['op'] == 'presence.upsert':
                self.presence_attempts.append(operation)
                if operation['data']['session_id'] not in self.sessions:
                    raise SyncError(404, {'error': {'code': 'not_found'}})
        result = super().request(endpoint, body, key)
        self.sessions.update(operation['id'] for operation in operations
                             if operation['op'] == 'session.upsert')
        return result


class NativeFirstPrompt(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = ParentRequiredClient()
        self.hook = TeamworkHook(Coordinator(Context([])),
            {'base_url': 'https://fixture.invalid', 'project_id': 'fixture', 'token': 'fixture-token'},
            Journal(Path(self.tmp.name) / 'journal.db'), self.client)

    async def test_native_consent_completion_does_not_publish_until_next_prompt(self):
        # Consent mounted the hook during a running tool call, after session:start.
        await self.hook.on_complete('prompt:complete', {'response': 'Connected'})
        self.assertFalse(self.hook.entered)
        self.assertEqual(self.client.requests, [])
        await self.hook.on_submit('prompt:submit', {'prompt': 'First opted-in turn'})
        self.assertEqual(self.client.sessions, {self.hook.sid})
        self.assertEqual(self.hook.presence_status, 'active')
        self.assertEqual(len(self.client.presence_attempts), 1)

    async def test_wait_can_observe_answer_after_native_first_prompt(self):
        await self.hook.on_submit('prompt:submit', {'prompt': 'First opted-in turn'})
        await self.hook.on_complete('prompt:complete', {'response': 'Ready'})
        self.hook.state['cache']['request:review'] = {'record': {
            'id': 'review', 'record_type': 'request', 'content': {
                'response': 'context', 'progress_note': '4560 cents',
                'responded_by': 'reviewer', 'title': 'Synthetic invoice review'}}}
        result = await WaitTool(self.hook).execute({
            'request_id': 'review', 'reason': 'Invoice review', 'seconds': 1})
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output['state'], 'answered')
        self.assertEqual(result.output['answer']['note'], '4560 cents')
        self.assertEqual(result.output['answer']['answered_by'], 'reviewer')

    async def test_parent_publish_failure_does_not_permanently_disable_presence(self):
        self.client.fail = True
        await self.hook.on_submit('prompt:submit', {'prompt': 'Offline first turn'})
        self.assertEqual(self.client.sessions, set())
        self.assertEqual(self.client.presence_attempts, [])
        self.assertEqual(self.hook.presence_status, 'unreported')
        self.client.fail = False
        await self.hook.on_submit('prompt:submit', {'prompt': 'Connection recovered'})
        self.assertEqual(self.client.sessions, {self.hook.sid})
        self.assertEqual(self.hook.presence_status, 'active')
        self.assertEqual(len(self.client.presence_attempts), 1)
