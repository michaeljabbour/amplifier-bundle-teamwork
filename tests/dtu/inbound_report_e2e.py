"""Inbound message -> report in the receiving side's own queue, end to end in a DTU.

Everything here is real: a live teamwork service, two separately enrolled harness
credentials, the bundle's own hook, and a local work queue standing on the pinned
bd/dolt that `setup_work_tracker.sh` installed. Nothing is stubbed except the
model itself -- this asserts on what reached the model's context, not on what the
model then said, because a model's reply is not evidence about delivery.

Run inside a container that has both halves:

    sh  tests/dtu/setup_work_tracker.sh <queue-name>
    python3 tests/dtu/inbound_report_e2e.py <sender-conn-dir> <recipient-conn-dir>

Pass --no-queue instead, in a container where no tracker was set up, to assert the
other half of the promise: the message still arrives, the turn is not blocked, and
the absence is named in the session's notice rather than raised as an error.

Connection directories are the ones enrollment wrote, each holding a 0600
connection.json. The two must be DIFFERENT enrollments: a message the service
would not deliver across a real boundary proves nothing about one.
"""

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "modules" / "hooks-teamwork"))

from amplifier_module_hooks_teamwork import HTTPClient, Journal, TeamworkHook, uid
from amplifier_module_hooks_teamwork.queue_name import normalise
from amplifier_module_hooks_teamwork.reports import Queue

CANARY = "CANARY-" + uuid.uuid4().hex[:8].upper()
BODY = (CANARY + " Please add jitter to the relay backoff before Thursday; we saw three "
        "consecutive drops in prod and the retries all landed together.")


class Context:
    """Stands in for the model's context. Records exactly what was handed to it."""

    def __init__(self):
        self.seen = []

    async def add_message(self, message):
        self.seen.append(message)


class Coordinator:
    # Fresh every run, as a real session id is. A fixed one would derive the same
    # shared-session id each time and collide with the version the service already
    # holds -- which reads as a service fault rather than as a reused id.
    session_id = "dtu-inbound-report-e2e-" + uuid.uuid4().hex

    def __init__(self, context):
        self.context = context
        self.mount_points = {}

    def get(self, name):
        return self.context if name == "context" else None


def tracker(queue, *args):
    out = subprocess.run(["amplifier-work-tracker", *args, "--project", queue],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit("amplifier-work-tracker %s failed: %s" % (args[0], out.stderr.strip()))
    return out.stdout


def read_item(queue, item_id):
    page = json.loads(tracker(queue, "list", "--id", item_id, "--json"))
    return page["items"][0]


def check(label, ok, detail=""):
    print("  %-52s %s%s" % (label, "PASS" if ok else "FAIL", (" -- " + detail) if detail else ""))
    return bool(ok)


async def main():
    sender_dir, recipient_dir = sys.argv[1], sys.argv[2]
    expect_queue = "--no-queue" not in sys.argv[3:]
    sender = json.loads((Path(sender_dir) / "connection.json").read_text())
    recipient = json.loads((Path(recipient_dir) / "connection.json").read_text())
    if sender["token"] == recipient["token"]:
        raise SystemExit("sender and recipient must be different enrollments")

    work = Path("/tmp/tw-e2e-" + uuid.uuid4().hex[:8])
    work.mkdir(parents=True)
    queue = normalise(recipient["project_id"])

    context = Context()
    hook = TeamworkHook(
        Coordinator(context), recipient, Journal(work / "outbox.sqlite3"),
        filing=Queue(recipient["project_id"], work / "queue-names.json",
                     service=recipient["base_url"]))

    # The real registration path: this is what makes the session addressable, and
    # the id the sender addresses is the one the hook computed for itself.
    await hook.on_start("session:start", {})
    print("recipient agent: %s (%s)" % (hook.sid, hook.agent_status))
    if hook.agent_status != "registered":
        raise SystemExit("the recipient never registered; there is nothing to address")

    HTTPClient(sender).request(
        "publish",
        {"operations": [{"op": "message.upsert", "id": uid(), "expected_version": 0,
                         "data": {"to_agent_id": hook.sid, "body": BODY}}]}, uid())
    print("sent one message from a different enrollment")

    before = ({i["id"] for i in json.loads(tracker(queue, "list", "--limit", "500", "--json"))["items"]}
              if expect_queue else set())
    notice = await hook.on_submit("prompt:submit", {"prompt": "what has come in for me?"})
    await hook.on_complete("prompt:complete", {"response": "Noted; nothing acted on."})
    print("\n--- notice shown to the session ---\n%s\n" % (notice.user_message or "(none)"))

    if not expect_queue:
        # The optional half of the dependency. Absence is a state to report, never
        # an error, and never a reason to withhold what a teammate sent.
        ok = check("the message still reached the model's context",
                   any(CANARY in m.get("content", "") for m in context.seen))
        ok &= check("the turn completed and returned a notice", notice is not None)
        ok &= check("the absence is named in the notice",
                    "no local work queue took them" in (notice.user_message or ""))
        ok &= check("nothing was recorded as filed", not hook.state.get("filed"))
        await hook.on_complete("prompt:complete", {"response": "Noted."})
        ok &= check("the absence is not repeated every turn",
                    "no local work queue" not in
                    ((await hook.on_submit("prompt:submit", {"prompt": "again?"})).user_message or ""))
        print("\nRESULT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1

    filed = [item for item in hook.state.get("filed", {}).values() if item.startswith(queue + "-")]
    ok = check("exactly one report was filed", len(filed) == 1, str(filed))
    if not ok:
        raise SystemExit(1)
    report = read_item(queue, filed[0])

    ok &= check("the message reached the model's context",
                any(CANARY in m.get("content", "") for m in context.seen))
    ok &= check("the report is new, not a pre-existing item", report["id"] not in before)
    ok &= check("the report is open and unheld",
                report["status"] == "open" and not report["holder"])
    fenced = report["description"].split("----- begin message -----\n")[1]
    fenced = fenced.split("\n----- end message -----")[0]
    ok &= check("the sender's words are stored verbatim", fenced == BODY)
    ok &= check("the report names itself a report, not an issue",
                "This is a REPORT, not an issue" in report["description"])
    ok &= check("the sender is attributed", "Sender:" in report["description"])

    # Idempotency: a second turn must not file the same words again.
    await hook.on_submit("prompt:submit", {"prompt": "and again?"})
    await hook.on_complete("prompt:complete", {"response": "Still nothing acted on."})
    again = [i for i in json.loads(tracker(queue, "list", "--limit", "500", "--json"))["items"]
             if i["id"] not in before and i["id"] != report["id"]]
    ok &= check("a second turn files no duplicate", not again, str([i["id"] for i in again]))

    # Triage: a SEPARATE object, linked, with the report's words left alone.
    issue = json.loads(tracker(queue, "add", "Jitter the relay backoff",
                               "--description", "Triaged from a teammate's report. Our words."))["added"]
    tracker(queue, "dep", "--id", issue, "--depends-on", report["id"], "--type", "discovered-from")
    links = json.loads(tracker(queue, "dep", "--id", issue))["links"]
    ok &= check("triage produced a separate item", issue != report["id"])
    ok &= check("linked discovered-from the report, non-blocking",
                any(link["id"] == report["id"] and link["type"] == "discovered-from"
                    and not link["blocking"] for link in links))
    ok &= check("triage left the report's words untouched",
                read_item(queue, report["id"])["description"] == report["description"])

    print("\nreport %s, issue %s, canary %s" % (report["id"], issue, CANARY))
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
