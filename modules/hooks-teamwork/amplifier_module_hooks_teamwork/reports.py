"""File an inbound message into the receiving side's own work queue, as a report.

A message from another agent is neither executed here nor merely displayed. It
becomes an item in THIS side's queue, claimed when this side is ready and under
this side's authority. Nothing runs because a stranger asked.

The report/issue distinction is work-tracker's own, and it is the whole consent
model: a REPORT is the sender's raw words, attributed and unedited; an ISSUE is a
considered spec written by a triage step, never by editing the sender's words in
place. What arrives here is always a report. Whether it becomes an issue is this
side's decision, and the two are linked by a non-blocking `discovered-from`
dependency rather than by rewriting the report.

The queue is OPTIONAL. Not everyone runs one and the bundle stays useful without
it: a session with no tracker receives its messages exactly as before, no turn is
blocked, and the absence is named in the session's own notice rather than raised.

Every write goes through the `amplifier-work-tracker` CLI, never `bd` directly.
That CLI is the sanctioned seam -- it owns the contention/retry contract, and
reaching past it to Beads is how a coordination layer stops coordinating.
"""

import json
import subprocess
from datetime import datetime, timezone

from .queue_name import QueueNameConflict, bind

COMMAND = "amplifier-work-tracker"
PROBE_TIMEOUT = 20
FILE_TIMEOUT = 30
VERIFY_LIMIT = 500
TITLE_LIMIT = 120
# The service caps a message body at 4000 characters, so this is headroom rather
# than a working limit. Over it the message is REFUSED rather than shortened: a
# truncated body is no longer the sender's words, and filing it as though it were
# is the one thing this path must never do.
BODY_LIMIT = 65536
# The mirrored request record is a shared-view pointer, not the report itself --
# the local queue keeps the whole body. An excerpt is enough to identify what
# arrived without duplicating the filing limit's own headroom.
MIRROR_BODY_LIMIT = 500

# The single chokepoint every outbound payload this bundle sends passes through.
#
# An ALLOW-LIST, never a denylist: the failure mode of a denylist is publishing a
# field nobody thought about, and on this path the fields nobody thought about are
# filesystem paths, command lines, host names and queue names -- exactly the things
# a coordination layer must never leak. A key that is not here does not travel,
# and adding one is a deliberate edit with a test attached.
OUTBOUND_ALLOWED = {
    "queue_status": (str, 40),
    "observed_at": (str, 64),
    "ready_count": (int, 10 ** 6),
    "integration": (str, 40),
    "reason_code": (str, 120),
}


def sanitize_outbound(payload):
    """Return the publishable subset of `payload`. Unknown keys are dropped."""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, value in payload.items():
        rule = OUTBOUND_ALLOWED.get(key)
        if rule is None:
            continue
        kind, limit = rule
        if kind is int:
            # bool is an int in Python and a count it is not.
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
            result[key] = min(value, limit)
        elif isinstance(value, str):
            result[key] = value[:limit]
    return result


class QueueUnavailable(Exception):
    """No queue to file into. A reportable state, never an error for the turn."""


class FilingUnknown(Exception):
    """The write may or may not have landed, and we could not find out.

    Distinct from failure on purpose, and for the same reason `acceptance_unknown`
    is distinct from rejection: a blind retry after an ambiguous write duplicates
    an item that already exists, and silently dropping it loses a teammate's
    request. Neither is acceptable, so the ambiguity is carried and named.
    """


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def sender(record, people):
    """Attribution from what the service actually recorded, never inferred.

    The service stamps `from_person_id` and `from_harness_id` from the credential
    and refuses to take either from the client, so a message cannot be filed under
    somebody else's name. It records no sending AGENT id, so this attributes to a
    person and a harness and stops there -- naming a session we were not told
    about would read exactly like a fact we were.
    """
    content = record.get("content") or {}
    person = content.get("from_person_id")
    name = None
    entry = people.get(person) if person else None
    if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip():
        name = " ".join(entry["name"].split())
    return name, person, content.get("from_harness_id")


