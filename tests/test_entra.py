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
    """A fake `azure.identity` module carrying only what entra.py touches."""
    module = types.ModuleType("azure.identity")

    class AzureCliCredential:
        def get_token(self, scope):
            return get_token(scope)

    module.AzureCliCredential = AzureCliCredential
    return module


class EntraTokenTests(unittest.TestCase):
    def setUp(self):
        entra._CACHE.clear()
        self.addCleanup(entra._CACHE.clear)

    def test_token_is_cached_per_scope_until_near_expiry(self):
        calls = []

        def get_token(scope):
            calls.append(scope)
            return types.SimpleNamespace(token="fixture-token", expires_on=time.time() + 3600)

        with patch.dict(sys.modules, {"azure.identity": stub_azure_identity(get_token)}):
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

        with patch.dict(sys.modules, {"azure.identity": stub_azure_identity(get_token)}):
            first = entra.access_token("scope-b")
            second = entra.access_token("scope-b")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first, second)

    def test_missing_azure_identity_raises_a_run_az_login_message(self):
        broken = types.ModuleType("azure.identity")  # no AzureCliCredential attribute
        with patch.dict(sys.modules, {"azure.identity": broken}):
            with self.assertRaises(entra.EntraUnavailable) as raised:
                entra.access_token("scope-c")
        message = str(raised.exception)
        self.assertIn("az login", message)
        self.assertNotIn("AzureCliCredential", message)
        self.assertNotIn("Traceback", message)

    def test_available_reflects_importability(self):
        with patch.dict(sys.modules, {"azure.identity": stub_azure_identity(lambda scope: None)}):
            self.assertTrue(entra.available())
        # `sys.modules[name] = None` is the documented way to make Python's
        # import system raise ImportError for that name, simulating a host
        # where azure.identity is genuinely not installed.
        with patch.dict(sys.modules, {"azure.identity": None}):
            self.assertFalse(entra.available())


if __name__ == "__main__":
    unittest.main()
