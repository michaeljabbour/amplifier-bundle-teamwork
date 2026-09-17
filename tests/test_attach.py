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

    def test_the_settings_file_carries_a_path_and_never_the_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp), token="super-secret-value")
            settings = (Path(tmp) / ".amplifier/settings.yaml").read_text()
            credential = json.loads((Path(tmp) / ".amplifier/teamwork-connection.json").read_text())
        self.assertNotIn("super-secret-value", settings)
        self.assertIn("teamwork-connection.json", settings)
        self.assertEqual(credential["token"], "super-secret-value")

    def test_no_env_var_reference_survives_in_the_settings_file(self):
        """The ${VAR} design is a trap here and this pins it shut.

        KeyManager reads `get_amplifier_home() / "keys.env"` and nothing else
        (app-cli key_manager.py:11), so a PROJECT-local .amplifier/keys.env is
        never loaded. A ${TEAMWORK_HARNESS_TOKEN} reference would never expand,
        resolve_connection() would receive the literal string, and the hook
        would mount INERT -- a session showing the tool module's tools and none
        of the hook's own, with no error anywhere. Measured, in a container,
        before this was changed to a connection file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp))
            settings = (Path(tmp) / ".amplifier/settings.yaml").read_text()
        self.assertNotIn("${", settings)
        self.assertFalse((Path(tmp) / ".amplifier/keys.env").exists())

    def test_the_consent_flag_is_written_or_the_hook_mounts_inert(self):
        """Without share_visible_turns the hook registers NOTHING, silently.

        mount() returns at __init__.py:2143 when the flag is absent, and a
        mount exception is absorbed by the host, so the failure is
        indistinguishable from the feature being switched off. Measured in a
        container: a session in a correctly-attached directory listed
        teamwork_connect and teamwork_bind -- the TOOL module's tools, which
        have no such gate -- and none of the hook's own. Nothing in stderr.

        Every other check had passed: credential verified, global settings
        byte-identical, files 0600, project discovered from /api/config. This
        is the assertion that would have caught it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp))
            written = json.loads((Path(tmp) / ".amplifier/settings.yaml").read_text())
        self.assertIs(written["overrides"]["hooks-teamwork"]["config"]["share_visible_turns"], True)

    def test_both_files_are_written_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp))
            for name in ("settings.yaml", "teamwork-connection.json"):
                mode = (Path(tmp) / ".amplifier" / name).stat().st_mode & 0o777
                self.assertEqual(mode, 0o600, name)

    def test_an_existing_settings_file_is_never_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / ".amplifier/settings.yaml"
            settings.parent.mkdir(parents=True)
            settings.write_text("mine: true\n")
            self._attach(Path(tmp))
            self.assertEqual(settings.read_text(), "mine: true\n")

    def test_re_attaching_replaces_the_credential_rather_than_accumulating(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._attach(Path(tmp), token="first")
            self._attach(Path(tmp), token="second")
            credential = json.loads((Path(tmp) / ".amplifier/teamwork-connection.json").read_text())
        self.assertEqual(credential["token"], "second")


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
