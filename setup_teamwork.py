"""Enroll privately and create an opt-in Amplifier overlay; no active config edits."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.error
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork.service_url import DEFAULT_BASE_URL, service_origin, validate_service_url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


# Same pattern class the hooks-teamwork redaction pass uses for anything
# credential-shaped that is not a specific known secret (see TeamworkHook.clean).
# Reused here, not imported, because this script has no connection/token
# object yet at the point a malformed body can appear -- there is nothing more
# specific to redact against.
_CREDENTIAL_PATTERN = re.compile(r"(?i)(?:sk-[a-z0-9_-]{16,}|bearer\s+[a-z0-9._~-]{16,})")


def _preview(raw, limit=80):
    """First `limit` characters of a response body, redacted and collapsed.

    Best-effort only: this exists to help a person see what went wrong, not to
    reproduce the body. A body that cannot be decoded as text becomes no body,
    same as the JSON parse failure it is standing in for.
    """
    try:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    except Exception:
        return ""
    text = _CREDENTIAL_PATTERN.sub("[REDACTED CREDENTIAL]", text)
    return " ".join(text.split())[:limit]


def bundle_reference(value):
    path = Path(value).expanduser()
    return path.resolve().as_uri() if path.exists() else value


def build_overlay(base_bundle, connection_file):
    bundle_path = Path(__file__).resolve().parent
    module_path = bundle_path / "modules/hooks-teamwork"
    return {"bundle": {"name": "teamwork-overlay", "version": "0.1.0"}, "includes": [{"bundle": bundle_reference(base_bundle)}, {"bundle": (bundle_path / "behaviors/teamwork.yaml").as_uri()}], "hooks": [{"module": "hooks-teamwork", "source": str(module_path), "config": {"connection_file": str(connection_file), "share_visible_turns": True}}]}


def project_paths(project):
    """Return collision-resistant private defaults for one Teamwork project."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "project"
    suffix = hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]
    directory = Path("~/.config/amplifier-teamwork").expanduser() / f"{slug}-{suffix}"
    return directory / "connection.json", directory / "teamwork-overlay.yaml"


# A cold start on the hosted service answers in about 25 seconds; a warm one in
# half a second. The old 30-second bound was therefore close to a coin flip, and
# losing it produced a stack trace rather than a sentence.
COLD_START_SECONDS = 60


def send(request, opener=None):
    """One enrollment request, forgiving of a service that was merely asleep.

    The service sleeps when idle. The person who meets it cold is, by definition,
    enrolling for the FIRST time -- so they are both the likeliest to find it
    asleep and the least equipped to read eleven frames of SSL internals and
    conclude "wait and run it again". Every wrong conclusion is available to them
    instead: my login code is wrong, I am not a member of this project, the
    address is wrong, the service is down. None of those are true.

    So a timeout is retried ONCE, because a cold start is a known and transient
    state of this deployment rather than a fault. If it still does not answer, the
    message says what is happening and what to do.

    A connection that cannot be made at all is NOT treated as sleep. A wrong
    address and a sleeping service both fail to connect, and telling somebody to
    "try again" when the address is wrong sends them round a loop forever -- so
    that one reports what the system actually said.

    HTTPError passes through untouched: an answering service that refuses is a
    different conversation, and the caller already says something useful about it.

    A 200 with a body this cannot parse as JSON is a THIRD kind of failure, distinct
    from both of the above -- the service answered and did not refuse, it just did
    not send back what this expects. Reported as its own SystemExit (same class the
    other failures use, so the caller does not need a fourth branch) rather than
    left to surface as a raw json.JSONDecodeError traceback.
    """
    opener = opener or urllib.request.build_opener(NoRedirect()).open
    for attempt in (1, 2):
        try:
            with opener(request, timeout=COLD_START_SECONDS) as response:
                raw = response.read()
                try:
                    return json.loads(raw), response.headers
                except ValueError:
                    status = getattr(response, "status", None) or getattr(response, "code", None) or "unknown"
                    raise SystemExit(
                        "The service answered (HTTP %s) but its response could not be read as JSON: %s"
                        % (status, _preview(raw) or "(no readable body)")) from None
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            raise SystemExit("Could not reach the service: %s. Check the address and your network."
                             % (getattr(error, "reason", None) or error)) from None
        except TimeoutError:
            if attempt == 2:
                raise SystemExit(
                    "The service did not answer in %d seconds. It sleeps when nobody is using it and "
                    "takes a few seconds to start, so this is usually not a problem with your login "
                    "code or your membership -- run this again." % COLD_START_SECONDS) from None


