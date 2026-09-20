"""Regression checks for event provenance, binding ownership, and honest recall."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_decision_detection import build, insight_ops, RECORD_VERDICT
from test_journal_decisions import Coordinator
from amplifier_module_hooks_teamwork import Journal, TeamworkHook, mount


async def detect(hook, **overrides):
    await hook._record_verdict(dict(RECORD_VERDICT, **overrides), 1,
        binding=hook.detection_binding(), uri_scheme="teamwork-decision-window://",
        reserve=None, release=None, log_label="decision")


class RecorderBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_rebinding_away_and_back_cannot_resurrect_a_verdict(self):
        h, client, _ = build(self)
        emit = h.coordinator.hooks.emit
        async def switch(event, payload):
            if event == "teamwork:decision_detected":
                project = h.connection["project_id"]
                h.rebind("other-project")
                h.rebind(project)
            await emit(event, payload)
        h.coordinator.hooks.emit = switch
        await detect(h)
        self.assertEqual(insight_ops(client), [])
        self.assertEqual(h.recall_decisions(), [])
        self.assertEqual(h._detected_candidates, {})

    async def test_observer_cannot_change_the_claim_or_replay_a_carrier(self):
        h, client, _ = build(self)
        emit = h.coordinator.hooks.emit
        async def mutate(event, payload):
            if event == "teamwork:decision_detected":
                payload["claim"] = "Different unjudged claim"
                payload["evidence"].clear()
                await emit(event, payload)
            await emit(event, payload)
        h.coordinator.hooks.emit = mutate
        await detect(h)
        self.assertEqual(len(insight_ops(client)), 1)
        self.assertEqual(insight_ops(client)[0]["data"]["claim"], RECORD_VERDICT["claim"])
        self.assertTrue(insight_ops(client)[0]["data"]["evidence_refs"])

    async def test_unsolicited_bus_verdict_cannot_publish(self):
        h, client, _ = build(self)
        await h.coordinator.hooks.emit("teamwork:decision_detected", {
            "detection_id": "not-a-detector-candidate", "session_id": h.sid,
            "kind": "decision", "claim": "Unrequested publication"})
        self.assertEqual(insight_ops(client), [])

    async def test_a_disabled_detector_kind_cannot_use_the_other_recorders_subscription(self):
        h, client, _ = build(self)
        self.assertFalse(h.lesson_detection)
        await h._record_verdict(dict(RECORD_VERDICT), 1,
            binding=h.detection_binding(), uri_scheme="teamwork-lesson-window://",
            reserve=None, release=None, log_label="lesson")
        self.assertEqual(insight_ops(client), [])

    async def test_carriers_scrub_all_text_and_remain_json_serializable(self):
        h, _, _ = build(self)
        seen = []
        async def observe(event, data):
            if event.endswith("_detected"):
                seen.append(json.dumps(data))
        h.coordinator.hooks.emit = observe
        token = h.connection["token"]
        await detect(h, claim=token, kind=token, what_it_does_not_establish=token)
        self.assertEqual(len(seen), 1)
        self.assertNotIn(token, seen[0])
        self.assertEqual(h._detected_candidates, {})

    async def test_observation_can_stay_enabled_with_recorder_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            coordinator = Coordinator()
            cleanup = await mount(coordinator, {
                "base_url": "https://team.example.invalid", "project_id": "p",
                "token": "synthetic-recorder-test", "share_visible_turns": True,
                "detect_decisions": True, "record_detected": False,
                "file_inbound_reports": False, "journal_path": str(Path(tmp)/"j.db")})
            h = cleanup.__self__
            self.assertTrue(h.decision_detection)
            self.assertNotIn("teamwork:decision_detected", coordinator.hooks.handlers)
            seen = []
            async def emit(event, data): seen.append(event)
            coordinator.hooks.emit = emit
            await detect(h)
            self.assertEqual(seen, ["teamwork:decision_detected"])
            self.assertEqual(h.recall_decisions(), [])

    async def test_real_core_recorder_returns_a_valid_hook_result(self):
        from amplifier_core import AmplifierSession
        session = AmplifierSession({"session": {"orchestrator": "loop-streaming", "context": "context-simple"},
                                    "providers": [], "tools": [], "hooks": []})
        await session.initialize()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cleanup = await mount(session.coordinator, {
                    "base_url": "https://team.example.invalid", "project_id": "p",
                    "token": "synthetic-recorder-test", "share_visible_turns": True,
                    "detect_decisions": True, "file_inbound_reports": False,
                    "journal_path": str(Path(tmp)/"j.db")})
                h = cleanup.__self__
                from test_decision_detection import Client
                class ConfirmedClient(Client):
                    def request(self, endpoint, body, key=None):
                        response = super().request(endpoint, body, key)
                        if endpoint == "publish":
                            return {"results": [{"id": op["id"], "version": 1} for op in body["operations"]]}
                        return response
                h.client = ConfirmedClient()
                with self.assertNoLogs("amplifier_core.hooks", level="ERROR"):
                    await detect(h)
                    await asyncio.gather(*tuple(h._detection_event_tasks))
                self.assertEqual(len(insight_ops(h.client)), 1)
                self.assertEqual(h.recall_decisions()[0]["outcome"], "recorded")
                await h.drain_detection_events()
        finally:
            await session.cleanup()


class JournalHonesty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"j.db"

    def test_same_claim_as_decision_and_lesson_keeps_both(self):
        journal = Journal(self.path)
        for kind in ("decision", "lesson"):
            journal.record_decision_body("s", "same-fingerprint", kind, "Same words")
        self.assertEqual({r["kind"] for r in journal.decisions("s")}, {"decision", "lesson"})

    def test_pre_release_table_migration_retains_rows_without_claiming_acceptance(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE decision_body (session TEXT, fingerprint TEXT, kind TEXT, claim TEXT, "
                         "basis TEXT, confidence TEXT, limitations TEXT, record_id TEXT, created_at TEXT, "
                         "PRIMARY KEY(session,fingerprint))")
            conn.execute("INSERT INTO decision_body VALUES ('s','fp','decision','Original claim',NULL,NULL,NULL,'r','2026-01-01')")
        journal = Journal(self.path)
        self.assertEqual(journal.decisions("s")[0]["outcome"], "unverified")
        self.assertEqual(Journal(self.path).decisions("s")[0]["claim"], "Original claim")
        journal.record_decision_body("s", "fp", "lesson", "Also a lesson")
        self.assertEqual(len(journal.decisions("s")), 2)

    def test_recall_bounds_all_fields_and_rechecks_binding_after_io(self):
        h = TeamworkHook(Coordinator(), {"base_url": "https://team.example.invalid", "project_id": "p",
                         "token": "synthetic-recall-test"}, Journal(self.path), object())
        h.journal.record_decision_body(h.sid, "fp", "decision", "claim", limitations="x"*100000,
                                       outcome="acceptance_unknown")
        for limit in (None, "bad", [], float("inf")):
            entry = h.recall_decisions(limit)[0]
            self.assertLessEqual(len(entry["limitations"]), 1000)
            self.assertEqual(entry["outcome"], "acceptance_unknown")
        original = h.journal.decisions
        def changed(sid, limit):
            rows = original(sid, limit)
            h.rebind("other-project")
            return rows
        h.journal.decisions = changed
        self.assertEqual(h.recall_decisions(), [])
