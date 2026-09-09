"""Enroll privately and create an opt-in Amplifier overlay; no active config edits."""
import argparse
import getpass
import json
import os
from pathlib import Path
import urllib.request
import urllib.error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def bundle_reference(value):
    path = Path(value).expanduser()
    return path.resolve().as_uri() if path.exists() else value


def build_overlay(base_bundle, connection_file):
    bundle_path = Path(__file__).resolve().parent
    module_path = bundle_path / "modules/hooks-teamwork"
    return {"bundle": {"name": "teamwork-overlay", "version": "0.1.0"}, "includes": [{"bundle": bundle_reference(base_bundle)}, {"bundle": (bundle_path / "behaviors/teamwork.yaml").as_uri()}], "hooks": [{"module": "hooks-teamwork", "source": str(module_path), "config": {"connection_file": str(connection_file), "share_visible_turns": True}}]}


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
        return os.fdopen(fd, "w")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://team.amplifier.run")
    parser.add_argument("--project", default="teamwork")
    parser.add_argument("--bundle", required=True, help="Your existing bundle name or path; never guessed")
    parser.add_argument("--label", default="Amplifier harness", help="Optional label; no machine hostname is collected")
    parser.add_argument("--output", default="teamwork-overlay.yaml")
    parser.add_argument("--connection-file", default="~/.config/amplifier-teamwork/connection.json")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not base.startswith("https://"): raise SystemExit("Use the HTTPS service URL")
    path = Path(args.connection_file).expanduser().resolve()
    if path.exists(): raise SystemExit("Connection file already exists; choose a new --connection-file (existing credential preserved)")
    output = Path(args.output).expanduser().resolve()
    if output.exists(): raise SystemExit("Overlay exists; choose a new --output path")
    name = input("Name or email: ").strip()
    token = getpass.getpass("Private member login code (not saved): ")
    def post(endpoint, body, cookie=None):
        headers = {"Content-Type": "application/json", "Origin": base, "X-Teamwork-Project": args.project}
        if cookie: headers["Cookie"] = cookie
        request = urllib.request.Request(base + endpoint, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response: return json.load(response), response.headers
        except urllib.error.HTTPError as error:
            status = error.code; error.close(); raise SystemExit(f"Enrollment HTTP {status}; check login and project membership") from None
    path, output = enroll_and_save(post, base, args.project, args.bundle, args.label, name, token, path, output)
    print("Created opt-in overlay:", output)
    print("Enrolled project:", args.project)
    print("Start a NEW session: amplifier run --bundle " + output.as_uri())
    print("Visible prompts/responses will be shared to this project. No active session or default bundle changed.")


if __name__ == "__main__": main()