def enroll_and_save(post, base, project, base_bundle, label, name, code, path, output):
    """Reserve both private outputs before issuing a remote harness credential."""
    path, output = path.expanduser().resolve(), output.expanduser().resolve()
    if path == output:
        raise ValueError("Connection and overlay must use different paths")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    owned = []
    credential = cookie = None
    def create(target):
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owned.append(target)
        return os.fdopen(fd, "w", encoding="utf-8")
    try:
        with create(path) as connection_out, create(output) as overlay_out:
            _, headers = post("/api/login", {"name": name, "token": code})
            cookie = headers["Set-Cookie"].split(";")[0]
            credential, _ = post("/api/harnesses", {"label": label, "scopes": ["context:read", "session:write"]}, cookie)
            json.dump({"base_url": base, "project_id": project, "token": credential["token"], "harness_id": credential["id"]}, connection_out)
            connection_out.flush(); os.fsync(connection_out.fileno())
            json.dump(build_overlay(base_bundle, path), overlay_out, indent=2)
            overlay_out.write("\n"); overlay_out.flush(); os.fsync(overlay_out.fileno())
    except BaseException:
        revoked = credential is None
        if credential:
            try:
                post("/api/harnesses/revoke", {"id": credential["id"]}, cookie)
                revoked = True
            except BaseException:
                print("Enrollment could not finish. Revoke harness " + credential["id"] + " in the Teamwork harness controls before retrying. Local recovery files were preserved.")
        if revoked:
            for target in owned:
                target.unlink(missing_ok=True)
        raise
    return path, output


def discover_project(base):
    """Return the project id the service publishes at /api/config.

    The service already knows its own project id and publishes it
    unauthenticated; asking a person to retype it is asking them to guess
    something the other end could have said. This reads it.

    NEVER fatal. A 401/403 means an auth layer sits in front of the service's
    own config -- the same "unavailable" the SSO path already tolerates, not a
    misconfiguration. Any other failure is equally non-fatal here: enrollment
    with the historical default is strictly better than refusing to enroll
    because an optional convenience read did not work. The explicit --project
    flag always wins and skips this entirely.
    """
    try:
        request = urllib.request.Request(base + "/api/config", headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(NoRedirect()).open
        with opener(request, timeout=COLD_START_SECONDS) as response:
            published = json.loads(response.read().decode("utf-8"))
        value = published.get("project_id") if isinstance(published, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return "teamwork"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", help="Project id; omit to use the one the service publishes "
                        "at /api/config (falls back to 'teamwork' when it cannot be read)")
    parser.add_argument("--bundle", required=True, help="Your existing bundle name or path; never guessed")
    parser.add_argument("--label", default="Amplifier harness", help="Optional label; no machine hostname is collected")
    parser.add_argument("--output")
    parser.add_argument("--connection-file")
    # A deployment may serve its web app on a different host from its API, and the
    # member plane gates on the WEB one. Deriving the Origin from --base-url is
    # right only when the two coincide, which is every environment this had been
    # exercised against and is not the one that matters; against a split
    # deployment it is refused 403 before a credential is ever examined. Defaults
    # to the derived value, so a same-origin deployment is unaffected.
    parser.add_argument("--origin", help="Public web origin, when it differs from --base-url")
    args = parser.parse_args()
    try:
        base = validate_service_url(args.base_url)
        # Validated by the same rule as the base URL: an unambiguous HTTP(S)
        # origin, HTTPS unless it is a literal loopback host.
        origin = service_origin(args.origin) if args.origin else service_origin(base)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    project = args.project or discover_project(base)
    default_connection, default_overlay = project_paths(project)
    path = Path(args.connection_file).expanduser().resolve() if args.connection_file else default_connection.resolve()
    if path.exists(): raise SystemExit("Connection file already exists; choose a new --connection-file (existing credential preserved)")
    output = Path(args.output).expanduser().resolve() if args.output else default_overlay.resolve()
    if output.exists(): raise SystemExit("Overlay exists; choose a new --output path")
    if path == output: raise SystemExit("Connection and overlay must use different paths")
    name = input("Name or email: ").strip()
    token = getpass.getpass("Private member login code (not saved): ")
    def post(endpoint, body, cookie=None):
        headers = {"Content-Type": "application/json", "Origin": origin, "X-Teamwork-Project": project}
        if cookie: headers["Cookie"] = cookie
        request = urllib.request.Request(base + endpoint, data=json.dumps(body).encode(), headers=headers)
        try:
            return send(request)
        except urllib.error.HTTPError as error:
            status = error.code; error.close(); raise SystemExit(f"Enrollment HTTP {status}; check login and project membership") from None
    path, output = enroll_and_save(post, base, project, args.bundle, args.label, name, token, path, output)
    print("Created opt-in overlay:", output)
    print("Enrolled project:", project)
    print("Start a NEW session: amplifier run --bundle " + output.as_uri())
    print("Visible prompts/responses will be shared to this project. No active session or default bundle changed.")


if __name__ == "__main__": main()
