"""decision_judge -- an in-process judge, not wired into any hook lifecycle event.

Covers: the judge never raises into its caller (on a construction failure, a
provider error, or a malformed response); a real child session with parent_id
set never receives the teamwork hook; a caller-supplied tool -- specifically
the caller's own already-bound RecordInsightTool instance -- can be mounted
into the judge session and called from it, keeping attribution on the PARENT
rather than on the throwaway judge session; and window-payload construction
stays bounded and excludes tool-call payloads.

The provenance and mounting tests build a real `amplifier_core.AmplifierSession`
(no fakes for the session itself) but never touch a network: no provider is
configured, and the fixture client used with `TeamworkHook` is in-memory, the
same style `tests/test_hook.py` already uses.
"""

import sys
import asyncio
import importlib.util
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import (
    Journal,
    RecordInsightTool,
    TeamworkHook,
    decision_judge,
    detection_model_setting,
)
from amplifier_module_hooks_teamwork import mount as teamwork_mount


class FakeJudgeSession:
    """Stands in for the real AmplifierSession judge_window() builds, so the
    never-raises tests can drive a provider error or a malformed response
    without a network."""

    def __init__(self, execute_result=None, execute_error=None):
        self.execute_result = execute_result
        self.execute_error = execute_error
        self.cleaned_up = False

    async def execute(self, prompt):
        if self.execute_error is not None:
            raise self.execute_error
        return self.execute_result

    async def cleanup(self):
        self.cleaned_up = True


EMPTY_WINDOW = {"turns": [], "tool_calls": []}


class JudgeNeverRaises(unittest.IsolatedAsyncioTestCase):
    """A judge that breaks a turn is worse than no judge -- every failure mode
    degrades to no_verdict(...) rather than raising."""

    async def test_a_provider_error_degrades_to_no_verdict_and_still_cleans_up(self):
        fake = FakeJudgeSession(execute_error=RuntimeError("provider is down"))
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note", True)),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])
        self.assertIn("provider call failed", verdict["reason"])
        self.assertTrue(
            fake.cleaned_up, "cleanup must still run after execute() raises"
        )

    async def test_malformed_non_json_output_degrades_to_no_verdict(self):
        fake = FakeJudgeSession(
            execute_result="I cannot comply with structured output today."
        )
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note", True)),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])
        self.assertIn("could not be parsed", verdict["reason"])

    async def test_a_response_missing_the_record_key_degrades_to_no_verdict(self):
        fake = FakeJudgeSession(execute_result='{"claim": "x", "confidence": "high"}')
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note", True)),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])

    async def test_session_construction_failure_degrades_to_no_verdict(self):
        async def boom(*args, **kwargs):
            raise RuntimeError("no provider configured for this environment")

        with patch.object(decision_judge, "build_judge_session", boom):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])
        self.assertIn("could not build a judge session", verdict["reason"])

    async def test_a_well_formed_fenced_verdict_is_parsed_through(self):
        raw = (
            "```json\n"
            '{"record": true, "kind": "permission boundary", "claim": "narrow the scope",\n'
            '"basis": "observation", "what_it_does_not_establish": "nothing about other cases",\n'
            '"confidence": "high", "reason": "structural"}\n'
            "```"
        )
        fake = FakeJudgeSession(execute_result=raw)
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note", True)),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertTrue(verdict["record"])
        self.assertEqual(verdict["claim"], "narrow the scope")
        self.assertEqual(verdict["confidence"], "high")

    async def test_an_invalid_enum_value_makes_the_verdict_unavailable(
        self,
    ):
        raw = (
            '{"record": true, "kind": "k", "claim": "c", "basis": "vibes", '
            '"what_it_does_not_establish": "w", "confidence": "super sure", "reason": "r"}'
        )
        fake = FakeJudgeSession(execute_result=raw)
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note", True)),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])
        self.assertFalse(verdict["available"])
        self.assertIsNone(verdict["basis"])
        self.assertIsNone(verdict["confidence"])


