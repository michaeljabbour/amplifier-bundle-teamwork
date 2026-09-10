"""Run the Teamwork bundle end-to-end inside a Digital Twin Universe container.

Executed INSIDE the DTU (see tests/dtu/README.md). It starts the loopback stub,
writes a private connection file and an opt-in overlay, runs two real Amplifier
sessions -- one opted in, one opted out -- and asserts the delivery boundary
from the stub's own request log.

What a PASS establishes:
  - the bundle and hook install from the configured remote source
  - shared context reaches the provider (a fixture canary comes back in the
    visible response, which a receipt alone cannot show)
  - no acknowledgement precedes the observed context attachment
  - the acknowledged injection is the injection published with the turn
  - the person projection drops fields outside the hook's whitelist
  - an explicitly disabled overlay emits nothing at all

What a PASS does NOT establish: the hosted service contract, provider
comprehension, credential-scope enforcement, or any other participant's
installation. The stub's response shapes are written from the hook's reader.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANARY = "PURPLE-OTTER-4417"
UNPROJECTED_MARKER = "UNPROJECTED-PERSON-FIELD"
STUB_TOKEN = "fixture-harness-credential"
MEMBER_CODE = "fixture-member-code"
MEMBER_NAME = "Fixture Participant"
PROMPT = "What is the project canary phrase? Reply with only the exact phrase."
REPO_URL = "https://github.com/michaeljabbour/amplifier-bundle-teamwork"

BEHAVIOR_SOURCE = (
    "git+https://github.com/michaeljabbour/amplifier-bundle-teamwork"
    "@{ref}#subdirectory=behaviors/teamwork.yaml"
)


class Failure(Exception):
    """A checked expectation did not hold."""


def write_private(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def write_overlay(path: Path, base_bundle: str, ref: str, connection: Path, enabled: bool) -> Path:
    overlay = {
        "bundle": {"name": "teamwork-e2e-overlay", "version": "0.1.0"},
        "includes": [
            {"bundle": base_bundle},
            {"bundle": BEHAVIOR_SOURCE.format(ref=ref)},
        ],
        "hooks": [
            {
                "module": "hooks-teamwork",
                "config": {
                    "connection_file": str(connection),
                    "share_visible_turns": enabled,
                },
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overlay, indent=2) + "\n", encoding="utf-8")
    return path


def start_stub(workdir: Path, port: int, crowded: bool = False) -> tuple[subprocess.Popen, Path]:
    log = workdir / "stub-requests.jsonl"
    if log.exists():
        log.unlink()
    process = subprocess.Popen(
        [
            sys.executable,
            str(HERE / "stub_service.py"),
            "--port",
            str(port),
            "--token",
            STUB_TOKEN,
            "--log",
            str(log),
        ] + (["--crowded"] if crowded else []),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}/api/v1/projects/teamwork/context"
    for _ in range(50):
        try:
            urllib.request.urlopen(
                urllib.request.Request(endpoint, data=b"{}", headers={"Content-Type": "application/json"}),
                timeout=2,
            )
        except urllib.error.HTTPError:
            return process, log  # 401 without a bearer: the stub is answering
        except OSError:
            time.sleep(0.2)
    process.kill()
    raise Failure(f"stub did not start on port {port}")


def run_session(overlay: Path, prompt: str, timeout: int) -> dict:
    completed = subprocess.run(
        ["amplifier", "run", "--bundle", overlay.as_uri(), prompt, "--output-format", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    for line in reversed(completed.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    # The CLI prints preparation progress before the JSON object.
    start = completed.stdout.find("{")
    if start != -1:
        try:
            return json.loads(completed.stdout[start:])
        except ValueError:
            pass
    raise Failure(f"no JSON result from amplifier run (exit {completed.returncode}): {completed.stderr[-400:]}")


def clone_repo(workdir: Path, ref: str) -> Path:
    """Clone this bundle the way a participant does; the DTU rewrites the origin."""
    target = workdir / "checkout"
    if target.exists():
        return target
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, REPO_URL, str(target)],
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    return target


def enroll(checkout: Path, workdir: Path, base_url: str, base_bundle: str,
           name: str = MEMBER_NAME, code: str = MEMBER_CODE) -> tuple[Path, Path]:
    """Drive the documented enrollment script against the stub's member plane."""
    connection = workdir / "enrolled" / "link.json"
    overlay = workdir / "enrolled" / "overlay.yaml"
    connection.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    completed = subprocess.run(
        [
            sys.executable,
            str(checkout / "setup_teamwork.py"),
            "--base-url",
            base_url,
            "--project",
            "teamwork",
            "--bundle",
            base_bundle,
            "--connection-file",
            str(connection),
            "--output",
            str(overlay),
        ],
        input=f"{name}\n{code}\n",
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise Failure(f"enrollment failed (exit {completed.returncode}): {completed.stderr[-400:]}")
    return connection, overlay


def assert_enrollment(rows: list, connection: Path, overlay: Path, checkout: Path,
                      base_url: str, code: str = MEMBER_CODE) -> list:
    """Assert the enrollment contract from the member-plane traffic and outputs."""
    results: list = []
    member = [row for row in rows if row.get("plane") == "member"]
    logins = [row for row in member if row["endpoint"] == "login"]
    mints = [row for row in member if row["endpoint"] == "harnesses"]

    check(results, "enrollment signed in", bool(logins))
    check(results, "credential minted after sign-in", bool(mints))
    if not (logins and mints):
        return results

    check(results, "sign-in preceded credential mint",
          member.index(logins[0]) < member.index(mints[0]))
    check(results, "enrollment sent the same-origin header",
          all(row["origin_header_present"] for row in member))
    check(results, "credential mint presented the session cookie", mints[0]["cookie_header_present"])
    check(results, "requested scopes are least privilege",
          mints[0].get("scopes") == ["context:read", "session:write"],
          str(mints[0].get("scopes")))

    check(results, "connection file is private",
          connection.exists() and (connection.stat().st_mode & 0o077) == 0,
          oct(connection.stat().st_mode & 0o777) if connection.exists() else "missing")
    saved = json.loads(connection.read_text(encoding="utf-8")) if connection.exists() else {}
    check(results, "connection file carries the credential", bool(saved.get("token")) and bool(saved.get("harness_id")))
    check(results, "connection file matches the enrolled service", saved.get("base_url") == base_url,
          str(saved.get("base_url")))
    check(results, "member code was not persisted", code not in connection.read_text(encoding="utf-8"))

    overlay_text = overlay.read_text(encoding="utf-8") if overlay.exists() else ""
    check(results, "overlay created", bool(overlay_text))
    check(results, "overlay holds no credential",
          bool(overlay_text) and saved.get("token", "\0") not in overlay_text and code not in overlay_text)
    check(results, "overlay references the connection file by path", str(connection) in overlay_text)
    check(results, "overlay enables sharing explicitly", '"share_visible_turns": true' in overlay_text)

    # Re-running enrollment must refuse rather than mint a second credential.
    before = len([row for row in rows if row.get("plane") == "member" and row["endpoint"] == "harnesses"])
    repeat = subprocess.run(
        [
            sys.executable,
            str(checkout / "setup_teamwork.py"),
            "--base-url", base_url, "--project", "teamwork", "--bundle", "unused",
            "--connection-file", str(connection), "--output", str(overlay),
        ],
        input=f"{MEMBER_NAME}\n{code}\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    check(results, "second enrollment refuses to overwrite", repeat.returncode != 0,
          (repeat.stdout + repeat.stderr).strip().splitlines()[-1][:80] if (repeat.stdout or repeat.stderr) else "")
    check(results, "refused enrollment minted nothing", before == len(
        [row for row in rows if row.get("plane") == "member" and row["endpoint"] == "harnesses"]))
    return results


def assert_enrollment_artifacts(connection: Path, overlay: Path, base_url: str, code: str) -> list:
    """Enrollment checks available without the service's own request log."""
    results: list = []
    check(results, "connection file is private",
          connection.exists() and (connection.stat().st_mode & 0o077) == 0,
          oct(connection.stat().st_mode & 0o777) if connection.exists() else "missing")
    saved = json.loads(connection.read_text(encoding="utf-8")) if connection.exists() else {}
    check(results, "connection file carries the credential",
          bool(saved.get("token")) and bool(saved.get("harness_id")))
    check(results, "connection file matches the enrolled service", saved.get("base_url") == base_url,
          str(saved.get("base_url")))
    check(results, "member code was not persisted",
          connection.exists() and code not in connection.read_text(encoding="utf-8"))
    overlay_text = overlay.read_text(encoding="utf-8") if overlay.exists() else ""
    check(results, "overlay created", bool(overlay_text))
    check(results, "overlay holds no credential",
          bool(overlay_text) and saved.get("token", "\0") not in overlay_text and code not in overlay_text)
    check(results, "overlay enables sharing explicitly", '"share_visible_turns": true' in overlay_text)
    for name in ("sign-in preceded credential mint", "requested scopes are least privilege"):
        skip(results, name, "no request log from this service")
    return results


def read_log(log) -> list:
    if log is None or not Path(log).exists():
        return []
    return [json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines() if line.strip()]


def check(results: list, name: str, condition: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": bool(condition), "detail": detail})


def skip(results: list, name: str, reason: str) -> None:
    """Record an unobservable expectation as skipped; never as passed."""
    results.append({"check": name, "ok": True, "skipped": True, "detail": reason})


def assert_opt_in(rows: list, response_text: str, prompt: str = PROMPT,
                  expect_canary: bool = True, crowded: bool = False) -> list:
    """Assert the delivery boundary from the hook's own traffic."""
    results: list = []
    hook_rows = [row for row in rows if row.get("plane", "harness") == "harness" and row["credential_accepted"]]
    endpoints = [row["endpoint"] for row in hook_rows]

    check(results, "hook reached the service", bool(hook_rows), f"{len(hook_rows)} authorized requests")

    acks = [row for row in hook_rows if row["endpoint"] == "acknowledgements"]
    contexts = [row for row in hook_rows if row["endpoint"] == "context"]
    turns = [
        operation
        for row in hook_rows
        if row["endpoint"] == "publish"
        for operation in row["body"].get("operations", [])
        if operation.get("op") == "turn.upsert"
    ]

    check(results, "context fetched", bool(contexts))
    check(results, "receipt recorded", bool(acks))
    check(results, "turn published", bool(turns))
    if not (contexts and acks and turns):
        return results

    check(
        results,
        "no receipt before context attachment",
        endpoints.index("acknowledgements") > endpoints.index("context"),
        " -> ".join(endpoints),
    )
    check(
        results,
        "receipt precedes the published turn",
        [row["endpoint"] for row in hook_rows].index("acknowledgements")
        < next(i for i, row in enumerate(hook_rows) if row["endpoint"] == "publish" and any(
            operation.get("op") == "turn.upsert" for operation in row["body"].get("operations", [])
        )),
    )

    receipt = acks[0]["body"]
    check(results, "delivery method is the observed boundary",
          receipt.get("delivery_method") == "harness_input_accepted", str(receipt.get("delivery_method")))
    check(results, "receipt boundary is before_turn", receipt.get("boundary") == "before_turn")
    check(results, "receipt items are marked derived",
          bool(receipt.get("items")) and all(item.get("representation") == "derived" for item in receipt["items"]),
          f"{len(receipt.get('items', []))} items")

    injection = receipt.get("injection", {})
    published = turns[0]["data"].get("hook_injections", [])
    check(results, "published injection matches the acknowledged one",
          bool(published) and published[0].get("content_sha256") == injection.get("content_sha256"),
          str(injection.get("content_sha256", ""))[:16])

    rendered = injection.get("rendered_text", "")
    if expect_canary:
        check(results, "shared context reached the provider", CANARY in response_text,
              f"response={response_text[:60]!r}")
        check(results, "canary was present in the injection", CANARY in rendered)
    else:
        skip(results, "shared context reached the provider", "fixture canary not expected from a real service")
    check(results, "unprojected person field withheld", UNPROJECTED_MARKER not in rendered)
    check(results, "injection within the 10000-byte bound", len(rendered.encode()) <= 10000,
          f"{len(rendered.encode())} bytes")
    if crowded:
        # The kinds the SERVICE offered, taken from the stub's own fixture -- not
        # from the receipt, which lists only what was already chosen and would
        # therefore agree with the excerpt by construction.
        sys.path.insert(0, str(HERE))
        from stub_service import crowd_items, fixture_items

        offered = {item["record_type"] for item in fixture_items() + crowd_items()}
        seated = {line.split(":")[0] for line in rendered.splitlines() if line.count(":") >= 2}
        missing = sorted(offered - seated)
        check(results, "no record kind starved out of the excerpt", not missing,
              ("offered " + str(len(offered)) + ", missing: " + ", ".join(missing)) if missing
              else f"all {len(offered)} offered kinds seated")

    data = turns[0]["data"]
    check(results, "visible prompt published", data.get("user_prompt") == prompt)
    check(results, "visible response published",
          bool(data.get("agent_responses")) and bool(data["agent_responses"][0].get("text")))

    check(results, "harness plane used bearer only",
          not any(row["origin_header_present"] or row["cookie_header_present"] for row in hook_rows))
    check(results, "mutations carried idempotency keys",
          all(row["idempotency_key_present"] for row in hook_rows if row["endpoint"] != "context"))
    check(results, "context read carried no idempotency key",
          all(not row["idempotency_key_present"] for row in hook_rows if row["endpoint"] == "context"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default="/root/teamwork-e2e")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--ref", default="main", help="Git ref of the behavior source to install")
    parser.add_argument(
        "--base-bundle",
        default="git+https://github.com/microsoft/amplifier-foundation@main",
        help="Bundle the overlay composes onto",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--skip-opt-out", action="store_true", help="Skip the disabled-overlay control")
    parser.add_argument(
        "--crowded",
        action="store_true",
        help="Serve many records of one kind, and assert no kind is starved out of the excerpt.",
    )
    parser.add_argument(
        "--service-url",
        default="",
        help="Run against an already-running service instead of the built-in stub "
        "(a real server in another container, or a hosted deployment).",
    )
    parser.add_argument(
        "--request-log",
        default="",
        help="JSONL request log written by that service, if it produces one. Without it, "
        "traffic-derived checks are reported as SKIP rather than assumed to pass.",
    )
    parser.add_argument("--member-name", default=MEMBER_NAME, help="Identity used for enrollment")
    parser.add_argument("--member-code", default=MEMBER_CODE, help="Member code used for enrollment")
    parser.add_argument("--prompt", default=PROMPT, help="Prompt for the opted-in session")
    parser.add_argument(
        "--no-canary",
        dest="expect_canary",
        action="store_false",
        help="Do not require the fixture canary in the response (real services carry real data)",
    )
    parser.set_defaults(expect_canary=True)
    parser.add_argument(
        "--no-enroll",
        dest="enroll",
        action="store_false",
        help="Skip real enrollment and hand-write the connection file instead",
    )
    parser.set_defaults(enroll=True)
    args = parser.parse_args()

    workdir = Path(args.workdir).expanduser()
    workdir.mkdir(parents=True, exist_ok=True)
    external = bool(args.service_url)
    base_url = args.service_url or f"http://127.0.0.1:{args.port}"

    stub = None
    if external:
        # The service under test is already running: a real server in another
        # container, or a deployed one. Only what it reports can be asserted.
        log = Path(args.request_log) if args.request_log else None
    else:
        stub, log = start_stub(workdir, args.port, crowded=args.crowded)

    results: list = []
    try:
        if args.enroll:
            # Full path: clone as a participant would, then run the real
            # enrollment script against the stub's member plane.
            checkout = clone_repo(workdir, args.ref)
            connection, enabled_overlay = enroll(
                checkout, workdir, base_url, args.base_bundle, args.member_name, args.member_code
            )
            if log is not None:
                results.extend(
                    assert_enrollment(
                        read_log(log), connection, enabled_overlay, checkout, base_url, args.member_code
                    )
                )
            else:
                results.extend(
                    assert_enrollment_artifacts(connection, enabled_overlay, base_url, args.member_code)
                )
        else:
            connection = write_private(
                workdir / "private" / "harness-link.json",
                json.dumps(
                    {
                        "base_url": base_url,
                        "project_id": "teamwork",
                        "token": STUB_TOKEN,
                        "harness_id": "fixture-harness",
                    }
                ),
            )
            enabled_overlay = write_overlay(
                workdir / "overlay-enabled.yaml", args.base_bundle, args.ref, connection, True
            )
        disabled_overlay = write_overlay(
            workdir / "overlay-disabled.yaml", args.base_bundle, args.ref, connection, False
        )

        session_start = len(read_log(log)) if log is not None else 0
        result = run_session(enabled_overlay, args.prompt, args.timeout)
        if result.get("status") != "success":
            raise Failure(f"opted-in session did not succeed: {result}")
        check(results, "opted-in session succeeds", True, str(result.get("status")))
        if log is not None:
            opt_in_rows = read_log(log)[session_start:]
            results.extend(assert_opt_in(opt_in_rows, result.get("response", ""), args.prompt,
                                         args.expect_canary, args.crowded))
        else:
            for name in (
                "no receipt before context attachment",
                "receipt precedes the published turn",
                "published injection matches the acknowledged one",
                "unprojected person field withheld",
                "harness plane used bearer only",
            ):
                skip(results, name, "no request log from this service")

        if not args.skip_opt_out:
            before = len(read_log(log)) if log is not None else 0
            control = run_session(disabled_overlay, "Reply with OK.", args.timeout)
            after = read_log(log) if log is not None else []
            check(
                results,
                "opted-out session succeeds",
                control.get("status") == "success",
                str(control.get("status")),
            )
            if log is None:
                skip(results, "opted-out session emits nothing", "no request log from this service")
            else:
                check(
                    results,
                    "opted-out session emits nothing",
                    len(after) == before,
                    f"{len(after) - before} new requests",
                )
    finally:
        if stub is not None:
            stub.terminate()
            try:
                stub.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stub.kill()

    width = max(len(item["check"]) for item in results)
    for item in results:
        marker = "SKIP" if item.get("skipped") else ("PASS" if item["ok"] else "FAIL")
        detail = f"  ({item['detail']})" if item["detail"] else ""
        print(f"{marker}  {item['check']:<{width}}{detail}")

    failed = [item for item in results if not item["ok"]]
    skipped = [item for item in results if item.get("skipped")]
    summary = {
        "checks": len(results),
        "passed": len(results) - len(failed) - len(skipped),
        "skipped": len(skipped),
        "failed": len(failed),
        "request_log": str(log) if log is not None else None,
    }
    print("\n" + json.dumps(summary))
    if failed:
        print("Request log retained for inspection; no result is inferred for a failed check.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"FAIL  {error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
