"""Native enrollment tool; private credentials never enter tool arguments/results."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from amplifier_module_hooks_teamwork import mount as mount_hook, sha
from amplifier_module_hooks_teamwork.service_url import DEFAULT_BASE_URL, service_origin, validate_service_url
from amplifier_module_hooks_teamwork import NoRedirect
from amplifier_module_tool_teamwork.page import form_page, result_page

__amplifier_module_type__ = "tool"

IDLE_TIMEOUT = 900
POINTER_NAME = "pending-form-url.txt"


class ConsentError(ValueError):
    """Self-authored message safe to render in the private form; never service text."""

    def __init__(self, message, field=None, hint=None):
        super().__init__(message)
        self.field = field
        self.hint = hint


class ConsentAborted(RuntimeError):
    """Self-authored message safe to return to the model; never service text."""


def connect(form, base, home):
    """Called only with private browser input, never model-supplied credentials."""
    project = form.get("project", "").strip()
    if not project:
        raise ConsentError("Enter the project ID you joined.", "project",
                           "Use the exact project ID shown in Teamwork.")
    if len(project) > 200:
        raise ConsentError("That project ID is too long.", "project", "Project IDs are at most 200 characters.")
    if form.get("consent") != "yes":
        raise ConsentError("Consent is required before anything is shared.", "consent",
                           "Tick the consent box to enable sharing for this session.")
    directory = home / sha(base + "\0" + project)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or (os.name != "nt" and directory.stat().st_mode & 0o077):
        raise ConsentError("The local connection folder is not private.", None,
                           "Remove group and other permissions on ~/.config/amplifier-teamwork/native, then retry.")
    path = directory / "connection.json"
    if path.exists():
        if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
            raise ConsentError("The saved connection file is not private.", None,
                               "Restore mode 0600 on the saved connection file, then retry.")
        try:
            saved = json.loads(path.read_text())
        except ValueError:
            raise ConsentError("The saved connection file is unreadable.", None,
                               "Revoke that harness in Teamwork, delete the saved file, then enroll again.") from None
        if saved.get("base_url") != base or saved.get("project_id") != project or not saved.get("token"):
            raise ConsentError("A different connection is already saved for this project.", "project",
                               "Revoke that harness in Teamwork and delete its saved file before re-enrolling.")
        return path, project
    if not form.get("name", "").strip() or not form.get("code"):
        raise ConsentError("First enrollment needs your name and private member code.",
                           "name" if not form.get("name", "").strip() else "code",
                           "This project is not enrolled yet on this machine, so both fields are required.")
    def post(endpoint, body, cookie=None):
        headers = {"Content-Type": "application/json", "Origin": service_origin(base), "X-Teamwork-Project": project}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(base + endpoint, json.dumps(body).encode(), headers)
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            return json.load(response), response.headers
    # Reserve before issuing a credential; never overwrite a concurrent enrollment.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    credential = cookie = None
    try:
        with os.fdopen(fd, "w") as output:
            _, headers = post("/api/login", {"name": form["name"].strip(), "token": form["code"]})
            cookie = headers["Set-Cookie"].split(";")[0]
            credential, _ = post("/api/harnesses", {"label": "Amplifier native", "scopes": ["context:read", "session:write"]}, cookie)
            json.dump({"base_url": base, "project_id": project, "token": credential["token"], "harness_id": credential["id"]}, output)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if credential:
            try:
                post("/api/harnesses/revoke", {"id": credential["id"]}, cookie)
            except Exception:
                # Preserve recovery evidence privately, without returning the credential.
                recovery = directory / ("recovery-" + uuid.uuid4().hex + ".json")
                with os.fdopen(os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
                    json.dump({"harness_id": credential["id"], "action": "Revoke in Teamwork harness controls"}, output)
        path.unlink(missing_ok=True)
        raise
    return path, project


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
    if pointer:
        lines.append("  Also saved to: " + str(pointer))
    lines.append("")
    try:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
    except Exception:
        pass


def private_browser_connect(base, home, stop, timeout=IDLE_TIMEOUT, notify=announce):
    """Serve a private consent form. `timeout` is the IDLE window; activity resets it."""
    nonce = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    style_nonce = secrets.token_urlsafe(16)
    minutes = max(1, round(timeout / 60))
    outcome, cancelled, expired = [], [], []
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
            self.reply(status, form_page(nonce, csrf, style_nonce, base, minutes, values, error, field, hint))

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
                cancelled.append(True)
                return self.reply(200, result_page(
                    style_nonce, "warn", "Cancelled", "No connection was made.",
                    "Nothing was shared and no credential was created.",
                    "Close this tab and return to Amplifier."))
            # Echo back only what the user typed into this local form, never the code.
            kept = {"project": form.get("project", ""), "name": form.get("name", "")}
            try:
                with guard:
                    if outcome:
                        return self.done()
                    outcome.append(connect(form, base, home))
            except ConsentError as error:
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
                expired.append(True)
                grace = time.monotonic() + min(20.0, timeout)
                while time.monotonic() < grace and not stop.is_set():
                    server.handle_request()
        finally:
            if pointer:
                Path(pointer).unlink(missing_ok=True)
    if cancelled:
        raise ConsentAborted("You cancelled the connection in the browser form. Nothing was shared.")
    if not outcome or stop.is_set():
        raise ConsentAborted(
            "The consent form closed before it was submitted"
            + ("" if opened else "; a browser could not be opened on this machine")
            + ". Nothing was shared.")
    return outcome[0]


class TeamworkConnect:
    name = "teamwork_connect"
    description = "Open a private local browser form to select a Teamwork project and explicitly enable sharing for this session. Use only when the user asks to connect. Never ask for credentials in chat or pass credentials to this tool. Requires a browser on the Amplifier host; the form's one-time address is also printed to the terminal. The form stays open for 15 minutes of inactivity and can be retried in place. Sharing begins with the next prompt."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, coordinator, config):
        self.coordinator = coordinator
        self.base = validate_service_url(config.get("base_url", DEFAULT_BASE_URL))
        # Forwarded so a session connected through this tool honours the same level.
        self.notice = {"verbosity": config["verbosity"]} if "verbosity" in config else {}
        self.home = Path(config.get("connection_directory", "~/.config/amplifier-teamwork/native")).expanduser()
        self.timeout = config.get("form_timeout", IDLE_TIMEOUT)
        self.lock = asyncio.Lock()

    async def execute(self, input):
        from amplifier_core import ToolResult
        if input:
            return ToolResult(success=False, error={"message": "This tool accepts no arguments. Enter information only in the private browser form."})
        async with self.lock:
            rebind = self.coordinator.get_capability("teamwork.rebind")
            stop = threading.Event()
            try:
                path, project = await asyncio.to_thread(private_browser_connect, self.base, self.home, stop, self.timeout)
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
        "Use teamwork_connect instead when no credential is configured yet."
    )
    input_schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "description": "Exact project id to bind this session to."}},
        "required": ["project_id"],
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
        if not project:
            return ToolResult(success=False, error={"message": "A project id is required."})
        async with self.lock:
            rebind = self.coordinator.get_capability("teamwork.rebind")
            if rebind is not None:
                # Already sharing: move this session without re-registering handlers.
                try:
                    rebind(project)
                except Exception:
                    return ToolResult(success=False, error={"message": "Could not bind that project."})
                return ToolResult(success=True, output={"project": project, "sharing": "rebound; effective from the next prompt"})
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


async def mount(coordinator, config=None):
    if getattr(coordinator, "parent_id", None):
        return None
    config = config or {}
    tool = TeamworkConnect(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    binder = TeamworkBind(coordinator, config)
    await coordinator.mount("tools", binder, name=binder.name)
    return None
