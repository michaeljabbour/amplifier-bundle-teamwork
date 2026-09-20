"""Host validation and aborted startup must not publish phantom shared sessions."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_decision_detection import build
from test_detection_lifecycle import AcceptedClient
import amplifier_module_hooks_teamwork as teamwork


class ValidationCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_released_host_validator_has_no_transport_side_effects(self):
        from amplifier_core.validation.hook import HookValidator

        client = AcceptedClient()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(teamwork, "HTTPClient", return_value=client):
            result = await HookValidator().validate("amplifier_module_hooks_teamwork", config={
                "share_visible_turns": True, "base_url": "https://team.example.invalid",
                "project_id": "synthetic-project", "token": "synthetic-token",
                "journal_path": str(Path(directory) / "journal.db"),
                "file_inbound_reports": False,
            })
        self.assertTrue(result.passed, result)
        self.assertEqual(client.requests, [])

    async def test_cleanup_before_start_does_not_register_or_flush(self):
        hook, client, _ = build(self, decision_detection=False)
        await hook.cleanup()
        await hook.on_end("session:end", {})
        self.assertEqual(client.requests, [])
        self.assertEqual(hook.state["version"], 0)
        self.assertFalse(hook.entered)

    async def test_started_session_is_finalized_exactly_once(self):
        hook, _, _ = build(self, decision_detection=False)
        hook.client = client = AcceptedClient()
        hook.ensure_session()
        await hook.cleanup()
        await hook.on_end("session:end", {})
        operations = [op for endpoint, body, _ in client.requests if endpoint == "publish"
                      for op in body["operations"] if op["op"] == "session.upsert"]
        self.assertEqual([op["data"]["status"] for op in operations], ["active", "completed"])
