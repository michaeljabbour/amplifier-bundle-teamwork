"""Project rebinding must not carry registration versions or private wait text."""
import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_hook import Client,Context,Coordinator
from amplifier_module_hooks_teamwork import Journal,TeamworkHook


class RebindCardState(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.connection={'base_url':'https://example.invalid','project_id':'first','token':'first-fixture-token'}
        self.client=Client([])
        self.hook=TeamworkHook(Coordinator(Context([])),self.connection,Journal(Path(self.tmp.name)/'queue.db'),self.client)
        self.hook.entered=True

    def move(self,project,token):
        self.hook.rebind(project,{'token':token});self.hook.client=Client([]);self.hook.entered=True

    async def test_new_project_starts_new_card_and_presence_without_prior_private_text(self):
        self.hook.register_agent();self.hook.waiting_reason='FIRST_ONLY_REASON';self.hook.waiting_on='first-request'
        self.hook.report_presence('waiting','FIRST_ONLY_SUBJECT')
        self.move('second','second-fixture-token')
        self.hook.register_agent();self.hook.report_presence('idle')
        ops=[o for _,body,_ in self.hook.client.requests for o in body['operations']]
        self.assertEqual([o['expected_version'] for o in ops],[0,0])
        self.assertIsNone(self.hook.waiting_reason);self.assertIsNone(self.hook.waiting_on)
        self.assertEqual(ops[-1]['data']['summary'],'')
        self.assertNotIn('FIRST_ONLY',str(ops))

    async def test_refusal_in_previous_project_does_not_disable_new_project(self):
        self.hook.agent_status=self.hook.presence_status='unavailable'
        self.move('second','second-fixture-token')
        self.hook.register_agent();self.hook.report_presence('idle')
        self.assertEqual(self.hook.agent_status,'registered')
        self.assertEqual(self.hook.presence_status,'idle')

    async def test_roundtrip_retains_versions_but_never_previous_subject_or_wait(self):
        self.hook.register_agent();self.hook.register_agent()
        self.hook.report_presence('running','FIRST_ONLY_SUBJECT')
        self.move('second','second-fixture-token');self.hook.register_agent()
        self.move('first','first-fixture-token');self.hook.register_agent();self.hook.report_presence('idle')
        ops=[o for _,body,_ in self.hook.client.requests for o in body['operations']]
        self.assertEqual([o['expected_version'] for o in ops],[2,1])
        self.assertEqual(ops[-1]['data']['summary'],'')

    async def test_same_binding_is_a_noop_preserving_live_turn_and_versions(self):
        self.hook.register_agent();self.hook.state['turn']={'id':'fixture-turn','boundary':'accepted','prompt':'fixture prompt','injections':[],'omitted':False}
        state=self.hook.state;client=self.hook.client
        self.hook.waiting_reason='still waiting';self.hook.waiting_on='same-project-request'
        self.hook.rebind('first',{'token':'first-fixture-token'})
        self.assertIs(self.hook.state,state);self.assertIs(self.hook.client,client)
        self.assertEqual(self.hook.state['turn']['id'],'fixture-turn')
        self.assertEqual(self.hook.agent_version,1)
        self.assertEqual(self.hook.waiting_on,'same-project-request')

    async def test_pending_presence_result_cannot_mutate_new_binding(self):
        entered,release=threading.Event(),threading.Event()
        def request(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture barrier timed out')
            return {'stored':True}
        with patch.object(self.client,'request',side_effect=request):
            task=asyncio.create_task(asyncio.to_thread(self.hook.report_presence,'running','FIRST_ONLY_SUBJECT'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2));self.move('second','second-fixture-token')
            finally:release.set()
            await asyncio.wait_for(task,3)
        self.assertEqual(self.hook.presence_version,0)
        self.assertEqual(self.hook.presence_status,'unreported')
        self.assertEqual(self.hook.presence_summary,'')
        self.assertEqual(self.hook.client.requests,[])

    async def test_late_success_preserves_old_version_for_a_later_roundtrip(self):
        entered,release=threading.Event(),threading.Event()
        def request(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture barrier timed out')
            return {'stored':True}
        with patch.object(self.client,'request',side_effect=request):
            task=asyncio.create_task(asyncio.to_thread(self.hook.register_agent))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2));self.move('second','second-fixture-token')
            finally:release.set()
            await asyncio.wait_for(task,3)
        self.move('first','first-fixture-token');self.hook.register_agent()
        self.assertEqual(self.hook.client.requests[-1][1]['operations'][0]['expected_version'],1)

    async def _late_reply_after_aba(self, kind):
        entered,release=threading.Event(),threading.Event()
        def request(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture barrier timed out')
            return {'stored':True}
        publish=self.hook.register_agent if kind=='agent' else lambda:self.hook.report_presence('running','FIRST_ONLY_SUBJECT')
        with patch.object(self.client,'request',side_effect=request):
            task=asyncio.create_task(asyncio.to_thread(publish))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                self.move('second','second-fixture-token');self.move('first','first-fixture-token')
            finally:release.set()
            await asyncio.wait_for(task,3)
        self.assertEqual(getattr(self.hook,kind+'_version'),1)
        self.assertEqual(getattr(self.hook,kind+'_status'),'unregistered' if kind=='agent' else 'unreported')
        self.assertEqual(self.hook.presence_summary,'')
        if kind=='agent':self.hook.register_agent()
        else:self.hook.report_presence('idle')
        self.assertEqual(self.hook.client.requests[-1][1]['operations'][0]['expected_version'],1)

    async def test_late_agent_reply_after_aba_updates_only_the_matching_version(self):
        await self._late_reply_after_aba('agent')

    async def test_late_presence_reply_after_aba_updates_only_the_matching_version(self):
        await self._late_reply_after_aba('presence')

    async def _same_record_not_republished_while_pending(self, kind):
        entered,release=threading.Event(),threading.Event()
        def request(*args):
            entered.set()
            if not release.wait(3):raise RuntimeError('fixture barrier timed out')
            return {'stored':True}
        publish=self.hook.register_agent if kind=='agent' else lambda:self.hook.report_presence('running','FIRST_ONLY_SUBJECT')
        with patch.object(self.client,'request',side_effect=request):
            task=asyncio.create_task(asyncio.to_thread(publish))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                self.move('second','second-fixture-token');self.move('first','first-fixture-token')
                if kind=='agent':self.hook.register_agent()
                else:self.hook.report_presence('idle')
                self.assertEqual(self.hook.client.requests,[])
            finally:release.set()
            await asyncio.wait_for(task,3)
        if kind=='agent':self.hook.register_agent()
        else:self.hook.report_presence('idle')
        self.assertEqual(self.hook.client.requests[-1][1]['operations'][0]['expected_version'],1)

    async def test_same_agent_record_is_not_republished_while_old_response_pending(self):
        await self._same_record_not_republished_while_pending('agent')

    async def test_same_presence_record_is_not_republished_while_old_response_pending(self):
        await self._same_record_not_republished_while_pending('presence')