class WindowPayloadTests(unittest.TestCase):
    """build_window_payload stays bounded and never carries tool-call payloads."""

    def test_only_the_last_max_turns_survive(self):
        turns = [
            {"user_prompt": "u%d" % i, "agent_responses": [{"text": "a%d" % i}]}
            for i in range(20)
        ]
        window = decision_judge.build_window_payload(turns, max_turns=3)
        self.assertEqual(len(window["turns"]), 3)
        self.assertEqual(window["turns"][-1]["user_prompt"], "u19")

    def test_long_text_is_clipped_to_the_bound(self):
        turns = [{"user_prompt": "x" * 10000, "agent_responses": []}]
        window = decision_judge.build_window_payload(turns, max_turn_chars=100)
        self.assertLessEqual(len(window["turns"][0]["user_prompt"]), 100)

    def test_tool_calls_are_reduced_to_name_and_target_only(self):
        calls = [
            {
                "name": "teamwork_record_insight",
                "target": "insight:abc123",
                "arguments": {"claim": "a claim nobody outside this turn should see"},
                "result": {"huge": "payload that must never reach the judge"},
            }
        ]
        window = decision_judge.build_window_payload([], calls)
        self.assertEqual(
            window["tool_calls"],
            [{"name": "teamwork_record_insight", "target": "insight:abc123"}],
        )

    def test_a_tool_call_missing_a_name_is_dropped_rather_than_guessed(self):
        calls = [{"target": "only-a-target"}]
        window = decision_judge.build_window_payload([], calls)
        self.assertEqual(window["tool_calls"], [])

    def test_an_empty_window_is_still_a_valid_bounded_payload(self):
        window = decision_judge.build_window_payload([], [])
        self.assertEqual(window, {"turns": [], "tool_calls": [], "tally": []})

    def test_a_turn_with_neither_prompt_nor_response_is_dropped(self):
        window = decision_judge.build_window_payload(
            [{"user_prompt": "", "agent_responses": []}]
        )
        self.assertEqual(window["turns"], [])

    def test_tally_is_bounded_by_count_and_reboundeds_a_caller_supplied_one(self):
        """build_window_payload never trusts an upstream tally either --
        matching the "never raises, never trusts an upstream bound" posture
        it already applies to turns and tool_calls.
        """
        tally = [
            {"record_type": "insight", "record_id": "i%d" % i, "version": 1, "claim": "c%d" % i}
            for i in range(30)
        ]
        window = decision_judge.build_window_payload([], [], tally, max_tally_entries=5)
        self.assertEqual(len(window["tally"]), 5)

    def test_tally_claim_text_is_clipped_to_the_bound(self):
        tally = [{"record_type": "insight", "record_id": "i1", "version": 1, "claim": "x" * 500}]
        window = decision_judge.build_window_payload([], [], tally, max_tally_chars=50)
        self.assertLessEqual(len(window["tally"][0]["claim"]), 50)

    def test_a_malformed_tally_entry_is_dropped_not_fatal(self):
        tally = [
            {"record_type": "insight", "record_id": "i1", "version": 1, "claim": "keep me"},
            {"record_type": "insight", "record_id": "i2", "claim": "missing a version"},
            {"record_type": "work", "record_id": "w1", "version": 1, "claim": "wrong record type"},
            "not even a dict",
            {"record_type": "idea", "record_id": "d1", "version": "not-an-int", "claim": "bad version type"},
        ]
        window = decision_judge.build_window_payload([], [], tally)
        self.assertEqual([entry["record_id"] for entry in window["tally"]], ["i1"])

    def test_no_tally_supplied_is_an_empty_list_not_a_missing_key(self):
        window = decision_judge.build_window_payload([], [])
        self.assertEqual(window["tally"], [])


