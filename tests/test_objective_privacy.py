"""Adversarial objective disclosure and old-service compatibility contracts."""
import asyncio
import json
import threading
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'modules/hooks-teamwork'))
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, SyncError, mount
from amplifier_module_hooks_teamwork import reports
from test_hook import Client, Context, Coordinator


def item(**values):
    return {'id': 'private-customer-refund', 'title': 'Private fixture topic',
            'status': 'held', 'holder': 'fixture-session-actor', **values}


class ObjectivePrivacy(unittest.TestCase):
    def queue(self, *, topic=False, actor='fixture-session-actor'):
        q = reports.Queue('fixture', Path('/unused-registry'), share_topic=topic)
        q.actor = actor
        return q

    def test_only_explicit_booleans_can_configure_topic_disclosure(self):
        for value in ('false', 'true', 0, 1, None, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.queue(topic=value)

    def test_unknown_session_actor_never_attributes_other_holders(self):
        self.assertEqual(self.queue(actor=None).objectives([item()]), [])

    def test_exact_actor_binding_excludes_other_sessions(self):
        got = self.queue(topic=True).objectives([item(holder='other-session'), item(id='mine')])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['title'], 'Private fixture topic')

    def test_default_topic_off_does_not_leak_topic_shaped_local_ids(self):
        q = self.queue()
        first = q.objectives([item()])
        self.assertEqual(len(first), 1)
        self.assertNotIn('private-customer', json.dumps(first))
        self.assertNotIn('Private fixture', json.dumps(first))
        self.assertEqual(first, q.objectives([item()]))
        other = self.queue(); other.project_id = 'other-project'
        self.assertNotEqual(first[0]['id'], other.objectives([item()])[0]['id'])

    def test_untrusted_tracker_shapes_and_unknown_status_are_not_objectives(self):
        rows = [None, 'private text', [], item(holder={'secret': 'text'}),
                item(id={'private': 'text'}), item(status='secret free text'),
                item(status='resolved'), item(status='deferred'), item(status='open'), item(status='ready'), item(title={'secret': 'text'})]
        self.assertEqual(self.queue(topic=True).objectives(rows), [])

    def test_duplicate_ids_do_not_fill_the_bounded_card(self):
        q = self.queue(); rows = [item(id='one')] * 10 + [item(id='two')]
        self.assertEqual(len(q.objectives(rows)), 2)

    def test_malformed_page_is_unavailable_without_raw_payload(self):
        q = self.queue(); q.name = 'fixture'
        for page in ([], {'items': {}}, {'items': ['private sentinel']}):
            with self.subTest(page=page), patch.object(q, 'run', return_value=json.dumps(page)):
                got = q.status()
                self.assertEqual(got['queue_status'], 'unavailable')
                self.assertNotIn('private sentinel', json.dumps(got))

    def test_failed_probe_does_not_reuse_topics_after_local_disclosure_revocation(self):
        q = self.queue(topic=True); q.name = 'fixture'
        with patch.object(q, 'run', return_value=json.dumps({'items': [item()]})):
            self.assertIn('Private fixture topic', json.dumps(q.status()))
        q.share_topic = False
        with patch.object(q, 'run', side_effect=reports.QueueUnavailable('fixture unavailable')):
            got = q.status()
        self.assertNotIn('Private fixture topic', json.dumps(got))
        self.assertEqual(got['queue_status'], 'unavailable')

    def test_title_bound_is_utf8_bytes_in_both_projection_and_sanitizer(self):
        text = "🔐" * 80
        projected = self.queue(topic=True).objectives([item(title=text)])[0]
        sanitized = reports.sanitize_outbound({'objectives': [{'id': 'safe', 'status': 'held', 'title': text}]})['objectives'][0]
        for row in (projected, sanitized):
            self.assertLessEqual(len(row['title'].encode('utf-8')), 160)
            self.assertTrue(text.startswith(row['title']))
            self.assertTrue(row['title'])

    def test_invalid_unicode_objective_is_omitted_before_encoding(self):
        invalid = chr(0xd800)
        self.assertEqual(self.queue(topic=True).objectives([item(title=invalid), item(id=invalid)]), [])
        self.assertEqual(reports.sanitize_outbound({'objectives': [{'id': 'safe', 'status': 'held', 'title': invalid}]}), {'objectives': []})

    def test_sanitizer_rejects_invalid_nested_state_and_empty_ids(self):
        got = reports.sanitize_outbound({'objectives': [
            {'id': '', 'status': 'held'}, {'id': 'x', 'status': 'secret text'},
            {'id': 'ok', 'status': 'blocked', 'holder': 'private-host'}]})
        self.assertEqual(got, {'objectives': [{'id': 'ok', 'status': 'blocked'}]})


class ObjectiveLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.events = []; self.client = Client(self.events)
        self.connection = {'base_url': 'https://example.invalid', 'project_id': 'first', 'token': 'fixture-private-token'}
        self.journal = Journal(Path(self.tmp.name) / 'queue.db')
        self.q = reports.Queue('first', Path(self.tmp.name) / 'registry', share_topic=True)
        self.q.actor = 'fixture-session-actor'; self.q.name = 'first'
        self.q.last_status = {'queue_status': 'ready', 'objectives': [{'id': 'old', 'status': 'held', 'title': 'FIRST_PROJECT_ONLY'}]}
        self.hook = TeamworkHook(Coordinator(Context(self.events)), self.connection, self.journal, self.client, filing=self.q)

    async def test_invalid_topic_setting_is_refused_before_credentials_or_registration(self):
        with patch('amplifier_module_hooks_teamwork.resolve_connection') as resolve:
            with self.assertRaisesRegex(ValueError, 'share_objective_topic'):
                await mount(object(), {'share_visible_turns': True, 'share_objective_topic': 'false'})
            resolve.assert_not_called()

    async def test_disabled_sharing_remains_inert_even_with_topic_configuration(self):
        with patch('amplifier_module_hooks_teamwork.resolve_connection') as resolve:
            self.assertIsNone(await mount(object(), {'share_objective_topic': True}))
            resolve.assert_not_called()

    async def test_rebind_drops_cached_topic_and_prior_project_topic_grant(self):
        self.hook.rebind('second', {'token': 'different-fixture-token'})
        with patch.object(self.q, 'run', side_effect=reports.QueueUnavailable('fixture unavailable')):
            self.assertNotIn('FIRST_PROJECT_ONLY', json.dumps(self.hook.queue_observation()))
        self.assertIsNone(self.q.last_status)
        self.assertIsNone(self.q.actor)
        self.assertFalse(self.q.share_topic)

    async def test_old_server_rejection_retries_once_without_objectives(self):
        calls = []
        def request(endpoint, body, key):
            calls.append(json.loads(json.dumps(body)))
            if len(calls) == 1:
                raise SyncError(422, {'error': {'code': 'invalid_request', 'message': 'Unknown queue observation field'}})
            return {'results': [{'id': self.hook.sid, 'version': 1}]}
        self.hook.entered = True
        with patch.object(self.q, 'status', return_value=self.q.last_status), patch.object(self.client, 'request', side_effect=request):
            self.hook.register_agent()
        self.assertEqual(self.hook.agent_status, 'registered')
        self.assertEqual(len(calls), 2)
        self.assertIn('objectives', calls[0]['operations'][0]['data']['queue'])
        self.assertNotIn('objectives', calls[1]['operations'][0]['data']['queue'])
        self.assertEqual(self.hook.agent_version, 1)
        self.assertEqual(self.journal.load(self.hook.sid).get('outbox', []), [])

    async def test_ambiguous_or_unrelated_registration_failures_are_never_retried(self):
        errors = [SyncError(0), SyncError(503), SyncError(403), SyncError(422, {'error': 'private text'}),
                  SyncError(422, {'error': {'code': 'invalid_request', 'message': 'Other refusal'}})]
        for error in errors:
            self.hook.entered = True; self.hook.agent_status = 'unregistered'
            with self.subTest(status=error.status), patch.object(self.q, 'status', return_value=self.q.last_status), patch.object(self.client, 'request', side_effect=error) as call:
                self.hook.register_agent(); self.assertEqual(call.call_count, 1)
                self.assertEqual(self.hook.agent_status, 'unavailable')

    async def test_registration_probe_rebind_cannot_publish_old_topic_with_new_binding(self):
        started, release = threading.Event(), threading.Event()
        old = self.q.last_status
        def status():
            started.set()
            if not release.wait(3): raise RuntimeError('fixture barrier timed out')
            return old
        self.hook.entered = True
        with patch.object(self.q, 'status', side_effect=status):
            task = asyncio.create_task(asyncio.to_thread(self.hook.register_agent))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.hook.rebind('second', {'token': 'other-fixture-token'})
                new_client = Client([]); self.hook.client = new_client
            finally:
                release.set()
            await asyncio.wait_for(task, 3)
        self.assertEqual(self.client.requests, [])
        self.assertEqual(new_client.requests, [])

    async def test_a_probe_finishing_after_rebind_cannot_restore_old_cache(self):
        started, release = threading.Event(), threading.Event()
        def run(*args):
            started.set()
            if not release.wait(3): raise RuntimeError('fixture barrier timed out')
            return json.dumps({'items': [item(title='FIRST_PROJECT_ONLY')]})
        with patch.object(self.q, 'run', side_effect=run):
            task = asyncio.create_task(asyncio.to_thread(self.q.status))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.hook.rebind('second')
            finally:
                release.set()
            result = await asyncio.wait_for(task, 3)
        self.assertIsNone(self.q.last_status)
        self.assertNotIn('FIRST_PROJECT_ONLY', json.dumps(result))

    async def test_initial_ready_probe_cannot_restore_old_queue_name_after_rebind(self):
        started, release = threading.Event(), threading.Event(); calls = []
        self.q.name = None
        def run(verb, args, timeout):
            calls.append(list(args))
            if len(calls) == 1:
                started.set()
                if not release.wait(3): raise RuntimeError('fixture barrier timed out')
            return json.dumps({'items': []})
        with patch.object(self.q, 'run', side_effect=run):
            task = asyncio.create_task(asyncio.to_thread(self.q.status))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.hook.rebind('second')
            finally:
                release.set()
            self.assertEqual((await asyncio.wait_for(task, 3))['queue_status'], 'unavailable')
            self.assertIsNone(self.q.name)
            self.q.status()
        self.assertEqual(calls[-1][1], 'second')
