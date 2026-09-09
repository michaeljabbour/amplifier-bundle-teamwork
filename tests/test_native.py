"""Focused native tool/enrollment tests; no provider or external service calls."""
import asyncio
import io
import json
import re
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_tool_teamwork import (announce, connect, ConsentAborted, ConsentError,
                                            POINTER_NAME, private_browser_connect, TeamworkConnect, mount)
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
    def drive(self, act, timeout=5, home='/unused'):
        """Run the private form and let `act(url, origin)` play the browser's part."""
        errors = []; workers = []
        def browser(url):
            def run():
                try: act(url, 'http://' + urllib.parse.urlsplit(url).netloc)
                except BaseException as error: errors.append(error)
            worker = threading.Thread(target=run); workers.append(worker); worker.start()
            return True
        with patch('webbrowser.open', side_effect=browser):
            try:
                result = private_browser_connect(BASE, Path(home), threading.Event(), timeout=timeout, notify=None)
            except Exception as error:
                result = error
        for worker in workers: worker.join(10)
        if errors: raise errors[0]
        return result

    def token(self, page):
        found = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(found, 'form must carry a csrf field')
        return found.group(1)

    def post(self, url, fields, headers):
        request = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), headers)
        try:
            with urllib.request.urlopen(request) as response: return response.status, response.read().decode()
        except urllib.error.HTTPError as error: return error.code, error.read().decode()

    def test_private_form_origin_guard_and_no_credential_echo(self):
        seen = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response:
                page = response.read().decode()
                self.assertIn('type="password"', page)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn("style-src 'nonce-", response.headers['Content-Security-Policy'])
            csrf = self.token(page)
            good = dict(FORM, csrf=csrf)
            # A cross-site submission is refused even holding a stolen token.
            status, _ = self.post(url, good, {'Origin': 'https://evil.invalid'})
            self.assertEqual(status, 403)
            status, _ = self.post(url, good, {'Origin': origin, 'Sec-Fetch-Site': 'cross-site'})
            self.assertEqual(status, 403)
            # Right origin, wrong/absent token is refused too.
            status, _ = self.post(url, dict(FORM, csrf='wrong'), {'Origin': origin})
            self.assertEqual(status, 403)
            status, _ = self.post(url, FORM, {'Origin': origin})
            self.assertEqual(status, 403)
            status, body = self.post(url, good, {'Origin': origin})
            self.assertEqual(status, 200)
            self.assertNotIn(FORM['code'], body)
        def enroll(form, base, home):
            seen.append(form); return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            result = self.drive(act)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('csrf', seen[0])
        self.assertEqual(result[1], 'selected')

    def test_browser_omitting_origin_under_no_referrer_still_enrolls(self):
        """Chrome sends `Origin: null` for a same-origin POST under Referrer-Policy: no-referrer."""
        seen = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            status, _ = self.post(url, dict(FORM, csrf=self.token(page)),
                                  {'Origin': 'null', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate'})
            self.assertEqual(status, 200)
        def enroll(form, base, home):
            seen.append(form); return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            self.drive(act)
        self.assertEqual(len(seen), 1)

    def test_failed_submission_re_renders_a_retryable_form(self):
        attempts = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            csrf = self.token(page)
            status, body = self.post(url, dict(FORM, project='rejected', csrf=csrf), {'Origin': origin})
            self.assertEqual(status, 400)
            # The user can fix and resubmit in place; typed values survive, the code never does.
            self.assertIn('Fixture reason', body)
            self.assertIn('value="rejected"', body)
            self.assertNotIn(FORM['code'], body)
            self.assertIn('type="password"', body)
            status, body = self.post(url, dict(FORM, csrf=self.token(body)), {'Origin': origin})
            self.assertEqual(status, 200)
        def enroll(form, base, home):
            attempts.append(form['project'])
            if form['project'] == 'rejected':
                raise ConsentError('Fixture reason', 'project', 'Fixture hint')
            return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            result = self.drive(act)
        self.assertEqual(attempts, ['rejected', 'selected'])
        self.assertEqual(result[1], 'selected')

    def test_service_failure_is_retryable_and_never_echoed(self):
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            status, body = self.post(url, dict(FORM, csrf=self.token(page)), {'Origin': origin})
            self.assertEqual(status, 502)
            for secret in ('service-detail-leak', 'session=cookievalue', FORM['code']):
                self.assertNotIn(secret, body)
            self.assertIn('type="password"', body)
        def enroll(form, base, home):
            raise RuntimeError('service-detail-leak session=cookievalue')
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            result = self.drive(act, timeout=1)
        self.assertIsInstance(result, ConsentAborted)

    def test_cancel_button_aborts_without_enrolling(self):
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            status, body = self.post(url, {'csrf': self.token(page), 'action': 'cancel'}, {'Origin': origin})
            self.assertEqual(status, 200)
            self.assertIn('Cancelled', body)
        with patch('amplifier_module_tool_teamwork.connect') as enroll:
            result = self.drive(act, timeout=30)
        enroll.assert_not_called()
        self.assertIsInstance(result, ConsentAborted)
        self.assertIn('cancelled', str(result).lower())

    def test_activity_extends_the_idle_window(self):
        """The window measures inactivity, so filling the form cannot time it out."""
        def act(url, origin):
            for _ in range(4):
                time.sleep(.35)
                with urllib.request.urlopen(url) as response: page = response.read().decode()
            self.post(url, dict(FORM, csrf=self.token(page)), {'Origin': origin})
        started = time.monotonic()
        with patch('amplifier_module_tool_teamwork.connect', return_value=(Path('/private/fixture.json'), 'selected')):
            result = self.drive(act, timeout=.6)
        self.assertGreater(time.monotonic() - started, .6)
        self.assertEqual(result[1], 'selected')

    def test_expired_window_explains_itself_instead_of_refusing_the_connection(self):
        late = {}
        def act(url, origin):
            # Land inside the grace period that follows expiry, not after it.
            time.sleep(1.4)
            try:
                with urllib.request.urlopen(url) as response: late['status'] = response.status
            except urllib.error.HTTPError as error:
                late['status'] = error.code; late['body'] = error.read().decode()
        with patch('amplifier_module_tool_teamwork.connect') as enroll:
            result = self.drive(act, timeout=1)
        enroll.assert_not_called()
        self.assertEqual(late.get('status'), 410)
        self.assertIn('expired', late.get('body', '').lower())
        self.assertIsInstance(result, ConsentAborted)

    def test_wrong_path_is_told_where_the_real_address_is(self):
        page = {}
        def act(url, origin):
            try: urllib.request.urlopen(url.rsplit('/', 1)[0] + '/')
            except urllib.error.HTTPError as error: page['body'] = error.read().decode(); page['code'] = error.code
        with patch('amplifier_module_tool_teamwork.connect'):
            self.drive(act, timeout=.4)
        self.assertEqual(page['code'], 404)
        self.assertIn(POINTER_NAME, page['body'])

    def test_form_address_is_printed_and_saved_privately_then_removed(self):
        printed = io.StringIO()
        with tempfile.TemporaryDirectory() as home:
            pointer = Path(home) / 'native' / POINTER_NAME
            seen = {}
            def act(url, origin):
                seen['url'] = url
                seen['saved'] = pointer.read_text().strip()
                seen['mode'] = pointer.stat().st_mode & 0o777
            def notify(url, path, opened, minutes):
                announce(url, path, opened, minutes, stream=printed)
            with patch('webbrowser.open', side_effect=lambda url: (act(url, None), True)[1]):
                with self.assertRaises(ConsentAborted):
                    private_browser_connect(BASE, Path(home) / 'native', threading.Event(), timeout=.2, notify=notify)
            self.assertEqual(seen['saved'], seen['url'])
            self.assertEqual(seen['mode'], 0o600)
            self.assertIn(seen['url'], printed.getvalue())
            self.assertFalse(pointer.exists(), 'the one-time address must not outlive the window')

    def test_missing_browser_still_serves_the_printed_address(self):
        with patch('webbrowser.open', return_value=False), patch('amplifier_module_tool_teamwork.connect') as enroll:
            with self.assertRaises(ConsentAborted) as error:
                private_browser_connect(BASE, Path('/unused'), threading.Event(), timeout=.1, notify=None)
            enroll.assert_not_called()
        self.assertIn('browser could not be opened', str(error.exception))

    def test_cancellation_event_stops_the_window(self):
        stop = threading.Event(); stop.set()
        with patch('webbrowser.open', return_value=True), patch('amplifier_module_tool_teamwork.connect') as enroll:
            with self.assertRaises(ConsentAborted):
                private_browser_connect(BASE, Path('/unused'), stop, timeout=30, notify=None)
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
