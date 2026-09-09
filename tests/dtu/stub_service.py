"""Loopback stub of the Teamwork service, for the DTU end-to-end check.

Serves the member plane used once during enrollment:

    POST /api/login                 -- member code for a session cookie
    POST /api/harnesses             -- cookie for a project-scoped credential
    POST /api/harnesses/revoke      -- release a credential

and the harness plane the hook uses thereafter:

    POST /api/v1/projects/{project}/context
    POST /api/v1/projects/{project}/publish
    POST /api/v1/projects/{project}/acknowledgements

Credentials issued by the member plane are the ones the harness plane accepts,
so a run can start from `setup_teamwork.py` rather than a hand-written
connection file. Every request is appended to a JSONL log so ordering, headers
and idempotency keys can be asserted after the session ends.

Boundary: the response shapes here are written from the hook's own reader, not
captured from the hosted service. This stub therefore demonstrates adapter
behaviour and recovery ordering. It is not evidence of the deployed service
contract, and a schema change upstream would not be detected by it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

CANARY = "PURPLE-OTTER-4417"
UNPROJECTED_MARKER = "UNPROJECTED-PERSON-FIELD"
MEMBER_CODE = "fixture-member-code"
SESSION_COOKIE = "teamwork_session=fixture-session"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def record(record_type: str, rid: str, content: dict) -> dict:
    body = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return {
        "key": f"{record_type}:{rid}:1",
        "id": rid,
        "version": 1,
        "record_type": record_type,
        "change": "upsert",
        "content": content,
        "content_sha256": sha(body),
    }


def fixture_items() -> list:
    """One record per projected type, plus a field that must never be projected."""
    return [
        record(
            "project",
            "teamwork",
            {
                "id": "teamwork",
                "name": "Teamwork",
                "goal": f"Shared fixture project. The project canary phrase is {CANARY}.",
                "description": "Fixture project for the bundle end-to-end check.",
            },
        ),
        record("plan_step", "p1", {"id": "p1", "text": "Agree on the shared goal", "done": False}),
        record(
            "work",
            "t1",
            {"id": "t1", "title": "Connect a session to this project", "status": "accepted"},
        ),
        record(
            "person",
            "fixture-person",
            {
                "id": "fixture-person",
                "name": "Fixture Person",
                "focus": "Verifying that context arrives before the turn",
                "interests": "Deterministic tests",
                "contribution_goal": "Prove the delivery boundary",
                "provenance": "Fixture",
                # Not in the hook's person whitelist; must never reach the excerpt.
                "private_note": UNPROJECTED_MARKER,
            },
        ),
    ]


class StubState:
    def __init__(self, token: str, log_path: str):
        self.log_path = log_path
        self.seq = 0
        self.issued = {token} if token else set()
        self.revoked: set = set()
        self.harness_ids: dict = {}
        self.lock = threading.Lock()

    def issue(self) -> dict:
        """Mint a project-scoped credential the harness plane will accept."""
        with self.lock:
            harness_id = f"harness-{len(self.harness_ids) + 1}"
            token = f"fixture-credential-{len(self.harness_ids) + 1}"
            self.issued.add(token)
            self.harness_ids[harness_id] = token
            return {"id": harness_id, "token": token}

    def revoke(self, harness_id: str) -> bool:
        with self.lock:
            token = self.harness_ids.get(harness_id)
            if token is None:
                return False
            self.issued.discard(token)
            self.revoked.add(harness_id)
            return True

    def accepts(self, token: str) -> bool:
        with self.lock:
            return token in self.issued

    def append(self, entry: dict) -> None:
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")


def make_handler(state: StubState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # stdlib signature
            return

        def _send(self, code: int, payload: dict, cookie: str = "") -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if cookie:
                self.send_header("Set-Cookie", cookie + "; HttpOnly; Path=/")
            self.end_headers()
            self.wfile.write(raw)

        def _member_plane(self, endpoint: str, body: dict) -> None:
            """Enrollment: a member code buys a cookie, a cookie buys a credential."""
            signed_in = (self.headers.get("Cookie") or "").startswith(SESSION_COOKIE.split("=")[0])
            if endpoint == "login":
                if body.get("token") != MEMBER_CODE or not str(body.get("name", "")).strip():
                    return self._send(401, {"error": {"code": "unauthorized", "message": "Invalid login"}})
                return self._send(200, {"person": {"name": body["name"]}}, cookie=SESSION_COOKIE)
            if not signed_in:
                return self._send(401, {"error": {"code": "unauthorized", "message": "Sign in required"}})
            if endpoint == "harnesses":
                return self._send(200, state.issue())
            if endpoint == "revoke":
                if state.revoke(str(body.get("id"))):
                    return self._send(200, {"revoked": True})
                return self._send(404, {"error": {"code": "not_found"}})
            return self._send(404, {"error": {"code": "not_found"}})

        def do_POST(self) -> None:  # stdlib signature
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {"unparsed": raw.decode("utf-8", "replace")}

            authorization = self.headers.get("Authorization") or ""
            bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
            endpoint = self.path.rstrip("/").split("/")[-1]
            member_plane = self.path.startswith("/api/login") or self.path.startswith("/api/harnesses")
            entry = {
                "ts": time.time(),
                "path": self.path,
                "endpoint": endpoint,
                "plane": "member" if member_plane else "harness",
                "credential_accepted": bool(bearer) and state.accepts(bearer),
                "idempotency_key_present": self.headers.get("Idempotency-Key") is not None,
                "origin_header_present": self.headers.get("Origin") is not None,
                "cookie_header_present": self.headers.get("Cookie") is not None,
                "project_header": self.headers.get("X-Teamwork-Project"),
                # Field names only: a member code or credential is never logged.
                "body_keys": sorted(body) if isinstance(body, dict) else None,
                "scopes": body.get("scopes") if isinstance(body, dict) else None,
                "body": body if not member_plane else {"redacted": "member plane"},
            }
            state.append(entry)

            if member_plane:
                return self._member_plane(endpoint, body)

            if not entry["credential_accepted"]:
                return self._send(
                    401,
                    {"error": {"code": "unauthorized", "message": "Invalid or expired harness credential"}},
                )
            if endpoint == "context":
                return self._send(
                    200,
                    {
                        "items": fixture_items(),
                        "next_cursor": "cursor-1",
                        "delivery_id": "delivery-1",
                        "has_more": False,
                        "truncated": False,
                    },
                )
            if endpoint in ("publish", "acknowledgements"):
                return self._send(200, {"stored": True})
            return self._send(404, {"error": {"code": "not_found"}})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--token",
        default="",
        help="Extra bearer value to accept. Omit to accept only credentials this stub issued.",
    )
    parser.add_argument("--log", required=True, help="JSONL request log path")
    args = parser.parse_args()

    open(args.log, "a", encoding="utf-8").close()
    state = StubState(args.token, args.log)
    HTTPServer(("127.0.0.1", args.port), make_handler(state)).serve_forever()


if __name__ == "__main__":
    main()