class BuildTallyTests(unittest.TestCase):
    """build_tally() reads the hook's own synchronized cache -- no network
    call -- and degrades safely on anything malformed.
    """

    def _source(self, record_type, record_id, version, content):
        return {
            "record": {"id": record_id, "record_type": record_type, "version": version, "content": content},
            "delivery_id": "manifest",
        }

    def test_only_insight_and_idea_record_types_are_read(self):
        cache = {
            "insight:i1": self._source("insight", "i1", 1, {"claim": "an insight"}),
            "idea:d1": self._source("idea", "d1", 1, {"text": "an idea"}),
            "work:w1": self._source("work", "w1", 1, {"title": "not knowledge"}),
            "person:p1": self._source("person", "p1", 1, {"name": "not knowledge either"}),
        }
        tally = decision_judge.build_tally(cache)
        self.assertEqual({e["record_id"] for e in tally}, {"i1", "d1"})

    def test_most_recent_first_by_updated_at(self):
        cache = {
            "insight:old": self._source("insight", "old", 1,
                                         {"claim": "older", "updated_at": "2026-01-01T00:00:00+00:00"}),
            "insight:new": self._source("insight", "new", 1,
                                         {"claim": "newer", "updated_at": "2026-09-01T00:00:00+00:00"}),
        }
        tally = decision_judge.build_tally(cache)
        self.assertEqual([e["record_id"] for e in tally], ["new", "old"])

    def test_falls_back_to_created_at_when_never_updated(self):
        cache = {
            "insight:a": self._source("insight", "a", 1,
                                       {"claim": "a", "created_at": "2026-01-01T00:00:00+00:00"}),
            "insight:b": self._source("insight", "b", 1,
                                       {"claim": "b", "created_at": "2026-06-01T00:00:00+00:00"}),
        }
        tally = decision_judge.build_tally(cache)
        self.assertEqual([e["record_id"] for e in tally], ["b", "a"])

    def test_entries_carry_claim_title_only_never_the_whole_record(self):
        cache = {
            "insight:i1": self._source("insight", "i1", 3, {
                "claim": "the transferable statement",
                "limitations": "must never leak into the tally",
                "source_session_id": "must never leak into the tally either",
            }),
        }
        tally = decision_judge.build_tally(cache)
        self.assertEqual(tally, [{"record_type": "insight", "record_id": "i1", "version": 3,
                                  "claim": "the transferable statement"}])

    def test_bounded_by_max_entries(self):
        cache = {
            "insight:i%d" % i: self._source("insight", "i%d" % i, 1, {"claim": "c%d" % i})
            for i in range(50)
        }
        tally = decision_judge.build_tally(cache, max_entries=7)
        self.assertEqual(len(tally), 7)

    def test_claim_text_is_clipped(self):
        cache = {"insight:i1": self._source("insight", "i1", 1, {"claim": "x" * 1000})}
        tally = decision_judge.build_tally(cache, max_chars=20)
        self.assertLessEqual(len(tally[0]["claim"]), 20)

    def test_empty_cache_degrades_to_an_empty_tally(self):
        self.assertEqual(decision_judge.build_tally({}), [])
        self.assertEqual(decision_judge.build_tally(None), [])

    def test_malformed_cache_entries_are_skipped_not_fatal(self):
        cache = {
            "insight:good": self._source("insight", "good", 1, {"claim": "the only usable entry"}),
            "insight:no-content": {"record": {"id": "x", "record_type": "insight", "version": 1}},
            "insight:no-version": {"record": {"id": "y", "record_type": "insight",
                                              "content": {"claim": "no version"}}},
            "insight:no-claim-text": self._source("insight", "z", 1, {"limitations": "no claim field"}),
            "broken": "not even a dict",
            "insight:none-record": {"record": None},
        }
        tally = decision_judge.build_tally(cache)
        self.assertEqual([e["record_id"] for e in tally], ["good"])