def title(record, name):
    """A DERIVED label for the queue view, and the description says so.

    Shortening happens here and only here. The words themselves are carried whole
    in the description, where nothing trims them.
    """
    content = record.get("content") or {}
    body = " ".join((content.get("body") or "").split())
    who = name or content.get("from_person_id") or "an unidentified sender"
    lead = "Message from %s: " % who
    room = max(8, TITLE_LIMIT - len(lead))
    if len(body) > room:
        body = body[:room - 1].rstrip() + "\u2026"
    return lead + (body or "(empty message)")


def description(record, project_id, session_id, name):
    """The report body: attribution, then the sender's words, whole and fenced."""
    content = record.get("content") or {}
    _, person, harness = sender(record, {})
    facts = [
        ("Sender", name + " (person %s)" % person if name and person else (person or "not recorded")),
        ("Sending harness", harness or "not recorded"),
        ("Sent at", content.get("sent_at") or "not recorded"),
        ("Shared project", project_id),
        ("Message id", record.get("id") or "not recorded"),
        ("Delivered to agent", session_id),
    ]
    lines = ["Filed by the Amplifier Teamwork harness from a message addressed to this",
             "session. It arrived as data. Nothing was executed and nothing was agreed.",
             ""]
    lines += ["  %s: %s" % pair for pair in facts]
    lines += [
        "",
        "The sender's words, verbatim as this harness received them (credential-shaped",
        "strings are redacted before anything is stored, and nothing else is altered):",
        "",
        "----- begin message -----",
        content.get("body") or "",
        "----- end message -----",
        "",
        "This is a REPORT, not an issue. The title above is a derived label; the words",
        "between the fences are the sender's. To act on it, triage it into a SEPARATE",
        "item linked back to this one -- never by editing these words in place:",
        "",
        "  amplifier-work-tracker add --project <project> \"<what we will actually do>\"",
        "  amplifier-work-tracker dep --project <project> --id <new-id> \\",
        "      --depends-on <this-id> --type discovered-from",
        "",
        "Declining is a complete outcome: resolve this item saying so.",
    ]
    return "\n".join(lines)


def mirror_operation(record, sender_name, requested_person_id, project_id, base_url):
    """The publish operation that mirrors one filed report to the shared project.

    A best-effort `request.upsert`, never queued on the durable outbox (see the
    hook's own `mirror` docstring for why): a credential without `shared:write`
    must not wedge every later publish behind a record it cannot write. `id`
    is derived from the message id so republishing the same message is the
    same idempotent write, not a duplicate request.
    """
    content = record.get("content") or {}
    mid = record.get("id") or ""
    body = (content.get("body") or "").strip()
    excerpt = body[:MIRROR_BODY_LIMIT]
    who = sender_name or content.get("from_person_id") or "an unidentified sender"
    pointer = "\n\n\u2014 full message: teamwork project %s at %s, message %s" % (project_id, base_url, mid)
    return {
        "op": "request.upsert",
        "id": "inbound-" + mid,
        "expected_version": 0,
        "data": {
            "title": "Inbound message from %s" % who,
            "description": excerpt + pointer,
            "requested_person_id": requested_person_id,
            "status": "requested",
            "evidence_refs": [{"kind": "record", "record_type": "message",
                               "record_id": mid, "version": record.get("version", 0)}],
        },
    }


