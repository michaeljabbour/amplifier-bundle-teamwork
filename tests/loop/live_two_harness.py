"""THE LOOP AGAINST THE REAL SERVICE: harness A waits, harness B answers, does A resume?

The sibling `two_harness_loop.py` drives the wait against a hand-built stand-in for
the service: its `retrieve()` reads a Python list. That proves the CLOCK -- that
`await_answer` keeps looking without a human -- and it cannot prove the round trip.
It would pass unchanged if the service rejected the answer, if the delta page never
carried the edit, or if the request were never created at all.

This one uses two REAL credentials and the REAL endpoints. A's side is the shipped
code: a real `TeamworkHook`, its real `flush`/`retrieve`, the real `WaitTool`. B is a
second independently-minted harness credential, publishing the answer the way any
other participant would. Nobody types in A's session between the question and the
answer.

WHAT IT CANNOT PROVE. Both credentials belong to the same person, because that is
the only person whose enrollment code we hold. The service lets only the request's
recipient answer it (`core.py:650-652`, `Only the request recipient may respond`),
so the request here is addressed to that same person and the recipient check passes
trivially. Two harnesses, one person: this shows the answer crossing the service
between two independent sessions, NOT that a teammate's answer reaches you. That
one still needs a second person and is still unproven.

Opt-in, and never part of the unit suite -- it writes a real record to a real
shared project:

    TEAMWORK_CONNECTION_A=... TEAMWORK_CONNECTION_B=... \\
      python tests/loop/live_two_harness.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, "modules/hooks-teamwork")
from amplifier_module_hooks_teamwork import HTTPClient, Journal, SyncError, TeamworkHook, WaitTool

ANSWER_AFTER = 12
HOLD_SECONDS = int(os.environ.get("TEAMWORK_LOOP_HOLD") or 120)
# THE NEGATIVE CONTROL, and the reason it is a flag rather than a comment: a
# check that cannot produce the failure it is looking for is not evidence. With
# this set, B never answers and A must come back "waiting" -- if it reports
# "answered" anyway, the run above proved nothing about the answer.
NO_ANSWER = os.environ.get("TEAMWORK_LOOP_NO_ANSWER") == "1"


class Coordinator:
    """Only what the hook reads off its host."""
    parent_id = None

    def __init__(self):
        self.mount_points = {}
        self.session_id = str(uuid.uuid4())
        self.context = Context()

    def get(self, name):
        return self.context if name == "context" else None

    def get_capability(self, name):
        return None

    async def mount(self, point, value, name):
        return None


class Context:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages(self):
        return list(self.messages)


def load(variable):
    path = os.environ.get(variable)
    if not path:
        raise SystemExit(
            "%s is required: this test writes to a real shared project, so it never "
            "guesses a credential" % variable)
    return json.loads(Path(path).expanduser().read_text())


def stamp(t0):
    return "t=%5.1fs" % (time.monotonic() - t0)



async def main():
    connection_a, connection_b = load("TEAMWORK_CONNECTION_A"), load("TEAMWORK_CONNECTION_B")
    if connection_a["token"] == connection_b["token"]:
        raise SystemExit("A and B are the same credential; that proves nothing about two harnesses")

    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="teamwork-live-loop-"))
    hook = TeamworkHook(Coordinator(), connection_a, Journal(work / "a.sqlite3"))
    client_b = HTTPClient(connection_b)

    print("%s harness A session %s" % (stamp(t0), hook.sid))
    await hook.on_start("session:start", {})
    print("%s harness A registered: agent=%s" % (stamp(t0), hook.agent_status))

    # The question. Addressed to the person this project knows A as -- which the
    # service told us at enrollment, and which B shares (see the docstring).
    person = connection_a.get("person_id") or os.environ.get("TEAMWORK_PERSON_ID")
    if not person:
        raise SystemExit("TEAMWORK_PERSON_ID is required: the request must name its recipient")
    request_id = str(uuid.uuid4())
    hook.client.request("publish", {"operations": [{
        "op": "request.upsert", "id": request_id, "expected_version": 0,
        "data": {
            "title": "Verification probe: does a waiting harness resume on a real answer?",
            "description": (
                "Raised by an automated end-to-end check (tests/loop/live_two_harness.py), "
                "not by a person, and it needs nothing from anyone: a second harness answers "
                "it seconds later. It is here because the claim -- that a session holding "
                "teamwork_wait resumes when the answer lands on the service -- had only ever "
                "been checked against a stand-in for the service."),
            "requested_person_id": person,
            "urgency": "low",
            "desired_response": "context"}}]}, str(uuid.uuid4()))
    print("%s harness A asked %s" % (stamp(t0), request_id))

    def answer(client, rid, note, version=1):
        """Record the answer, rebasing once if the record moved underneath us.

        B reads nothing first, on purpose: /context is scoped to the CALLER'S OWN
        session (`core.py:own_session`), and B has none -- it never started one,
        because answering does not need one. A blind read cost a whole live run
        with a 404 that named the session rather than the mistake. The service
        already reports the version it has on a conflict, so ask it the only way
        that cannot be stale: by writing.
        """
        data = {"response": "context", "progress_note": note}
        try:
            client.request("publish", {"operations": [{
                "op": "request.upsert", "id": rid,
                "expected_version": version, "data": data}]}, str(uuid.uuid4()))
            return version
        except SyncError as error:
            actual = ((error.body or {}).get("error") or {}).get("current_version")
            if error.status != 409 or not isinstance(actual, int):
                raise
            client.request("publish", {"operations": [{
                "op": "request.upsert", "id": rid,
                "expected_version": actual, "data": data}]}, str(uuid.uuid4()))
            return actual

    async def harness_b_answers():
        """The SECOND, INDEPENDENT credential. Nobody types in A's session."""
        await asyncio.sleep(ANSWER_AFTER)
        version = await asyncio.to_thread(
            answer, client_b, request_id, "Answered by the second harness, to close the loop.")
        print("%s harness B answered (version %s)" % (stamp(t0), version))

    answering = asyncio.create_task(asyncio.sleep(0) if NO_ANSWER else harness_b_answers())
    print("%s harness A: teamwork_wait(request_id=..., seconds=%d), holding...%s"
          % (stamp(t0), HOLD_SECONDS, " (NEGATIVE CONTROL: nobody will answer)" if NO_ANSWER else ""))
    started = time.monotonic()
    result = await WaitTool(hook).execute({
        "reason": "waiting on the verification probe", "request_id": request_id,
        "seconds": HOLD_SECONDS})
    elapsed = time.monotonic() - started
    await answering

    state = (result.output or {}).get("state")
    print("%s harness A returned after %.1fs: success=%s state=%s"
          % (stamp(t0), elapsed, result.success, state))
    print("   note: %s" % ((result.output or {}).get("note") or (result.error or {}).get("message")))

    resumed = bool(result.success) and state == "answered"
    ontime = elapsed < HOLD_SECONDS - 5
    if NO_ANSWER:
        held = bool(result.success) and state == "waiting" and not ontime
        print("\nRESULT: %s" % (
            "CONTROL HELD -- with no answer, A held its full %ds and said so" % HOLD_SECONDS
            if held else
            "CONTROL FAILED -- A reported state=%s after %.1fs with nobody answering; "
            "the positive run proves nothing" % (state, elapsed)))
        return 0 if held else 1
    print("\nRESULT: %s" % (
        "LOOP CLOSED -- A resumed on B's answer, through the live service, nobody typed"
        if resumed and ontime else
        "LOOP OPEN -- A did not resume on the answer (state=%s, %.1fs)" % (state, elapsed)))
    print("EVIDENCE: request %s, session %s" % (request_id, hook.sid))
    return 0 if resumed and ontime else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SyncError as error:
        raise SystemExit("service refused the run: HTTP %s %s" % (error.status, error.body))