class ParseLinksTests(unittest.TestCase):
    """_parse_links() is the backstop when a judge's `links` field is
    malformed -- an entry that does not match RecordInsightTool's own
    `record` evidence-kind shape is dropped, never fatal to the rest.
    """

    def test_a_well_formed_link_survives(self):
        links = decision_judge._parse_links(
            [{"record_type": "insight", "record_id": "i1", "version": 2}]
        )
        self.assertEqual(links, [{"record_type": "insight", "record_id": "i1", "version": 2}])

    def test_non_list_input_degrades_to_no_links(self):
        for raw in (None, "not a list", {"record_type": "insight"}, 42):
            with self.subTest(raw=raw):
                self.assertEqual(decision_judge._parse_links(raw), [])

    def test_a_malformed_entry_is_dropped_not_fatal_to_the_rest(self):
        links = decision_judge._parse_links([
            {"record_type": "insight", "record_id": "keep", "version": 1},
            {"record_type": "person", "record_id": "bad-type", "version": 1},
            {"record_type": "idea", "record_id": "", "version": 1},
            {"record_type": "idea", "record_id": "bad-version", "version": "not-an-int"},
            {"record_type": "work", "record_id": "bad-version-bool", "version": True},
            "not a dict",
        ])
        self.assertEqual(links, [{"record_type": "insight", "record_id": "keep", "version": 1}])

    def test_parse_verdict_carries_valid_links_through_only_on_record_true(self):
        raw = json.dumps({
            "record": True, "kind": "k", "claim": "c", "basis": "observation",
            "what_it_does_not_establish": "w", "confidence": "high", "reason": "r",
            "links": [{"record_type": "insight", "record_id": "i1", "version": 1}],
        })
        verdict = decision_judge._parse_verdict(raw)
        self.assertEqual(verdict["links"], [{"record_type": "insight", "record_id": "i1", "version": 1}])

    def test_parse_verdict_skip_verdict_always_has_empty_links(self):
        raw = json.dumps({
            "record": False, "kind": None, "claim": None, "basis": None,
            "what_it_does_not_establish": None, "confidence": "high", "reason": "duplicate of i1",
            "links": [{"record_type": "insight", "record_id": "i1", "version": 1}],
        })
        verdict = decision_judge._parse_verdict(raw)
        self.assertEqual(verdict["links"], [])

    def test_parse_verdict_missing_links_field_entirely_still_parses(self):
        """links is deliberately NOT in VERDICT_FIELDS -- its absence must
        never invalidate an otherwise well-formed verdict."""
        raw = json.dumps({
            "record": True, "kind": "k", "claim": "c", "basis": "observation",
            "what_it_does_not_establish": "w", "confidence": "high", "reason": "r",
        })
        verdict = decision_judge._parse_verdict(raw)
        self.assertTrue(verdict["available"])
        self.assertEqual(verdict["links"], [])

    def test_no_verdict_carries_an_empty_links_list(self):
        self.assertEqual(decision_judge.no_verdict("x")["links"], [])


