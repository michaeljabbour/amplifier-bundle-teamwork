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
from amplifier_module_hooks_teamwork.service_url import service_origin, validate_service_url


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


def project_paths(project):
    """Return collision-resistant private defaults for one Teamwork project."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "project"
    suffix = hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]
    directory = Path("~/.config/amplifier-teamwork").expanduser() / f"{slug}-{suffix}"
    return directory / "connection.json", directory / "teamwork-overlay.yaml"


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://team.amplifier.run")
    parser.add_argument("--project", default="teamwork")
    parser.add_argument("--bundle", required=True, help="Your existing bundle name or path; never guessed")
    parser.add_argument("--label", default="Amplifier harness", help="Optional label; no machine hostname is collected")
    parser.add_argument("--output")
    parser.add_argument("--connection-file")
    args = parser.parse_args()
    try:
        base = validate_service_url(args.base_url)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    default_connection, default_overlay = project_paths(args.project)
    path = Path(args.connection_file).expanduser().resolve() if args.connection_file else default_connection.resolve()
    if path.exists(): raise SystemExit("Connection file already exists; choose a new --connection-file (existing credential preserved)")
    output = Path(args.output).expanduser().resolve() if args.output else default_overlay.resolve()
    if output.exists(): raise SystemExit("Overlay exists; choose a new --output path")
    if path == output: raise SystemExit("Connection and overlay must use different paths")
    name = input("Name or email: ").strip()
    token = getpass.getpass("Private member login code (not saved): ")
    def post(endpoint, body, cookie=None):
        headers = {"Content-Type": "application/json", "Origin": service_origin(base), "X-Teamwork-Project": args.project}
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
