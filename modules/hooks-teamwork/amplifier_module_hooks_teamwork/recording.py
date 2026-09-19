"""Keeping a detected verdict, and putting it into the team's knowledge.

ONE RESPONSIBILITY: given a verdict somebody else detected, decide whether it is
new, keep it locally, publish it, and say what happened. It does not detect, does
not judge, does not know what a session or a turn is.

WHY IT IS NOT ON THE HOOK. With the publish welded into the judge, three jobs sat
in one function -- notice, persist, announce -- and "record deliberately, never
automatically" could only be honoured by switching detection off, losing the
observation along with the record. Subscribing to the detector's event makes this
removable on its own, and the detector keeps observing either way.

WHY IT TAKES COLLABORATORS AND NOT A HOOK. Everything it needs arrives as four
small things: a journal, a way to ask what binding is current, a way to publish,
and a way to report an outcome. So it is exercised with four fakes and no
session, no credential, no service and no coordinator -- which is the difference
between a test that documents this behaviour and a test that documents how to
build a TeamworkHook.
"""
import asyncio


class Recorder:
    """Subscribe to a detected verdict; keep it and publish it.

    journal   -- record_{decision,lesson}_fingerprint / release_... / record_decision_body
    binding   -- (detected payload) -> binding tuple, or None when it is no longer current
    publish   -- async (binding, fields) -> result with .success / .error / .output
    report    -- async (session_id, kind, outcome, tally_size, link_count)
    """

    KINDS = ("decision", "lesson")

    def __init__(self, journal, binding, publish, report):
        self.journal, self.binding, self.publish, self.report = journal, binding, publish, report

    def _reservation(self, kind):
        if kind == "decision":
            return self.journal.record_decision_fingerprint, self.journal.release_decision_fingerprint
        return self.journal.record_lesson_fingerprint, self.journal.release_lesson_fingerprint

    async def handle(self, event, data):
        """Handle one detected verdict. Returns the outcome it reported, or None.

        Returning the outcome is for the caller that wants to assert on it; the
        durable statement of what happened is the report, not this value.
        """
        data = data if isinstance(data, dict) else {}
        kind = data.get("kind")
        if kind not in self.KINDS:
            return None
        tally, links = data.get("tally_size"), data.get("link_count")
        binding = self.binding(data)
        # A verdict that arrives after the session moved on belongs to nobody:
        # publishing it would attribute one project's thinking to another. The
        # check needs what was captured when the window was judged, not what is
        # true now -- hence the payload.
        if not binding or binding[0] != data.get("session_id"):
            return await self._reported(data.get("session_id"), kind, "binding_changed", tally, links)
        session = binding[0]
        reserve, release = self._reservation(kind)
        # Atomic, and before any await. Unknown outcomes and cancellation retain
        # the reservation; only a definite refusal releases it.
        if not reserve(session, data.get("fingerprint")):
            return await self._reported(session, kind, "duplicate_fingerprint", tally, links)
        try:
            result = await self.publish(binding, {
                "claim": data.get("claim"), "basis": data.get("basis"),
                "confidence": data.get("confidence"), "limitations": data.get("limitations"),
                "evidence": data.get("evidence"), "title": data.get("title")})
        except BaseException as error:
            # Cancellation must propagate -- the reservation stays, and a retry
            # would otherwise publish the same claim twice.
            outcome = "write_cancelled" if isinstance(error, asyncio.CancelledError) else "write_raised"
            await self._reported(session, kind, outcome, tally, links)
            if outcome == "write_cancelled":
                raise
            return outcome
        error = result.error or {}
        if not result.success and error.get("outcome") != "unknown":
            release(session, data.get("fingerprint"))
            return await self._reported(session, kind, "refused", tally, links)
        # KEPT ON AN UNKNOWN ACCEPTANCE TOO, deliberately: the claim was judged
        # and sent, and a local copy of what we tried to say is exactly what a
        # later turn needs in order to find out whether it landed.
        self.journal.record_decision_body(
            session, data.get("fingerprint"), kind, data.get("claim"), data.get("basis"),
            data.get("confidence"), data.get("limitations"),
            (result.output or {}).get("recorded") if result.success
            else error.get("attempted_record_id"))
        return await self._reported(session, kind,
                                    "recorded" if result.success else "acceptance_unknown", tally, links)

    async def _reported(self, session, kind, outcome, tally, links):
        await self.report(session, kind, outcome, tally, links)
        return outcome


def subscribe(coordinator, hook, insight_tool, events):
    """Assemble a Recorder from the hook's collaborators and subscribe it.

    ONE ASSEMBLY POINT, used by `mount()` and by tests alike. The wiring is the
    part most likely to drift between how the bundle really runs and how it is
    exercised, so there is one of it. `insight_tool` and `events` are passed in
    rather than imported: this module must not depend on the package that
    depends on it.
    """
    async def publish(binding, fields):
        return await insight_tool(hook, binding=binding, automatic=True).execute(fields)

    def current(data):
        """The binding to publish under, or None if the moment has passed.

        Live object identities come from the hook NOW; the deliberate-record
        generation comes from what the detector captured. Both matter and they
        are not the same question: a rebind replaces the objects, while a person
        recording by hand mid-judgment bumps the generation, and either one means
        this verdict must not be written.
        """
        binding = hook.detection_binding()
        if not hook.detection_binding_current(binding):
            return None
        generation = data.get("generation")
        return (*binding[:4], generation if generation is not None else binding[4])

    recorder = Recorder(journal=hook.journal, binding=current, publish=publish,
                        report=hook.detection_outcome)
    for name in (events.DECISION_DETECTED, events.LESSON_DETECTED):
        coordinator.hooks.register(name, recorder.handle, priority=50,
                                   name="teamwork-record-" + name.split(":")[-1])
    return recorder
