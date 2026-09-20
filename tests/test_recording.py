"""The recorder, exercised with four fakes and no hook.

That is the point of it being a separate component. Every test below builds the
thing under test in three lines: no session, no credential, no coordinator, no
service, no journal file. If keeping a detected verdict ever needs more setup
than this to test, the separation has been lost.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import recording

BINDING = ("sess-1", {"state": True}, object(), {"connection": True}, 0)


class Journal:
    """Only the four methods the recorder touches."""

    def __init__(self, reserved=True):
        self.reserved, self.released, self.bodies, self._accept = [], [], [], reserved
        self.outcomes = []

    def record_decision_fingerprint(self, sid, fp):
        self.reserved.append((sid, fp))
        return self._accept

    def release_decision_fingerprint(self, sid, fp):
        self.released.append((sid, fp))

    record_lesson_fingerprint = record_decision_fingerprint
    release_lesson_fingerprint = release_decision_fingerprint

    def record_decision_body(self, *args, **kwargs):
        self.bodies.append(args)
        self.outcomes.append(kwargs["outcome"])


def result(success=True, output=None, error=None):
    return SimpleNamespace(success=success, output=output or {}, error=error)


def build(journal=None, publish=None, binding=BINDING):
    journal = journal or Journal()
    reports = []

    async def report(*args):
        reports.append(args)

    async def default_publish(_binding, _fields):
        return result(output={"recorded": "rec-1"})

    recorder = recording.Recorder(journal=journal, binding=lambda data: binding,
                                  publish=publish or default_publish, report=report)
    return recorder, journal, reports


def detected(**overrides):
    payload = {"session_id": "sess-1", "kind": "decision", "fingerprint": "fp-1",
               "claim": "We ship single-tenant first.", "basis": "inference",
               "confidence": "high", "limitations": "One customer.",
               "evidence": [], "tally_size": 3, "link_count": 1, "generation": 0}
    payload.update(overrides)
    return payload


class ADetectedVerdictBecomesTeamKnowledge(unittest.IsolatedAsyncioTestCase):
    async def test_it_publishes_and_keeps_the_decision(self):
        recorder, journal, reports = build()
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "recorded")
        self.assertEqual(journal.bodies[0][3], "We ship single-tenant first.")
        self.assertEqual(journal.bodies[0][-1], "rec-1")
        self.assertEqual(reports[-1][2], "recorded")

    async def test_the_publish_receives_what_was_detected(self):
        seen = {}

        async def publish(binding, fields):
            seen.update(fields)
            return result()

        recorder, _, _ = build(publish=publish)
        await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(seen["claim"], "We ship single-tenant first.")
        self.assertEqual(seen["basis"], "inference")

    async def test_a_claim_already_reserved_is_not_published_twice(self):
        recorder, journal, reports = build(journal=Journal(reserved=False))
        seen = []

        async def publish(binding, fields):
            seen.append(fields)
            return result()

        recorder.publish = publish
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "duplicate_fingerprint")
        self.assertEqual(seen, [])
        self.assertEqual(journal.bodies, [])


class WhatMustNotBecomeKnowledge(unittest.IsolatedAsyncioTestCase):
    async def test_a_verdict_whose_moment_has_passed_is_not_published(self):
        """Publishing it would attribute one project's thinking to another."""
        recorder, journal, reports = build(binding=None)
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "binding_changed")
        self.assertEqual(journal.reserved, [])
        self.assertEqual(journal.bodies, [])

    async def test_a_verdict_for_another_session_is_refused(self):
        recorder, journal, _ = build()
        outcome = await recorder.handle("teamwork:decision_detected",
                                        detected(session_id="somebody-else"))
        self.assertEqual(outcome, "binding_changed")
        self.assertEqual(journal.reserved, [])

    async def test_an_unknown_kind_does_nothing_at_all(self):
        recorder, journal, reports = build()
        self.assertIsNone(await recorder.handle("teamwork:decision_detected",
                                                detected(kind="something_else")))
        self.assertEqual((journal.reserved, journal.bodies, reports), ([], [], []))


class WhenThePublishDoesNotLand(unittest.IsolatedAsyncioTestCase):
    async def test_a_definite_refusal_releases_the_claim_for_a_later_window(self):
        async def refused(binding, fields):
            return result(success=False, error={"message": "permission denied"})

        recorder, journal, _ = build(publish=refused)
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "refused")
        self.assertEqual(journal.released, [("sess-1", "fp-1")])
        self.assertEqual(journal.bodies, [])

    async def test_an_unknown_acceptance_keeps_the_claim_and_the_local_copy(self):
        """A blind retry after an ambiguous write duplicates a record that may
        already exist; dropping it loses a real one. So both are kept."""
        async def unknown(binding, fields):
            return result(success=False, error={"outcome": "unknown",
                                                "attempted_record_id": "rec-maybe"})

        recorder, journal, _ = build(publish=unknown)
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "acceptance_unknown")
        self.assertEqual(journal.released, [])
        self.assertEqual(journal.bodies[0][-1], "rec-maybe")
        self.assertEqual(journal.outcomes, ["acceptance_unknown"])

    async def test_a_raising_publish_keeps_the_reservation(self):
        async def explode(binding, fields):
            raise RuntimeError("service fell over")

        recorder, journal, _ = build(publish=explode)
        outcome = await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(outcome, "write_raised")
        self.assertEqual(journal.released, [])

    async def test_cancellation_propagates_and_keeps_the_reservation(self):
        """Swallowing it would let a retry publish the same claim twice."""
        async def cancelled(binding, fields):
            raise asyncio.CancelledError()

        recorder, journal, reports = build(publish=cancelled)
        with self.assertRaises(asyncio.CancelledError):
            await recorder.handle("teamwork:decision_detected", detected())
        self.assertEqual(journal.released, [])
        self.assertEqual(reports[-1][2], "write_cancelled")


if __name__ == "__main__":
    unittest.main()
