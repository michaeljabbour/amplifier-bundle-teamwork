#!/usr/bin/env python3
"""Seed the local Teamwork service with five fictional members and rich,
distinguishing profiles for the guidance-contribution eval.

Run AFTER `python3 scripts/local_dev.py --port <PORT>` (from an
amplifier-app-teamwork checkout) has created `.local/access.json` with member
names and private login codes. This script:

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

Exit codes: 0 on success. Any failure raises with a plain message -- this
script does not swallow errors, since a silently-unseeded profile would make
the eval's routing signal meaningless.
"""

from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--port", type=int, required=True, help="port local_dev.py is listening on"
    )
    ap.add_argument(
        "--access-json",
        default=None,
        help="path to access.json (default: <amplifier-app-teamwork checkout>/.local/access.json "
        "resolved relative to --app-repo)",
    )
    ap.add_argument(
        "--app-repo",
        default="/workspace/amplifier-app-teamwork",
        help="path to the amplifier-app-teamwork checkout (used to default --access-json)",
    )
    ap.add_argument(
        "--owner",
        default=None,
        help="print this member's login code to stdout after seeding (for setup_teamwork.py)",
    )
    args = ap.parse_args()

    access_path = args.access_json or f"{args.app_repo}/.local/access.json"
    base_url = f"http://localhost:{args.port}"
    origin = base_url

    tokens = load_access(access_path)
    missing = set(PROFILES) - set(tokens)
    if missing:
        raise SystemExit(f"access.json is missing expected members: {sorted(missing)}")

    for name, value in PROFILES.items():
        cookie = login(base_url, origin, name, tokens[name])
        person = set_profile(base_url, origin, cookie, name, value)
        if person.get("focus") != value["focus"]:
            raise SystemExit(
                f"profile for {name!r} did not read back as written: {person}"
            )
        print(
            f"seeded profile: {name} -> focus={person.get('focus')!r}", file=sys.stderr
        )

    if args.owner:
        if args.owner not in tokens:
            raise SystemExit(f"--owner {args.owner!r} is not one of the seeded members")
        # Deliberately the only line on stdout: consumed directly by the
        # shell driving setup_teamwork.py (`OWNER_CODE=$(python3 seed.py ...)`).
        print(tokens[args.owner])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
