"""Optional cards can recover from a temporary outage on the next real update."""
import tempfile
import unittest
from pathlib import Path
from test_hook import Coordinator, Context, Client
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, SyncError


class RecoveringClient(Client):
    def __init__(self, kind, status, body=None):
        super().__init__([])
        self.kind, self.status, self.body = kind, status, body
        self.failed = False

    def request(self, endpoint, body, key=None):
        if not self.failed and any(op['op'] == self.kind+'.upsert' for op in body.get('operations', [])):
            self.failed = True
            raise SyncError(self.status, self.body)
        return super().request(endpoint, body, key)


class CardRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_transient_errors_retry_current_presence_and_card_at_next_boundary(self):
        for kind in ('presence', 'agent'):
            for status, body in [(0, None), (503, None), (429, None), (404, {'error': {'code': 'session_not_found'}})]:
                with self.subTest(kind=kind, status=status), tempfile.TemporaryDirectory() as folder:
                    client = RecoveringClient(kind, status, body)
                    hook = TeamworkHook(Coordinator(Context([])),
                        {'base_url': 'https://fixture.invalid', 'project_id': 'fixture', 'token': 'fixture-credential'},
                        Journal(Path(folder) / 'journal.db'), client)
                    hook.entered = True
                    if kind == 'presence':
                        hook.report_presence('active', 'Old subject')
                        hook.report_presence('idle', 'Current subject')
                        self.assertEqual(hook.presence_status, 'idle')
                        payload = client.requests[-1][1]['operations'][0]['data']
                        self.assertEqual(payload['state'], 'idle')
                        self.assertEqual(payload['summary'], 'Current subject')
                    else:
                        hook.register_agent(); hook.register_agent()
                        self.assertEqual(hook.agent_status, 'registered')
                    with hook.journal.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)

    async def test_definite_unsupported_or_forbidden_response_stops_optional_retries(self):
        for kind in ('presence', 'agent'):
            for status in (400, 403, 404, 422):
                with self.subTest(kind=kind, status=status), tempfile.TemporaryDirectory() as folder:
                    client = RecoveringClient(kind, status)
                    hook = TeamworkHook(Coordinator(Context([])),
                        {'base_url': 'https://fixture.invalid', 'project_id': 'fixture', 'token': 'fixture-credential'},
                        Journal(Path(folder) / 'journal.db'), client)
                    hook.entered = True
                    if kind == 'presence':hook.report_presence('active');hook.report_presence('idle')
                    else:hook.register_agent();hook.register_agent()
                    self.assertEqual(getattr(hook,kind+'_status'), 'unavailable')
                    self.assertEqual(client.requests, [])
