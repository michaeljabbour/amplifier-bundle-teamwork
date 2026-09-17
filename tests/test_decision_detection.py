"""Decision detection -- the wiring, not the judge.

Covers what decision_judge.py explicitly left out of scope (see its own
docstring): what fires the judge (`tool:pre` for a delegation call, and
`session:end`'s retrospective sweep), the stateful watermark and fingerprint
dedup that stop one decision being re-detected and re-published forever, the
never-raises contract at the wiring layer (not just inside the judge itself),
and that the PARENT session's own RecordInsightTool -- never a fresh one bound
to a judge session -- performs the write, so `source_session_id` stays honest.

Specification: docs/scenarios/08-the-decision-the-session-made-itself.md and
its twin docs/scenarios/08b-the-path-taken-that-binds-nothing.md.

Style matches tests/test_decision_judge.py: `decision_judge.judge_window` is
patched directly (never a network provider call), and the HTTP client is the
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
    decision_detection_enabled,
    decision_judge,
)
from amplifier_module_hooks_teamwork import mount as teamwork_mount


class Context:
    async def add_message(self, message):
        pass


class Coordinator:
    session_id = "root-session-decision-detect"
    parent_id = None

    def __init__(self):
        self.context = Context()

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
    "kind": "permission boundary",
    "claim": "Always narrow the scope rather than requesting the wide one.",
    "basis": "observation",
    "confidence": "high",
    "what_it_does_not_establish": "Nothing about other credentials.",
    "reason": "structural",
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
    """Await every decision-detection task the fixture has scheduled so far."""
    for task in list(hook._decision_tasks):
        await task


def build(test, decision_detection=True):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    connection = {"base_url": "https://team.example.invalid", "project_id": "teamwork",
                  "token": "fixture-token-value"}
    client = Client()
    journal = Journal(Path(tmp.name) / "q.db")
    hook = TeamworkHook(Coordinator(), connection, journal, client, decision_detection=decision_detection)
    return hook, client, journal


def outcomes(hook):
    """Read the journal-only detection_outcome rows. Deliberately read through
    sqlite rather than a log: nothing about this reaches a user."""
    with hook.journal.connect() as conn:
        return [{"kind": k, "outcome": o, "tally_size": t, "link_count": l}
                for k, o, t, l in conn.execute(
                    "SELECT kind, outcome, tally_size, link_count FROM detection_outcome ORDER BY rowid")]


def insight_ops(client):
    return [op for endpoint, body, _ in client.requests if endpoint == "publish"
            for op in body["operations"] if op["op"] == "insight.upsert"]


class OptInResolution(unittest.TestCase):
    """detect_decisions is resolved exactly like share_visible_turns: absent
    or False is off; anything else non-True is an ambiguous misconfiguration.
    """

    def test_absent_is_off(self):
        self.assertFalse(decision_detection_enabled({}))

    def test_explicit_false_is_off(self):
        self.assertFalse(decision_detection_enabled({"detect_decisions": False}))

    def test_explicit_true_is_on(self):
        self.assertTrue(decision_detection_enabled({"detect_decisions": True}))

    def test_an_ambiguous_value_is_refused(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            decision_detection_enabled({"detect_decisions": "yes"})

    async def _unused(self):
        pass


class OptInOffMeansZeroCost(unittest.IsolatedAsyncioTestCase):
    """The most important test here: off means no judge call, no state
    written, no publish -- not just "off by default until a tool binds one".
    """

    async def test_detect_decision_is_a_no_op_when_off(self):
        hook, client, journal = build(self, decision_detection=False)
        add_turn(hook, "we need this to run out of band", "reasoning... delegating")
        # finish() only ever appends to decision_turns when detection is on --
        # this alone proves no state was written for it.
        self.assertNotIn("decision_turns", hook.state)
        with patch.object(decision_judge, "judge_window", AsyncMock()) as judge:
            task = hook.detect_decision(tool_calls=[{"name": "delegate", "target": "builder"}])
        self.assertIsNone(task)
        judge.assert_not_called()
        self.assertEqual(journal.decision_watermark(hook.sid), 0)
        self.assertEqual(client.requests, [])

    async def test_on_tool_pre_is_a_no_op_when_off(self):
        hook, client, _journal = build(self, decision_detection=False)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock()) as judge:
            result = await hook.on_tool_pre(
                "tool:pre", {"tool_name": "delegate", "tool_input": {"agent": "builder"}})
        judge.assert_not_called()
        self.assertEqual(client.requests, [])
        self.assertEqual(hook._decision_tasks, set())
        self.assertEqual(result.action, "continue")

    async def test_mount_without_detect_decisions_mounts_a_hook_with_detection_off(self):
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
                "token": "fixture-token-value",
                "journal_path": str(Path(directory) / "queue.sqlite3"),
            })
        hook = root.hooks.handlers[0][0][1].__self__
        self.assertFalse(hook.decision_detection)

    async def test_mount_with_an_ambiguous_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            await teamwork_mount(object(), {
                "share_visible_turns": True,
                "detect_decisions": "sure",
                "base_url": "https://team.example.invalid",
                "project_id": "configured",
                "token": "fixture-token-value",
            })


class DuplicateDecisionIsPublishedOnce(unittest.IsolatedAsyncioTestCase):
    """The reason this is stateful at all (docs/scenarios/08's open
    questions): duplication. The same claim text surfacing at two separate
    triggers must not become two records.
    """

    async def test_the_same_claim_across_two_triggers_is_recorded_once(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "a structural reason for narrowing scope")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision(tool_calls=[{"name": "delegate", "target": "agent-a"}])
            await drain(hook)
        add_turn(hook, "u2", "the same reasoning restated for a second delegation")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision(tool_calls=[{"name": "delegate", "target": "agent-b"}])
            await drain(hook)
        self.assertEqual(len(insight_ops(client)), 1)

    async def test_a_different_claim_is_recorded_separately(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "first structural reason")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision()
            await drain(hook)
        second = dict(RECORD_VERDICT, claim="A completely different structural constraint applies here.")
        add_turn(hook, "u2", "second structural reason")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=second)):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(len(insight_ops(client)), 2)


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

        with patch.object(decision_judge, "judge_window", fake_judge):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(len(captured), 1)
        self.assertEqual([t["user_prompt"] for t in captured[0]["turns"]], ["u1", "u2"])
        self.assertGreater(journal.decision_watermark(hook.sid), 0)

        add_turn(hook, "u3", "r3")
        with patch.object(decision_judge, "judge_window", fake_judge):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(len(captured), 2)
        self.assertEqual([t["user_prompt"] for t in captured[1]["turns"]], ["u3"])

    async def test_nothing_new_since_the_watermark_skips_the_judge_entirely(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=decision_judge.no_verdict("x"))):
            hook.detect_decision()
            await drain(hook)
        with patch.object(decision_judge, "judge_window", AsyncMock()) as judge:
            task = hook.detect_decision()
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

        with patch.object(decision_judge, "judge_window", boom):
            task = hook.detect_decision()
            await task  # must not raise
        self.assertEqual(client.requests, [])

    async def test_a_record_verdict_with_an_unusable_shape_is_not_recorded(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        broken = dict(RECORD_VERDICT, basis=None)  # e.g. the model's enum failed to parse
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=broken)):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(insight_ops(client), [])

    async def test_a_record_verdict_the_service_refuses_is_not_fingerprinted(self):
        """A refused write must not be treated as recorded -- otherwise a
        transient 403/409 would permanently suppress a decision that was
        never actually published anywhere.
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
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision()
            await drain(hook)
        fingerprint_recorded = journal.decision_seen(
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
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision()
            await drain(hook)
        ops = insight_ops(client)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["data"]["source_session_id"], hook.sid)

    async def test_the_evidence_names_the_examined_window_not_an_http_link(self):
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision()
            await drain(hook)
        ops = insight_ops(client)
        refs = ops[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["kind"], "external")
        self.assertIn(hook.sid, refs[0]["uri"])
        self.assertFalse(refs[0]["uri"].lower().startswith(("http://", "https://")))


