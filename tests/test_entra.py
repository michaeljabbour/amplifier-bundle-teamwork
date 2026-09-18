"""Entra token helper; azure.identity is stubbed, never really invoked in tests."""
import sys
import time
import types
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import entra


def stub_azure_identity(get_token):
    """Fake `azure` + `azure.identity` modules carrying only what entra.py touches.

    `import azure.identity` first imports the parent `azure` package. On a
    clean venv with no real `azure` namespace package installed, patching only
    `sys.modules["azure.identity"]` is not enough: Python still tries (and
    fails) to import the real `azure` parent first. Both must be seeded.
    """
    parent = types.ModuleType("azure")
    module = types.ModuleType("azure.identity")

    class AzureCliCredential:
        def get_token(self, scope):
            return get_token(scope)

    module.AzureCliCredential = AzureCliCredential
    parent.identity = module
    return parent, module


class EntraTokenTests(unittest.TestCase):
    def setUp(self):
        entra._CACHE.clear()
        self.addCleanup(entra._CACHE.clear)
        cli = patch.object(entra.shutil, "which", return_value="/usr/bin/az")
        cli.start()
        self.addCleanup(cli.stop)

    def test_token_is_cached_per_scope_until_near_expiry(self):
        calls = []

        def get_token(scope):
            calls.append(scope)
            return types.SimpleNamespace(token="fixture-token", expires_on=time.time() + 3600)

        parent, module = stub_azure_identity(get_token)
        with patch.dict(sys.modules, {"azure": parent, "azure.identity": module}):
            first = entra.access_token("scope-a")
            second = entra.access_token("scope-a")
        self.assertEqual(first, "fixture-token")
        self.assertEqual(second, "fixture-token")
        self.assertEqual(len(calls), 1)

    def test_expired_cache_entry_is_refetched(self):
        calls = []

        def get_token(scope):
            calls.append(scope)
            return types.SimpleNamespace(token="fixture-token-%d" % len(calls), expires_on=time.time() + 100)

        parent, module = stub_azure_identity(get_token)
        with patch.dict(sys.modules, {"azure": parent, "azure.identity": module}):
            first = entra.access_token("scope-b")
            second = entra.access_token("scope-b")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first, second)

    def test_missing_azure_identity_names_the_missing_dependency(self):
        parent = types.ModuleType("azure")
        broken = types.ModuleType("azure.identity")  # no AzureCliCredential attribute
        parent.identity = broken
        with patch.dict(sys.modules, {"azure": parent, "azure.identity": broken}):
            with self.assertRaises(entra.EntraUnavailable) as raised:
                entra.access_token("scope-c")
        message = str(raised.exception)
        self.assertIn("optional Azure identity support", message)
        self.assertNotIn("Run `az login`", message)
        self.assertNotIn("AzureCliCredential", message)
        self.assertNotIn("Traceback", message)

    def test_available_reflects_importability(self):
        parent, module = stub_azure_identity(lambda scope: None)
        with patch.dict(sys.modules, {"azure": parent, "azure.identity": module}):
            self.assertTrue(entra.available())
        # `sys.modules[name] = None` is the documented way to make Python's
        # import system raise ImportError for that name, simulating a host
        # where azure.identity is genuinely not installed.
        with patch.dict(sys.modules, {"azure.identity": None}):
            self.assertFalse(entra.available())


class AvailabilityMeansSignInCanActuallyBeAttempted(unittest.TestCase):
    """Availability probes prerequisites without authenticating or using a network."""

    def setUp(self):
        parent, module = stub_azure_identity(lambda scope: self.fail("Availability must not request a token"))
        sdk = patch.dict(sys.modules, {"azure": parent, "azure.identity": module})
        sdk.start()
        self.addCleanup(sdk.stop)

    def test_a_machine_with_the_library_and_no_az_is_not_available(self):
        with patch.object(entra.shutil, "which", return_value=None):
            self.assertFalse(entra.available())

    def test_a_machine_with_both_is_available(self):
        with patch.object(entra.shutil, "which", return_value="/usr/bin/az"):
            self.assertTrue(entra.available())

    def test_an_unimportable_library_is_still_unavailable(self):
        with patch.dict(sys.modules, {"azure.identity": None}), \
             patch.object(entra.shutil, "which", return_value="/usr/bin/az"):
            # A None entry in sys.modules makes the import raise, which is the
            # closest faithful stand-in for the package being absent.
            self.assertFalse(entra.available())


class TheRemedyMustBeActionableOnTHATMachine(unittest.TestCase):
    """Telling someone to run `az login` on a host with no `az` is advice they
    cannot take, and it leaves them no way to learn what would help -- which is
    how a container user reads "unavailable" as "broken" and stops.
    """

    def _failure(self, az_path):
        entra._CACHE.clear()
        def fail(scope):
            raise RuntimeError("fixture-private-token: service unreachable")
        parent, module = stub_azure_identity(fail)
        with patch.object(entra.shutil, "which", return_value=az_path), \
             patch.dict(sys.modules, {"azure": parent, "azure.identity": module}):
            with self.assertRaises(entra.EntraUnavailable) as caught:
                entra.access_token("api://x/.default")
        return str(caught.exception)

    def test_token_failure_does_not_claim_an_account_is_signed_out(self):
        message = self._failure("/usr/bin/az")
        self.assertIn("az login", message)
        self.assertIn("az account show", message)
        self.assertNotIn("no account is signed in", message)
        self.assertNotIn("fixture-private-token", message)
        self.assertNotIn("service unreachable", message)

    def test_az_absent_is_never_told_to_run_a_command_it_does_not_have(self):
        message = self._failure(None)
        self.assertNotIn("Run `az login`", message)
        self.assertIn("not installed", message)
        # It must name the route that DOES exist from a machine with no browser.
        self.assertIn("supply it to this machine", message)

    def test_missing_cli_does_not_attempt_token_acquisition(self):
        entra._CACHE.clear()
        calls = []
        parent, module = stub_azure_identity(lambda scope: calls.append(scope))
        with patch.object(entra.shutil, "which", return_value=None), \
             patch.dict(sys.modules, {"azure": parent, "azure.identity": module}):
            with self.assertRaises(entra.EntraUnavailable):
                entra.access_token("new-scope")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
