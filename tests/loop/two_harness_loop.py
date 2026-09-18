"""THE LOOP, PROVEN OR NOT: harness A waits on a request; harness B answers it;
does A resume WITHOUT anyone typing?

Drives two independent TeamworkHook instances against a stub of the service.
A calls teamwork_wait(request_id=...) which BLOCKS. While A is blocked, B
records the answer. A must return "answered" on its own.

WHAT THIS ONE CANNOT PROVE. Its `retrieve()` reads a Python list, so it proves
the CLOCK -- that await_answer keeps looking with nobody typing -- and nothing
about the round trip. It would pass unchanged if the service rejected the
answer, if the delta page never carried the edit, or if the request were never
created. `live_two_harness.py` beside it runs the same shape against two real
credentials and the real endpoints; that is where the round trip is evidenced.
"""
import asyncio, sys, time
sys.path.insert(0, "modules/hooks-teamwork")
import amplifier_module_hooks_teamwork as tw
from amplifier_module_hooks_teamwork import WaitTool

REQ = "req-alpha"


class HookA:
    """Only what WaitTool + await_answer touch."""
    def __init__(self, shared):
        self.shared = shared
        self.lock = asyncio.Lock()
        self.presence_status = "unreported"
        self.waiting_reason = self.waiting_on = None
        self.looks = 0

    async def declare_wait(self, reason, request_id=None):
        self.waiting_reason, self.waiting_on = reason, request_id
        self.presence_status = "waiting"

    async def flush(self):
        return None

    async def retrieve(self):
        # Exactly what the real retrieve does for our purposes: read the
        # service, dispatch each record to note_answer.
        self.looks += 1
        for record in self.shared["records"]:
            self.note_answer(record)

    def note_answer(self, record):
        if not self.waiting_on or record.get("record_type") != "request":
            return
        if record.get("id") != self.waiting_on or record.get("change") in ("delete", "evict"):
            return
        content = record.get("content") if isinstance(record.get("content"), dict) else {}
        if content.get("response") in tw.ANSWER_LABELS:
            self.waiting_reason = self.waiting_on = None

    async def await_answer(self, seconds):
        return await tw.TeamworkHook.await_answer(self, seconds)


async def harness_b_answers(shared, after):
    """The SECOND, INDEPENDENT harness. Nobody types in A's session."""
    await asyncio.sleep(after)
    shared["records"].append({
        "record_type": "request", "id": REQ, "change": "upsert",
        "content": {"response": "act", "body": "yes, ship it"}})
    print("    [harness B] answered %s at t=%.1fs" % (REQ, time.monotonic() - shared["t0"]))


async def main():
    shared = {"records": [{"record_type": "request", "id": REQ, "change": "upsert",
                           "content": {"body": "may I ship?"}}],  # the question, NO response yet
              "t0": time.monotonic()}
    a = HookA(shared)
    tw.WAIT_POLL_SECONDS = 1

    print("  harness A: teamwork_wait(request_id=%s), blocking..." % REQ)
    asyncio.create_task(harness_b_answers(shared, after=3))
    start = time.monotonic()
    result = await WaitTool(a).execute({"reason": "waiting on a ship decision",
                                        "request_id": REQ, "seconds": 30})
    elapsed = time.monotonic() - start

    print("    [harness A] returned at t=%.1fs after %d looks" % (elapsed, a.looks))
    print("    success=%s state=%s observed=%s" % (
        result.success, result.output.get("state"), result.output.get("observed")))
    print("    note: %s" % result.output.get("note"))
    ok = result.success and result.output.get("state") == "answered" and elapsed < 10
    print("\n  RESULT: %s" % ("LOOP CLOSED -- A resumed on B's answer, nobody typed"
                              if ok else "LOOP BROKEN"))
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