class TallyReachesTheJudge(unittest.IsolatedAsyncioTestCase):
    """The hook builds the tally from its OWN synchronized cache (no new
    network call) and hands it to the judge as part of the window -- see
    decision_judge.py's "THE TALLY".
    """

    def _insight_source(self, record_id, version, claim):
        return {
            "record": {"id": record_id, "record_type": "insight", "version": version,
                       "content": {"claim": claim}},
            "delivery_id": "manifest",
        }

    async def test_cache_contents_reach_the_judge_as_a_tally(self):
        hook, _client, _journal = build(self)
        hook.state["cache"] = {
            "insight:i1": self._insight_source("i1", 2, "Already-recorded knowledge."),
        }
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_window", fake_judge):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(captured[0]["tally"], [
            {"record_type": "insight", "record_id": "i1", "version": 2,
             "claim": "Already-recorded knowledge."},
        ])

    async def test_a_malformed_cache_entry_does_not_break_detection(self):
        """A never-raises guarantee at the wiring layer, not just inside
        decision_judge.build_tally() itself: one broken cache entry must not
        prevent the good ones (or the window itself) from reaching the judge.
        """
        hook, _client, _journal = build(self)
        hook.state["cache"] = {
            "insight:good": self._insight_source("good", 1, "The only usable entry."),
            "insight:broken": {"record": {"id": "broken", "record_type": "insight"}},  # no version, no content
            "not-even-a-source": "garbage",
        }
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_window", fake_judge):
            hook.detect_decision()
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

        with patch.object(decision_judge, "judge_window", fake_judge):
            hook.detect_decision()
            await drain(hook)
        self.assertEqual(captured[0]["tally"], [])


