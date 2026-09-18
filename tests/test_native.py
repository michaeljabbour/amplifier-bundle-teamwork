"""Focused native tool/enrollment tests; no provider or external service calls."""
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
from amplifier_module_tool_teamwork import (announce, connect, ConsentAborted, ConsentError, POINTER_NAME,
                                            private_browser_connect, TeamworkConnect, TeamworkBind, mount)
from amplifier_module_hooks_teamwork import sha
from amplifier_module_hooks_teamwork import service_url
from amplifier_module_tool_teamwork.page import form_page

BASE = "https://team.example.invalid"
# The public web origin the member plane gates on. Passed explicitly at every
# call site on purpose: deriving it from BASE is the bug teamwork-cio names.
ORIGIN = "https://team.example.invalid"
FORM = {"project": "selected", "name": "Fixture", "code": "private-fixture-code", "consent": "yes"}


class DefaultsTests(unittest.TestCase):
    def test_default_base_url_is_the_azure_web_origin(self):
        self.assertEqual(
            service_url.DEFAULT_BASE_URL,
            "https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io",
        )
        self.assertEqual(
            service_url.validate_service_url(service_url.DEFAULT_BASE_URL),
            service_url.DEFAULT_BASE_URL,
        )


class ConsentCopyTests(unittest.TestCase):
    def test_consent_copy_names_shared_write_publishing(self):
        page = form_page("nonce", "csrf", "style-nonce", BASE, 15)
        self.assertIn("shared:write", page)
        self.assertIn("publish shared knowledge and work requests", page)

    def test_sso_form_shows_the_detected_repository_and_makes_project_optional(self):
        page = form_page("nonce", "csrf", "style-nonce", BASE, 15,
                          sso=True, repository="https://github.com/owner/repo")
        self.assertIn("<dt>Repository</dt><dd>https://github.com/owner/repo</dd>", page)
        self.assertIn("<dt>Sign-in</dt><dd>Your Microsoft account (az login)</dd>", page)
        self.assertIn("member-code fallback only", page)
        project_input = re.search(r'<input id="project"[^>]*>', page)
        self.assertIsNotNone(project_input)
        self.assertNotIn("required", project_input.group())

    def test_non_sso_form_still_requires_a_project_id(self):
        page = form_page("nonce", "csrf", "style-nonce", BASE, 15)
        project_input = re.search(r'<input id="project"[^>]*>', page)
        self.assertIsNotNone(project_input)
        self.assertIn("required", project_input.group())
        self.assertNotIn("<dt>Sign-in</dt>", page)
        self.assertNotIn("<dt>Repository</dt>", page)

    def test_form_never_renders_a_member_code(self):
        for kwargs in ({}, {"sso": True, "repository": "https://github.com/owner/repo"}):
            with self.subTest(kwargs=kwargs):
                page = form_page("nonce", "csrf", "style-nonce", BASE, 15, **kwargs)
                code_input = re.search(r'<input id="code"[^>]*>', page)
                self.assertIsNotNone(code_input)
                self.assertNotIn("value=", code_input.group())

    def test_credential_field_renders_as_a_password_and_never_echoes(self):
        page = form_page("nonce", "csrf", "style-nonce", BASE, 15)
        credential_input = re.search(r'<input id="credential"[^>]*>', page)
        self.assertIsNotNone(credential_input)
        self.assertIn('type="password"', credential_input.group())
        self.assertNotIn("value=", credential_input.group())
        self.assertIn("Harnesses &amp; agents", page)


class Response:
    def __init__(self, body, headers=None):
        self.body = json.dumps(body).encode()
        self.headers = headers or {}
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *args): pass


def validation_probe(request):
    if request.full_url.endswith('/context'):
        status, code, message = 404, 'session_not_found', 'Session not found'
    elif request.full_url.endswith('/publish'):
        status, code, message = 422, 'invalid_request', 'Supply 1–20 operations'
    else:
        return
    payload = json.dumps({'error': {'code': code, 'message': message}}).encode()
    raise urllib.error.HTTPError(request.full_url, status, 'Fixture validation', {}, io.BytesIO(payload))


