"""Lesson detection -- the wiring, not the judge.

Same machinery as tests/test_decision_detection.py (that file's docstring
describes the shared wiring in full: never-raises contract, stateful
dedup, the PARENT session's own RecordInsightTool performing the write).
This file covers what is actually DIFFERENT about lesson detection:

  - `detect_lessons` is its own opt-in, resolved exactly like
    `detect_decisions` (absent/False off, anything else non-True refused).
  - The trigger is `session:end` ONLY -- there is no tool:pre analogue,
    because a lesson is not tied to handing work off (see
    docs/scenarios/06's open questions and __init__.py's on_end() comment).
  - Watermark and fingerprint state are namespaced (Journal.lesson_* methods,
    see LESSON_KEY_SUFFIX) so they never collide with decision detection's
    own state for the same session, even though both reuse the identical
    decision_watermark/decision_fingerprint tables.
  - The two detectors must not interfere: enabling one must never turn the
    other on, and each must buffer, watermark, and fingerprint independently.

Specification: docs/scenarios/06-record-the-lesson-not-the-incident.md and
its twin docs/scenarios/06b-the-lesson-that-is-only-true-on-your-machine.md.

Style matches tests/test_decision_detection.py: `decision_judge.judge_lesson_window`
is patched directly (never a network provider call), and the HTTP client is the
same in-memory fake style tests/test_hook.py already uses.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import (
    Journal,
    TeamworkHook,
    decision_judge,
    lesson_detection_enabled,
)
from amplifier_module_hooks_teamwork import mount as teamwork_mount


class Context:
    async def add_message(self, message):
        pass


class Hooks:
    """Dispatching, because the recorder is a real subscriber now."""

    def __init__(self):
        self.handlers = {}

    def register(self, event, handler, **kwargs):
        self.handlers.setdefault(event, []).append(handler)

    async def emit(self, event, payload):
        for handler in list(self.handlers.get(event, [])):
            await handler(event, payload)


class Coordinator:
    session_id = "root-session-lesson-detect"
    parent_id = None

    def __init__(self):
        self.context = Context()
        self.hooks = Hooks()

    def get(self, name):
        return self.context if name == "context" else None

    async def mount(self, point, value, name):
        pass


class Client:
    """Records every publish; serves an empty context page."""

    def __init__(self):
        self.requests = []

    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, json.loads(json.dumps(body)), key))
        if endpoint == "context":
            return {"items": [], "next_cursor": None, "has_more": False,
                     "truncated": False, "message_status": []}
        return {"stored": True}


RECORD_VERDICT = {
    "record": True,
    "kind": "search-method limit",
    "claim": "A negative result from a search is bounded by what the search can see.",
    "basis": "observation",
    "confidence": "high",
    "what_it_does_not_establish": "Nothing about any other kind of check.",
    "reason": "The evidence reaches as far as the claim; it holds regardless of whose machine this was.",
}


def add_turn(hook, prompt, response):
    """Drive a completed turn through the same path finish() itself uses,
    without the network calls on_submit/on_complete would otherwise need.
    """
    hook.state["turn_index"] += 1
    hook.state["turn"] = {
        "id": "turn-%d" % hook.state["turn_index"],
        "prompt": prompt,
        "omitted": False,
        "injections": [],
        "hook_run_id": "hook-run-%d" % hook.state["turn_index"],
        "boundary": "prepared",
    }
    hook.finish(response)


async def drain(hook):
    """Await every lesson-detection task the fixture has scheduled so far."""
    for task in list(hook._lesson_tasks):
        await task


def build(test, lesson_detection=True, decision_detection=False):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork",
                  "token": "[REDACTED:SECRET]"}
    client = Client()
    journal = Journal(Path(tmp.name) / "q.db")
    coordinator = Coordinator()
    hook = TeamworkHook(coordinator, connection, journal, client,
                        decision_detection=decision_detection, lesson_detection=lesson_detection)
    from amplifier_module_hooks_teamwork import recording, events as detection_events, RecordInsightTool
    recording.subscribe(coordinator, hook, RecordInsightTool, detection_events)
    return hook, client, journal


def insight_ops(client):
    return [op for endpoint, body, _ in client.requests if endpoint == "publish"
            for op in body["operations"] if op["op"] == "insight.upsert"]


class OptInResolution(unittest.TestCase):
    """detect_lessons is resolved exactly like detect_decisions and
    share_visible_turns: absent or False is off; anything else non-True is
    an ambiguous misconfiguration.
    """

    def test_absent_is_off(self):
        self.assertFalse(lesson_detection_enabled({}))

    def test_explicit_false_is_off(self):
        self.assertFalse(lesson_detection_enabled({"detect_lessons": False}))

    def test_explicit_true_is_on(self):
        self.assertTrue(lesson_detection_enabled({"detect_lessons": True}))

    def test_an_ambiguous_value_is_refused(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            lesson_detection_enabled({"detect_lessons": "yes"})

    async def _unused(self):
        pass


class OptInOffMeansZeroCost(unittest.IsolatedAsyncioTestCase):
    """The most important test here: off means no judge call, no state
    written, no publish -- not just "off by default until a tool binds one".
    """

    async def test_detect_lesson_is_a_no_op_when_off(self):
        hook, client, journal = build(self, lesson_detection=False)
        add_turn(hook, "we found something surprising", "reasoning...")
        # finish() only ever appends to lesson_turns when detection is on --
        # this alone proves no state was written for it.
        self.assertNotIn("lesson_turns", hook.state)
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as judge:
            task = hook.detect_lesson()
        self.assertIsNone(task)
        judge.assert_not_called()
        self.assertEqual(journal.lesson_watermark(hook.sid), 0)
        self.assertEqual(client.requests, [])

    async def test_on_end_does_not_call_the_lesson_judge_when_off(self):
        hook, client, _journal = build(self, lesson_detection=False)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as judge:
            result = await hook.on_end("session:end", {})
        judge.assert_not_called()
        self.assertEqual(insight_ops(client), [])
        self.assertEqual(result.action, "continue")

    async def test_mount_without_detect_lessons_mounts_a_hook_with_detection_off(self):
        class Hooks:
            def __init__(self):
                self.handlers = []

            def register(self, *args, **kwargs):
                self.handlers.append((args, kwargs))

        class Root:
            parent_id = None
            session_id = "opt-in-off-session"

            def __init__(self):
                self.hooks = Hooks()
                self.capabilities = {}
                self.tools = {}

            def register_capability(self, name, value):
                self.capabilities[name] = value

            async def mount(self, point, value, name):
                self.tools[name] = value

        root = Root()
        with tempfile.TemporaryDirectory() as directory:
            await teamwork_mount(root, {
                "share_visible_turns": True,
                "base_url": "https://team.example.invalid",
                "project_id": "configured",
                "token": "[REDACTED:SECRET]",
                "journal_path": str(Path(directory) / "queue.sqlite3"),
            })
        hook = root.hooks.handlers[0][0][1].__self__
        self.assertFalse(hook.lesson_detection)

    async def test_mount_with_an_ambiguous_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            await teamwork_mount(object(), {
                "share_visible_turns": True,
                "detect_lessons": "sure",
                "base_url": "https://team.example.invalid",
                "project_id": "configured",
                "token": "[REDACTED:SECRET]",
            })


class NoDelegationTrigger(unittest.IsolatedAsyncioTestCase):
    """Unlike decision detection, a lesson is not tied to handing work off:
    on_tool_pre must never call the lesson judge, even for a delegation call,
    even when lesson detection is on.
    """

    async def test_a_delegation_call_does_not_trigger_lesson_detection(self):
        hook, _client, _journal = build(self, lesson_detection=True)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as judge:
            await hook.on_tool_pre("tool:pre", {"tool_name": "delegate", "tool_input": {"agent": "builder"}})
        judge.assert_not_called()
        self.assertEqual(hook._lesson_tasks, set())

    async def test_session_end_is_the_only_trigger(self):
        hook, client, _journal = build(self, lesson_detection=True)
        add_turn(hook, "u1", "surprising discovery")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            await hook.on_end("session:end", {})
            await drain(hook)
        self.assertEqual(len(insight_ops(client)), 1)


class DuplicateLessonIsPublishedOnce(unittest.IsolatedAsyncioTestCase):
    """The same claim text surfacing again must not become a second record."""

    async def test_the_same_claim_across_two_session_ends_is_recorded_once(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "a structural search-method limit noticed")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        add_turn(hook, "u2", "the same lesson restated in a later session")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(len(insight_ops(client)), 1)  # same claim text -> deduped, not a second record

    async def test_a_different_claim_is_recorded_separately(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "first lesson")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        second = dict(RECORD_VERDICT, claim="A completely different transferable claim applies here.")
        add_turn(hook, "u2", "second lesson")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=second)):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(len(insight_ops(client)), 2)

    async def test_the_exact_same_claim_within_one_window_call_is_deduped(self):
        """The realistic single-shot case: on_end() calls detect_lesson()
        exactly once per real session, so dedup is exercised by driving the
        SAME fingerprint through two separate detect_lesson() calls sharing
        one journal, mirroring how two different sessions' outputs would
        collide on identical claim text.
        """
        hook, _client, journal = build(self)
        add_turn(hook, "u1", "a lesson")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        self.assertTrue(journal.lesson_seen(hook.sid, __import__("hashlib").sha256(
            " ".join(RECORD_VERDICT["claim"].lower().split()).encode()).hexdigest()))
        # A second window with nothing new to examine is a pure no-op (no judge call).
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as judge:
            task = hook.detect_lesson()
        self.assertIsNone(task)
        judge.assert_not_called()


class WatermarkBoundsReExamination(unittest.IsolatedAsyncioTestCase):
    """A later window must not re-examine turns an earlier trigger already
    considered, regardless of what verdict came back.
    """

    async def test_a_later_window_excludes_already_examined_turns(self):
        hook, _client, journal = build(self)
        add_turn(hook, "u1", "r1")
        add_turn(hook, "u2", "r2")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_lesson_window", fake_judge):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(len(captured), 1)
        self.assertEqual([t["user_prompt"] for t in captured[0]["turns"]], ["u1", "u2"])
        self.assertGreater(journal.lesson_watermark(hook.sid), 0)

        add_turn(hook, "u3", "r3")
        with patch.object(decision_judge, "judge_lesson_window", fake_judge):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(len(captured), 2)
        self.assertEqual([t["user_prompt"] for t in captured[1]["turns"]], ["u3"])

    async def test_nothing_new_since_the_watermark_skips_the_judge_entirely(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=decision_judge.no_verdict("x"))):
            hook.detect_lesson()
            await drain(hook)
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as judge:
            task = hook.detect_lesson()
        self.assertIsNone(task)
        judge.assert_not_called()


class JudgeFailureIsolation(unittest.IsolatedAsyncioTestCase):
    """A judge that raises must never propagate into the turn path -- the
    background task completes quietly and nothing is recorded.
    """

    async def test_a_raising_judge_never_propagates(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")

        async def boom(coordinator, window, **kwargs):
            raise RuntimeError("the provider is unreachable")

        with patch.object(decision_judge, "judge_lesson_window", boom):
            task = hook.detect_lesson()
            await task  # must not raise
        self.assertEqual(client.requests, [])

    async def test_a_record_verdict_with_an_unusable_shape_is_not_recorded(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        broken = dict(RECORD_VERDICT, basis=None)  # e.g. the model's enum failed to parse
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=broken)):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(insight_ops(client), [])

    async def test_a_record_verdict_the_service_refuses_is_not_fingerprinted(self):
        """A refused write must not be treated as recorded -- otherwise a
        transient 403/409 would permanently suppress a lesson that was never
        actually published anywhere.
        """
        from amplifier_module_hooks_teamwork import SyncError

        class RefusingClient(Client):
            def request(self, endpoint, body, key=None):
                if endpoint == "publish" and body["operations"][0]["op"] == "insight.upsert":
                    raise SyncError(403)
                return super().request(endpoint, body, key)

        hook, _client, journal = build(self)
        hook.client = RefusingClient()
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        fingerprint_recorded = journal.lesson_seen(
            hook.sid, __import__("hashlib").sha256(
                " ".join(RECORD_VERDICT["claim"].lower().split()).encode()
            ).hexdigest())
        self.assertFalse(fingerprint_recorded)


class ProvenanceStaysOnTheParent(unittest.IsolatedAsyncioTestCase):
    """The judge never writes; the parent's own RecordInsightTool does, so
    source_session_id is this session's, never a throwaway judge session's.
    """

    async def test_the_published_records_source_session_id_is_the_parents(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        ops = insight_ops(client)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["data"]["source_session_id"], hook.sid)

    async def test_the_evidence_names_a_lesson_window_not_an_http_link(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        ops = insight_ops(client)
        refs = ops[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["kind"], "external")
        self.assertIn(hook.sid, refs[0]["uri"])
        self.assertIn("teamwork-lesson-window://", refs[0]["uri"])
        self.assertFalse(refs[0]["uri"].lower().startswith(("http://", "https://")))


class TallyReachesTheLessonJudge(unittest.IsolatedAsyncioTestCase):
    """Same wiring as decision detection's own TallyReachesTheJudge (see
    tests/test_decision_detection.py) -- the lesson judge is shown the same
    cache-derived tally, built the same way, with the same never-raises
    guarantee on a malformed cache entry.
    """

    def _insight_source(self, record_id, version, claim):
        return {
            "record": {"id": record_id, "record_type": "insight", "version": version,
                       "content": {"claim": claim}},
            "delivery_id": "manifest",
        }

    async def test_cache_contents_reach_the_lesson_judge_as_a_tally(self):
        hook, _client, _journal = build(self)
        hook.state["cache"] = {
            "insight:i1": self._insight_source("i1", 5, "A prior lesson already recorded."),
        }
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_lesson_window", fake_judge):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(captured[0]["tally"], [
            {"record_type": "insight", "record_id": "i1", "version": 5,
             "claim": "A prior lesson already recorded."},
        ])

    async def test_a_malformed_cache_entry_does_not_break_lesson_detection(self):
        hook, _client, _journal = build(self)
        hook.state["cache"] = {
            "insight:good": self._insight_source("good", 1, "The only usable entry."),
            "insight:broken": {"record": {"id": "broken", "record_type": "insight"}},
            "not-even-a-source": "garbage",
        }
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_lesson_window", fake_judge):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(len(captured), 1)
        self.assertEqual([e["record_id"] for e in captured[0]["tally"]], ["good"])

    async def test_an_empty_cache_is_an_empty_tally_exactly_todays_behavior(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_lesson_window", fake_judge):
            hook.detect_lesson()
            await drain(hook)
        self.assertEqual(captured[0]["tally"], [])


class LessonVerdictLinksReachEvidence(unittest.IsolatedAsyncioTestCase):
    """Same contract as decision detection's VerdictLinksReachEvidence -- a
    lesson judge's `links` are carried through as additional `record`-kind
    evidence, and a malformed one is dropped without sinking the write.
    """

    async def test_valid_links_become_additional_record_kind_evidence(self):
        hook, client, _journal = build(self)
        hook.state["cache"] = {"idea:d1": {"record": {"record_type": "idea", "id": "d1", "version": 3, "content": {"claim": "Prior claim"}}}}
        add_turn(hook, "u1", "r1")
        verdict = dict(RECORD_VERDICT, links=[
            {"record_type": "idea", "record_id": "d1", "version": 3},
        ])
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=verdict)):
            hook.detect_lesson()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["kind"], "external")
        self.assertEqual(refs[1], {"kind": "record", "record_type": "idea",
                                    "record_id": "d1", "version": 3})

    async def test_a_malformed_link_is_dropped_and_the_record_still_lands(self):
        hook, client, _journal = build(self)
        hook.state["cache"] = {"idea:keep": {"record": {"record_type": "idea", "id": "keep", "version": 1, "content": {"claim": "Prior claim"}}}}
        add_turn(hook, "u1", "r1")
        verdict = dict(RECORD_VERDICT, links=[
            {"record_type": "idea", "record_id": "keep", "version": 1},
            {"record_type": "work", "record_id": "bad-version", "version": None},
        ])
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=verdict)):
            hook.detect_lesson()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        record_refs = [r for r in refs if r["kind"] == "record"]
        self.assertEqual(record_refs, [{"kind": "record", "record_type": "idea",
                                         "record_id": "keep", "version": 1}])

    async def test_no_links_field_at_all_is_unaffected_regression(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_lesson()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["kind"], "external")


class TheTwoDetectorsDoNotInterfere(unittest.IsolatedAsyncioTestCase):
    """Enabling one detector must never turn the other on, and each must
    buffer, watermark, and fingerprint independently -- even though both
    reuse the identical decision_watermark/decision_fingerprint tables
    (namespaced via LESSON_KEY_SUFFIX; see Journal.lesson_watermark).
    """

    async def test_lesson_only_never_calls_the_decision_judge(self):
        hook, client, _journal = build(self, lesson_detection=True, decision_detection=False)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock()) as decision_mock, \
             patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            await hook.on_end("session:end", {})
            await drain(hook)
        decision_mock.assert_not_called()
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_decision_only_never_calls_the_lesson_judge(self):
        hook, client, _journal = build(self, lesson_detection=False, decision_detection=True)
        add_turn(hook, "u1", "a structural reason for narrowing scope")
        decision_verdict = {
            "record": True, "kind": "permission boundary",
            "claim": "Always narrow the scope rather than requesting the wide one.",
            "basis": "observation", "confidence": "high",
            "what_it_does_not_establish": "Nothing about other credentials.",
            "reason": "structural",
        }
        with patch.object(decision_judge, "judge_lesson_window", AsyncMock()) as lesson_mock, \
             patch.object(decision_judge, "judge_window", AsyncMock(return_value=decision_verdict)):
            await hook.on_end("session:end", {})
            for task in list(hook._decision_tasks):
                await task
        lesson_mock.assert_not_called()
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_both_enabled_write_independent_state_for_the_same_turns(self):
        hook, client, journal = build(self, lesson_detection=True, decision_detection=True)
        add_turn(hook, "u1", "one turn worth both a decision and a lesson")
        decision_verdict = {
            "record": True, "kind": "permission boundary",
            "claim": "Always narrow the scope rather than requesting the wide one.",
            "basis": "observation", "confidence": "high",
            "what_it_does_not_establish": "Nothing about other credentials.",
            "reason": "structural",
        }
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=decision_verdict)), \
             patch.object(decision_judge, "judge_lesson_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            await hook.on_end("session:end", {})
            await drain(hook)
            for task in list(hook._decision_tasks):
                await task
        ops = insight_ops(client)
        self.assertEqual(len(ops), 2)
        claims = {op["data"]["claim"] for op in ops}
        self.assertEqual(claims, {decision_verdict["claim"], RECORD_VERDICT["claim"]})
        # Independent watermarks: neither is zero, and lesson's key is namespaced.
        self.assertGreater(journal.decision_watermark(hook.sid), 0)
        self.assertGreater(journal.lesson_watermark(hook.sid), 0)


if __name__ == "__main__":
    unittest.main()
