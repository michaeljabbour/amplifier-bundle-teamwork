"""Native enrollment tool; private credentials never enter tool arguments/results."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from amplifier_module_hooks_teamwork import RETIRED_HOSTS, RETIRED_MESSAGE, entra, git_remote, mount as mount_hook, sha
from amplifier_module_hooks_teamwork.service_url import DEFAULT_BASE_URL, service_origin, validate_service_url
from amplifier_module_hooks_teamwork import NoRedirect
from amplifier_module_tool_teamwork.page import form_page, result_page

__amplifier_module_type__ = "tool"

IDLE_TIMEOUT = 900
POINTER_NAME = "pending-form-url.txt"

# One consent checkbox mints all three; the member-code path now requests the
# same set so a later publish call never needs a separate re-enrollment.
SCOPES = ["context:read", "session:write", "shared:write"]

# The Azure Container Apps origin can cold-start after scale-to-zero, taking
# 30-60s to answer its first request. A request-level timeout shorter than
# that turns a normal cold start into a spurious enrollment failure.
HTTP_TIMEOUT = 75

# Distinguishes "the discover endpoint refused the request" (EasyAuth gating,
# or a service that has not rolled out bearer auth on it yet) from "reached
# the service, no matching project" -- the two need different messages.
_DISCOVER_UNAVAILABLE = object()


class ConsentError(ValueError):
    """Self-authored message safe to render in the private form; never service text."""

    def __init__(self, message, field=None, hint=None):
        super().__init__(message)
        self.field = field
        self.hint = hint


class ConsentAborted(RuntimeError):
    """Self-authored message safe to return to the model; never service text."""


class ConsentAttempt:
    """Cancel pending work, or seal a completed connection, under one lock."""

    def __init__(self, stop):
        self.stop = stop
        self.lock = threading.Lock()
        self.requested = False
        self.completed = False

    def check(self):
        with self.lock:
            if self.requested or self.stop.is_set():
                raise ConsentAborted("The connection was cancelled before completion.")

    def complete(self):
        with self.lock:
            if self.completed:
                return
            if self.requested or self.stop.is_set():
                raise ConsentAborted("The connection was cancelled before completion.")
            self.completed = True

    def cancel(self):
        with self.lock:
            if self.completed:
                return False
            self.requested = True
            return True


def _check_attempt(attempt, *, complete=False):
    if attempt is not None:
        attempt.complete() if complete else attempt.check()


class _MintError(Exception):
    """An HTTP error response from a bearer-authenticated mint/revoke call."""

    def __init__(self, status, payload):
        super().__init__(status)
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {}


def _http(base, endpoint, body=None, headers=None, origin=None):
    """A single request; NoRedirect refuses redirects. Raises urllib.error.HTTPError untouched.

    `origin` overrides the derived `service_origin(base)` when a deployment
    serves its public web app on a different host than the API: the member
    (cookie) plane gates requests on the WEB origin, so deriving it from the
    API host is refused before a credential is ever examined on a split
    deployment. Defaults to the derived value, so a same-origin deployment
    is unaffected.
    """
    headers = dict(headers or {})
    headers.setdefault("Origin", origin or service_origin(base))
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(base + endpoint, data=data, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=HTTP_TIMEOUT) as response:
        return json.load(response), response.headers


def _http_bearer(base, endpoint, body, bearer, extra_headers=None):
    """POST with an Entra bearer; HTTP error responses surface their JSON body via _MintError."""
    headers = {"Authorization": "Bearer " + bearer}
    if extra_headers:
        headers.update(extra_headers)
    try:
        return _http(base, endpoint, body, headers)
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read() or b"{}")
        except ValueError:
            payload = {}
        finally:
            error.close()
        raise _MintError(error.code, payload) from None


def _safe_directory(home, base, project):
    directory = home / sha(base + "\0" + project)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or (os.name != "nt" and directory.stat().st_mode & 0o077):
        raise ConsentError("The local connection folder is not private.", None,
                           "Remove group and other permissions on ~/.config/amplifier-teamwork/native, then retry.")
    return directory


def _reuse_saved_connection(path, base, project, *, verify=True, attempt=None):
    if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
        raise ConsentError("The saved connection file is not private.", None,
                           "Restore mode 0600 on the saved connection file, then retry.")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise ConsentError("The saved connection file is unreadable.", None,
                           "Revoke that harness in Teamwork, delete the saved file, then enroll again.") from None
    if not isinstance(saved, dict) or saved.get("base_url") != base or saved.get("project_id") != project or not saved.get("token"):
        raise ConsentError("A different connection is already saved for this project.", "project",
                           "Revoke that harness in Teamwork and delete its saved file before re-enrolling.")
    if verify:
        _verify_connection(base, project, saved["token"], saved=True, attempt=attempt)
        _check_attempt(attempt, complete=True)
    return path, project


def _write_connection(output, base, project, credential, repository):
    data = {"base_url": base, "project_id": project, "token": credential["token"], "harness_id": credential["id"]}
    if repository:
        data["repository_url"] = repository
    json.dump(data, output)
    output.flush()
    os.fsync(output.fileno())


def _write_recovery_breadcrumb(directory, harness_id):
    """Preserve recovery evidence privately, without returning the credential."""
    recovery = directory / ("recovery-" + uuid.uuid4().hex + ".json")
    with os.fdopen(os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
        json.dump({"harness_id": harness_id, "action": "Revoke in Teamwork harness controls"}, output)


def _member_request(base, endpoint, body, headers, origin):
    """Translate known stage/status pairs, never render arbitrary service text."""
    try:
        return _http(base, endpoint, body, headers, origin=origin)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if endpoint == "/api/login" and status == 401:
            raise ConsentError("Your name/email or personal access code was not accepted.", "code",
                               "Use the personal access code supplied for your account, not a portal agent credential.") from None
        if endpoint == "/api/harnesses" and status == 404:
            raise ConsentError("Sign-in succeeded, but this account has no access to that project.", "project",
                               "Check the project ID and ask a project owner to grant access if needed.") from None
        if endpoint == "/api/harnesses" and status == 403:
            raise ConsentError("Sign-in succeeded, but this account cannot enroll an agent in that project.", "project",
                               "Agent enrollment requires contributor or owner access to the selected project.") from None
        if endpoint == "/api/harnesses" and status not in (401, 429):
            raise ConsentError("The enrollment outcome is unknown; a new agent credential may have been created.", None,
                               "Review Harnesses & agents in the portal and revoke any credential from this attempt before retrying.") from None
        if status == 429:
            raise ConsentError("Too many sign-in attempts. Try again shortly.", None,
                               "Wait a minute before submitting again.") from None
        if status in (301, 302, 303, 307, 308, 401, 403):
            raise ConsentError("The service refused this sign-in request.", None,
                               "Check the configured Teamwork web service URL. No credential was forwarded to a redirect.") from None
        raise ConsentError("The Teamwork service is unavailable for enrollment.", None,
                           "Try again later. If a previous attempt was interrupted, review your portal's agent credentials first.") from None
    except (OSError, ValueError):
        if endpoint == "/api/harnesses":
            raise ConsentError("The enrollment outcome is unknown; a new agent credential may have been created.", None,
                               "Review Harnesses & agents in the portal and revoke any credential from this attempt before retrying.") from None
        raise ConsentError("The Teamwork service is unavailable for enrollment.", None,
                           "Check your connection and the configured service URL, then try again.") from None


def _mint_member(form, base, home, project, repository, origin=None, attempt=None):
    """Member-code path: reserve the file before issuing a credential; never overwrite.

    `origin` is threaded to every request here because this path is the one
    that authenticates via a cookie (`/api/login` then `/api/harnesses` with
    that cookie): the member plane gates cookie-authenticated requests on the
    deployment's public web origin, which is refused before a credential is
    ever examined when it is derived from a split-deployment's API host.
    """
    directory = _safe_directory(home, base, project)
    path = directory / "connection.json"
    if path.exists():
        if form.get("name", "").strip() or form.get("code"):
            raise ConsentError("This project already has a saved connection; your new login details were not used.", "name",
                               "Clear the name and personal access code to verify and reuse it, or review the saved agent credential in the portal before reconnecting.")
        return _reuse_saved_connection(path, base, project, attempt=attempt)
    if not form.get("name", "").strip() or not form.get("code"):
        raise ConsentError("First enrollment needs your name and private member code.",
                           "name" if not form.get("name", "").strip() else "code",
                           "This project is not enrolled yet on this machine, so both fields are required.")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    credential = None
    cookie = None
    try:
        with os.fdopen(fd, "w") as output:
            _check_attempt(attempt)
            _, headers = _member_request(base, "/api/login", {"name": form["name"].strip(), "token": form["code"]},
                                         {"X-Teamwork-Project": project}, origin)
            cookie = headers["Set-Cookie"].split(";")[0]
            _check_attempt(attempt)
            credential, _ = _member_request(base, "/api/harnesses", {"label": "Amplifier native", "scopes": SCOPES},
                                            {"X-Teamwork-Project": project, "Cookie": cookie}, origin)
            _check_attempt(attempt)
            _write_connection(output, base, project, credential, repository)
        _check_attempt(attempt, complete=True)
    except BaseException:
        cleanup_failed = False
        if credential:
            try:
                _http(base, "/api/harnesses/revoke", {"id": credential["id"]},
                     {"X-Teamwork-Project": project, "Cookie": cookie}, origin=origin)
            except Exception:
                cleanup_failed = True
                try:
                    _write_recovery_breadcrumb(directory, credential["id"])
                except Exception:
                    pass
        path.unlink(missing_ok=True)
        if cleanup_failed:
            raise ConsentError("The new agent credential could not be revoked after enrollment stopped.", None,
                               "Review Harnesses & agents in the portal and revoke the new credential before retrying.") from None
        raise
    return path, project


def _fetch_service_config(base):
    """GET /api/config. A 401/403 (EasyAuth-at-the-gateway) means SSO is not
    offered here -- treated identically to an explicitly unconfigured
    service. Any OTHER failure (DNS, TLS, connection refused, timeout, 5xx)
    is a service reachability problem, not an SSO-configuration signal, and
    must not be misdiagnosed as "SSO not enabled": it is surfaced as a clear,
    retryable error naming only the host, never the full URL or any path.
    """
    try:
        config, _ = _http(base, "/api/config")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return {}
        raise ConsentError(
            "Teamwork service unreachable at " + (urlsplit(base).hostname or base) + ".", "code",
            "Check the configured Teamwork service URL and your connection, then try again.",
        ) from error
    except Exception as error:
        raise ConsentError(
            "Teamwork service unreachable at " + (urlsplit(base).hostname or base) + ".", "code",
            "Check the configured Teamwork service URL and your connection, then try again.",
        ) from error
    return config or {}


def _mint_sso(form, base, home, project, repository, attempt=None):
    """Entra-authenticated enrollment.

    The directory key `sha(base + "\\0" + project)` is unknowable before the mint
    when no project was typed, so this path inverts the member-code order: mint
    first, then reserve the file. A conflict (an enrollment already saved under
    the minted project) revokes the new credential with the same bearer and
    reuses the saved connection instead of overwriting it.
    """
    _check_attempt(attempt)
    api_app_id = _fetch_service_config(base).get("api_app_id") or ""
    if not api_app_id:
        raise ConsentError("Microsoft sign-in enrollment is not enabled on this service yet.", "code",
                           "Enter your name and private member code below, then submit again.")
    try:
        _check_attempt(attempt)
        bearer = entra.access_token("api://" + api_app_id + "/.default")
    except entra.EntraUnavailable as error:
        raise ConsentError(str(error), "code",
                           "Enter your name and private member code below, then submit again.") from error

    body = {"label": "Amplifier native", "scopes": SCOPES}
    extra_headers = {}
    if project:
        extra_headers["X-Teamwork-Project"] = project
    elif repository:
        body["repository_url"] = repository

    try:
        _check_attempt(attempt)
        credential = _http_bearer(base, "/api/harnesses", body, bearer, extra_headers)[0]
    except _MintError as error:
        if error.status == 409:
            detail = error.payload.get("error")
            choices = detail.get("choices", []) if isinstance(detail, dict) else []
            candidates = []
            if isinstance(choices, list):
                for choice in choices[:20]:
                    pid = choice.get("id") if isinstance(choice, dict) else None
                    if isinstance(pid, str) and 0 < len(pid) <= 200 and not any(ord(c) < 32 for c in pid) and pid not in candidates:
                        candidates.append(pid)
            raise ConsentError(
                "That repository does not identify exactly one Teamwork project.", "project",
                ("Type one of these project ids: " + ", ".join(candidates))
                if candidates else "Type the exact project ID you joined.",
            ) from None
        detail = error.payload.get("error")
        if error.status == 403 and isinstance(detail, dict) and detail.get("code") == "not_a_member":
            raise ConsentError(
                "This Microsoft account is not a member of this workspace. Ask a workspace maintainer to add it.", "name",
                "A maintainer can add your Microsoft email to the workspace sign-in roster. "
                "You can also enroll with your name and private member code below.",
            ) from None
        if error.status == 401:
            raise ConsentError(
                "Microsoft sign-in could not be verified.", "name",
                "Sign in again with az login, or enroll with your name and private member code below.",
            ) from None
        if error.status == 403:
            raise ConsentError(
                "Microsoft sign-in was not allowed to enroll an agent in this project.", "project",
                "Check the project ID and ask a project owner to confirm your enrollment access.",
            ) from None
        raise

    directory = path = None
    created = False
    revoke_needed = True

    def revoke_new():
        nonlocal revoke_needed
        revoke_needed = False
        try:
            _http_bearer(base, "/api/harnesses/revoke", {"id": credential["id"]}, bearer)
        except Exception:
            if directory is not None:
                try:
                    _write_recovery_breadcrumb(directory, credential["id"])
                except Exception:
                    pass
            raise ConsentError("Enrollment could not be saved and its new agent credential could not be revoked.", None,
                               "Review Harnesses & agents in the Teamwork portal and revoke the new credential before retrying.") from None

    try:
        _check_attempt(attempt)
        projects = credential.get("projects")
        minted_project = project or (projects[0] if isinstance(projects, list) and len(projects) == 1 else None)
        if not isinstance(minted_project, str) or not minted_project:
            raise ConsentError("Microsoft sign-in did not return one project.", "project",
                               "Enter the project ID you joined.")
        directory = _safe_directory(home, base, minted_project)
        path = directory / "connection.json"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            revoke_new()
            return _reuse_saved_connection(path, base, minted_project, attempt=attempt)
        created = True
        with os.fdopen(fd, "w") as output:
            _write_connection(output, base, minted_project, credential, repository)
        _check_attempt(attempt, complete=True)
    except BaseException:
        try:
            if revoke_needed:
                revoke_new()
        finally:
            if created:
                path.unlink(missing_ok=True)
        raise
    return path, minted_project


def _verify_connection(base, project, token, *, saved=False, attempt=None):
    """Require both sharing scopes using requests that cannot retrieve or publish.

    The app checks auth, project membership, then scope before these deliberate
    validation failures. Exceptions roll back the transaction, including last-use
    metadata. Only the exact known envelopes prove access; unrelated 404/422 or
    even an unexpected 2xx are not evidence. No session, cursor or work is created.
    """
    label = "The saved connection" if saved else "That credential"
    recovery = ("The saved file was kept unchanged. Review this project's agent credentials in the portal before reconnecting."
                if saved else "Use a portal agent credential with context:read and session:write for this project.")
    probes = [("context", {}, 404, "session_not_found", "Session not found"),
              ("publish", {"operations": []}, 422, "invalid_request", "Supply 1–20 operations")]
    for endpoint, body, status, code, message in probes:
        _check_attempt(attempt)
        try:
            _http_bearer(base, "/api/v1/projects/" + urllib.parse.quote(project, safe="") + "/" + endpoint, body, token)
        except _MintError as error:
            detail = error.payload.get("error")
            if isinstance(detail, dict) and error.status == status and detail.get("code") == code and detail.get("message") == message:
                continue
            if error.status == 401:
                raise ConsentError(label + " was rejected or has expired.", "credential", recovery) from None
            if error.status == 403:
                raise ConsentError(label + " lacks the permissions needed to share and receive context.", "credential", recovery) from None
            if error.status == 404 and isinstance(detail, dict) and detail.get("code") == "not_found":
                raise ConsentError(label + " has no access to that project.", "project", recovery) from None
        except Exception:
            pass
        raise ConsentError("Could not verify that credential with the service; try again.", "credential",
                           "No new connection was saved. Check service availability and try again.") from None


def _verify_portal_credential(base, project, token, attempt=None):
    _verify_connection(base, project, token, attempt=attempt)


def _mint_credential(form, base, home, project, repository, token, attempt=None):
    """Portal-issued credential: verify it, then reserve. No /api/login, no az.

    Requires an explicit project id: unlike SSO's mint response, a manually
    pasted credential carries no `projects` list to auto-pick from.

    An existing saved connection for this project is never overwritten (see
    AGENTS.md). Unlike the SSO/member-code paths -- where reuse is silent
    because the user did not just hand over something new -- a pasted
    credential IS something new, so silently keeping the old one would be a
    surprise. This is reported explicitly, with how to rotate.
    """
    if not project:
        raise ConsentError("Enter the project ID you joined.", "project",
                           "Use the exact project ID shown in Teamwork.")
    directory = _safe_directory(home, base, project)
    path = directory / "connection.json"
    if path.exists():
        _reuse_saved_connection(path, base, project, verify=False)  # Do not use or replace either credential.
        raise ConsentError(
            "A connection for this project is already saved on this machine; the pasted credential was not used.",
            "credential",
            "To use a different credential, revoke the old harness in Teamwork, delete the saved connection "
            "file for this project, then reconnect.",
        )
    _verify_portal_credential(base, project, token, attempt=attempt)
    _check_attempt(attempt)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            data = {"base_url": base, "project_id": project, "token": token, "harness_id": None}
            if repository:
                data["repository_url"] = repository
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        _check_attempt(attempt, complete=True)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, project


def connect(form, base, home, repository=None, origin=None, attempt=None):
    """Called only with private browser input, never model-supplied credentials.

    No network call happens before consent. Dispatch order: (1) a portal
    credential, when supplied, always wins -- `_mint_credential`; (2) SSO,
    only when the code field is blank, Entra sign-in is available on this
    host, AND the service advertises an `api_app_id` -- `_mint_sso`;
    (3) otherwise the member-code path -- `_mint_member`, widened to request
    all three scopes the single consent checkbox describes.

    `origin` is the deployment's PUBLIC WEB origin, forwarded to the
    member-code path (`_mint_member`), which authenticates via a cookie and
    is refused by a split api/web deployment when the origin is derived from
    the API host instead. Defaults (via `_http`) to the derived value, so a
    same-origin deployment is unaffected.
    """
    if form.get("consent") != "yes":
        raise ConsentError("Consent is required before anything is shared.", "consent",
                           "Tick the consent box to enable sharing for this session.")
    _check_attempt(attempt)
    project = form.get("project", "").strip()
    if len(project) > 200:
        raise ConsentError("That project ID is too long.", "project", "Project IDs are at most 200 characters.")
    credential = form.get("credential", "").strip()
    if credential:
        # Takes precedence: a portal credential is a complete, already-minted
        # answer, so neither SSO nor the member-code fields are consulted.
        return _mint_credential(form, base, home, project, repository, credential, attempt=attempt)
    use_sso = not form.get("code") and entra.available()

    if project:
        # The directory key is known upfront, so an existing saved connection
        # is verified before anything is minted, for either auth method. A stale
        # credential must not silently report a working connection.
        directory = _safe_directory(home, base, project)
        path = directory / "connection.json"
        if path.exists():
            if form.get("name", "").strip() or form.get("code"):
                raise ConsentError("This project already has a saved connection; your new login details were not used.", "name",
                                   "Clear the name and personal access code to verify and reuse it, or review the saved agent credential in the portal before reconnecting.")
            return _reuse_saved_connection(path, base, project, attempt=attempt)
        if use_sso:
            return _mint_sso(form, base, home, project, repository, attempt=attempt)
        if not form.get("name", "").strip() or not form.get("code"):
            raise ConsentError("First enrollment needs your name and private member code.",
                               "name" if not form.get("name", "").strip() else "code",
                               "This project is not enrolled yet on this machine, so both fields are required.")
        return _mint_member(form, base, home, project, repository, origin=origin, attempt=attempt)

    if use_sso:
        return _mint_sso(form, base, home, project, repository, attempt=attempt)
    raise ConsentError("Enter the project ID you joined.", "project",
                       "Use the exact project ID shown in Teamwork.")


def publish_pointer(home, url):
    """Write the one-time form URL privately so a missing browser tab is recoverable."""
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = home / POINTER_NAME
        path.unlink(missing_ok=True)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            output.write(url + "\n")
        return path
    except OSError:
        return None


def announce(url, pointer, opened, minutes, stream=None):
    """Print the form address to the terminal; a captured stdout must not hide it."""
    stream = sys.stderr if stream is None else stream
    lines = [
        "",
        "  Teamwork consent form " + ("opened in your browser." if opened else "could NOT open a browser."),
        "  If you do not see it, open this one-time address yourself:",
        "",
        "    " + url,
        "",
        "  Stays open for " + str(minutes) + " minutes of inactivity. Nothing is shared until you submit it.",
    ]
    port = urlsplit(url).port
    if port:
        lines.extend([
            "  On a remote host, forward this loopback port from your own computer:",
            "    ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:" + str(port) + ":127.0.0.1:" + str(port) + " USER@REMOTE_HOST",
            "  Replace USER@REMOTE_HOST with the host running Amplifier, then open the SAME address above locally.",
            "  Keep the tunnel open until you finish. Never expose this port publicly or share the one-time address.",
        ])
    if pointer:
        lines.append("  Also saved to: " + str(pointer))
    lines.append("")
    try:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
    except Exception:
        pass


def private_browser_connect(base, home, stop, timeout=IDLE_TIMEOUT, notify=announce, repository=None, origin=None):
    """Serve a private consent form. `timeout` is the IDLE window; activity resets it."""
    origin = origin or service_origin(base)
    nonce = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    style_nonce = secrets.token_urlsafe(16)
    minutes = max(1, round(timeout / 60))
    sso = entra.available()
    outcome, cancelled, expired = [], [], []
    cancellation_errors = []
    attempt = ConsentAttempt(stop)
    active = threading.Event()
    activity = [0]
    guard = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'nonce-" + style_nonce + "'; "
                             "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body.encode())

        def valid(self):
            return self.headers.get("Host") == address and self.path == "/" + nonce

        def same_origin(self):
            """Referrer-Policy: no-referrer makes browsers send `Origin: null` on a
            same-origin form POST, so the unguessable csrf field is the real check."""
            origin = self.headers.get("Origin")
            site = self.headers.get("Sec-Fetch-Site")
            return origin in (None, "null", "http://" + address) and site in (None, "same-origin", "none")

        def form(self, status=200, values=None, error=None, field=None, hint=None):
            self.reply(status, form_page(nonce, csrf, style_nonce, base, minutes, values, error, field, hint,
                                         sso=sso, repository=repository))

        def stale(self):
            # A late request gets an explanation instead of ERR_CONNECTION_REFUSED.
            self.reply(410, result_page(
                style_nonce, "warn", "This consent window expired", "Nothing was shared.",
                "The form closed after " + str(minutes) + " minutes without activity.",
                "Ask Amplifier to connect to Teamwork again to open a fresh form."))

        def elsewhere(self):
            self.reply(404, result_page(
                style_nonce, "warn", "Wrong address", "This is not the consent form.",
                "The consent form lives at a one-time address containing a private code.",
                "Amplifier printed that full address in the terminal and saved it to "
                "~/.config/amplifier-teamwork/native/" + POINTER_NAME + "."))

        def do_GET(self):
            activity[0] += 1
            if not self.valid():
                return self.elsewhere()
            if expired:
                return self.stale()
            if outcome:
                return self.done()
            self.form()

        def done(self):
            self.reply(200, result_page(
                style_nonce, "ok", "Connected", "Sharing is enabled for this session.",
                "Close this tab and return to Amplifier. Sharing starts with your next prompt.",
                "Stop the session to stop sharing."))

        def refuse(self):
            return self.reply(403, result_page(
                style_nonce, "bad", "Request refused", "That submission did not come from this form.",
                "Nothing was shared. Reload the form from the address Amplifier printed."))

        def drain(self):
            """Consume the body before replying. Closing on an unread body sends an
            RST that costs the client the response it was about to read."""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if not 0 <= length <= 65536:
                return None
            return self.rfile.read(length)

        def do_POST(self):
            activity[0] += 1
            raw = self.drain()
            if not self.valid() or not self.same_origin():
                return self.refuse()
            if expired or stop.is_set():
                return self.stale()
            if outcome:
                return self.done()
            malformed = {"error": "That submission was malformed.",
                         "hint": "Reload this page and submit the form again."}
            try:
                if raw is None or not 0 < len(raw) <= 8192 or self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                    return self.form(400, **malformed)
                fields = urllib.parse.parse_qs(raw.decode(), strict_parsing=True)
                if any(len(values) != 1 for values in fields.values()):
                    return self.form(400, **malformed)
                form = {key: values[0] for key, values in fields.items()}
                if not secrets.compare_digest(form.pop("csrf", ""), csrf):
                    return self.refuse()
            except Exception:
                return self.form(400, **malformed)
            if form.get("action") == "cancel":
                if not attempt.cancel():
                    return self.done()
                cancelled.append(True)
                return self.reply(200, result_page(
                    style_nonce, "warn", "Cancelled", "Sharing was not enabled.",
                    "An enrollment request is still finishing. A new credential may need cleanup; review Harnesses & agents before retrying."
                    if active.is_set() else "No new connection was saved.",
                    "Close this tab and return to Amplifier."))
            # Echo back only what the user typed into this local form, never the code.
            kept = {"project": form.get("project", ""), "name": form.get("name", "")}
            try:
                with guard:
                    if outcome:
                        return self.done()
                    attempt.check()
                    active.set()
                    try:
                        result = connect(form, base, home, repository=repository, origin=origin, attempt=attempt)
                        attempt.complete()
                        outcome.append(result)
                    finally:
                        active.clear()
            except ConsentAborted:
                cancelled.append(True)
                return self.reply(410, result_page(
                    style_nonce, "warn", "Cancelled", "Sharing was not enabled.",
                    "The pending connection was cancelled. No new connection was saved."))
            except ConsentError as error:
                if cancelled or expired or stop.is_set():
                    cancellation_errors.append(str(error) + " " + (error.hint or ""))
                return self.form(400, kept, str(error), error.field, error.hint)
            except Exception:
                # Never echo service errors, submitted values, cookies or credentials.
                return self.form(502, kept, "The service did not complete the enrollment.",
                                 hint="Check your project membership and member code, then submit again. "
                                      "If an earlier attempt was interrupted, review Teamwork's harness controls first.")
            self.done()

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        address = "127.0.0.1:" + str(server.server_port)
        url = "http://" + address + "/" + nonce
        server.timeout = 0.2
        pointer = publish_pointer(home, url)
        try:
            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
        if notify:
            notify(url, pointer, opened, minutes)
        try:
            deadline = time.monotonic() + timeout
            seen = activity[0]
            while not outcome and not cancelled and not stop.is_set() and time.monotonic() < deadline:
                server.handle_request()
                if activity[0] != seen:
                    seen = activity[0]
                    deadline = time.monotonic() + timeout
            if not outcome and not cancelled and not stop.is_set():
                attempt.cancel()
                expired.append(True)
                grace = time.monotonic() + min(20.0, timeout)
                while time.monotonic() < grace and not stop.is_set():
                    server.handle_request()
        finally:
            if not outcome:
                attempt.cancel()
            if pointer:
                Path(pointer).unlink(missing_ok=True)
    if cancelled:
        if cancellation_errors:
            raise ConsentAborted(cancellation_errors[-1] + " Sharing was not enabled.")
        if active.is_set():
            raise ConsentAborted("Cancellation was requested while enrollment was in progress. A new credential may still need cleanup; review Harnesses & agents before retrying. Sharing was not enabled.")
        raise ConsentAborted("The connection was cancelled. Sharing was not enabled.")
    if not outcome or stop.is_set():
        if attempt.completed:
            raise ConsentAborted("The connection finished before cancellation. Its saved credential was kept, but sharing was not enabled.")
        if cancellation_errors:
            raise ConsentAborted(cancellation_errors[-1] + " Sharing was not enabled.")
        if active.is_set():
            raise ConsentAborted("The consent form closed while enrollment was in progress. A new credential may still need cleanup; review Harnesses & agents before retrying. Sharing was not enabled.")
        raise ConsentAborted(
            "The consent form closed before it was submitted"
            + ("" if opened else "; a browser could not be opened on this machine")
            + ". Nothing was shared.")
    return outcome[0]


class TeamworkConnect:
    name = "teamwork_connect"
    description = "Open a private local browser form to select a Teamwork project and explicitly enable sharing for this session. Use only when the user asks to connect. Never ask for credentials in chat or pass credentials to this tool. Use a local browser, or forward the printed loopback port over SSH and open the one-time address on your computer. The form stays open for 15 minutes of inactivity and can be retried in place. Sharing begins with the next prompt."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, coordinator, config):
        self.coordinator = coordinator
        self.base = validate_service_url(config.get("base_url", DEFAULT_BASE_URL))
        # The public web origin, when a deployment serves its web app on a
        # different host than the API (a split deployment). None means
        # "derive it from base_url," which is what every same-origin
        # deployment needs and what this always did.
        self.origin = service_origin(config["web_origin"]) if config.get("web_origin") else None
        # Forwarded so a session connected through this tool honours the same level.
        self.notice = {"verbosity": config["verbosity"]} if "verbosity" in config else {}
        self.home = Path(config.get("connection_directory", "~/.config/amplifier-teamwork/native")).expanduser()
        self.timeout = config.get("form_timeout", IDLE_TIMEOUT)
        self.lock = asyncio.Lock()

    async def execute(self, input):
        from amplifier_core import ToolResult
        if input:
            return ToolResult(success=False, error={"message": "This tool accepts no arguments. Enter information only in the private browser form."})
        if urlsplit(self.base).hostname in RETIRED_HOSTS:
            return ToolResult(success=False, error={"message": RETIRED_MESSAGE})
        async with self.lock:
            rebind = self.coordinator.get_capability("teamwork.rebind")
            stop = threading.Event()
            repository = await asyncio.to_thread(lambda: git_remote.repository_identity(git_remote.origin_url()))
            try:
                path, project = await asyncio.to_thread(
                    private_browser_connect, self.base, self.home, stop, self.timeout, announce, repository, self.origin)
                if rebind is not None:
                    # Already sharing: move this session rather than mounting twice,
                    # handing over the project-scoped credential the form just minted.
                    rebind(project, json.loads(Path(path).read_text(encoding="utf-8")))
                else:
                    await mount_hook(self.coordinator, dict(self.notice, connection_file=str(path), share_visible_turns=True))
                return ToolResult(success=True, output={"project": project, "sharing": "enabled for subsequent prompts in this session"})
            except ConsentAborted as reason:
                return ToolResult(success=False, error={"message": str(reason) + " Ask to connect again to reopen the form."})
            except Exception:
                return ToolResult(success=False, error={"message": "Teamwork connection did not complete. Use a local browser and check the private form. No sharing was enabled by this tool."})
            finally:
                stop.set()


class TeamworkBind:
    name = "teamwork_bind"
    description = (
        "Bind this Amplifier session to a Teamwork project, or move it to a different one. "
        "Uses the service URL and harness credential already configured on this machine; "
        "never ask for or pass credentials. Sharing begins with the next prompt. "
        "Use teamwork_connect instead when no credential is configured yet. "
        "Omit project_id to bind the project linked to this working directory's git remote."
    )
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Exact project id to bind this session to."}},
        "additionalProperties": False,
    }

    def __init__(self, coordinator, config):
        self.coordinator = coordinator
        self.config = config
        self.notice = {"verbosity": config["verbosity"]} if "verbosity" in config else {}
        self.lock = asyncio.Lock()

    async def execute(self, input):
        from amplifier_core import ToolResult
        project = str((input or {}).get("project_id", "")).strip()
        configured_base = self.config.get("base_url")
        if configured_base and urlsplit(configured_base).hostname in RETIRED_HOSTS:
            return ToolResult(success=False, error={"message": RETIRED_MESSAGE})
        async with self.lock:
            saved_connection = None
            if not project:
                resolved = await self._resolve_project_from_git_remote()
                if isinstance(resolved, ToolResult):
                    return resolved
                project, saved_connection = resolved
            rebind = self.coordinator.get_capability("teamwork.rebind")
            if rebind is not None:
                # Already sharing: move this session without re-registering handlers.
                try:
                    rebind(project, saved_connection)
                except Exception:
                    return ToolResult(success=False, error={"message": "Could not bind that project."})
                return ToolResult(success=True, output={"project": project, "sharing": "rebound; effective from the next prompt"})
            if saved_connection is not None:
                settings = {key: saved_connection[key] for key in ("base_url", "token", "harness_id") if saved_connection.get(key)}
            else:
                settings = {key: self.config[key] for key in ("base_url", "token", "connection_file") if self.config.get(key)}
            if not settings.get("token") and not settings.get("connection_file"):
                return ToolResult(success=False, error={"message": "No Teamwork credential is configured. Use teamwork_connect to enroll first."})
            try:
                await mount_hook(self.coordinator, dict(settings, **self.notice, project_id=project, share_visible_turns=True))
            except Exception:
                return ToolResult(success=False, error={"message": "Could not bind that project with the configured credential."})
            if self.coordinator.get_capability("teamwork.session_id") is None:
                return ToolResult(success=False, error={"message": "Binding did not take effect; nothing is being shared."})
            return ToolResult(success=True, output={"project": project, "sharing": "enabled for subsequent prompts in this session"})

    async def _resolve_project_from_git_remote(self):
        """Local-first auto-bind for an omitted project_id (A7).

        The canonical repository_url stored in connection.json at enrollment is
        matched against this directory's git remote across saved connections.
        Network `discover` is retained only as a confirm/diagnose fallback: a
        harness credential can never see a project other than its own, so it
        cannot move a session between projects -- it can only confirm the one
        the credential already holds.
        """
        from amplifier_core import ToolResult
        identity = await asyncio.to_thread(lambda: git_remote.repository_identity(git_remote.origin_url()))
        if not identity:
            return ToolResult(success=False, error={"message": "This directory has no usable GitHub remote. Pass project_id."})
        matches = await asyncio.to_thread(self._saved_connections_matching, identity)
        if len(matches) == 1:
            saved = matches[0]
            return saved["project_id"], saved
        if len(matches) > 1:
            candidates = ", ".join(sorted(saved["project_id"] for saved in matches))
            return ToolResult(success=False, error={
                "message": "Several saved connections match this repository: " + candidates + ". Pass project_id."})
        discovered = await self._discover_project(identity)
        if discovered is _DISCOVER_UNAVAILABLE:
            return ToolResult(success=False, error={
                "message": "Project discovery is not available on this service yet. Pass project_id."})
        if discovered:
            return discovered, None
        return ToolResult(success=False, error={
            "message": "No Teamwork project you can reach is linked to this repository. Link it in Teamwork, or pass project_id."})

    def _saved_connections_matching(self, identity):
        """Mode-0600 connection.json files under the native enrollment directory only."""
        home = Path("~/.config/amplifier-teamwork/native").expanduser()
        matches = []
        if not home.is_dir():
            return matches
        for connection_file in sorted(home.glob("*/connection.json")):
            try:
                if os.name != "nt" and connection_file.stat().st_mode & 0o077:
                    continue
                saved = json.loads(connection_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if saved.get("repository_url") == identity and saved.get("project_id") and saved.get("token"):
                matches.append(saved)
        return matches

    async def _discover_project(self, identity):
        """Network confirm/diagnose only; the currently configured credential
        can only ever see its own project (see module docstring above)."""
        base = self.config.get("base_url")
        token = self.config.get("token")
        if not base or not token:
            return None

        def call():
            headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token,
                       "Origin": service_origin(base)}
            request = urllib.request.Request(base + "/api/projects/discover",
                                             json.dumps({"repository_url": identity}).encode(), headers)
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=HTTP_TIMEOUT) as response:
                return json.load(response)

        try:
            result = await asyncio.to_thread(call)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                # An EasyAuth-gated origin, or one that has not yet rolled out
                # this endpoint's bearer auth, refuses the request before any
                # project lookup happens -- distinct from "reached the
                # service, no match found".
                return _DISCOVER_UNAVAILABLE
            return None
        except Exception:
            return None
        return (result or {}).get("selected_project_id") or None


async def mount(coordinator, config=None):
    if getattr(coordinator, "parent_id", None):
        return None
    config = config or {}
    tool = TeamworkConnect(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    binder = TeamworkBind(coordinator, config)
    await coordinator.mount("tools", binder, name=binder.name)
    return None