class EnrollmentTests(unittest.TestCase):
    def test_project_scoped_private_enrollment_and_reuse(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                validation_probe(request)
                if request.full_url.endswith('/api/login'):
                    return Response({}, {'Set-Cookie': 'fixture=cookie; HttpOnly'})
                return Response({'token': 'harness-fixture', 'id': 'harness-id'})
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            path, project = connect(FORM, BASE, Path(home), origin=ORIGIN)
            self.assertEqual(project, 'selected')
            self.assertEqual(json.loads(path.read_text())['token'], 'harness-fixture')
            self.assertNotIn(FORM['code'], path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(requests[1].data)['scopes'], ['context:read', 'session:write', 'shared:write'])
            self.assertTrue(all(r.get_header('X-teamwork-project') == 'selected' for r in requests))
            self.assertEqual(connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home), origin=ORIGIN), (path, project))
            self.assertEqual(len(requests), 4)
            for bad in ({'project': 'selected'}, {'consent': 'yes'}):
                with self.assertRaises(ValueError): connect(bad, BASE, Path(home), origin=ORIGIN)

    def test_failed_enrollment_removes_reserved_file(self):
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', side_effect=OSError('fixture')):
            with self.assertRaises(ConsentError): connect(FORM, BASE, Path(home), origin=ORIGIN)
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
            with self.assertRaises(OSError): connect(FORM, BASE, Path(home), origin=ORIGIN)
            self.assertTrue(requests[-1].endswith('/api/harnesses/revoke'))
            self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_insecure_saved_connection_rejected(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / sha(BASE + '\0selected')
            folder.mkdir(mode=0o700)
            path = folder / 'connection.json'
            path.write_text('{}'); path.chmod(0o644)
            with self.assertRaises(ValueError): connect(FORM, BASE, Path(home), origin=ORIGIN)


class SSOEnrollmentTests(unittest.TestCase):
    """Entra-authenticated enrollment; azure.identity itself is never really invoked."""

    def setUp(self):
        available_patcher = patch('amplifier_module_tool_teamwork.entra.available', return_value=True)
        token_patcher = patch('amplifier_module_tool_teamwork.entra.access_token', return_value='fixture-bearer-token')
        available_patcher.start()
        token_patcher.start()
        self.addCleanup(available_patcher.stop)
        self.addCleanup(token_patcher.stop)

    def test_sso_enrollment_mints_all_three_scopes_and_stores_project_and_repository(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                if request.full_url.endswith('/api/config'):
                    return Response({'tenant_id': 'fixture-tenant', 'api_app_id': 'fixture-app-id'})
                if request.full_url.endswith('/api/harnesses'):
                    return Response({'id': 'harness-id', 'token': 'sso-harness-token', 'projects': ['auto-project']})
                raise AssertionError('unexpected endpoint ' + request.full_url)
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            path, project = connect({'consent': 'yes'}, BASE, Path(home), repository='https://github.com/owner/repo')
            self.assertEqual(project, 'auto-project')
            saved = json.loads(path.read_text())
            self.assertEqual(saved['project_id'], 'auto-project')
            self.assertEqual(saved['repository_url'], 'https://github.com/owner/repo')
            self.assertEqual(saved['token'], 'sso-harness-token')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        mint_request = next(r for r in requests if r.full_url.endswith('/api/harnesses'))
        body = json.loads(mint_request.data)
        self.assertEqual(body['scopes'], ['context:read', 'session:write', 'shared:write'])
        self.assertEqual(body['repository_url'], 'https://github.com/owner/repo')
        self.assertEqual(mint_request.get_header('Authorization'), 'Bearer fixture-bearer-token')

    def test_sso_enrollment_sends_only_the_canonical_repository_url(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                if request.full_url.endswith('/api/config'):
                    return Response({'api_app_id': 'fixture-app-id'})
                return Response({'id': 'harness-id', 'token': 'sso-harness-token', 'projects': ['typed-project']})
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            path, project = connect({'project': 'typed-project', 'consent': 'yes'}, BASE, Path(home),
                                    repository='https://github.com/owner/repo')
        self.assertEqual(project, 'typed-project')
        mint_request = next(r for r in requests if r.full_url.endswith('/api/harnesses'))
        body = json.loads(mint_request.data)
        self.assertNotIn('repository_url', body)
        self.assertEqual(mint_request.get_header('X-teamwork-project'), 'typed-project')

    def test_ambiguous_repository_is_a_retryable_form_error(self):
        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.endswith('/api/config'):
                    return Response({'api_app_id': 'fixture-app-id'})
                error_body = json.dumps({'error': {'code': 'choose_project', 'choices': [{'id': 'proj-a'}, {'id': 'proj-b'}]}}).encode()
                raise urllib.error.HTTPError(request.full_url, 409, 'Conflict', {}, io.BytesIO(error_body))
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'consent': 'yes'}, BASE, Path(home), repository='https://github.com/owner/repo')
            self.assertEqual(raised.exception.field, 'project')
            self.assertIn('proj-a', raised.exception.hint)
            self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_service_without_api_app_id_asks_for_the_member_code(self):
        class Opener:
            def open(self, request, **kwargs):
                return Response({'api_app_id': ''})
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'code')
        self.assertIn('member code', raised.exception.hint.lower())

    def test_unmapped_entra_identity_falls_back_to_the_member_code_fields(self):
        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.endswith('/api/config'):
                    return Response({'api_app_id': 'fixture-app-id'})
                error_body = json.dumps({'error': {'code': 'not_a_member', 'message': 'untrusted service text'}}).encode()
                raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(error_body))
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'name')
        self.assertIn('member code', raised.exception.hint.lower())
        self.assertIn('Ask a workspace maintainer to add it', str(raised.exception))
        self.assertNotIn('untrusted service text', str(raised.exception))

    def test_other_entra_refusals_do_not_claim_missing_membership(self):
        for status, code, expected, field in (
            (401, 'unauthorized', 'could not be verified', 'name'),
            (403, 'forbidden', 'not allowed to enroll', 'project'),
        ):
            with self.subTest(status=status):
                class Opener:
                    def open(self, request, **kwargs):
                        if request.full_url.endswith('/api/config'):
                            return Response({'api_app_id': 'fixture-app-id'})
                        body = json.dumps({'error': {'code': code, 'message': 'untrusted service text'}}).encode()
                        raise urllib.error.HTTPError(request.full_url, status, 'Refused', {}, io.BytesIO(body))
                with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
                    with self.assertRaises(ConsentError) as raised:
                        connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
                    self.assertEqual(list(Path(home).rglob('connection.json')), [])
                self.assertIn(expected, str(raised.exception))
                self.assertEqual(raised.exception.field, field)
                self.assertNotIn('not a member', str(raised.exception))
                self.assertNotIn('untrusted service text', str(raised.exception))

    def test_sso_conflict_revokes_the_new_credential_and_reuses_the_saved_one(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            directory = home_path / sha(BASE + "\0" + "auto-project")
            directory.mkdir(parents=True, mode=0o700)
            saved_path = directory / "connection.json"
            saved_path.write_text(json.dumps({
                "base_url": BASE, "project_id": "auto-project",
                "token": "existing-token", "harness_id": "existing-harness",
            }))
            saved_path.chmod(0o600)

            revoked = []
            class Opener:
                def open(self, request, **kwargs):
                    validation_probe(request)
                    if request.full_url.endswith('/api/config'):
                        return Response({'api_app_id': 'fixture-app-id'})
                    if request.full_url.endswith('/api/harnesses/revoke'):
                        revoked.append(json.loads(request.data))
                        return Response({'revoked': True})
                    if request.full_url.endswith('/api/harnesses'):
                        return Response({'id': 'new-harness-id', 'token': 'new-token', 'projects': ['auto-project']})
                    raise AssertionError('unexpected endpoint ' + request.full_url)
            with patch('urllib.request.build_opener', return_value=Opener()):
                path, project = connect({'consent': 'yes'}, BASE, home_path, repository='https://github.com/owner/repo')
            self.assertEqual(project, 'auto-project')
            self.assertEqual(path, saved_path)
            self.assertEqual(json.loads(path.read_text())['token'], 'existing-token')
            self.assertEqual(revoked, [{'id': 'new-harness-id'}])

    def test_member_code_path_is_unchanged_and_now_requests_shared_write(self):
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
        mint_request = requests[1]
        self.assertEqual(json.loads(mint_request.data)['scopes'], ['context:read', 'session:write', 'shared:write'])


    def test_config_401_is_tolerated_and_sso_is_not_offered(self):
        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.endswith('/api/config'):
                    raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))
                raise AssertionError('unexpected endpoint ' + request.full_url)
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'code')
        self.assertIn('not enabled', str(raised.exception).lower())
        self.assertEqual(list(Path(home).rglob('connection.json')), [])


