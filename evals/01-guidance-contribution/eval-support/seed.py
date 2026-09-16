#!/usr/bin/env python3
"""Draft local-service fixture helpers; the seeding CLI is disabled.

These retained helpers describe five fictional member profiles for a future
safe guidance-contribution runner. They are not invoked by offline comparison.

The proposed seeding design assumed `python3 scripts/local_dev.py --port <PORT>` (from an
amplifier-app-teamwork checkout) has created `.local/access.json` with member
names and private login codes. The proposed steps were:

  1. Reads the login codes for the five fixed members (Alex, Blair, Casey,
     Drew, Ellis) from `access.json`.
  2. Logs in as each member (`POST /api/login`, capturing the `Set-Cookie`
     header explicitly -- `http.cookiejar`/`curl -c` do not reliably capture a
     bare `Set-Cookie` from this server; the header is read directly).
  3. Sets that member's profile via `POST /api/action` with
     `{"type": "profile", "value": {...}}` (verified against
     `backend/core.py`'s `profile` action, NOT `/api/profile`, which is a
     different endpoint covering only phone/title/about/location).

Profiles are fixed and identical across every trial and every task: the two
tasks differ only in WHO is asking (the owner) and WHAT is being asked, never
in who knows what. Task A's subject belongs to Casey; Task B's subject belongs
to Drew, who is also that task's owner.

The CLI exits 2 before reading member codes, contacting a service, changing
profiles, or printing a login code. Live transport and setup remain deferred.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PROFILES = {
    "Alex": {
        "focus": "project coordination, release scheduling, cross-team roadmap tradeoffs",
        "relevant_experience": "Ran the last three release cycles end to end, including go/no-go calls.",
        "contribution_goal": "keep the roadmap realistic and releases predictable",
        "topic_preferences": [
            {"topic": "roadmap", "priority": "high", "state": "following"},
            {"topic": "release-scheduling", "priority": "normal", "state": "following"},
        ],
    },
    "Blair": {
        "focus": "CLI onboarding UX, first-run error messages, terminal ergonomics",
        "relevant_experience": "Rewrote the onboarding wizard and cut first-run failures by half.",
        "contribution_goal": "make the CLI approachable for first-time users",
        "uncertainties": "not deep on backend API versioning or storage internals",
        "topic_preferences": [
            {"topic": "cli-ux", "priority": "high", "state": "following"},
            {"topic": "onboarding", "priority": "high", "state": "following"},
        ],
    },
    "Casey": {
        "focus": "public API design, backward compatibility, request/response versioning, deprecation policy",
        "relevant_experience": (
            "Designed the v2 REST API's pagination, throttling and deprecation windows; "
            "owns the decision on when an old alias can be removed."
        ),
        "contribution_goal": "keep the public surface stable across releases",
        "topic_preferences": [
            {"topic": "api-design", "priority": "high", "state": "following"},
            {
                "topic": "backward-compatibility",
                "priority": "high",
                "state": "following",
            },
        ],
    },
    "Drew": {
        "focus": "hook lifecycle internals, orchestrator event ordering, bundle composition, session lifecycle hooks",
        "relevant_experience": (
            "Designed the hook mount/lifecycle sequencing used across the bundle's "
            "session:start, prompt:submit and prompt:complete points."
        ),
        "contribution_goal": "keep hook ordering assumptions documented and correct",
        "topic_preferences": [
            {"topic": "hook-lifecycle", "priority": "high", "state": "following"},
            {
                "topic": "orchestrator-internals",
                "priority": "normal",
                "state": "following",
            },
        ],
    },
    "Ellis": {
        "focus": "storage schema design, SQLite migrations, durability and idempotency guarantees",
        "relevant_experience": "Designed the durable outbox and idempotency-key retry storage.",
        "contribution_goal": "keep on-disk state recoverable after a crash",
        "topic_preferences": [
            {"topic": "storage", "priority": "high", "state": "following"},
        ],
    },
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "Redirect refused", headers, fp
        )


def _post(base_url: str, origin: str, path: str, body: dict, cookie: str | None = None):
    headers = {"Content-Type": "application/json", "Origin": origin}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(
        base_url + path, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=20) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}, response.headers


def login(base_url: str, origin: str, name: str, token: str) -> str:
    _, headers = _post(base_url, origin, "/api/login", {"name": name, "token": token})
    set_cookie = headers.get("Set-Cookie")
    if not set_cookie:
        raise RuntimeError(f"login for {name!r} returned no Set-Cookie header")
    return set_cookie.split(";")[0]


def set_profile(
    base_url: str, origin: str, cookie: str, name: str, value: dict
) -> dict:
    """POST /api/action returns the FULL workspace state (public_state()), not
    a `person` shortcut key -- the updated record is read back from `people`
    by name."""
    result, _ = _post(
        base_url,
        origin,
        "/api/action",
        {"type": "profile", "value": value},
        cookie=cookie,
    )
    person = next((p for p in result.get("people", []) if p.get("name") == name), None)
    if person is None:
        raise RuntimeError(
            f"profile update for {name!r} returned no matching person: {result}"
        )
    return person


def load_access(access_json_path: str) -> dict[str, str]:
    with open(access_json_path, encoding="utf-8") as f:
        data = json.load(f)
    return {m["name"]: m["token"] for m in data["members"]}


def main() -> int:
    print(
        "Live evaluation is unavailable: fixture seeding is disabled until "
        "secure source transport and bounded setup are implemented. "
        "No member code was read or profile changed; see ../README.md.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