class HonestJudgeFailures(unittest.IsolatedAsyncioTestCase):
    async def test_failure_details_are_not_returned_or_logged(self):
        private_detail = "private-provider-request-and-credential-fixture"
        fake = FakeJudgeSession(execute_error=RuntimeError(private_detail))
        with patch.object(decision_judge, "build_judge_session", AsyncMock(return_value=(fake, "note", True))):
            with self.assertLogs(decision_judge.logger, level="WARNING") as logs:
                verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["available"])
        self.assertNotIn(private_detail, json.dumps(verdict))
        self.assertNotIn(private_detail, "\n".join(logs.output))

    async def test_incomplete_record_cannot_be_a_valid_verdict(self):
        fake = FakeJudgeSession(execute_result='{"record": true}')
        with patch.object(decision_judge, "build_judge_session", AsyncMock(return_value=(fake, "note", True))):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["available"])
        self.assertFalse(verdict["record"])

    async def test_failed_initialization_cleans_up_partial_session(self):
        fake = SimpleNamespace(initialize=AsyncMock(side_effect=RuntimeError("fixture failure")),
                               cleanup=AsyncMock())
        with patch("amplifier_core.AmplifierSession", return_value=fake):
            with self.assertRaises(RuntimeError):
                await decision_judge.build_judge_session(object())
        fake.cleanup.assert_awaited_once()

    async def test_cancelled_initialization_cleans_up_and_preserves_cancellation(self):
        fake = SimpleNamespace(initialize=AsyncMock(side_effect=asyncio.CancelledError()),
                               cleanup=AsyncMock())
        with patch("amplifier_core.AmplifierSession", return_value=fake):
            with self.assertRaises(asyncio.CancelledError):
                await decision_judge.judge_window(object(), EMPTY_WINDOW)
        fake.cleanup.assert_awaited_once()

    async def test_hanging_cleanup_is_bounded_after_cancellation(self):
        async def hanging_cleanup():
            await asyncio.Event().wait()
        fake = SimpleNamespace(initialize=AsyncMock(side_effect=asyncio.CancelledError()),
                               cleanup=AsyncMock(side_effect=hanging_cleanup))
        with patch("amplifier_core.AmplifierSession", return_value=fake):
            with patch.object(decision_judge, "CLEANUP_TIMEOUT_SECONDS", 0.01):
                with self.assertLogs(decision_judge.logger, level="WARNING"):
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(decision_judge.judge_window(object(), EMPTY_WINDOW), timeout=1)
        fake.cleanup.assert_awaited_once()

    async def test_resolved_fast_provider_cannot_lose_to_parent_priority(self):
        config = {"providers": [
            {"module": "provider-expensive", "config": {"priority": 1, "default_model": "large"}},
            {"module": "provider-cheap", "config": {"priority": 100, "default_model": "old"}},
        ]}
        snapshot = json.dumps(config, sort_keys=True)
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=[{"provider": "cheap", "model": "small"}]))
        parent = SimpleNamespace(config=config, get_capability=lambda name: resolver)
        providers, _, _honored = await decision_judge._resolve_providers(parent, "fast")
        self.assertEqual([spec["module"] for spec in providers], ["provider-cheap"])
        self.assertEqual(providers[0]["config"]["default_model"], "small")
        self.assertEqual(json.dumps(config, sort_keys=True), snapshot)

    async def test_unavailable_output_is_not_credited_as_a_correct_skip(self):
        path = Path(__file__).resolve().parents[1] / "evals/02-decision-detection/run.py"
        spec = importlib.util.spec_from_file_location("decision_eval_runner", path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        result = runner.assess_case({"id": "fixture", "expected": "SKIP", "note": "fixture"},
                                    decision_judge.no_verdict("provider unavailable"))
        self.assertEqual(result["judged"], "UNAVAILABLE")
        self.assertFalse(result["correct"])

        fake = SimpleNamespace(initialize=AsyncMock(side_effect=RuntimeError("fixture setup")),
                               cleanup=AsyncMock())
        with patch("amplifier_core.AmplifierSession", return_value=fake):
            with self.assertRaises(RuntimeError):
                await runner._build_root_session()
        fake.cleanup.assert_awaited_once()


class StandardFoundationSession(unittest.IsolatedAsyncioTestCase):
    async def test_standard_loop_executes_one_offline_call_with_parent_source_resolver(self):
        import amplifier_module_context_simple
        import amplifier_module_loop_streaming
        from amplifier_core.message_models import ChatResponse, TextBlock
        from amplifier_core.models import ProviderInfo

        class OfflineProvider:
            name = "offline-fixture"
            priority = 1
            context_window = 32000
            max_output_tokens = 256
            calls = 0

            def get_info(self):
                return ProviderInfo(id=self.name, display_name=self.name)

            async def complete(self, request, **kwargs):
                self.calls += 1
                return ChatResponse(content=[TextBlock(text=json.dumps({
                    "record": False, "kind": None, "claim": None, "basis": None,
                    "what_it_does_not_establish": None, "confidence": "high",
                    "reason": "A synthetic fixture with no durable decision.",
                }))])

            def parse_tool_calls(self, response):
                return []

        sources = {
            "loop-streaming": Path(amplifier_module_loop_streaming.__file__).parent.parent,
            "context-simple": Path(amplifier_module_context_simple.__file__).parent.parent,
        }
        class LocalResolver:
            def resolve(self, module_id, **kwargs):
                return SimpleNamespace(resolve=lambda: sources[module_id])
        resolver = LocalResolver()
        parent = SimpleNamespace(session_id="offline-parent", config={"providers": []},
                                 get=lambda name: resolver if name == "module-source-resolver" else None,
                                 approval_system=object())
        session, _, _honored = await decision_judge.build_judge_session(parent)
        provider = OfflineProvider()
        try:
            self.assertIs(session.coordinator.get("module-source-resolver"), resolver)
            self.assertIs(session.coordinator.approval_system, parent.approval_system)
            self.assertEqual(session.parent_id, parent.session_id)
            self.assertEqual(session.config["session"]["orchestrator"]["config"]["max_iterations"], 1)
            await session.coordinator.mount("providers", provider, name=provider.name)
            raw = await session.execute(decision_judge._prompt_for(EMPTY_WINDOW))
            self.assertEqual(provider.calls, 1)
            self.assertTrue(decision_judge._parse_verdict(raw)["available"])
            self.assertIsNone(session.coordinator.get_capability("teamwork.session_id"))
        finally:
            await session.cleanup()


class ChildSessionExcludesTeamworkHook(unittest.IsolatedAsyncioTestCase):
    """A real child session with parent_id set never mounts the teamwork hook --
    the exact mechanism build_judge_session relies on instead of a second guard.
    """

    async def test_a_real_child_session_with_parent_id_set_never_mounts_the_hook(self):
        from amplifier_core import AmplifierSession

        config = {
            "session": {"orchestrator": "loop-streaming", "context": "context-simple"},
            "providers": [],
            "tools": [],
            "hooks": [],
        }
        session = AmplifierSession(config, parent_id="fake-parent-for-this-test")
        await session.initialize()
        try:
            coordinator = session.coordinator
            self.assertEqual(coordinator.parent_id, "fake-parent-for-this-test")
            result = await teamwork_mount(
                coordinator,
                {
                    "share_visible_turns": True,
                    "base_url": "https://team.example.invalid",
                    "project_id": "p",
                    "token": "t",
                },
            )
            self.assertIsNone(result)
            self.assertIsNone(coordinator.get_capability("teamwork.session_id"))
            self.assertEqual(coordinator.mount_points.get("tools", {}), {})
        finally:
            await session.cleanup()


class StubTool:
    def __init__(self, name="stub_tool"):
        self.name = name
        self.description = "stub"
        self.input_schema = {"type": "object", "properties": {}}
        self.calls = []

    async def execute(self, input):
        from amplifier_core.models import ToolResult

        self.calls.append(input)
        return ToolResult(success=True, output={"echo": input})


class ToolMountingTests(unittest.IsolatedAsyncioTestCase):
    """build_judge_session must be able to mount tools when asked -- this is a
    requirement, not an option."""

    async def test_a_supplied_stub_tool_is_mounted_and_callable(self):
        stub = StubTool()
        session, _note, _honored = await decision_judge.build_judge_session(
            object(), extra_tools=[stub]
        )
        try:
            fetched = session.coordinator.get("tools", "stub_tool")
            self.assertIs(fetched, stub)
            result = await fetched.execute({"x": 1})
            self.assertTrue(result.success)
            self.assertEqual(stub.calls, [{"x": 1}])
        finally:
            await session.cleanup()

    async def test_no_tools_are_mounted_by_default(self):
        session, _note, _honored = await decision_judge.build_judge_session(object())
        try:
            self.assertEqual(session.coordinator.mount_points.get("tools", {}), {})
        finally:
            await session.cleanup()

    async def test_mounting_the_callers_own_record_insight_tool_keeps_attribution_on_the_parent(
        self,
    ):
        """The provenance guarantee this module's docstring describes: a
        RecordInsightTool already bound to the PARENT's own hook, mounted into
        and called from inside a judge child session, still writes
        source_session_id = the PARENT's hook.sid -- never this throwaway
        judge session's own identity.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        connection = {
            "base_url": "https://team.example.invalid",
            "project_id": "teamwork",
            "token": "test-credential-no-real-secret",
        }

        class FakeParentCoordinator:
            session_id = "parent-native-session"

            def __init__(self):
                self.config = {"providers": []}

            def get(self, name):
                return None

            async def mount(self, point, value, name):
                pass

            def get_capability(self, name):
                return None

        class FakeClient:
            def __init__(self):
                self.requests = []

            def request(self, endpoint, body, key=None):
                self.requests.append((endpoint, body, key))
                return {"results": [{"id": op["id"], "version": 1}
                                    for op in body["operations"]]}

        parent_coordinator = FakeParentCoordinator()
        journal = Journal(Path(tmp.name) / "q.db")
        client = FakeClient()
        parent_hook = TeamworkHook(parent_coordinator, connection, journal, client)
        record_tool = RecordInsightTool(parent_hook)

        session, _note, _honored = await decision_judge.build_judge_session(
            parent_coordinator, extra_tools=[record_tool]
        )
        try:
            self.assertNotEqual(session.session_id, parent_hook.sid)
            mounted = session.coordinator.get("tools", "teamwork_record_insight")
            self.assertIs(mounted, record_tool)
            result = await mounted.execute(
                {
                    "claim": "Retries above 3 do not improve delivery odds for this transport.",
                    "basis": "observation",
                    "confidence": "high",
                    "limitations": "Only observed against the staging relay, not production.",
                    "evidence": [
                        {
                            "kind": "artifact",
                            "uri": "https://example.invalid/logs/1",
                            "label": "run log",
                        }
                    ],
                }
            )
            self.assertTrue(result.success, result.error)
            op = next(op for _, body, _ in client.requests for op in body["operations"]
                      if op["op"] == "insight.upsert")
            self.assertEqual(op["data"]["source_session_id"], parent_hook.sid)
            self.assertNotEqual(op["data"]["source_session_id"], session.session_id)
        finally:
            await session.cleanup()


if __name__ == "__main__":
    unittest.main()


class ConfiguredDetectionModelTakesOverWhenTheRoleCannotBeHonored(unittest.IsolatedAsyncioTestCase):
    """The `fast` role is a preference, and in practice it is usually NOT
    honored -- measured twice: a resolver that resolves it to a glob whose
    live model-list lookup fails, and a session with no resolver registered.
    Both then run the judge on the CALLING session's frontier model, on every
    judged turn. `detection_model` is the operator's answer, and it applies
    only where the role already failed.
    """

    def _parent(self, resolver=None):
        config = {"providers": [
            {"module": "provider-anthropic", "config": {"priority": 1, "default_model": "claude-opus-5"}},
        ]}
        return SimpleNamespace(config=config, get_capability=lambda name: resolver)

    async def test_no_resolver_at_all_falls_back_to_the_configured_model(self):
        providers, note, honored = await decision_judge._resolve_providers(
            self._parent(resolver=None), "fast", "anthropic/claude-haiku-4-5")
        self.assertTrue(honored)
        self.assertEqual(providers[0]["config"]["default_model"], "claude-haiku-4-5")
        self.assertIn("detection_model", note)

    async def test_a_glob_the_judge_cannot_expand_falls_back_to_the_configured_model(self):
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=[{"provider": "anthropic", "model": "claude-haiku-*"}]))
        providers, _note, honored = await decision_judge._resolve_providers(
            self._parent(resolver), "fast", "claude-haiku-4-5")
        self.assertTrue(honored)
        self.assertEqual(providers[0]["config"]["default_model"], "claude-haiku-4-5")

    async def test_a_role_that_DOES_resolve_wins_over_the_configured_model(self):
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=[{"provider": "anthropic", "model": "resolved-small"}]))
        providers, _note, honored = await decision_judge._resolve_providers(
            self._parent(resolver), "fast", "anthropic/ignored-me")
        self.assertTrue(honored)
        self.assertEqual(providers[0]["config"]["default_model"], "resolved-small")

    async def test_without_a_configured_model_todays_behaviour_is_unchanged(self):
        providers, _note, honored = await decision_judge._resolve_providers(
            self._parent(resolver=None), "fast", None)
        self.assertFalse(honored)
        self.assertEqual(providers[0]["config"]["default_model"], "claude-opus-5")

    async def test_a_configured_provider_not_among_the_parents_is_refused_not_invented(self):
        providers, note, honored = await decision_judge._resolve_providers(
            self._parent(resolver=None), "fast", "openai/gpt-nope")
        self.assertFalse(honored)
        self.assertEqual(providers[0]["config"]["default_model"], "claude-opus-5")
        self.assertIn("not", note)

    def test_the_setting_refuses_an_ambiguous_value_rather_than_coercing_it(self):
        self.assertIsNone(detection_model_setting({}))
        self.assertEqual(detection_model_setting({"detection_model": "  anthropic/x  "}), "anthropic/x")
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            detection_model_setting({"detection_model": True})
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            detection_model_setting({"detection_model": "   "})