class PortalCredentialTests(unittest.TestCase):
    """Copy/paste enrollment: a credential minted in the portal, no /api/login, no az."""

    def test_credential_path_verifies_stores_and_never_calls_login_or_az(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                validation_probe(request)
        with tempfile.TemporaryDirectory() as home, \
             patch('urllib.request.build_opener', return_value=Opener()), \
             patch('amplifier_module_tool_teamwork.entra.available', side_effect=AssertionError('entra touched')), \
             patch('amplifier_module_tool_teamwork.entra.access_token', side_effect=AssertionError('entra touched')):
            path, project = connect(
                {'project': 'portal-project', 'credential': 'portal-token', 'consent': 'yes'},
                BASE, Path(home), repository='https://github.com/owner/repo')
            self.assertEqual(project, 'portal-project')
            saved = json.loads(path.read_text())
            self.assertEqual(saved['project_id'], 'portal-project')
            self.assertEqual(saved['token'], 'portal-token')
            self.assertIsNone(saved['harness_id'])
            self.assertEqual(saved['repository_url'], 'https://github.com/owner/repo')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0].full_url.endswith('/api/v1/projects/portal-project/context'))
        self.assertTrue(requests[1].full_url.endswith('/api/v1/projects/portal-project/publish'))
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer portal-token')
        self.assertEqual(json.loads(requests[0].data), {})
        self.assertEqual(json.loads(requests[1].data), {'operations': []})
        self.assertFalse(any(r.full_url.endswith('/api/login') for r in requests))

    def test_rejected_credential_is_a_retryable_form_error_and_writes_nothing(self):
        class Opener:
            def open(self, request, **kwargs):
                raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'portal-project', 'credential': 'bad-token', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'credential')
        self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_credential_takes_precedence_over_sso_and_member_code_fields(self):
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                validation_probe(request)
        with tempfile.TemporaryDirectory() as home, \
             patch('urllib.request.build_opener', return_value=Opener()), \
             patch('amplifier_module_tool_teamwork.entra.available', side_effect=AssertionError('entra touched')):
            path, project = connect(
                {'project': 'portal-project', 'credential': 'portal-token',
                 'name': 'Fixture', 'code': 'private-fixture-code', 'consent': 'yes'},
                BASE, Path(home))
        self.assertEqual(project, 'portal-project')
        self.assertTrue(all('/api/login' not in r.full_url and '/api/harnesses' not in r.full_url
                            for r in requests))

    def test_credential_path_reuses_an_existing_saved_connection(self):
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            directory = home_path / sha(BASE + "\0" + "portal-project")
            directory.mkdir(parents=True, mode=0o700)
            saved_path = directory / "connection.json"
            saved_path.write_text(json.dumps({
                "base_url": BASE, "project_id": "portal-project",
                "token": "existing-token", "harness_id": None,
            }))
            saved_path.chmod(0o600)
            with patch('urllib.request.build_opener', side_effect=AssertionError('network touched')):
                with self.assertRaises(ConsentError) as raised:
                    connect(
                        {'project': 'portal-project', 'credential': 'new-token', 'consent': 'yes'}, BASE, home_path)
            self.assertIn('already saved', str(raised.exception).lower())
            self.assertIn('delete', raised.exception.hint.lower())
            self.assertEqual(json.loads(saved_path.read_text())['token'], 'existing-token')

    def test_valid_credential_returns_both_exact_validation_envelopes(self):
        """Deliberate missing-session and empty-publish failures prove both scopes."""
        requests = []
        class Opener:
            def open(self, request, **kwargs):
                requests.append(request)
                validation_probe(request)
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            path, project = connect(
                {'project': 'portal-project', 'credential': 'portal-token', 'consent': 'yes'}, BASE, Path(home))
            self.assertEqual(project, 'portal-project')
            self.assertEqual(json.loads(path.read_text())['token'], 'portal-token')
        self.assertEqual(len(requests), 2)

    def test_credential_verification_5xx_is_not_treated_as_verified_and_writes_nothing(self):
        class Opener:
            def open(self, request, **kwargs):
                raise urllib.error.HTTPError(request.full_url, 500, 'Internal Server Error', {}, io.BytesIO(b'{}'))
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'portal-project', 'credential': 'portal-token', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'credential')
        self.assertIn('could not verify', str(raised.exception).lower())
        self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_credential_verification_unexpected_4xx_is_not_treated_as_verified_and_writes_nothing(self):
        class Opener:
            def open(self, request, **kwargs):
                raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, io.BytesIO(b'{}'))
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'portal-project', 'credential': 'portal-token', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'credential')
        self.assertIn('could not verify', str(raised.exception).lower())
        self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_credential_verification_timeout_is_not_treated_as_verified_and_writes_nothing(self):
        import socket
        class Opener:
            def open(self, request, **kwargs):
                raise socket.timeout('timed out')
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'portal-project', 'credential': 'portal-token', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'credential')
        self.assertIn('could not verify', str(raised.exception).lower())
        self.assertEqual(list(Path(home).rglob('connection.json')), [])


