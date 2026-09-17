"""Publication rejects invented references even if the judge wrapper is replaced."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
import amplifier_module_hooks_teamwork as teamwork
from test_decision_detection import RECORD_VERDICT, add_turn, build, insight_ops


class AcceptedClient:
    def __init__(self):
        self.requests = []

    def request(self, endpoint, body, key=None):
        self.requests.append((endpoint, body, key))
        return {"results": [{"id": op["id"], "version": 1} for op in body.get("operations", [])]}


class PublicationReferenceBoundary(unittest.IsolatedAsyncioTestCase):
    async def publish(self, detector, shown, returned, mutate=False):
        hook, _, journal = build(self)
        hook.client = client = AcceptedClient()
        add_turn(hook, "Synthetic context", "Synthetic response")
        window = {"turns": [], "tool_calls": [], "tally": shown}

        async def untrusted_judge(*args, **kwargs):
            if mutate:
                window["tally"][:] = [dict(returned[-1], claim="Later cache value")]
            return dict(RECORD_VERDICT, available=True, links=returned)

        entry = hook._judge_and_record if detector == "decision" else hook._judge_and_record_lesson
        name = "judge_window" if detector == "decision" else "judge_lesson_window"
        with patch.object(teamwork.decision_judge, name, untrusted_judge):
            await entry(window, 1)
        with journal.connect() as connection:
            outcomes = connection.execute("SELECT kind,outcome,tally_size,link_count FROM detection_outcome").fetchall()
        self.assertEqual(len(insight_ops(client)), 1)
        self.assertEqual(outcomes[0][:2], (detector, "recorded"))
        return insight_ops(client)[0]["data"]["evidence_refs"], outcomes[0]

    async def test_both_publication_paths_drop_unseen_wrong_version_and_duplicate_links(self):
        valid = {"record_type": "insight", "record_id": "shown", "version": 2}
        for detector in ("decision", "lesson"):
            with self.subTest(detector=detector):
                refs, outcome = await self.publish(detector, [dict(valid, claim="Prior claim")], [
                    dict(valid, record_id="not-shown"), dict(valid, version=1),
                    dict(valid, version=True), dict(valid, version=-1),
                    dict(valid, record_type="work"), valid, valid,
                ])
                self.assertEqual(refs[0]["kind"], "external")
                self.assertEqual(refs[1:], [{"kind": "record", **valid}])
                self.assertEqual(outcome[2:], (1, 1))

    async def test_no_tally_still_records_claim_without_invented_evidence(self):
        refs, outcome = await self.publish("decision", [], [
            {"record_type": "idea", "record_id": "invented", "version": 1}])
        self.assertEqual([ref["kind"] for ref in refs], ["external"])
        self.assertEqual(outcome[2:], (0, 0))

    async def test_publishing_uses_original_snapshot_after_judge_mutates_window(self):
        original = {"record_type": "idea", "record_id": "original", "version": 1}
        later = dict(original, record_id="later")
        refs, _ = await self.publish("lesson", [dict(original, claim="Original")], [original, later], mutate=True)
        self.assertEqual(refs[1:], [{"kind": "record", **original}])

    async def test_publication_count_is_bounded_even_with_replaced_judge(self):
        refs = [{"record_type": "insight", "record_id": "i%d" % i, "version": 1} for i in range(30)]
        evidence, outcome = await self.publish("decision", [dict(ref, claim="Claim") for ref in refs], refs+refs)
        self.assertEqual(len(evidence), 21)
        self.assertEqual(outcome[3], 20)


class ModelValidationAtMount(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_setting_is_rejected_before_connection_or_journal_access(self):
        coordinator = SimpleNamespace(parent_id=None)
        for value in ("anthropic/", "/small", "a/b/c", "anthropic/model*", "model name", True):
            with self.subTest(value=value), patch.object(teamwork, "resolve_connection") as resolve:
                with self.assertRaises(ValueError):
                    await teamwork.mount(coordinator, {"share_visible_turns": True, "detect_decisions": True,
                                                       "detection_model": value})
                resolve.assert_not_called()
