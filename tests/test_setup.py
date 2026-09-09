import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_teamwork import build_overlay, bundle_reference, NoRedirect


class SetupTests(unittest.TestCase):
    def test_existing_bundle_is_file_uri_and_overlay_keeps_secret_out(self):
        with tempfile.TemporaryDirectory(prefix='bundle with spaces ') as directory:
            base = Path(directory) / 'bundle.md'
            base.write_text('fixture')
            connection = Path(directory) / 'connection.json'
            connection.write_text('{"token":"fixture-secret-not-to-be-read"}')
            overlay = build_overlay(str(base), connection)
            self.assertEqual(overlay['includes'][0]['bundle'], base.resolve().as_uri())
            self.assertTrue(Path(overlay['hooks'][0]['source']).is_absolute())
            self.assertTrue(overlay['hooks'][0]['config']['share_visible_turns'])
            self.assertNotIn('fixture-secret-not-to-be-read', json.dumps(overlay))
            self.assertNotIn('providers', overlay)
            self.assertNotIn('session', overlay)

    def test_registry_and_git_references_preserved(self):
        for value in ('existing-bundle', 'git+https://example.invalid/bundle@main'):
            self.assertEqual(bundle_reference(value), value)


class RedirectTests(unittest.TestCase):
    def test_member_cookie_redirect_is_refused(self):
        request = urllib.request.Request('https://team.example.invalid/api/harnesses', headers={'Cookie': 'fixture'})
        with self.assertRaises(urllib.error.HTTPError):
            NoRedirect().redirect_request(request, None, 302, 'Moved', {}, 'https://other.example.invalid/')


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.connection = self.root / 'private' / 'connection.json'
        self.overlay = self.root / 'overlays' / 'overlay.yaml'
        self.calls = []

    def post(self, endpoint, body, cookie=None):
        self.calls.append((endpoint, body))
        if endpoint == '/api/login':
            self.assertTrue(self.connection.exists() and self.overlay.exists())
            return {}, {'Set-Cookie': 'fixture-cookie; HttpOnly'}
        if endpoint == '/api/harnesses':
            return {'id': 'synthetic-harness', 'token': 'synthetic-token'}, {}
        return {}, {}

    def enroll(self):
        from setup_teamwork import enroll_and_save
        return enroll_and_save(self.post, 'https://example.invalid', 'synthetic-project', 'existing-bundle', 'Amplifier harness', 'Synthetic member', 'synthetic-code', self.connection, self.overlay)

    def test_reserves_outputs_then_saves_private_connection_without_login_code(self):
        self.enroll()
        self.assertEqual(self.connection.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic-code', self.connection.read_text())
        self.assertNotIn('synthetic-token', self.overlay.read_text())
        self.assertEqual(self.calls[1][1]['label'], 'Amplifier harness')
        self.assertEqual(self.calls[1][1]['scopes'], ['context:read', 'session:write'])

    def test_existing_overlay_prevents_remote_calls_and_preserves_existing_data(self):
        self.overlay.parent.mkdir(parents=True)
        self.overlay.write_text('existing')
        with self.assertRaises(FileExistsError):
            self.enroll()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.overlay.read_text(), 'existing')
        self.assertFalse(self.connection.exists())

    def test_failed_local_write_revokes_new_harness_and_cleans_owned_outputs(self):
        from unittest.mock import patch
        with patch('setup_teamwork.os.fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.enroll()
        self.assertEqual(self.calls[-1], ('/api/harnesses/revoke', {'id': 'synthetic-harness'}))
        self.assertFalse(self.connection.exists())
        self.assertFalse(self.overlay.exists())


class ReplayTests(unittest.TestCase):
    def test_unresolved_prepared_input_is_reported_even_before_resuming_session(self):
        from replay_pending import unresolved_inputs
        self.assertEqual(unresolved_inputs({'turn': {'prepared_injection': {'id': 'synthetic'}, 'boundary': 'prepared'}}), 1)
        self.assertEqual(unresolved_inputs({'turn': {'prepared_injection': {'id': 'synthetic'}, 'boundary': 'harness_input_accepted'}}), 0)
        self.assertEqual(unresolved_inputs({'turn': None, 'acceptance_unknown': {'synthetic-turn': {}}}), 1)