class ServiceConfigTests(unittest.TestCase):
    """_fetch_service_config narrows: auth-layer rejection tolerated, other failures surfaced."""

    def test_connection_error_reaching_config_is_a_clear_retryable_error(self):
        class Opener:
            def open(self, request, **kwargs):
                raise urllib.error.URLError('Connection refused')
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()), \
             patch('amplifier_module_tool_teamwork.entra.available', return_value=True):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
        self.assertIn('unreachable', str(raised.exception).lower())
        self.assertIn(urllib.parse.urlsplit(BASE).hostname, str(raised.exception))
        self.assertEqual(list(Path(home).rglob('connection.json')), [])

    def test_config_401_still_proceeds_to_the_member_code_fallback(self):
        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.endswith('/api/config'):
                    raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))
                raise AssertionError('unexpected endpoint ' + request.full_url)
        with tempfile.TemporaryDirectory() as home, patch('urllib.request.build_opener', return_value=Opener()), \
             patch('amplifier_module_tool_teamwork.entra.available', return_value=True):
            with self.assertRaises(ConsentError) as raised:
                connect({'project': 'selected', 'consent': 'yes'}, BASE, Path(home))
        self.assertEqual(raised.exception.field, 'code')
        self.assertIn('not enabled', str(raised.exception).lower())


