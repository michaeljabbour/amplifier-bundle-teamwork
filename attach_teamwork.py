"""Attach a project to a harness credential you ALREADY hold. Mints nothing.

`setup_teamwork.py` is the enrollment path: it takes a member login code and
asks the service to MINT a new harness. This script is the other half, and
until now it did not exist: you already have a credential -- the portal minted
one for you -- and you need a project directory to use it.

Why that gap mattered. `setup_teamwork.py` was the only path the bundle
offered, and it always mints. So every re-onboard -- reopening a folder,
recreating a container, retrying after a failure -- produced ANOTHER backend
harness. Not because the service duplicated anything, but because the client
asked it to. A teammate doing first-time UX testing hit the same wall from the
other side: the portal minted a credential and nothing in the bundle could
consume it.

TWO THINGS THIS DELIBERATELY DOES NOT DO.

It never calls `POST /api/harnesses`. Re-running it is therefore safe and
idempotent against the backend: the bearer IS the harness identity, so reusing
a token already reattaches to the same harness record. The service needs no
reconnect endpoint and does not have one.

It never writes to `~/.amplifier/`. A harness is spawned IN A FOLDER and its
credential belongs to that folder. A credential in user-global settings would
silently give every session on the host one identity, publishing one project's
work under another project's harness. So configuration lands in the PROJECT
scope -- `<project>/.amplifier/settings.yaml` and `<project>/.amplifier/keys.env`
-- with the credential in its own 0600 file that the hook reads directly, and
the settings override carrying only that file's path.

A project `keys.env` deliberately is NOT used: KeyManager reads
`~/.amplifier/keys.env` and nothing else, so a project-local one is never
loaded and a `${VAR}` reference to it would never expand -- silently. See
`settings_block()`.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork.service_url import DEFAULT_BASE_URL, validate_service_url

COLD_START_SECONDS = 60


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def _open(request):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=COLD_START_SECONDS)


def published_project(base):
    """The project id the service publishes at /api/config, or None.

    Unauthenticated and read-only. Never fatal: an unreachable or unparseable
    response just means the caller must say which project they meant.
    """
    try:
        request = urllib.request.Request(base + "/api/config", headers={"Accept": "application/json"})
        with _open(request) as response:
            published = json.loads(response.read().decode("utf-8"))
        value = published.get("project_id") if isinstance(published, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def verify(base, project, token):
    """Prove the credential is accepted BEFORE writing it anywhere.

    A read-only `/context` call publishes nothing, so this is safe to run on
    every attach. The distinction that matters is not success-vs-failure but
    WHICH failure: 401/403 means the bearer itself was rejected (wrong
    credential, or revoked), while any other status means the service
    authenticated it and merely disliked this particular request -- which is
    all the proof this needs.

    That distinction is not academic. Handed a portal-minted harness bearer, an
    operator with the source open assumed it was a member login code, got 401
    from /api/login three times, and only identified it correctly when a
    /context probe answered 404 rather than 401. Writing an unverified
    credential into a settings file would push that same confusion into a
    session, where it surfaces later as silence.
    """
    request = urllib.request.Request(
        base + "/api/v1/projects/" + project + "/context", data=b"{}",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        with _open(request):
            return
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status in (401, 403):
            raise SystemExit(
                "The service refused this credential (HTTP %d). It is not a harness token for this "
                "project, or it has been revoked. Nothing was written." % status) from None
    except Exception as error:
        raise SystemExit(
            "Could not reach the service to verify the credential (%s). Nothing was written."
            % type(error).__name__) from None


def settings_block(base, project, connection_file):
    """The project-scoped override, keyed by module id.

    `overrides.<module-id>.config` is applied after the full mount plan is
    assembled and is keyed by module IDENTITY, so it reaches the hook wherever
    it is declared -- including a behavior installed the documented way, with
    `amplifier bundle add <url> --app`. That is why no hand-written overlay
    bundle is needed here, and why this does not fight the app-layer install.

    THE SETTINGS FILE CARRIES A PATH, NEVER A SECRET, AND NEVER A ${VAR}.

    The obvious design -- put the token in a project keys.env and reference it
    as ${TEAMWORK_HARNESS_TOKEN} -- DOES NOT WORK, and fails silently. Measured:
    KeyManager reads `get_amplifier_home() / "keys.env"` and nothing else
    (app-cli key_manager.py:11, "Manage API keys in ~/.amplifier/keys.env
    file"), so a PROJECT-local .amplifier/keys.env is never loaded. The variable
    stays undefined, expansion leaves the literal string in place, and
    resolve_connection() receives a token-shaped value that is not a token. The
    hook then mounts inert: a session shows teamwork_connect and teamwork_bind
    from the tool module and none of the hook's own tools, with no error
    anywhere.

    settings.yaml is project-scoped. keys.env is not. They live in the same
    .amplifier directory and follow different rules.

    So the credential goes in its own 0600 file that the HOOK reads directly,
    and the override carries only its absolute path. No env expansion in the
    chain means nothing to silently fail to expand.
    """
    config = {"base_url": base, "project_id": project,
              "connection_file": str(connection_file)}
    return {"overrides": {"hooks-teamwork": {"config": dict(config)},
                          "tool-teamwork": {"config": dict(config)}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", help="The harness credential you already hold. Omit to read it from stdin.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project", help="Project id; omit to use the one the service publishes at /api/config")
    parser.add_argument("--dir", default=".", help="Project directory to configure (default: the current one)")
    args = parser.parse_args()

    try:
        base = validate_service_url(args.base_url)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    token = (args.token or sys.stdin.readline()).strip()
    if not token:
        raise SystemExit("A harness credential is required (--token, or on stdin)")

    project = args.project or published_project(base)
    if not project:
        raise SystemExit(
            "Could not read the project id from the service; pass --project explicitly")

    verify(base, project, token)

    # Project scope, always. `~/.amplifier` is never touched -- see this
    # module's docstring for why that is a correctness property and not a
    # preference.
    home = (Path(args.dir).expanduser().resolve() / ".amplifier")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection, settings = home / "teamwork-connection.json", home / "settings.yaml"

    # The secret, and only the secret, and only here. Rewritten wholesale on a
    # re-attach so a rotated credential replaces the old one rather than
    # accumulating beside it.
    connection.write_text(json.dumps(
        {"base_url": base, "project_id": project, "token": token}, indent=2) + "\n",
        encoding="utf-8")
    os.chmod(connection, 0o600)

    block = settings_block(base, project, connection)
    if settings.exists():
        # Refuse rather than merge. A settings file is the user's, this script
        # has no YAML parser to merge it faithfully (the bundle carries no
        # third-party dependency), and a clobbered settings file is a worse
        # outcome than a paste. So: say exactly what to add, and touch nothing.
        print("%s already exists -- not modified. Add this to it:\n" % settings)
        print(json.dumps(block, indent=2))
    else:
        # JSON is valid YAML, so this needs no YAML writer to produce a file
        # the loader reads correctly.
        settings.write_text(json.dumps(block, indent=2) + "\n", encoding="utf-8")
        os.chmod(settings, 0o600)
        print("Wrote", settings)

    print("Wrote", connection, "(0600, credential only)")
    print("Attached project:", project)
    print("No harness was minted. Re-running this attaches to the SAME harness.")
    print("Install the behavior in this harness if it is not already:")
    print("  amplifier bundle add \"git+https://github.com/michaeljabbour/amplifier-bundle-teamwork"
          "@main#subdirectory=behaviors/teamwork.yaml\" --app")


if __name__ == "__main__":
    main()
