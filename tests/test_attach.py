"""Existing credentials attach only with verified access and explicit consent."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import attach_teamwork as attach
from amplifier_module_tool_teamwork import _MintError

BASE = "https://svc.example.invalid"
TOKEN = "fixture-portal-agent-credential"
VALID = [
    _MintError(404, {"error": {"code": "session_not_found", "message": "Session not found"}}),
    _MintError(422, {"error": {"code": "invalid_request", "message": "Supply 1–20 operations"}}),
]


class CredentialVerificationTests(unittest.TestCase):
    def test_both_scopes_are_proved_without_minting_or_publishing(self):
        with patch("amplifier_module_tool_teamwork._http_bearer", side_effect=VALID) as request:
            attach.verify(BASE, "one/two", TOKEN)
        calls = request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, (BASE, "/api/v1/projects/one%2Ftwo/context", {}, TOKEN))
        self.assertEqual(calls[1].args, (BASE, "/api/v1/projects/one%2Ftwo/publish", {"operations": []}, TOKEN))

    def test_unexpected_statuses_and_transport_failures_never_write(self):
        failures = [
            _MintError(401, {}), _MintError(403, {}),
            _MintError(404, {"error": {"code": "not_found", "message": TOKEN}}),
            _MintError(404, {"error": {"code": "session_not_found", "message": TOKEN}}),
            _MintError(422, {"error": {"code": "invalid_request", "message": TOKEN}}),
            _MintError(500, {}), _MintError(302, {}), TimeoutError(TOKEN), None,
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as folder:
                with patch("amplifier_module_tool_teamwork._http_bearer", side_effect=[failure]):
                    with self.assertRaises(SystemExit) as raised:
                        attach.attach(Path(folder), BASE, "project", TOKEN)
                self.assertNotIn(TOKEN, str(raised.exception))
                self.assertFalse((Path(folder) / ".amplifier").exists())

    def test_context_only_credential_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("amplifier_module_tool_teamwork._http_bearer", side_effect=[VALID[0], _MintError(403, {})]):
                with self.assertRaises(SystemExit):
                    attach.attach(Path(folder), BASE, "project", TOKEN)
            self.assertFalse((Path(folder) / ".amplifier").exists())


class PrivateConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.directory = self.root / ".amplifier"
        self.connection = self.directory / "teamwork-connection.json"
        self.settings = self.directory / "settings.yaml"
        self.verify = patch.object(attach, "verify").start()
        self.addCleanup(patch.stopall)

    def run_attach(self, token=TOKEN, project="project"):
        return attach.attach(self.root, BASE, project, token)

    def test_private_files_reference_secret_and_explicit_sharing(self):
        self.assertTrue(self.run_attach())
        saved = json.loads(self.connection.read_text())
        settings = json.loads(self.settings.read_text())
        self.assertEqual(saved, {"base_url": BASE, "project_id": "project", "token": TOKEN})
        for module in ("hooks-teamwork", "tool-teamwork"):
            config = settings["overrides"][module]["config"]
            self.assertIs(config["share_visible_turns"], True)
            self.assertEqual(config["connection_file"], str(self.connection))
        self.assertNotIn(TOKEN, self.settings.read_text())
        self.assertNotIn("${", self.settings.read_text())
        self.assertFalse((self.directory / "keys.env").exists())
        for target in (self.connection, self.settings):
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_secret_is_private_at_creation_even_with_permissive_umask(self):
        real_open = os.open
        observed = []
        def open_checked(path, flags, mode=0o777, **kwargs):
            fd = real_open(path, flags, mode, **kwargs)
            if Path(path).name == "teamwork-connection.json":
                observed.append(os.fstat(fd).st_mode & 0o777)
            return fd
        old_umask = os.umask(0)
        try:
            with patch.object(attach.os, "open", side_effect=open_checked):
                self.run_attach()
        finally:
            os.umask(old_umask)
        self.assertEqual(observed, [0o600])

    def test_identical_reattach_is_verified_without_changing_files(self):
        self.run_attach()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir()}
        self.assertFalse(self.run_attach())
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.directory.iterdir()})
        self.assertEqual(self.verify.call_count, 2)

    def test_changed_token_or_project_preserves_existing_connection_and_settings(self):
        self.run_attach()
        before = (self.connection.read_bytes(), self.settings.read_bytes())
        for kwargs in ({"token": "different-fixture"}, {"project": "other"}):
            with self.assertRaises(SystemExit):
                self.run_attach(**kwargs)
            self.assertEqual(before, (self.connection.read_bytes(), self.settings.read_bytes()))
        self.assertEqual(self.verify.call_count, 1)

    def test_existing_user_settings_are_refused_before_any_mutation(self):
        self.directory.mkdir()
        self.settings.write_text("user_provider: preserved\n")
        with self.assertRaises(SystemExit):
            self.run_attach()
        self.assertEqual(self.settings.read_text(), "user_provider: preserved\n")
        self.assertFalse(self.connection.exists())
        self.verify.assert_not_called()

    def test_symlink_targets_and_directory_are_refused(self):
        victim = self.root / "victim"
        victim.write_text("preserve")
        self.directory.mkdir()
        for name in ("teamwork-connection.json", "settings.yaml", ".gitignore"):
            target = self.directory / name
            target.symlink_to(victim)
            with self.assertRaises(SystemExit):
                self.run_attach()
            self.assertEqual(victim.read_text(), "preserve")
            target.unlink()
        self.directory.rmdir()
        self.directory.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(SystemExit):
            self.run_attach()
        self.verify.assert_not_called()

    def test_global_amplifier_directory_is_refused(self):
        with patch.dict(os.environ, {"AMPLIFIER_HOME": str(self.directory)}):
            with self.assertRaises(SystemExit):
                self.run_attach()
        self.assertFalse(self.directory.exists())

    def test_global_settings_are_unchanged(self):
        global_home = self.root / "global"
        global_home.mkdir()
        global_settings = global_home / "settings.yaml"
        global_settings.write_text("provider: preserved\n")
        before = global_settings.read_bytes()
        with patch.dict(os.environ, {"AMPLIFIER_HOME": str(global_home)}):
            self.run_attach()
        self.assertEqual(global_settings.read_bytes(), before)

    def test_partial_write_failure_removes_only_new_outputs(self):
        real_write = attach._write_new
        def fail_settings(path, content, owned, **kwargs):
            if path == self.settings:
                raise OSError("fixture disk failure")
            return real_write(path, content, owned, **kwargs)
        with patch.object(attach, "_write_new", side_effect=fail_settings):
            with self.assertRaises(OSError):
                self.run_attach()
        self.assertFalse(self.connection.exists())
        self.assertFalse(self.settings.exists())

    def test_git_ignores_private_file_and_retains_existing_rules(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.directory.mkdir()
        ignore = self.directory / ".gitignore"
        ignore.write_text("existing-rule\n")
        self.run_attach()
        result = subprocess.run(["git", "-C", str(self.root), "check-ignore", ".amplifier/teamwork-connection.json"], capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(ignore.read_text().startswith("existing-rule\n"))

    def test_tracked_private_path_is_refused(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.directory.mkdir()
        self.connection.write_text("fixture placeholder")
        subprocess.run(["git", "-C", str(self.root), "add", ".amplifier/teamwork-connection.json"], check=True, capture_output=True)
        self.connection.unlink()
        with self.assertRaises(SystemExit):
            self.run_attach()
        self.assertFalse(self.connection.exists())
        self.verify.assert_not_called()

    def test_directory_symlink_appearing_during_verification_cannot_touch_global_home(self):
        global_home = self.root / "global"
        global_home.mkdir()
        self.verify.side_effect = lambda *args: self.directory.symlink_to(global_home, target_is_directory=True)
        with patch.dict(os.environ, {"AMPLIFIER_HOME": str(global_home)}):
            with self.assertRaises(SystemExit):
                self.run_attach()
        self.assertEqual(list(global_home.iterdir()), [])

    def test_directory_swapped_after_open_does_not_redirect_private_writes(self):
        global_home = self.root / "global"
        global_home.mkdir()
        original = self.root / "original"
        real_write = attach._write_new
        def swap_then_write(path, content, owned, **kwargs):
            if not original.exists():
                self.directory.rename(original)
                self.directory.symlink_to(global_home, target_is_directory=True)
            return real_write(path, content, owned, **kwargs)
        with patch.object(attach, "_write_new", side_effect=swap_then_write):
            with self.assertRaises(SystemExit):
                self.run_attach()
        self.assertEqual(list(global_home.iterdir()), [])
        self.assertFalse((original / self.connection.name).exists())
        self.assertFalse((original / self.settings.name).exists())

    def test_idempotent_attach_rejects_missing_private_ignore_rules(self):
        self.run_attach()
        ignore = self.directory / ".gitignore"
        ignore.unlink()
        before = (self.connection.read_bytes(), self.settings.read_bytes())
        with self.assertRaises(SystemExit):
            self.run_attach()
        self.assertFalse(ignore.exists())
        self.assertEqual(before, (self.connection.read_bytes(), self.settings.read_bytes()))


class ConsentAndInputTests(unittest.TestCase):
    def test_missing_private_terminal_does_not_fall_back_to_echoed_input(self):
        def unavailable(*args):
            warnings.warn("fixture cannot mask", attach.getpass.GetPassWarning)
        with patch.object(sys, "argv", ["attach_teamwork.py", "--project", "project", "--share-visible-turns"]), \
             patch.object(sys, "stdin") as stdin, patch.object(attach.getpass, "getpass", side_effect=unavailable), \
             patch.object(attach, "attach") as call:
            stdin.isatty.return_value = True
            with self.assertRaises(SystemExit):
                attach.main()
        call.assert_not_called()
        stdin.readline.assert_not_called()

    def test_missing_consent_does_not_read_secret_or_make_request(self):
        with patch.object(sys, "argv", ["attach_teamwork.py"]), patch.object(attach, "attach") as call, \
             patch.object(attach, "published_project") as discover, patch.object(sys, "stdin") as stdin:
            with self.assertRaises(SystemExit):
                attach.main()
        call.assert_not_called()
        discover.assert_not_called()
        stdin.readline.assert_not_called()

    def test_stdin_secret_is_not_echoed_and_discovered_project_is_used(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["attach_teamwork.py", "--share-visible-turns"]), \
             patch.object(sys, "stdin", io.StringIO(TOKEN + "\n")), \
             patch.object(attach, "published_project", return_value="discovered"), \
             patch.object(attach, "attach", return_value=True) as call, contextlib.redirect_stdout(output):
            attach.main()
        self.assertEqual(call.call_args.args[2:], ("discovered", TOKEN))
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertIn("Attached project: discovered", output.getvalue())