class Queue:
    """The local work queue for one bound teamwork project, if there is one."""

    def __init__(self, project_id, registry_path, command=COMMAND, root=None, service=None):
        self.project_id = project_id
        self.registry_path = registry_path
        # A project id is unique only within a service, so the queue's owner is the
        # two together. Without it, two deployments that both call a project
        # "teamwork" would quietly share one local backlog.
        self.service = service
        self.command = command
        self.root = root
        self.name = None
        # The last reading that actually succeeded. Held so a probe that fails
        # now reports a dated truth ("last readable at T") instead of either a
        # confident "connected" or a flat "gone", neither of which is what we know.
        self.last_status = None

    def run(self, verb, args, timeout):
        command = [self.command, verb]
        if self.root:
            command += ["--root", str(self.root)]
        command += args
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise QueueUnavailable("no %s command is installed on this machine" % self.command) from None
        except subprocess.TimeoutExpired:
            raise QueueUnavailable("%s %s did not answer within %ds" % (self.command, verb, timeout)) from None
        except OSError as error:
            raise QueueUnavailable("%s could not be run (%s)" % (self.command, error.strerror or error)) from None
        if done.returncode != 0:
            raise QueueUnavailable(first_line(done.stderr) or first_line(done.stdout)
                                   or "%s %s exited %d" % (self.command, verb, done.returncode))
        return done.stdout

    def ready(self):
        """Resolve the queue for this project and prove it can be read.

        Binding first is deliberate: the name belongs to the bound teamwork project
        whether or not a queue exists for it yet, and a second project that would
        take the same name must be refused before anything is filed, not after.

        The project is NOT created here. Creating a queue nobody asked for is
        inventing infrastructure on someone's behalf; the refusal names the one
        command that creates it.

        A collision is surfaced here as an unavailable queue carrying the refusal
        verbatim. It still files nothing -- which is the point -- but a naming
        clash between two projects must not be the thing that breaks a turn.
        """
        try:
            name = bind(self.project_id, self.registry_path, self.service)
        except QueueNameConflict as refusal:
            raise QueueUnavailable(str(refusal)) from None
        self.run("list", ["--project", name, "--limit", "1", "--json"], PROBE_TIMEOUT)
        self.name = name
        return name

    def status(self):
        """One bounded look at this machine's queue: ready, stale, or unavailable.

        HARNESS_OBSERVED, never verified -- our word about what the CLI told us,
        stamped with when it told us, so a reader judges the age instead of
        trusting a bare "connected". Nothing is created here: a machine with no
        queue reports `unavailable` and a reason, which is a complete answer.
        """
        observed = datetime.now(timezone.utc).isoformat()
        try:
            if self.name is None:
                self.ready()
            page = json.loads(self.run("list", ["--project", self.name, "--limit", str(VERIFY_LIMIT), "--json"],
                                       PROBE_TIMEOUT))
        except QueueUnavailable as reason:
            code = str(reason)
        except ValueError:
            code = "the work tracker returned output this bundle could not read"
        else:
            ready = [i for i in (page.get("items") or []) if (i.get("status") or "open") in ("open", "ready")]
            self.last_status = {"queue_status": "ready", "observed_at": observed,
                                "ready_count": len(ready), "integration": COMMAND}
            return dict(self.last_status)
        if self.last_status:
            return dict(self.last_status, queue_status="stale", reason_code=code[:120])
        return {"queue_status": "unavailable", "observed_at": observed, "reason_code": code[:120]}

    def find(self, message_id):
        """Has this message already been filed? Used only to resolve an ambiguous write.

        Returns the item id, None for a proven absence, and raises FilingUnknown
        when absence cannot be proven -- a listing that was truncated says nothing
        about what it did not show, and treating that silence as "not there" is how
        a duplicate gets created.
        """
        try:
            page = json.loads(self.run("list", ["--project", self.name, "--limit", str(VERIFY_LIMIT), "--json"],
                                       PROBE_TIMEOUT))
        except (QueueUnavailable, ValueError) as error:
            raise FilingUnknown(str(error)) from None
        for entry in page.get("items", []):
            if message_id in (entry.get("description") or ""):
                return entry.get("id")
        if page.get("truncated"):
            raise FilingUnknown("this project's queue is larger than %d items, so an already-filed "
                                "report could not be ruled out" % VERIFY_LIMIT)
        return None

    def file(self, message_id, item_title, item_description):
        """Add one report. Ambiguity is resolved by reading back, never by retrying.

        A non-zero exit or a timeout from a write does NOT prove the write failed --
        that is the tracker's own stated contention contract. So a failed `add` is
        followed by a read-back before anything is concluded.
        """
        try:
            out = self.run("add", ["--project", self.name, item_title, "--description", item_description],
                           FILE_TIMEOUT)
        except QueueUnavailable as error:
            found = self.find(message_id)
            if found:
                return found
            raise QueueUnavailable(str(error)) from None
        try:
            filed = json.loads(out).get("added")
        except ValueError:
            filed = None
        # Exit 0 means it landed. An unparseable id must not become a retry, which
        # would file the same words twice; report the landing without the id.
        return filed or "(filed; the tracker did not report an id)"