class BrowserTests(unittest.TestCase):
    def drive(self, act, timeout=5, home='/unused', origin=None):
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
                result = private_browser_connect(BASE, Path(home), threading.Event(), timeout=timeout,
                                                 notify=None, origin=origin)
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
        def enroll(form, base, home, repository=None, origin=None, attempt=None):
            seen.append(form); return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            result = self.drive(act)
        self.assertEqual(len(seen), 1)
        self.assertNotIn('csrf', seen[0])
        self.assertEqual(result[1], 'selected')

    def test_the_web_origin_reaches_enrollment_and_is_not_derived_from_the_api_host(self):
        """A deployment may serve its web app on another host, and the member plane
        gates on THAT one. Deriving the Origin from the API host is refused 403
        before a credential is examined -- measured against the live canonical
        deployment, 3 of 3 (teamwork-cio)."""
        seen = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            self.assertEqual(self.post(url, dict(FORM, csrf=self.token(page)),
                                       {'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})[0], 200)
        def enroll(form, base, home, repository=None, origin=None, attempt=None):
            seen.append(origin); return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            self.drive(act, origin='https://web.example.invalid')
        self.assertEqual(seen, ['https://web.example.invalid'])

    def test_an_unsupplied_web_origin_still_derives_from_the_service(self):
        # Every same-origin deployment must behave exactly as it did before.
        seen = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            self.assertEqual(self.post(url, dict(FORM, csrf=self.token(page)),
                                       {'Origin': origin, 'Sec-Fetch-Site': 'same-origin'})[0], 200)
        def enroll(form, base, home, repository=None, origin=None, attempt=None):
            seen.append(origin); return Path('/private/fixture.json'), 'selected'
        with patch('amplifier_module_tool_teamwork.connect', side_effect=enroll):
            self.drive(act)
        self.assertEqual(seen, [BASE])

    def test_browser_omitting_origin_under_no_referrer_still_enrolls(self):
        """Chrome sends `Origin: null` for a same-origin POST under Referrer-Policy: no-referrer."""
        seen = []
        def act(url, origin):
            with urllib.request.urlopen(url) as response: page = response.read().decode()
            status, _ = self.post(url, dict(FORM, csrf=self.token(page)),
                                  {'Origin': 'null', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'navigate'})
            self.assertEqual(status, 200)
        def enroll(form, base, home, repository=None, origin=None, attempt=None):
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
        def enroll(form, base, home, repository=None, origin=None, attempt=None):
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
            self.assertIn('service availability', body)
            self.assertIn('configured URL and network connection', body)
            self.assertNotIn('Check your project membership and member code', body)
            self.assertIn('If an earlier attempt was interrupted', body)
            self.assertIn('harness controls first', body)
        for error in (RuntimeError('service-detail-leak session=cookievalue'),
                      TimeoutError('service-detail-leak session=cookievalue'),
                      urllib.error.HTTPError(BASE, 503, 'service-detail-leak', {},
                                             io.BytesIO(b'session=cookievalue'))):
            with self.subTest(failure=type(error).__name__), \
                 patch('amplifier_module_tool_teamwork.connect', side_effect=error):
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

    def test_printed_form_can_complete_when_browser_cannot_open(self):
        errors, workers = [], []
        def notify(url, pointer, opened, minutes):
            self.assertFalse(opened)
            def submit():
                try:
                    with urllib.request.urlopen(url) as response:
                        csrf = self.token(response.read().decode())
                    origin = 'http://' + urllib.parse.urlsplit(url).netloc
                    status, body = self.post(url, dict(FORM, csrf=csrf), {'Origin': origin})
                    self.assertEqual(status, 200)
                    self.assertNotIn(FORM['code'], body)
                except BaseException as error:
                    errors.append(error)
            worker = threading.Thread(target=submit); workers.append(worker); worker.start()
        with tempfile.TemporaryDirectory() as home, patch('webbrowser.open', return_value=False), patch(
                'amplifier_module_tool_teamwork.connect', return_value=(Path('/private/fixture.json'), 'selected')) as enroll:
            result = private_browser_connect(BASE, Path(home), threading.Event(), timeout=5, notify=notify)
            for worker in workers: worker.join(10)
            if errors: raise errors[0]
            self.assertEqual(result[1], 'selected')
            enroll.assert_called_once()
            self.assertFalse((Path(home) / POINTER_NAME).exists())

    def test_remote_host_notice_has_matching_loopback_forward(self):
        printed = io.StringIO()
        announce('http://127.0.0.1:43210/private-fixture', None, False, 15, stream=printed)
        output = printed.getvalue()
        self.assertIn('ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:43210:127.0.0.1:43210 USER@REMOTE_HOST', output)
        self.assertIn('SAME address', output)
        self.assertIn('Never expose this port publicly', output)

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
        self.assertEqual(sorted(root.tools), ['teamwork_bind', 'teamwork_connect'])
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
                self.assertEqual(len(root.handlers), 6)
                hook = root.handlers[0][1].__self__
                self.assertIsNone(hook.state.get('turn'))
                # Completing the connection prompt cannot publish it.
                with patch.object(hook, 'flush'):
                    await hook.on_complete('prompt:complete', {'response': 'previous turn'})
                self.assertIsNone(hook.state.get('turn'))
                # Re-triggering is allowed so a running session can change project,
                # and it rebinds the mounted hook rather than registering again.
                bound = hook.sid
                browser.return_value = (path, 'second-project')
                again = await tool.execute({})
                self.assertTrue(again.success)
                self.assertEqual(browser.call_count, 2)
                self.assertEqual(len(root.handlers), 6)
                self.assertNotEqual(hook.sid, bound)
                self.assertEqual(hook.connection['project_id'], 'second-project')

    async def test_bind_selects_a_project_from_configured_settings(self):
        root = Coordinator()
        tool = TeamworkBind(root, {'base_url': BASE, 'token': 'configured-fixture'})
        rejected = await tool.execute({'project_id': '  '})
        self.assertFalse(rejected.success)
        self.assertFalse(root.handlers)
        result = await tool.execute({'project_id': 'chosen'})
        self.assertTrue(result.success)
        self.assertEqual(len(root.handlers), 6)
        hook = root.handlers[0][1].__self__
        self.assertEqual(hook.connection['project_id'], 'chosen')
        first = hook.sid
        moved = await tool.execute({'project_id': 'another'})
        self.assertTrue(moved.success)
        self.assertEqual(len(root.handlers), 6)
        self.assertEqual(hook.connection['project_id'], 'another')
        self.assertNotEqual(hook.sid, first)

    async def test_bind_without_a_configured_credential_refuses(self):
        root = Coordinator()
        result = await TeamworkBind(root, {'base_url': BASE}).execute({'project_id': 'chosen'})
        self.assertFalse(result.success)
        self.assertIn('teamwork_connect', str(result))
        self.assertFalse(root.handlers)

    async def test_arguments_and_browser_failure_do_not_enable_sharing(self):
        root = Coordinator(); tool = TeamworkConnect(root, {})
        with patch('amplifier_module_tool_teamwork.private_browser_connect', side_effect=RuntimeError('private-fixture-code')) as browser:
            result = await tool.execute({'code': 'secret'})
            self.assertFalse(result.success); browser.assert_not_called()
            result = await tool.execute({})
            self.assertFalse(result.success)
            self.assertNotIn('private-fixture-code', str(result))
            self.assertFalse(root.handlers)


class AutoBindTests(unittest.IsolatedAsyncioTestCase):
    """teamwork_bind with an omitted project_id: local-first, network as fallback (A7)."""

    def _native_dir(self, home):
        directory = Path(home) / ".config" / "amplifier-teamwork" / "native"
        directory.mkdir(parents=True)
        return directory

    def _write_connection(self, directory, name, data):
        folder = directory / name
        folder.mkdir()
        path = folder / "connection.json"
        path.write_text(json.dumps(data))
        path.chmod(0o600)
        return path

    async def test_bind_without_project_uses_a_saved_connection_matching_the_git_remote(self):
        root = Coordinator()
        with tempfile.TemporaryDirectory() as home:
            native = self._native_dir(home)
            self._write_connection(native, "match", {
                "base_url": BASE, "project_id": "auto-project", "token": "auto-token",
                "repository_url": "https://github.com/owner/repo",
            })
            with patch.dict(os.environ, {"HOME": home}, clear=False), \
                 patch('amplifier_module_tool_teamwork.git_remote.origin_url',
                       return_value='git@github.com:owner/repo.git'):
                tool = TeamworkBind(root, {})
                result = await tool.execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.output['project'], 'auto-project')
        hook = root.handlers[0][1].__self__
        self.assertEqual(hook.connection['project_id'], 'auto-project')
        self.assertEqual(hook.connection['token'], 'auto-token')

    async def test_bind_reports_candidates_when_two_saved_connections_match(self):
        root = Coordinator()
        with tempfile.TemporaryDirectory() as home:
            native = self._native_dir(home)
            self._write_connection(native, "a", {
                "base_url": BASE, "project_id": "proj-a", "token": "t",
                "repository_url": "https://github.com/owner/repo",
            })
            self._write_connection(native, "b", {
                "base_url": BASE, "project_id": "proj-b", "token": "t",
                "repository_url": "https://github.com/owner/repo",
            })
            with patch.dict(os.environ, {"HOME": home}, clear=False), \
                 patch('amplifier_module_tool_teamwork.git_remote.origin_url',
                       return_value='git@github.com:owner/repo.git'):
                tool = TeamworkBind(root, {})
                result = await tool.execute({})
        self.assertFalse(result.success)
        self.assertIn('proj-a', str(result))
        self.assertIn('proj-b', str(result))
        self.assertFalse(root.handlers)

    async def test_bind_falls_back_to_discover_when_no_saved_connection_matches(self):
        root = Coordinator()
        with tempfile.TemporaryDirectory() as home:
            self._native_dir(home)  # empty: no saved connection matches

            class Opener:
                def open(self, request, **kwargs):
                    return Response({'selected_project_id': 'discovered-project'})

            with patch.dict(os.environ, {"HOME": home}, clear=False), \
                 patch('amplifier_module_tool_teamwork.git_remote.origin_url',
                       return_value='git@github.com:owner/repo.git'), \
                 patch('urllib.request.build_opener', return_value=Opener()):
                tool = TeamworkBind(root, {'base_url': BASE, 'token': 'discover-token'})
                result = await tool.execute({})
        self.assertTrue(result.success)
        self.assertEqual(result.output['project'], 'discovered-project')
        hook = root.handlers[0][1].__self__
        self.assertEqual(hook.connection['project_id'], 'discovered-project')

    async def test_bind_discover_401_is_tolerated_with_a_clear_message(self):
        root = Coordinator()
        with tempfile.TemporaryDirectory() as home:
            self._native_dir(home)  # empty: no saved connection matches

            class Opener:
                def open(self, request, **kwargs):
                    raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{}'))

            with patch.dict(os.environ, {"HOME": home}, clear=False), \
                 patch('amplifier_module_tool_teamwork.git_remote.origin_url',
                       return_value='git@github.com:owner/repo.git'), \
                 patch('urllib.request.build_opener', return_value=Opener()):
                tool = TeamworkBind(root, {'base_url': BASE, 'token': 'discover-token'})
                result = await tool.execute({})
        self.assertFalse(result.success)
        self.assertIn('not available', str(result).lower())
        self.assertFalse(root.handlers)

    async def test_bind_without_a_git_remote_asks_for_a_project_id(self):
        root = Coordinator()
        with patch('amplifier_module_tool_teamwork.git_remote.origin_url', return_value=None):
            result = await TeamworkBind(root, {'base_url': BASE, 'token': 'token'}).execute({})
        self.assertFalse(result.success)
        self.assertIn('project_id', str(result))
        self.assertFalse(root.handlers)


if __name__ == '__main__': unittest.main()


class ProjectIdIsDiscoveredNotAssumed(unittest.TestCase):
    """The service publishes its own project id unauthenticated at /api/config.
    Asking a person to retype it is asking them to guess something the other end
    could have said -- it was the first question real onboarding produced.
    """

    def _module(self, source_transform=lambda s: s):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "setup_teamwork.py"
        spec = importlib.util.spec_from_file_location("setup_probe", path)
        module = importlib.util.module_from_spec(spec)
        exec(compile(source_transform(path.read_text()), str(path), "exec"), module.__dict__)
        return module

    def test_a_published_project_id_is_used(self):
        module = self._module()
        published = json.dumps({"project_id": "some-other-project"}).encode()
        with patch.object(module.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = published
            self.assertEqual(module.discover_project("https://svc.example.invalid"), "some-other-project")

    def test_an_unreachable_service_falls_back_and_never_raises(self):
        # A convenience read must not be able to stop an enrollment that would
        # otherwise succeed -- 401/403 (an auth layer in front of the service's
        # own config) included, which is the same "unavailable" the SSO path
        # already tolerates.
        module = self._module()
        with patch.object(module.urllib.request, "build_opener", side_effect=OSError("down")):
            self.assertEqual(module.discover_project("https://svc.example.invalid"), "teamwork")

    def test_a_blank_or_non_string_project_id_is_not_trusted(self):
        module = self._module()
        for bad in (json.dumps({"project_id": "   "}).encode(),
                    json.dumps({"project_id": 7}).encode(),
                    b"not json at all"):
            with patch.object(module.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = bad
                self.assertEqual(module.discover_project("https://svc.example.invalid"), "teamwork")


class TheSuccessLineNamesTheProjectThatGotConsent(unittest.TestCase):
    """Regression for a bug this suite could not have caught: every use site of
    the project was moved to the resolved variable EXCEPT the success line, so
    the discovery path printed `Enrolled project: None` directly above "Visible
    prompts/responses will be shared to this project."

    Nothing asserted the output, so 463 green tests said nothing about the one
    line whose entire job is telling a person which project just received their
    consent. The live enrollment run that "proved" discovery had exercised the
    PREVIOUS commit, not this code.
    """

    def _run_main(self, argv, tmp):
        import importlib.util, io, contextlib
        path = Path(__file__).resolve().parents[1] / "setup_teamwork.py"
        spec = importlib.util.spec_from_file_location("setup_main_probe", path)
        module = importlib.util.module_from_spec(spec)
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        buffer = io.StringIO()
        with patch.object(module, "discover_project", return_value="discovered-project"), \
             patch.object(module, "enroll_and_save",
                          side_effect=lambda *a, **k: (Path(tmp) / "c.json", Path(tmp) / "o.yaml")), \
             patch.object(module, "input", create=True, return_value="Someone"), \
             patch.object(module, "getpass", create=True, return_value="code"), \
             patch.object(sys, "argv", argv), contextlib.redirect_stdout(buffer):
            module.main()
        return buffer.getvalue()

    def test_an_omitted_project_flag_prints_the_discovered_id_not_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run_main(
                ["setup_teamwork.py", "--bundle", "b.yaml",
                 "--connection-file", str(Path(tmp) / "c.json"),
                 "--output", str(Path(tmp) / "o.yaml")], tmp)
        self.assertIn("Enrolled project: discovered-project", out)
        self.assertNotIn("None", out)

    def test_an_explicit_project_flag_still_wins_and_is_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self._run_main(
                ["setup_teamwork.py", "--bundle", "b.yaml", "--project", "typed-project",
                 "--connection-file", str(Path(tmp) / "c.json"),
                 "--output", str(Path(tmp) / "o.yaml")], tmp)
        self.assertIn("Enrolled project: typed-project", out)


class TheOriginComesFromTheDeploymentNotFromGuesswork(unittest.TestCase):
    """A deployment that moved its web origin must not 403 every new joiner.

    MEASURED, 2026-09-18, against the live project. The shipped default base URL
    still names the Azure Container Apps web host. The member plane is gated on
    `TEAMWORK_PUBLIC_ORIGIN` (`backend/http.py:origin_check`), and that value is
    now `https://teamwork.amplifier.ms`. Same credentials, one variable changed:

        POST <aca-host>/api/login   Origin: <aca-host>   -> 403 invalid_origin
        POST teamwork.amplifier.ms/api/login  (same)     -> 200, harness minted

    So enrollment followed the README and was refused BEFORE the credential was
    examined -- and the error a joiner sees, "check login and project membership",
    points at the two things that were fine.

    The deployment already publishes the answer. `/api/config` returns `share_url`,
    and that field IS `core.public_origin()` (`backend/http.py:64`) -- the exact
    value the check compares against. Deriving the Origin from --base-url asks the
    joiner to know something the service will tell us; `--origin` stays as the
    escape hatch for anyone who needs to override it.
    """

    def _module(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "setup_teamwork.py"
        spec = importlib.util.spec_from_file_location("setup_origin_probe", path)
        module = importlib.util.module_from_spec(spec)
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        return module

    def _published(self, module, body):
        opener = patch.object(module.urllib.request, "build_opener")
        started = opener.start()
        self.addCleanup(opener.stop)
        started.return_value.open.return_value.__enter__.return_value.read.return_value = body
        return started

    def test_a_published_share_url_is_used_as_the_origin(self):
        module = self._module()
        self._published(module, json.dumps({"share_url": "https://teamwork.example.ms"}).encode())
        self.assertEqual(module.discover_origin("https://aca-host.example.invalid"),
                         "https://teamwork.example.ms")

    def test_an_absent_or_unusable_share_url_leaves_the_derived_origin_alone(self):
        module = self._module()
        for body in (json.dumps({"project_id": "teamwork"}).encode(),
                     json.dumps({"share_url": "   "}).encode(),
                     json.dumps({"share_url": 7}).encode(),
                     b"not json at all"):
            with patch.object(module.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = body
                self.assertIsNone(module.discover_origin("https://svc.example.invalid"))

    def test_an_unreachable_service_never_raises(self):
        module = self._module()
        with patch.object(module.urllib.request, "build_opener", side_effect=OSError("down")):
            self.assertIsNone(module.discover_origin("https://svc.example.invalid"))

    def test_enrollment_sends_the_published_origin_not_the_base_host(self):
        """The whole point: the header that actually leaves the machine."""
        import contextlib
        module = self._module()
        sent = []

        def enroll(post, *args, **kwargs):
            post("/api/login", {"name": "Someone", "token": "code"})
            return Path(args[6]), Path(args[7])

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "discover_project", return_value="teamwork"), \
                 patch.object(module, "discover_origin", return_value="https://teamwork.example.ms"), \
                 patch.object(module, "send", side_effect=lambda request, **k: (sent.append(request), ({}, {}))[1]), \
                 patch.object(module, "enroll_and_save", side_effect=enroll), \
                 patch.object(module, "input", create=True, return_value="Someone"), \
                 patch.object(module, "getpass", create=True, return_value="code"), \
                 patch.object(sys, "argv",
                              ["setup_teamwork.py", "--bundle", "b.yaml",
                               "--base-url", "https://aca-host.example.invalid",
                               "--connection-file", str(Path(tmp) / "c.json"),
                               "--output", str(Path(tmp) / "o.yaml")]), \
                 contextlib.redirect_stdout(io.StringIO()):
                module.main()

        self.assertEqual([r.headers.get("Origin") for r in sent], ["https://teamwork.example.ms"])

    def test_an_explicit_origin_flag_still_wins(self):
        module = self._module()
        import contextlib
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(module, "discover_project", return_value="teamwork"), \
                 patch.object(module, "discover_origin", return_value="https://teamwork.example.ms"), \
                 patch.object(module, "send", side_effect=lambda request, **k: (sent.append(request), ({}, {}))[1]), \
                 patch.object(module, "enroll_and_save",
                              side_effect=lambda post, *a, **k: (post("/api/login", {}), (Path(a[6]), Path(a[7])))[1]), \
                 patch.object(module, "input", create=True, return_value="Someone"), \
                 patch.object(module, "getpass", create=True, return_value="code"), \
                 patch.object(sys, "argv",
                              ["setup_teamwork.py", "--bundle", "b.yaml",
                               "--base-url", "https://aca-host.example.invalid",
                               "--origin", "https://typed.example.ms",
                               "--connection-file", str(Path(tmp) / "c.json"),
                               "--output", str(Path(tmp) / "o.yaml")]), \
                 contextlib.redirect_stdout(io.StringIO()):
                module.main()
        self.assertEqual([r.headers.get("Origin") for r in sent], ["https://typed.example.ms"])
