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
            AsyncMock(return_value=(fake, "note")),
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
            AsyncMock(return_value=(fake, "note")),
        ):
            verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["record"])
        self.assertIn("could not be parsed", verdict["reason"])

    async def test_a_response_missing_the_record_key_degrades_to_no_verdict(self):
        fake = FakeJudgeSession(execute_result='{"claim": "x", "confidence": "high"}')
        with patch.object(
            decision_judge,
            "build_judge_session",
            AsyncMock(return_value=(fake, "note")),
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
            AsyncMock(return_value=(fake, "note")),
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
            AsyncMock(return_value=(fake, "note")),
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
        self.assertEqual(window, {"turns": [], "tool_calls": []})

    def test_a_turn_with_neither_prompt_nor_response_is_dropped(self):
        window = decision_judge.build_window_payload(
            [{"user_prompt": "", "agent_responses": []}]
        )
        self.assertEqual(window["turns"], [])


class HonestJudgeFailures(unittest.IsolatedAsyncioTestCase):
    async def test_failure_details_are_not_returned_or_logged(self):
        private_detail = "private-provider-request-and-credential-fixture"
        fake = FakeJudgeSession(execute_error=RuntimeError(private_detail))
        with patch.object(decision_judge, "build_judge_session", AsyncMock(return_value=(fake, "note"))):
            with self.assertLogs(decision_judge.logger, level="WARNING") as logs:
                verdict = await decision_judge.judge_window(object(), EMPTY_WINDOW)
        self.assertFalse(verdict["available"])
        self.assertNotIn(private_detail, json.dumps(verdict))
        self.assertNotIn(private_detail, "\n".join(logs.output))

    async def test_incomplete_record_cannot_be_a_valid_verdict(self):
        fake = FakeJudgeSession(execute_result='{"record": true}')
        with patch.object(decision_judge, "build_judge_session", AsyncMock(return_value=(fake, "note"))):
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
        providers, _ = await decision_judge._resolve_providers(parent, "fast")
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
        session, _ = await decision_judge.build_judge_session(parent)
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
        session, _note = await decision_judge.build_judge_session(
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
        session, _note = await decision_judge.build_judge_session(object())
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

        session, _note = await decision_judge.build_judge_session(
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
