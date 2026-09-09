"""Native enrollment tool; private credentials never enter tool arguments/results."""
import asyncio
import html
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from amplifier_module_hooks_teamwork import mount as mount_hook, sha
from amplifier_module_hooks_teamwork.service_url import service_origin, validate_service_url
from amplifier_module_hooks_teamwork import NoRedirect

__amplifier_module_type__ = "tool"


def connect(form, base, home):
    """Called only with private browser input, never model-supplied credentials."""
    project = form.get("project", "").strip()
    if not project or len(project) > 200 or form.get("consent") != "yes":
        raise ValueError("Select a project and explicitly consent to sharing")
    directory = home / sha(base + "\0" + project)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or (os.name != "nt" and directory.stat().st_mode & 0o077):
        raise ValueError("Connection directory must be private")
    path = directory / "connection.json"
    if path.exists():
        if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
            raise ValueError("Connection file must be private")
        saved = json.loads(path.read_text())
        if saved.get("base_url") != base or saved.get("project_id") != project or not saved.get("token"):
            raise ValueError("Saved connection does not match the selected project")
        return path, project
    if not form.get("name", "").strip() or not form.get("code"):
        raise ValueError("Name and private member code required for first enrollment")
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


def private_browser_connect(base, home, stop, timeout=180):
    nonce = secrets.token_urlsafe(32)
    outcome = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body.encode())

        def valid(self):
            return self.headers.get("Host") == address and self.path == "/" + nonce

        def do_GET(self):
            if not self.valid():
                return self.reply(404, "Not found")
            self.reply(200, '<!doctype html><meta charset="utf-8"><title>Connect Teamwork</title>'
                '<h1>Connect this Amplifier session</h1><p>Service: ' + html.escape(base) + '</p>'
                '<p>Enter the exact project ID you joined. For a saved connection, leave login fields blank.</p>'
                '<form method="post"><label>Project ID <input name="project" required maxlength="200"></label><br>'
                '<label>Name or email <input name="name" autocomplete="username"></label><br>'
                '<label>Private member code <input name="code" type="password" autocomplete="off"></label><br>'
                '<label><input name="consent" type="checkbox" value="yes" required>Share subsequent visible prompts and final responses in this session with this project, and receive its bounded context.</label>'
                '<p>Credentials stay on this machine and go directly to the service; never paste them into Amplifier chat.</p>'
                '<button>Connect and enable sharing</button></form>')

        def do_POST(self):
            if not self.valid() or self.headers.get("Origin") != "http://" + address or outcome or stop.is_set():
                return self.reply(403, "Request refused")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    return self.reply(400, "Invalid form")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                    return self.reply(400, "Invalid form")
                fields = urllib.parse.parse_qs(self.rfile.read(length).decode(), strict_parsing=True)
                if any(len(values) != 1 for values in fields.values()):
                    return self.reply(400, "Invalid form")
                outcome.append(connect({key: values[0] for key, values in fields.items()}, base, home))
            except Exception:
                # Never echo service errors, submitted values, cookies or credentials.
                return self.reply(400, "Connection failed. Check project membership, private login and local file permissions. If enrollment was interrupted, review harness controls before retrying.")
            self.reply(200, "Connection saved. Close this tab and return to Amplifier for confirmation. After the tool confirms, sharing starts with your next prompt.")

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        address = "127.0.0.1:" + str(server.server_port)
        server.timeout = 0.2
        if not webbrowser.open("http://" + address + "/" + nonce):
            raise RuntimeError("A local browser is required")
        deadline = time.monotonic() + timeout
        while not outcome and not stop.is_set() and time.monotonic() < deadline:
            server.handle_request()
    if not outcome or stop.is_set():
        raise RuntimeError("Connection cancelled or timed out")
    return outcome[0]


class TeamworkConnect:
    name = "teamwork_connect"
    description = "Open a private local browser form to select a Teamwork project and explicitly enable sharing for this session. Use only when the user asks to connect. Never ask for credentials in chat or pass credentials to this tool. Requires a browser on the Amplifier host. Sharing begins with the next prompt."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(self, coordinator, config):
        self.coordinator = coordinator
        self.base = validate_service_url(config.get("base_url", "https://team.amplifier.run"))
        self.home = Path(config.get("connection_directory", "~/.config/amplifier-teamwork/native")).expanduser()
        self.lock = asyncio.Lock()

    async def execute(self, input):
        from amplifier_core import ToolResult
        if input:
            return ToolResult(success=False, error={"message": "This tool accepts no arguments. Enter information only in the private browser form."})
        async with self.lock:
            if self.coordinator.get_capability("teamwork.session_id"):
                return ToolResult(success=True, output="This session is already connected. Start a new session to choose another project.")
            stop = threading.Event()
            try:
                path, project = await asyncio.to_thread(private_browser_connect, self.base, self.home, stop)
                await mount_hook(self.coordinator, {"connection_file": str(path), "share_visible_turns": True})
                return ToolResult(success=True, output={"project": project, "sharing": "enabled for subsequent prompts in this session"})
            except Exception:
                return ToolResult(success=False, error={"message": "Teamwork connection did not complete. Use a local browser and check the private form. No sharing was enabled by this tool."})
            finally:
                stop.set()


async def mount(coordinator, config=None):
    if getattr(coordinator, "parent_id", None):
        return None
    tool = TeamworkConnect(coordinator, config or {})
    await coordinator.mount("tools", tool, name=tool.name)
    return None
