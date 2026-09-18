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
from amplifier_module_hooks_teamwork import (AnswerTool, Journal, SyncError, TasksTool,
                                             TeamworkHook, WaitTool)

ANSWER_AFTER = 12
HOLD_SECONDS = int(os.environ.get("TEAMWORK_LOOP_HOLD") or 120)
# THE NEGATIVE CONTROL, and the reason it is a flag rather than a comment: a
# check that cannot produce the failure it is looking for is not evidence. With
# this set, B never answers and A must come back "waiting" -- if it reports
# "answered" anyway, the run above proved nothing about the answer.
NO_ANSWER = os.environ.get("TEAMWORK_LOOP_NO_ANSWER") == "1"
QUESTION_TITLE = "Verification probe: does a waiting harness resume on a real answer?"
ANSWER_NOTE = ("Yes -- answered from a second session with teamwork_answer, "
               "nobody typing in either.")


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



async def run(connection_a, connection_b, work, owned, tasks):
    if connection_a["token"] == connection_b["token"]:
        raise SystemExit("A and B are the same credential; that proves nothing about two harnesses")

    t0 = time.monotonic()
    hook = TeamworkHook(Coordinator(), connection_a, Journal(work / "a.sqlite3"))
    # B is a REAL session too, not a raw client: it has to SEE the question before
    # it can honestly be said to have answered one.
    hook_b = TeamworkHook(Coordinator(), connection_b, Journal(work / "b.sqlite3"))
    owned.extend((hook, hook_b))

    print("%s harness A session %s" % (stamp(t0), hook.sid))
    await hook.on_start("session:start", {})
    await hook_b.on_start("session:start", {})
    print("%s harness A registered: agent=%s / harness B session %s"
          % (stamp(t0), hook.agent_status, hook_b.sid))

    # The question. Addressed to the person this project knows A as -- which the
    # service told us at enrollment, and which B shares (see the docstring).
    person = connection_a.get("person_id") or os.environ.get("TEAMWORK_PERSON_ID")
    if not person:
        raise SystemExit("TEAMWORK_PERSON_ID is required: the request must name its recipient")
    request_id = str(uuid.uuid4())
    await asyncio.to_thread(hook.client.request, "publish", {"operations": [{
        "op": "request.upsert", "id": request_id, "expected_version": 0,
        "data": {
            "title": QUESTION_TITLE,
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

    async def harness_b_sees_and_answers():
        """The SECOND session: find the question the way a session actually can, then answer it.

        B looks with teamwork_tasks, which asks the service for the records
        addressed to this person. It does NOT look in the rendered excerpt: that
        is a bounded best-effort sample -- measured here at 12 of 221 records,
        with a request addressed to that very session not among them -- so a
        question found there is luck, and a question missing from there is not
        evidence of anything. The pull is deterministic; the excerpt is ambient.
        """
        await asyncio.sleep(ANSWER_AFTER)
        listed = await TasksTool(hook_b).execute({})
        assigned = (listed.output or {}).get("assigned", [])
        mine = next((w for w in assigned if w.get("id") == request_id), None)
        print("%s harness B listed %d records addressed to it; the question is among them: %s"
              % (stamp(t0), len(assigned), bool(mine)))
        if not mine:
            print("   B was never handed the question -- answering it would prove nothing")
            return False
        result = await AnswerTool(hook_b).execute({
            "request_id": request_id, "response": "context", "note": ANSWER_NOTE})
        print("%s harness B answered with teamwork_answer: success=%s %s"
              % (stamp(t0), result.success, "recorded" if result.success else "refused"))
        return bool(result.success)

    answering = asyncio.create_task(asyncio.sleep(0) if NO_ANSWER else harness_b_sees_and_answers())
    tasks.append(answering)
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
    # RESUMING IS NOT THE SAME AS BEING TOLD, and only one of the two is what a
    # caller needs. `state=answered` says the wait ended. The ANSWER has to come
    # back on the same channel the caller was already awaiting -- the tool result
    # -- because the shared excerpt is allowed to drop it and was measured doing
    # exactly that.
    answer = (result.output or {}).get("answer") or {}
    told = answer.get("note") == ANSWER_NOTE and answer.get("response") == "context"
    print("%s the answer came back IN THE TOOL RESULT: %s" % (stamp(t0), bool(told)))
    if answer:
        print("   %s (%s) -- %s" % (answer.get("response"), answer.get("means"), answer.get("note")))

    print("\nRESULT: %s" % (
        "LOOP CLOSED -- B was handed the question, answered it with teamwork_answer, A resumed on "
        "its own and the answer's own words came back in A's tool result. Nobody typed."
        if resumed and ontime and told else
        "LOOP INCOMPLETE -- resumed=%s ontime=%s answer-reached-the-caller=%s"
        % (resumed, ontime, told)))
    print("EVIDENCE: request %s, A session %s, B session %s"
          % (request_id, hook.sid, hook_b.sid))
    return 0 if resumed and ontime and told else 1


async def main():
    connection_a, connection_b = load("TEAMWORK_CONNECTION_A"), load("TEAMWORK_CONNECTION_B")
    if (connection_a["base_url"], connection_a["project_id"]) != (connection_b["base_url"], connection_b["project_id"]):
        raise SystemExit("Both explicit connections must name the same service and QA project")
    owned, tasks = [], []
    with tempfile.TemporaryDirectory(prefix="teamwork-live-loop-") as work:
        try:
            return await asyncio.wait_for(run(connection_a, connection_b, Path(work), owned, tasks),
                                          timeout=min(HOLD_SECONDS, 120) + 120)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for hook in owned:
                try:
                    await asyncio.wait_for(hook.cleanup(), timeout=10)
                except (TimeoutError, SyncError):
                    print("Shared-session cleanup not confirmed; inspect the isolated QA session.")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SyncError as error:
        raise SystemExit("service refused the run: HTTP %s" % error.status)
    except Exception as error:
        raise SystemExit("loop check failed (%s); private details omitted" % type(error).__name__)