class VerdictLinksReachEvidence(unittest.IsolatedAsyncioTestCase):
    """A judge's `links` (built from the tally it was shown) are carried
    through as additional `record`-kind evidence, alongside the window's own
    `external` reference -- never replacing it.
    """

    async def test_valid_links_become_additional_record_kind_evidence(self):
        hook, client, _journal = build(self)
        hook.state["cache"] = {"insight:i1": {"record": {"record_type": "insight", "id": "i1", "version": 2, "content": {"claim": "Prior claim"}}}}
        add_turn(hook, "u1", "r1")
        verdict = dict(RECORD_VERDICT, links=[
            {"record_type": "insight", "record_id": "i1", "version": 2},
        ])
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=verdict)):
            hook.detect_decision()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["kind"], "external")  # the hardcoded window ref stays first
        self.assertEqual(refs[1], {"kind": "record", "record_type": "insight",
                                    "record_id": "i1", "version": 2})

    async def test_a_malformed_link_is_dropped_and_the_record_still_lands(self):
        hook, client, _journal = build(self)
        hook.state["cache"] = {"insight:keep": {"record": {"record_type": "insight", "id": "keep", "version": 1, "content": {"claim": "Prior claim"}}}}
        add_turn(hook, "u1", "r1")
        verdict = dict(RECORD_VERDICT, links=[
            {"record_type": "insight", "record_id": "keep", "version": 1},
            {"record_type": "person", "record_id": "bad-type", "version": 1},
            {"record_type": "idea", "record_id": "bad-version", "version": "not-an-int"},
        ])
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=verdict)):
            hook.detect_decision()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        record_refs = [r for r in refs if r["kind"] == "record"]
        self.assertEqual(record_refs, [{"kind": "record", "record_type": "insight",
                                         "record_id": "keep", "version": 1}])

    async def test_no_links_field_at_all_is_unaffected_regression(self):
        """RECORD_VERDICT as used everywhere else in this file has no
        "links" key at all -- must degrade to exactly one evidence entry,
        same as before this change.
        """
        hook, client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=dict(RECORD_VERDICT))):
            hook.detect_decision()
            await drain(hook)
        refs = insight_ops(client)[0]["data"]["evidence_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["kind"], "external")


class ToolPreWindowConstruction(unittest.IsolatedAsyncioTestCase):
    """The delegation call itself is reduced to name+target only -- the
    instruction (where the conclusion is stated, per 08's open questions)
    must never reach the judge as raw tool payload.
    """

    async def test_a_delegation_call_is_reduced_to_name_and_target_only(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_window", fake_judge):
            await hook.on_tool_pre("tool:pre", {
                "tool_name": "delegate",
                "tool_input": {"agent": "builder", "instruction": "a private plan nobody else should see"},
            })
            await drain(hook)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["tool_calls"], [{"name": "delegate", "target": "builder"}])
        self.assertNotIn("private plan", json.dumps(captured[0]))

    async def test_the_task_alias_is_also_treated_as_a_delegation(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock(return_value=decision_judge.no_verdict("x"))) as judge:
            await hook.on_tool_pre("tool:pre", {"tool_name": "task", "tool_input": {"agent": "builder"}})
            await drain(hook)
        judge.assert_called_once()

    async def test_a_non_delegation_tool_call_does_not_trigger_detection(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        with patch.object(decision_judge, "judge_window", AsyncMock()) as judge:
            await hook.on_tool_pre("tool:pre", {"tool_name": "teamwork_wait", "tool_input": {"reason": "x"}})
        judge.assert_not_called()
        self.assertEqual(hook._decision_tasks, set())

    async def test_a_delegation_call_missing_an_agent_still_triggers_without_a_target(self):
        hook, _client, _journal = build(self)
        add_turn(hook, "u1", "r1")
        captured = []

        async def fake_judge(coordinator, window, **kwargs):
            captured.append(window)
            return decision_judge.no_verdict("test probe")

        with patch.object(decision_judge, "judge_window", fake_judge):
            await hook.on_tool_pre("tool:pre", {"tool_name": "delegate", "tool_input": {}})
            await drain(hook)
        self.assertEqual(captured[0]["tool_calls"], [{"name": "delegate"}])


if __name__ == "__main__":
    unittest.main()


class DetectionOutcomeIsObservableInTheJournalOnly(unittest.IsolatedAsyncioTestCase):
    """Every detector path used to end in the SAME observable state -- a
    watermark moved and no record -- whether it was suppressed, judged SKIP,
    refused, or written with an unknown outcome. That ambiguity cost four
    failed verification runs against a live DTU. These assert the paths are
    now distinguishable, and that nothing about it reaches a user.
    """

    async def test_a_skip_verdict_and_a_deliberate_suppression_are_distinguishable(self):
        hook, client, _journal = build(self)
        hook.state["decision_turns"] = [{"turn_index": 1, "user_prompt": "u", "agent_responses": [{"text": "a"}]}]
        with patch.object(decision_judge, "judge_window",
                          AsyncMock(return_value={"record": False, "available": True})):
            task = hook.detect_decision()
            if task:
                await task
        rows = outcomes(hook)
        self.assertEqual([r["outcome"] for r in rows], ["skip_verdict"])

        hook2, _client2, _j2 = build(self)
        hook2.state["decision_turns"] = [{"turn_index": 1, "user_prompt": "u", "agent_responses": [{"text": "a"}]}]
        hook2.state["deliberate_record_turn"] = 1
        with patch.object(decision_judge, "judge_window", AsyncMock()) as judge:
            task = hook2.detect_decision()
            if task:
                await task
        judge.assert_not_called()
        self.assertEqual([r["outcome"] for r in outcomes(hook2)], ["skipped_deliberate"])

    async def test_the_tally_size_the_judge_actually_saw_is_recorded(self):
        hook, client, _journal = build(self)
        hook.state["decision_turns"] = [{"turn_index": 1, "user_prompt": "u", "agent_responses": [{"text": "a"}]}]
        hook.state["cache"] = {
            "insight:aaa": {"record": {"record_type": "insight", "id": "aaa", "version": 1,
                                       "content": {"claim": "already known"}}, "delivery_id": "d"},
        }
        with patch.object(decision_judge, "judge_window",
                          AsyncMock(return_value={"record": False, "available": True})):
            task = hook.detect_decision()
            if task:
                await task
        rows = outcomes(hook)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tally_size"], 1)

    def test_recording_an_outcome_can_never_break_detection(self):
        hook, _client, _j = build(self)
        hook.journal.path = "/nonexistent/dir/does-not-exist.sqlite3"
        hook.journal.record_detection_outcome("s", "decision", "recorded", 0, 0)
