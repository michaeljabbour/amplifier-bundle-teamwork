"""attach_teamwork.py -- the "I already have a credential" path.

Every test here pins a guarantee whose violation is silent in production: a
credential written to user-global settings, a secret inlined into a settings
file, or a client that mints a second harness when it should have reattached.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "attach_teamwork.py"


def load():
    spec = importlib.util.spec_from_file_location("attach_probe", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(MODULE_PATH.parent / "modules/hooks-teamwork"))
    exec(compile(MODULE_PATH.read_text(), str(MODULE_PATH), "exec"), module.__dict__)
    return module


class ItCanNeverMintAHarness(unittest.TestCase):
    def test_the_source_contains_no_call_to_the_mint_endpoint(self):
        # Structural, not behavioural, and deliberately so: the guarantee is
        # "this file cannot mint", which a mock could never establish because a
        # mock only proves the paths the test happened to drive.
        source = MODULE_PATH.read_text()
        calls = [line for line in source.splitlines()
                 if "api/harnesses" in line and not line.lstrip().startswith(("#", "It never", '"'))]
        self.assertEqual(calls, [], "attach must never reference the mint endpoint in code")


class ItWritesOnlyProjectScope(unittest.TestCase):
    """A harness is spawned IN A FOLDER. A credential in user-global settings
    gives every session on the host one identity, which publishes one project's
    work under another project's harness -- silently.
    """

    def _attach(self, project_dir, token="tok-abc"):
        module = load()
        with patch.object(module, "verify", return_value=None), \
             patch.object(module, "published_project", return_value="teamwork"), \
             patch.object(sys, "argv",
                          ["attach_teamwork.py", "--token", token, "--dir", str(project_dir)]):
            module.main()
        return module

    def test_the_project_gets_the_config_and_the_home_directory_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as fake_home:
            home_settings = Path(fake_home) / "settings.yaml"
            home_settings.write_text("untouched: true\n")
            before = home_settings.read_bytes()
            self._attach(Path(tmp))
            self.assertEqual(home_settings.read_bytes(), before)
            written = json.loads((Path(tmp) / ".amplifier/settings.yaml").read_text())
            self.assertEqual(sorted(written["overrides"]), ["hooks-teamwork", "tool-teamwork"])
            self.assertEqual(written["overrides"]["hooks-teamwork"]["config"]["project_id"], "teamwork")

    def test_the_secret_is_referenced_not_inlined(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp), token="super-secret-value")
            settings = (Path(tmp) / ".amplifier/settings.yaml").read_text()
            keys = (Path(tmp) / ".amplifier/keys.env").read_text()
        self.assertNotIn("super-secret-value", settings)
        self.assertIn("${TEAMWORK_HARNESS_TOKEN}", settings)
        self.assertIn("TEAMWORK_HARNESS_TOKEN=super-secret-value", keys)

    def test_both_files_are_written_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp))
            for name in ("settings.yaml", "keys.env"):
                mode = (Path(tmp) / ".amplifier" / name).stat().st_mode & 0o777
                self.assertEqual(mode, 0o600, name)

    def test_an_existing_settings_file_is_never_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / ".amplifier/settings.yaml"
            settings.parent.mkdir(parents=True)
            settings.write_text("mine: true\n")
            self._attach(Path(tmp))
            self.assertEqual(settings.read_text(), "mine: true\n")

    def test_re_attaching_replaces_the_token_line_rather_than_appending_a_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp), token="first")
            self._attach(Path(tmp), token="second")
            keys = (Path(tmp) / ".amplifier/keys.env").read_text()
        self.assertEqual(keys.count("TEAMWORK_HARNESS_TOKEN="), 1)
        self.assertIn("TEAMWORK_HARNESS_TOKEN=second", keys)


class ARejectedCredentialWritesNothing(unittest.TestCase):
    def test_a_401_or_403_stops_before_any_file_is_created(self):
        import urllib.error
        for status in (401, 403):
            module = load()
            with tempfile.TemporaryDirectory() as tmp:
                def boom(*a, **k):
                    raise urllib.error.HTTPError("u", status, "no", {}, None)
                with patch.object(module, "_open", side_effect=boom), \
                     patch.object(module, "published_project", return_value="teamwork"), \
                     patch.object(sys, "argv",
                                  ["attach_teamwork.py", "--token", "t", "--dir", tmp]):
                    with self.assertRaises(SystemExit) as caught:
                        module.main()
                self.assertIn(str(status), str(caught.exception))
                self.assertFalse((Path(tmp) / ".amplifier").exists(),
                                 "a refused credential must leave no files behind")

    def test_a_non_auth_status_is_treated_as_accepted(self):
        # 404 session_not_found is what the live service returns to a VALID
        # bearer sending an empty body -- the exact response that identified a
        # portal credential after /api/login had rejected it three times.
        import urllib.error
        module = load()

        def not_found(*a, **k):
            raise urllib.error.HTTPError("u", 404, "session_not_found", {}, None)
        with patch.object(module, "_open", side_effect=not_found):
            module.verify("https://svc.example.invalid", "teamwork", "tok")
