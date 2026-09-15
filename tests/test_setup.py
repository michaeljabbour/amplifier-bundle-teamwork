import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
import urllib.error
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from setup_teamwork import build_overlay, bundle_reference, NoRedirect, project_paths, send
from amplifier_module_hooks_teamwork.service_url import service_origin, validate_service_url


class ColdServiceTests(unittest.TestCase):
    """The service sleeps when idle. Waking it must not look like a broken account.

    Measured: a cold start answers in about 25 seconds while a warm one answers in
    half a second. Enrollment allowed 30, so a cold service was close to a coin
    flip -- and losing it printed eleven frames of SSL internals.

    The person who hits that is, by definition, enrolling for the FIRST time. So
    they are both the most likely to find the service cold and the least equipped
    to read a stack trace and conclude "wait and run it again". Every wrong
    conclusion is available to them instead: my code is wrong, I am not a member,
    the address is wrong, the service is down. None of them are true.
    """

    def waking(self, failures, error=None):
        """A service that refuses `failures` times and then answers."""
        error = error or TimeoutError("The read operation timed out")
        calls = []
        class Response:
            headers = {}
            def read(self): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def opener(request, timeout=None):
            calls.append(timeout)
            if len(calls) <= failures: raise error
            return Response()
        return opener, calls

    def test_a_service_that_was_merely_asleep_is_waited_for_not_reported_as_broken(self):
        opener, calls = self.waking(failures=1)
        send(object(), opener=opener)
        self.assertEqual(len(calls), 2, "a known-transient cold start deserves one retry")

    def test_the_timeout_allows_for_the_cold_start_we_measured(self):
        opener, calls = self.waking(failures=0)
        send(object(), opener=opener)
        self.assertGreater(calls[0], 25, "30s against a 25s cold start is a coin flip")

    def test_a_service_that_never_answers_says_so_in_a_sentence(self):
        opener, _ = self.waking(failures=99)
        with self.assertRaises(SystemExit) as raised:
            send(object(), opener=opener)
        message = str(raised.exception).lower()
        self.assertNotIn("traceback", message)
        for word in ("start", "again"):
            self.assertIn(word, message, "must tell them it is waking and to retry: " + message)

    def test_an_unreachable_address_is_not_dressed_up_as_a_sleeping_service(self):
        # A wrong URL and a sleeping service both fail to connect. Telling someone
        # to "try again" when the address is wrong sends them round a loop forever.
        opener, _ = self.waking(failures=99, error=urllib.error.URLError("Name or service not known"))
        with self.assertRaises(SystemExit) as raised:
            send(object(), opener=opener)
        self.assertIn("name or service not known", str(raised.exception).lower())


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

    def test_project_paths_are_separate_and_collision_resistant(self):
        first_connection, first_overlay = project_paths("design/review")
        second_connection, second_overlay = project_paths("design:review")
        self.assertNotEqual(first_connection.parent, second_connection.parent)
        self.assertEqual(first_connection.name, "connection.json")
        self.assertEqual(first_overlay.name, "teamwork-overlay.yaml")

    def test_main_uses_project_private_defaults_without_output_flags(self):
        import setup_teamwork

        with tempfile.TemporaryDirectory() as directory:
            captured = {}
            expected_connection, expected_overlay = (
                Path(directory)
                / ".config"
                / "amplifier-teamwork"
                / "design-review-32c5473fa4db"
                / "connection.json",
                Path(directory)
                / ".config"
                / "amplifier-teamwork"
                / "design-review-32c5473fa4db"
                / "teamwork-overlay.yaml",
            )

            def enroll(*args):
                captured["connection"] = args[-2]
                captured["overlay"] = args[-1]
                return args[-2], args[-1]

            with patch.dict(os.environ, {"HOME": directory}, clear=False), patch.object(
                sys, "argv", [
                    "setup_teamwork.py",
                    "--base-url", "https://team.example.invalid",
                    "--project", "design/review",
                    "--bundle", "existing-bundle",
                ],
            ), patch("builtins.input", return_value="Fixture member"), patch(
                "setup_teamwork.getpass.getpass", return_value="fixture-member-code"
            ), patch.object(setup_teamwork, "enroll_and_save", side_effect=enroll):
                setup_teamwork.main()

            self.assertEqual(captured["connection"], expected_connection.resolve())
            self.assertEqual(captured["overlay"], expected_overlay.resolve())


class ServiceURLTests(unittest.TestCase):
    def test_service_url_accepts_https_base_paths_and_loopback_http(self):
        self.assertEqual(
            validate_service_url("https://team.example.invalid/teamwork/"),
            "https://team.example.invalid/teamwork",
        )
        self.assertEqual(
            service_origin("https://team.example.invalid/teamwork"),
            "https://team.example.invalid",
        )
        self.assertEqual(validate_service_url("http://[::1]:8765/api"), "http://[::1]:8765/api")

    def test_service_url_rejects_ambiguous_or_nonloopback_recipients(self):
        for value in (
            "https://team.example.invalid@evil.example",
            "https://user:password@example.invalid",
            "https://team.example.invalid/path?recipient=evil",
            "https://team.example.invalid/#fragment",
            "https://team.example.invalid:bad",
            "http://team.example.invalid",
            " https://team.example.invalid",
            "https://team.example.invalid\n",
            "https://team.example.invalid\x7f",
            "https://team.example.invalid:0",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_service_url(value)

    def test_invalid_service_url_stops_before_credentials_are_requested(self):
        import setup_teamwork

        with patch.object(sys, "argv", [
            "setup_teamwork.py", "--bundle", "existing-bundle",
            "--base-url", "https://team.example.invalid@evil.example",
        ]), patch("builtins.input", side_effect=AssertionError("prompted")), patch(
            "setup_teamwork.getpass.getpass", side_effect=AssertionError("prompted")
        ):
            with self.assertRaises(SystemExit):
                setup_teamwork.main()


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

    def test_invalid_recipient_is_rejected_before_an_empty_journal_is_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = Path(directory) / "connection.json"
            connection.write_text(
                json.dumps({"base_url": "https://team.example.invalid@evil.example", "token": "fixture"}),
                encoding="utf-8",
            )
            replay = Path(__file__).resolve().parents[1] / "replay_pending.py"
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(replay), "--connection-file", str(connection)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(Path(directory).glob("outbox-*.sqlite3")))

    def test_connection_file_is_required_before_any_file_is_read(self):
        replay = Path(__file__).resolve().parents[1] / "replay_pending.py"
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(replay)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("the following arguments are required: --connection-file", result.stderr)
        self.assertNotIn("FileNotFoundError", result.stderr)
