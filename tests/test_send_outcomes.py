"""A message may commit even when its HTTP response is lost."""
import tempfile
import unittest
from pathlib import Path
from test_hook import Coordinator, Context
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, SendTool, SyncError


class MessageClient:
    def __init__(self, response=None, failure=None):
        self.response, self.failure, self.calls = response, failure, []

    def request(self, endpoint, body, key):
        self.calls.append(body)
        if self.failure is not None:
            raise SyncError(self.failure)
        if self.response == "valid":
            return {"results": [{"id": body["operations"][0]["id"], "version": 1}]}
        return self.response


class SendOutcomes(unittest.IsolatedAsyncioTestCase):
    def hook(self, client):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        return TeamworkHook(Coordinator(Context([])),
            {"base_url": "https://fixture.invalid", "project_id": "original", "token": "private-fixture-credential"},
            Journal(Path(temp.name) / "journal.db"), client)

    async def test_lost_response_and_server_errors_keep_attempt_identity_without_resending(self):
        for status in (0, 500, 503):
            with self.subTest(status=status):
                client = MessageClient(failure=status)
                result = await SendTool(self.hook(client)).execute({"to_agent_id": "recipient", "body": "Synthetic request"})
                self.assertFalse(result.success)
                self.assertEqual(result.error.get("outcome"), "unknown")
                self.assertEqual(result.error.get("attempted_message_id"), client.calls[0]["operations"][0]["id"])
                self.assertEqual(result.error.get("project_id"), "original")
                self.assertNotIn("Not sent", result.error["message"])
                self.assertEqual(len(client.calls), 1)

    async def test_malformed_success_is_not_a_receipt(self):
        for response in ({}, None, [], {"results": []}, {"results": [{"id": "different", "version": 1}]}):
            with self.subTest(response=response):
                result = await SendTool(self.hook(MessageClient(response=response))).execute({"to_agent_id": "recipient", "body": "Synthetic request"})
                self.assertFalse(result.success)
                self.assertEqual(result.error.get("outcome"), "unknown")

    async def test_supported_secret_patterns_and_connection_credential_are_redacted(self):
        client = MessageClient(response="valid")
        result = await SendTool(self.hook(client)).execute({"to_agent_id": "recipient", "body": "Synthetic private-fixture-credential sk-SYNTHETIC_0123456789abcdef"})
        self.assertTrue(result.success)
        body = client.calls[0]["operations"][0]["data"]["body"]
        self.assertNotIn("private-fixture-credential", body)
        self.assertNotIn("sk-SYNTHETIC_0123456789abcdef", body)

    async def test_definite_refusal_stays_distinct(self):
        result = await SendTool(self.hook(MessageClient(failure=404))).execute({"to_agent_id": "recipient", "body": "Synthetic request"})
        self.assertFalse(result.success)
        self.assertNotEqual(result.error.get("outcome"), "unknown")
        self.assertIn("Not sent", result.error["message"])
