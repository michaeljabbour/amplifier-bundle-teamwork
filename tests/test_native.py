"""Focused native tool/enrollment tests; no provider or external service calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_tool_teamwork import connect, private_browser_connect, TeamworkConnect, mount
from amplifier_module_hooks_teamwork import sha

BASE = "https://team.example.invalid"
FORM = {"project": "selected", "name": "Fixture", "code": "private-fixture-code", "consent": "yes"}


class Response:
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode()
        self.headers = headers or {}
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *args): pass


class EnrollmentTests(unittest.TestCase):
    def test_project_scoped_private_enrollment_and_reuse(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                if request.full_url.endswith('/api/login'):
                    return Response({}, {'Set-Cookie': 'fixture=cookie; HttpOnly'})
                return Response({'token': 'harness-fixture', 'id': 'harness-id'})
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            path, project = connect(FORM, BASE, Path(home))
            self.assertEqual(project, 'selected')
            self.assertEqual(json.loads(path.read_text())['token'], 'harness-fixture')
            self.assertNotIn(FORM['code'], path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(requests[1].data)['scopes'], ['context:read', 'session:write'])
            self.assertTrue(all(r.get_header('X-teamwork-project') == 'selected' for r in requests))
            self.assertEqual(connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home)), (path, project))
            self.assertEqual(len(requests), 2)
            for bad in ({'project': 'selected'}, {'consent': 'yes'}):
                with self.assertRaises(ValueError): connect(bad, BASE, Path(home))

    def test_failed_enrollment_removes_reserved_file(self):
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', side_effect=OSError('fixture')):
            with self.assertRaises(OSError): connect(FORM, BASE, Path(home))
            self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_failed_write_revokes_issued_credential(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request.full_url)
                if request.full_url.endswith('/api/login'):
                    return Response({}, {'Set-Cookie': 'fixture=cookie'})
                return Response({'token': 'harness-fixture', 'id': 'harness-id'})
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()), patch('os.fsync', side_effect=OSError('disk fixture')):
            with self.assertRaises(OSError): connect(FORM, BASE, Path(home))
            self.assertTrue(requests[-1].endswith('/api/harnesses/revoke'))
            self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_insecure_saved_connection_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / sha(BASE + '\0selected')
            folder.mkdir(mode=0o700)
            path = folder / 'connection.json'
            path.write_text('{}'); path.chmod(0o644)
            with self.assertRaises(ValueError): connect(FORM, BASE, Path(home))


class BrowserTests(unittest.TestCase):
    def test_private_form_origin_guard_and_no_credential_echo(self):
        seen = []; errors = []; workers = []
        def browser(url):
            def submit():
                try:
                    origin = 'http://' + urllib.parse.urlsplit(url).netloc
                    with urllib.request.urlopen(url) as response:
                        page = response.read().decode()
                        self.assertIn('type="password"', page)
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    data = urllib.parse.urlencode(FORM).encode()
                    bad = urllib.request.Request(url, data, {'Origin': 'https://evil.invalid'})
                    with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(bad)
                    self.assertEqual(error.exception.code, 403)
                    request = urllib.request.Request(url, data, {'Origin': origin})
                    with urllib.request.urlopen(request) as response:
                        self.assertNotIn(FORM['code'], response.read().decode())
                except BaseException as error: errors.append(error)
            worker = threading.Thread(target=submit); workers.append(worker); worker.start()
            return True
        def enroll(form, base, home):
            seen.append(form)
            return Path('/private/fixture.json'), 'selected'
        with patch('webbrowser.open', side_effect=browser), patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            result = private_browser_connect(BASE, Path('/unused'), threading.Event(), timeout=5)
        for worker in workers: worker.join(5)
        if errors: raise errors[0]
        self.assertEqual(len(seen), 1)
        self.assertEqual(result[1], 'selected')

    def test_missing_browser_and_cancellation_do_not_enroll(self):
        with patch('webbrowser.open', return_value=False), patch('amplifier_module_tool_teamwork.connect') as enroll:
            with self.assertRaises(RuntimeError): private_browser_connect(BASE, Path('/unused'), threading.Event(), timeout=.1)
            enroll.assert_not_called()


class Coordinator:
    parent_id = None
    session_id = 'native-test'
    def __init__(self):
        self.capabilities = {}; self.tools = {}; self.handlers = []
        self.hooks = self
    def get_capability(self, key): return self.capabilities.get(key)
    def register_capability(self, key, value): self.capabilities[key] = value
    def register(self, event, callback, **kwargs): self.handlers.append((event, callback))
    async def mount(self, point, value, name): self.tools[name] = value


class NativeToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_mount_is_native_and_inert_and_children_excluded(self):
        root = Coordinator()
        self.assertIsNone(await mount(root))
        self.assertEqual(list(root.tools), ['teamwork_connect'])
        self.assertFalse(root.handlers)
        root.parent_id = 'parent'; root.tools.clear()
        await mount(root)
        self.assertFalse(root.tools)

    async def test_connect_mounts_hook_without_retroactive_turn_and_no_secret_output(self):
        root = Coordinator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connection.json'
            path.write_text(json.dumps({'base_url': BASE, 'project_id': 'selected', 'token': 'harness-fixture'})); path.chmod(0o600)
            tool = TeamworkConnect(root, {})
            with patch('amplifier_module_tool_teamwork.private_browser_connect', return_value=(path, 'selected')) as browser:
                result = await tool.execute({})
                self.assertTrue(result.success)
                self.assertNotIn('harness-fixture', str(result))
                self.assertNotIn(str(path), str(result))
                self.assertEqual(len(root.handlers), 4)
                hook = root.handlers[0][1].__self__
                self.assertIsNone(hook.state.get('turn'))
                # Completing the connection prompt cannot publish it.
                with patch.object(hook, 'flush'):
                    await hook.on_complete('prompt:complete', {'response': 'previous turn'})
                self.assertIsNone(hook.state.get('turn'))
                await tool.execute({})
                self.assertEqual(browser.call_count, 1)
                self.assertEqual(len(root.handlers), 4)

    async def test_arguments_and_browser_failure_do_not_enable_sharing(self):
        root = Coordinator(); tool = TeamworkConnect(root, {})
        with patch('amplifier_module_tool_teamwork.private_browser_connect', side_effect=RuntimeError('private-fixture-code')) as browser:
            result = await tool.execute({'code': 'secret'})
            self.assertFalse(result.success); browser.assert_not_called()
            result = await tool.execute({})
            self.assertFalse(result.success)
            self.assertNotIn('private-fixture-code', str(result))
            self.assertFalse(root.handlers)


if __name__ == '__main__': unittest.main()
