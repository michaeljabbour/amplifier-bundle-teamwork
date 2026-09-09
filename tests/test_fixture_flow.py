"""Bounded local HTTP proof for enrollment, mounted delivery, and recovery."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "modules/hooks-teamwork"))
import setup_teamwork
from amplifier_module_hooks_teamwork import mount


class FixtureServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.events = []
        self.publish_attempts = 0

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_port}/teamwork"


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def respond(self, status, body, headers=()):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.events.append((
            "request",
            self.path,
            body,
            self.headers.get("Idempotency-Key"),
            self.headers.get("Origin"),
            self.headers.get("Authorization"),
            self.headers.get("Cookie"),
            self.headers.get("X-Teamwork-Project"),
        ))
        if self.path == "/teamwork/api/login":
            self.respond(200, {}, (("Set-Cookie", "fixture-session=1; HttpOnly"),))
        elif self.path == "/teamwork/api/harnesses":
            self.respond(200, {"id": "fixture-harness", "token": "fixture-harness-token"})
        elif self.path == "/teamwork/api/harnesses/revoke":
            self.respond(200, {"revoked": True})
        elif self.path == "/teamwork/api/v1/projects/fixture/publish":
            self.server.publish_attempts += 1
            if self.server.publish_attempts == 1:
                self.respond(503, {"error": "retry"})
            else:
                self.respond(200, {"stored": True})
        elif self.path == "/teamwork/api/v1/projects/fixture/context":
            self.respond(200, {
                "next_cursor": "fixture-cursor",
                "delivery_id": "fixture-delivery",
                "has_more": False,
                "truncated": False,
                "items": [{
                    "key": "project:fixture",
                    "id": "fixture",
                    "record_type": "project",
                    "change": "upsert",
                    "content": {"goal": "Fixture context"},
                    "content_sha256": "fixture-source-hash",
                }],
            })
        elif self.path == "/teamwork/api/v1/projects/fixture/acknowledgements":
            self.respond(200, {"stored": True})
        else:
            self.respond(404, {"error": "unknown fixture endpoint"})


class Hooks:
    def __init__(self):
        self.handlers = {}

    def register(self, event, handler, **kwargs):
        self.handlers[event] = handler


class Context:
    def __init__(self, events):
        self.events = events

    async def add_message(self, message):
        self.events.append(("input_accepted", message))


class Coordinator:
    parent_id = None
    session_id = "fixture-session"

    def __init__(self, events):
        self.hooks = Hooks()
        self.context = Context(events)
        self.capabilities = {}

    def get(self, name):
        return self.context if name == "context" else None

    def register_capability(self, name, value):
        self.capabilities[name] = value


class FixtureFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = FixtureServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def run_setup(self, connection, overlay):
        with patch.object(sys, "argv", [
            "setup_teamwork.py",
            "--base-url", self.server.base_url,
            "--project", "fixture",
            "--bundle", "existing-bundle",
            "--connection-file", str(connection),
            "--output", str(overlay),
        ]), patch("builtins.input", return_value="Fixture member"), patch(
            "setup_teamwork.getpass.getpass", return_value="fixture-member-code"
        ):
            setup_teamwork.main()

    async def test_local_enrollment_mount_and_standalone_replay(self):
        root = Path(self.temp.name)
        connection = root / "private" / "connection.json"
        overlay = root / "private" / "teamwork-overlay.yaml"
        self.run_setup(connection, overlay)
        self.assertTrue(connection.exists())
        self.assertTrue(overlay.exists())
        self.assertEqual(connection.stat().st_mode & 0o777, 0o600)
        login = next(event for event in self.server.events if event[1].endswith("/api/login"))
        self.assertEqual(login[4], self.server.base_url.rsplit("/teamwork", 1)[0])
        self.assertEqual(login[2], {"name": "Fixture member", "token": "fixture-member-code"})
        self.assertEqual(login[7], "fixture")
        self.assertIsNone(login[5])
        self.assertIsNone(login[6])
        enrollment = next(event for event in self.server.events if event[1].endswith("/api/harnesses"))
        self.assertEqual(enrollment[2], {
            "label": "Amplifier harness",
            "scopes": ["context:read", "session:write"],
        })
        self.assertEqual(enrollment[6], "fixture-session=1")
        self.assertEqual(enrollment[7], "fixture")

        events = self.server.events
        coordinator = Coordinator(events)
        self.assertIsNone(await mount(coordinator, {
            "share_visible_turns": True,
            "connection_file": str(connection),
        }))
        await coordinator.hooks.handlers["session:start"]("session:start", {})
        first_publish = [event for event in events if event[1].endswith("/publish")][0]
        self.assertIsNotNone(first_publish[3])
        self.assertEqual(first_publish[5], "Bearer fixture-harness-token")

        replay = ROOT / "replay_pending.py"
        replayed = subprocess.run(
            [sys.executable, "-I", "-S", str(replay), "--connection-file", str(connection)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertIn('"pending_requests": 0', replayed.stdout)
        publishes = [event for event in events if event[1].endswith("/publish")]
        self.assertEqual(publishes[0][3], publishes[1][3])
        self.assertEqual(publishes[0][2], publishes[1][2])

        await coordinator.hooks.handlers["prompt:submit"]("prompt:submit", {"prompt": "Fixture prompt"})
        await coordinator.hooks.handlers["prompt:complete"]("prompt:complete", {"response": "Fixture response"})
        await coordinator.hooks.handlers["session:end"]("session:end", {})
        project_calls = [
            event
            for event in events
            if event[0] == "request" and "/api/v1/projects/fixture/" in event[1]
        ]
        self.assertTrue(project_calls)
        self.assertTrue(all(event[5] == "Bearer fixture-harness-token" for event in project_calls))
        accepted = next(index for index, event in enumerate(events) if event[0] == "input_accepted")
        receipt = next(
            index for index, event in enumerate(events)
            if event[0] == "request" and event[1].endswith("/acknowledgements")
        )
        self.assertLess(accepted, receipt)
        acknowledgement = events[receipt]
        self.assertEqual(acknowledgement[5], "Bearer fixture-harness-token")
        acknowledgement_body = acknowledgement[2]
        self.assertEqual(acknowledgement_body["delivery_method"], "harness_input_accepted")
        self.assertEqual(
            acknowledgement_body["items"],
            [{
                "key": "project:fixture",
                "content_sha256": "fixture-source-hash",
                "representation": "derived",
            }],
        )
        final_operations = [
            operation
            for event in events if event[0] == "request" and event[1].endswith("/publish")
            for operation in event[2]["operations"]
        ]
        turn = next(
            operation
            for operation in final_operations
            if operation["op"] == "turn.upsert"
        )
        self.assertEqual(turn["data"]["hook_injections"][0], acknowledgement_body["injection"])
        self.assertEqual(
            turn["data"]["hook_injections"][0]["content_sha256"],
            acknowledgement_body["injection"]["content_sha256"],
        )
        self.assertTrue(any(
            operation["op"] == "turn.upsert"
            and operation["data"]["agent_responses"][0]["text"] == "Fixture response"
            for operation in final_operations
        ))
        self.assertTrue(any(
            operation["op"] == "session.upsert"
            and operation["data"]["status"] == "completed"
            for operation in final_operations
        ))

    async def test_local_setup_write_failure_revokes_harness(self):
        root = Path(self.temp.name)
        connection = root / "private" / "connection.json"
        overlay = root / "private" / "teamwork-overlay.yaml"
        with patch("setup_teamwork.os.fsync", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                self.run_setup(connection, overlay)
        self.assertFalse(connection.exists())
        self.assertFalse(overlay.exists())
        revoke = next(event for event in self.server.events if event[1] == "/teamwork/api/harnesses/revoke")
        self.assertEqual(revoke[2], {"id": "fixture-harness"})
        self.assertEqual(revoke[6], "fixture-session=1")
        self.assertEqual(revoke[7], "fixture")
