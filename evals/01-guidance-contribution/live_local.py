#!/usr/bin/env python3
"""Bounded local 2x2 routing observation, using installed Foundation modules.

Only the explicitly selected Anthropic model receives fictional conversation
content. The service runs on loopback from a local checkout; no mirrors, shell
tools, production connections, transcript files, or credential copies are used.
The old DTU entry points remain disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from collections import Counter
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata as metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODEL = "claude-haiku-4-5-20251001"
PEOPLE = frozenset({"Alex", "Blair", "Casey", "Drew", "Ellis"})
TASKS = {
    "01-ask-the-expert": {
        "owner": "Blair", "recipient": "Casey",
        "prompt": "I'm finishing the export-endpoint cleanup and hit a decision I can't make myself: should we remove the deprecated /v1/list alias in this release, or keep it a while longer for backward compatibility? I don't know our policy here. Help me get this decided so I can unblock the release.",
        "followup": "I still don't know our API compatibility policy. Continue from our previous turn and summarize the current decision status. Do not resend a message that was already sent, and don't assume anyone has replied.",
    },
    "02-owner-is-the-expert": {
        "owner": "Drew", "recipient": "Drew",
        "prompt": "I'm mid-change on the bundle's hook and hit a design question: should the durable outbox flush run at session:start or at prompt:submit? Reading code won't settle this: it's about what should already have happened at that point in the lifecycle. Help me think this through.",
        "followup": "From my hook lifecycle experience, I lean toward session:start: prompt:submit may run several times per session, which adds idempotency assumptions. Continue from our previous turn and reflect that input in the next step. Do not resend a message that was already sent.",
    },
}
# Counterbalance arm order across tasks; fixed and recorded, not randomized.
ORDER = [("with-guidance", "01-ask-the-expert"), ("without-guidance", "02-owner-is-the-expert"),
         ("without-guidance", "01-ask-the-expert"), ("with-guidance", "02-owner-is-the-expert")]
COMMON = """You are an assistant working with {owner} in a fictional local Teamwork project.
Shared records describe the project and its people. Tools can act on this local
project. Help with the user's request. Do not invent a reply or completed action.
For consistent observation, your final response on each turn must be a JSON
object containing exactly: question_for_user (a substantive question string or
null), summary (a string), decision (a string or null). Tool calls may precede
that final response. Put any substantive question to the current user in
question_for_user, not in summary. Do not wrap the object in Markdown.
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_manifest(base, paths):
    return {str(path.relative_to(base)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def runtime_manifests():
    """Hash loaded public module bytes, not just their often-shared 1.0.0 labels."""
    result = {}
    for name in ("amplifier_foundation", "amplifier_module_provider_anthropic",
                 "amplifier_module_loop_streaming", "amplifier_module_context_simple",
                 "amplifier_module_tool_skills"):
        spec = importlib.util.find_spec(name)
        base = Path(spec.origin).parent
        result[name] = file_manifest(base, base.rglob("*.py"))
    return result


def source_identity(path, subtree=None, *, measured_paths=(), expected=None):
    head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    args = ["git", "-C", str(path), "diff", "--quiet", "HEAD", "--"]
    args += [subtree] if subtree else []
    if subprocess.run(args).returncode:
        raise ValueError("A measured source tree has uncommitted changes")
    tracked = set(subprocess.check_output(["git", "-C", str(path), "ls-files", "-z"], text=True).split("\0"))
    if any(str(item.relative_to(path)) not in tracked for item in measured_paths):
        raise ValueError("A measured source file is untracked")
    if expected is not None and head != expected:
        raise ValueError("Selected source does not match the required full commit")
    return head


def response_object(text):
    """Strict observable response contract; invalid output is incomplete."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != {"question_for_user", "summary", "decision"}:
        return None
    if not isinstance(value["summary"], str):
        return None
    for field in ("question_for_user", "decision"):
        if value[field] is not None and (not isinstance(value[field], str) or not value[field].strip()):
            return None
    return value


def transcript_calls(messages, name):
    """Read stock context-simple messages; count each tool call only once.

    loop-streaming stores both structured content AND tool_calls. Prefer the
    explicit tool_calls field, falling back to content for legacy transcripts.
    """
    calls = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        explicit = message.get("tool_calls")
        if isinstance(explicit, list) and explicit:
            blocks = [(item.get("tool", item.get("name")), item.get("arguments", item.get("input")))
                      for item in explicit if isinstance(item, dict)]
        else:
            content = message.get("content")
            blocks = [(item.get("name"), item.get("input", item.get("arguments")))
                      for item in content if isinstance(item, dict) and item.get("type") in ("tool_use", "tool_call")] if isinstance(content, list) else []
        calls.extend(value for tool, value in blocks if tool == name and isinstance(value, dict))
    return calls


def visible_text(message):
    """Only user-visible assistant text; never thinking, tool input or metadata."""
    if message.get("role") != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item["text"] for item in content if isinstance(item, dict)
                         and item.get("type") == "text" and isinstance(item.get("text"), str))
    return ""


def final_assistant_text(messages):
    # execute() concatenates pre-tool preambles and final text. The last context
    # message is the unambiguous final model response; do not regex-extract JSON.
    assistants = [item for item in messages if item.get("role") == "assistant"]
    if not assistants or assistants[-1].get("tool_calls"):
        return ""
    content = assistants[-1].get("content")
    if isinstance(content, list) and any(isinstance(item, dict) and item.get("type") in ("tool_use", "tool_call") for item in content):
        return ""
    return visible_text(assistants[-1])


def check(checks, name, value):
    """A later turn cannot erase an earlier failed integration check."""
    checks[name] = checks.get(name, True) and bool(value)


def recipient(call, agents):
    selected = [key for key in ("to_person", "to_agent_id", "to_node_label") if call.get(key)]
    if len(selected) != 1:
        return "unresolved"
    value = call[selected[0]] if selected[0] == "to_person" else agents.get(call[selected[0]])
    return value if isinstance(value, str) and value in PEOPLE else "unresolved"


def profiles_visible(text, profiles):
    return all(json.dumps(name) in text and all(json.dumps(value, ensure_ascii=False, sort_keys=True) in text
               for value in profile.values()) for name, profile in profiles.items())


def review_text(text, forbidden=()):
    """Bounded fictional evidence; strip credentials, runtime IDs and paths."""
    text = text if isinstance(text, str) else ""
    for value in sorted(set(forbidden), key=len, reverse=True):
        if value:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "[fixture-id]", text, flags=re.I)
    text = re.sub(r"(?:https?://(?:127\.0\.0\.1|localhost)(?::\d+)?|/(?:Users|private|tmp|Volumes)/)[^\s\"<>]*", "[local-reference]", text)
    if len(text) > 16000:
        raise ValueError("Fictional review evidence exceeds its bound")
    return text


def grade(task_id, outputs, calls, queued, agents, mechanism_valid):
    """Observable routing gates, not the historical semantic 100-point rubric.

    Stored messages establish queued routing, not recipient acceptance. These
    automatic gates cannot establish relevant questions, justified reasoning,
    honest pending status or meaningful use of owner input. Independent semantic
    review remains mandatory even if every automatic gate passes.
    """
    parsed = [response_object(value) for value in outputs]
    valid = mechanism_valid and len(parsed) == 2 and all(item is not None for item in parsed)
    addressed = [recipient(call, agents) for call in calls]
    question = (parsed[0]["question_for_user"] or "").casefold() if parsed and parsed[0] else ""
    # A greeting or administrative permission question cannot satisfy this gate.
    # This is a deliberately narrow lexical proxy, not a semantic expert grader.
    questions = bool(question and any(marker in question for marker in
                     ("session:start", "prompt:submit", "lifecycle", "flush", "ordering", "guarantee")))
    if task_id == "01-ask-the-expert":
        gates = {"queued_for_casey": "Casey" in queued,
                 "no_other_recipient_attempted": bool(addressed) and set(addressed) == {"Casey"},
                 "never_asked_owner_to_decide": bool(parsed) and all(item is not None and item["question_for_user"] is None for item in parsed)}
    else:
        gates = {"owner_question_channel_mentions_lifecycle_on_first_turn": questions,
                 "no_other_recipient_attempted": not (set(addressed) - {"Drew"}),
                 "no_other_recipient_queued": not (set(queued) - {"Drew"})}
    return {"automatic_measurement_valid": bool(valid), "automatic_gates_pass": bool(valid and all(gates.values())),
            "gates": gates, "attempted_recipients": addressed, "queued_recipients": queued,
            "response_contract_valid": [item is not None for item in parsed],
            "semantic_review": "pending", "task_pass": None}


@contextmanager
def isolated_environment(runtime):
    keys = {key for key in os.environ if key.startswith("TEAMWORK_")} | {"DATABASE_URL", "AMPLIFIER_HOME", "AMPLIFIER_HOME_ENV"}
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(AMPLIFIER_HOME=str(runtime / "home"), AMPLIFIER_HOME_ENV="0")
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("TEAMWORK_") or key in keys:
                os.environ.pop(key, None)
        os.environ.update({key: value for key, value in previous.items() if value is not None})


class Fixture:
    """Actual backend dispatch over loopback, memory-only database and codes."""
    def __init__(self, runtime):
        from backend import auth, core
        from backend.http import dispatch
        from backend.store import MemoryStore
        spec = importlib.util.spec_from_file_location("guidance_seed", HERE / "eval-support/seed.py")
        seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed)
        self.profiles = copy.deepcopy(seed.PROFILES)
        self.codes = {name: secrets.token_urlsafe(32) for name in self.profiles}
        self.store = MemoryStore({"project_id": "teamwork", "goal": "Fictional routing observation",
                                  "people": [{"name": name} for name in self.profiles], "tasks": []})
        self.enrolled, self.cookies, self.agents = {}, {}, {}
        self.runtime, self.http = runtime, Counter()
        lock = threading.RLock()
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with lock:
                    try:
                        status, output, headers = dispatch("POST", self.path, self.headers, body, fixture.store)
                    except core.APIError as error:
                        status, output, headers = error.status, {"error": {"code": error.code}}, {}
                    fixture.http[str(status)] += 1
                encoded = json.dumps(output).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(encoded)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        os.environ.update(TEAMWORK_PROJECT_ID="teamwork", TEAMWORK_PUBLIC_ORIGIN=self.origin,
                          TEAMWORK_COOKIE_INSECURE="1", TEAMWORK_SESSION_SECRET=secrets.token_urlsafe(48),
                          TEAMWORK_MEMBERS_JSON=json.dumps([{"name": name, "token_sha256": auth.sha(code),
                              "role": "maintainer" if name == "Alex" else "contributor"} for name, code in self.codes.items()]))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def post(self, path, body, cookie=None, token=None):
        import setup_teamwork
        headers = {"Content-Type": "application/json", "Origin": self.origin, "X-Teamwork-Project": "teamwork"}
        if cookie:
            headers["Cookie"] = cookie
        if token:
            headers["Authorization"] = "Bearer " + token
        if path.endswith("/publish"):
            headers["Idempotency-Key"] = str(uuid.uuid4())
        request = urllib.request.Request(self.origin + path, json.dumps(body).encode(), headers)
        with urllib.request.build_opener(setup_teamwork.NoRedirect()).open(request, timeout=10) as response:
            return json.load(response), dict(response.headers)

    def seed(self, owner):
        import setup_teamwork
        for name, profile in self.profiles.items():
            _, headers = self.post("/api/login", {"name": name, "token": self.codes[name]})
            cookie = headers["Set-Cookie"].split(";")[0]
            self.cookies[name] = cookie
            updated, _ = self.post("/api/action", {"type": "profile", "value": profile}, cookie)
            person = next(p for p in updated["people"] if p["name"] == name)
            if any(person.get(key) != value for key, value in profile.items()):
                raise ValueError("Fictional profile readback mismatch")
            if name == owner:
                connection, overlay = self.runtime / "connection.json", self.runtime / "enrolled.yaml"
                with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                    setup_teamwork.enroll_and_save(self.post, self.origin, "teamwork", "fixture-base", "Routing owner", name,
                                                   self.codes[name], connection, overlay)
                saved = json.loads(connection.read_text())
                self.enrolled[name] = {"id": saved["harness_id"], "token": saved["token"]}
                if connection.stat().st_mode & 0o077:
                    raise ValueError("Enrollment file is not private")
            else:
                enrolled, _ = self.post("/api/harnesses", {"label": "Fictional recipient", "scopes": ["context:read", "session:write"]}, cookie)
                self.enrolled[name] = enrolled
                sid = str(uuid.uuid4())
                self.post("/api/v1/projects/teamwork/publish", {"operations": [
                    {"op": "session.upsert", "id": sid, "expected_version": 0, "data": {"title": "Fictional peer", "status": "active"}},
                    {"op": "agent.upsert", "id": sid, "expected_version": 0, "data": {"session_id": sid, "node_label": "fixture-" + name.lower()}}
                ]}, token=enrolled["token"])
                self.agents[sid] = self.agents["fixture-" + name.lower()] = name

    def close(self):
        revoked = True
        try:
            for name, enrolled in self.enrolled.items():
                try:
                    self.post("/api/harnesses/revoke", {"id": enrolled["id"]}, self.cookies[name])
                    revoked = revoked and self.store.state["_auth"]["harnesses"][enrolled["id"]]["revoked"]
                except Exception:
                    revoked = False
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.codes.clear()
            self.enrolled.clear()
        return revoked and not self.thread.is_alive()


async def trial(arm, task_id, runtime, live, bundle_source):
    from amplifier_foundation import Bundle
    task = TASKS[task_id]
    fixture = None
    session = None
    case = {"arm": arm, "task": task_id, "mechanism_checks": {}, "provider_calls": 0, "provider_models": [], "usage": {}, "review_evidence": []}
    checks, outputs, messages, events = case["mechanism_checks"], [], [], []
    same_id = str(uuid.uuid4())
    empty_skills = runtime / "empty-skills"
    empty_skills.mkdir()
    skills = bundle_source / "skills" if arm == "with-guidance" else empty_skills
    config = {"connection_file": str(runtime / "connection.json"), "share_visible_turns": True,
              "journal_path": str(runtime / "outbox.sqlite3"), "verbosity": "silent", "file_inbound_reports": False,
              "detect_decisions": False, "detect_lessons": False, "node_label": "fixture-owner"}
    instruction = COMMON.format(owner=task["owner"])
    if arm == "with-guidance":
        instruction += "\n" + (bundle_source / "context/teamwork-awareness.md").read_text()
    # Same module bytes/config in both arms, except the reviewed pointer and skill path.
    bundle = Bundle(name="guidance-local", base_path=runtime, instruction=instruction,
        session={"orchestrator": {"module": "loop-streaming", "config": {"max_iterations": 6 if live else 0, "extended_thinking": False}},
                 "context": {"module": "context-simple", "config": {"max_tokens": 32768, "auto_compact": False}}},
        providers=[{"module": "provider-anthropic", "config": {"default_model": MODEL, "base_url": "https://api.anthropic.com",
                    "max_tokens": 1024, "temperature": 0, "max_retries": 0}}],
        hooks=[{"module": "hooks-teamwork", "config": config}],
        tools=[{"module": "tool-skills", "config": {"skills": [str(skills)]}}])
    async def observer(event, data):
        if event == "llm:request":
            case["provider_calls"] += 1
            case["provider_models"].append(data.get("model"))
            try:
                provider = next(iter(session.coordinator.get("providers").values()))
                request_view = await context.get_messages_for_request(provider=provider)
                request_text = "\n".join(str(item.get("content", "")) for item in request_view)
                check(checks, "request_profiles_visible", profiles_visible(request_text, fixture.profiles))
            except Exception:
                check(checks, "request_profiles_visible", False)
        elif event == "llm:response":
            for key, value in (data.get("usage") or {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    case["usage"][key] = case["usage"].get(key, 0) + value
        elif event == "tool:pre":
            events.append({"name": data.get("tool_name"), "input": copy.deepcopy(data.get("tool_input", {}))})
    try:
        fixture = Fixture(runtime)
        fixture.seed(task["owner"])
        checks["five_profiles_read_back"] = True
        checks["real_setup_enrollment"] = True
        prepared = await bundle.prepare(install_deps=False)
        case["mount_plan_sha256"] = digest({"session": bundle.session, "providers": bundle.providers,
            "tools": [{"module": "tool-skills", "skill_catalog": arm}], "hooks": [{"module": "hooks-teamwork", "config": {k: v for k, v in config.items() if k not in ("connection_file", "journal_path")}}], "instruction": instruction})
        shared_sid = None
        for turn, prompt in enumerate((task["prompt"], task["followup"])):
            session = await prepared.create_session(session_id=same_id, session_cwd=runtime)
            tools = session.coordinator.get("tools")
            check(checks, "real_tools_mounted", {"teamwork_send", "teamwork_wait", "load_skill"} <= set(tools))
            check(checks, "skill_catalog_isolated", set(tools["load_skill"].skills) == ({"teamwork-protocol"} if arm == "with-guidance" else set()))
            check(checks, "no_external_action_tools", set(tools) == {"teamwork_send", "teamwork_tasks", "teamwork_claim", "teamwork_progress", "teamwork_wait", "teamwork_publish_work", "teamwork_record_insight", "load_skill"})
            hook = tools["teamwork_send"].hook
            check(checks, "same_shared_session_on_resume", shared_sid is None or hook.sid == shared_sid)
            shared_sid = hook.sid
            context = session.coordinator.get("context")
            if turn:
                await context.set_messages(copy.deepcopy(messages))
                check(checks, "exact_context_restored", await context.get_messages() == messages)
            request_messages = await context.get_messages_for_request()
            check(checks, "common_instruction_mounted", any(item.get("role") == "system" and COMMON.format(owner=task["owner"]) in str(item.get("content", "")) for item in request_messages))
            if not all(checks.values()):
                raise ValueError("Integration preconditions failed before provider execution")
            before_messages = copy.deepcopy(await context.get_messages())
            first_new_message = len(before_messages)
            first_new_event = len(events)
            for event in ("llm:request", "llm:response", "tool:pre"):
                session.coordinator.hooks.register(event, observer, priority=999, name="routing-eval-" + event)
            output = await asyncio.wait_for(session.execute(prompt), timeout=120)
            await session.coordinator.hooks.emit("prompt:complete", {"response": output})
            messages = await context.get_messages()
            check(checks, "review_turn_boundary_verified", messages[:first_new_message] == before_messages)
            turn_messages = messages[first_new_message:]
            final_text = final_assistant_text(turn_messages)
            outputs.append(final_text)
            if live:
                forbidden = [str(runtime), str(bundle_source), *fixture.codes.values(),
                             *(item["token"] for item in fixture.enrolled.values()), *fixture.cookies.values()]
                case["review_evidence"].append({"turn": turn + 1,
                    "assistant_visible_text": [review_text(visible_text(item), forbidden) for item in turn_messages if visible_text(item)],
                    "final_response": review_text(final_text, forbidden),
                    "attempted_outbound_messages": [{"recipient": recipient(item["input"], fixture.agents),
                        "body": review_text(item["input"].get("body"), forbidden)} for item in events[first_new_event:] if item["name"] == "teamwork_send"]})
            fresh_context = "\n".join(message["content"] for message in turn_messages if isinstance(message.get("content"), str) and "Teamwork shared project context" in message["content"])
            check(checks, "shared_context_reached_session", bool(fresh_context))
            check(checks, "all_five_profiles_visible_this_turn", profiles_visible(fresh_context, fixture.profiles))
            if not live and turn == 0:
                result = await tools["teamwork_send"].execute({"to_person": "Casey", "body": "Synthetic preflight routing transport check."})
                checks["preflight_real_send"] = result.success
            await asyncio.wait_for(session.cleanup(), timeout=20)
            session = None
        calls = transcript_calls(messages, "teamwork_send")
        event_calls = [item["input"] for item in events if item["name"] == "teamwork_send"]
        checks["transcript_matches_tool_events"] = calls == event_calls
        # IDs are selected from the exact session object, never newest-directory heuristics.
        checks["two_turns_one_shared_session"] = sum(s["id"] == shared_sid for s in fixture.store.state["sessions"]) == 1
        turns = [turn for turn in fixture.store.state.get("turns", []) if turn.get("session_id") == shared_sid]
        checks["two_visible_turns_recorded"] = len(turns) == 2
        queued = [fixture.agents.get(message.get("to_agent_id"), task["owner"] if message.get("to_agent_id") == shared_sid else "unresolved")
                     for message in fixture.store.state.get("messages", [])]
        if live:
            case["queued_messages"] = [{"recipient": name, "body": review_text(message.get("body"), forbidden)}
                for name, message in zip(queued, fixture.store.state.get("messages", []))]
        checks["provider_pinned"] = (case["provider_calls"] > 0 and set(case["provider_models"]) == {MODEL}) if live else case["provider_calls"] == 0
        case["skill_load_calls"] = sum(item["name"] == "load_skill" and item["input"].get("skill_name") == "teamwork-protocol" for item in events)
        case["tool_call_count"] = len(events)
        case["session_transcript_sha256"] = digest(messages)
        case["tool_events_sha256"] = digest(events)
        case["grading"] = grade(task_id, outputs, calls, queued, fixture.agents, all(checks.values())) if live else None
        case["execution"] = "completed"
    except Exception as error:
        case["execution"] = "incomplete"
        case["error_type"] = type(error).__name__
    finally:
        try:
            if session:
                try:
                    await asyncio.wait_for(session.cleanup(), timeout=20)
                except Exception:
                    checks["session_cleanup"] = False
        finally:
            checks["local_credentials_revoked_and_server_stopped"] = fixture.close() if fixture else True
        if not all(checks.values()) and case.get("grading"):
            case["grading"]["automatic_measurement_valid"] = case["grading"]["automatic_gates_pass"] = False
        outputs.clear()
        messages.clear()
        events.clear()
    return case


async def run(args):
    bundle_source, app_source = args.bundle_source, args.app_source
    mechanism_paths = list((bundle_source / "modules/hooks-teamwork").rglob("*.py"))
    service_paths = list((app_source / "backend").rglob("*.py"))
    guidance_paths = [bundle_source / "context/teamwork-awareness.md", bundle_source / "skills/teamwork-protocol/SKILL.md"]
    enrollment_paths = [bundle_source / "setup_teamwork.py"]
    source = {"bundle_commit": source_identity(bundle_source, "modules/hooks-teamwork", measured_paths=mechanism_paths, expected=args.bundle_commit),
              "app_commit": source_identity(app_source, "backend", measured_paths=service_paths, expected=args.app_commit),
              "mechanism_files": file_manifest(bundle_source, mechanism_paths),
              "service_files": file_manifest(app_source, service_paths),
              "guidance_files": file_manifest(bundle_source, guidance_paths),
              "enrollment_files": file_manifest(bundle_source, enrollment_paths),
              "profile_seed_sha256": hashlib.sha256((HERE / "eval-support/seed.py").read_bytes()).hexdigest(),
              "semantic_rubric_files": file_manifest(HERE, HERE.glob("tasks/*/grader.yaml"))}
    source_identity(bundle_source, "skills", measured_paths=guidance_paths)
    source_identity(bundle_source, "context")
    source_identity(bundle_source, "setup_teamwork.py", measured_paths=enrollment_paths)
    # Public module imports only. Never add a private app checkout to a model tool.
    sys.path.insert(0, str(bundle_source))
    sys.path.insert(0, str(bundle_source / "modules/hooks-teamwork"))
    sys.path.insert(0, str(app_source))
    import amplifier_core
    import amplifier_module_hooks_teamwork
    import setup_teamwork
    import backend
    if (Path(amplifier_module_hooks_teamwork.__file__).resolve().parent != bundle_source / "modules/hooks-teamwork/amplifier_module_hooks_teamwork"
        or Path(setup_teamwork.__file__).resolve() != bundle_source / "setup_teamwork.py"
        or Path(backend.__file__).resolve().parent != app_source / "backend"):
        raise ValueError("Teamwork mechanism does not match the selected checkout")
    versions = {name: metadata.version(name) for name in ("amplifier-core", "amplifier-foundation", "amplifier-module-provider-anthropic", "amplifier-module-loop-streaming", "amplifier-module-context-simple", "amplifier-module-tool-skills")}
    if versions["amplifier-core"] != "1.6.1":
        raise ValueError("This runner requires released host Core 1.6.1")
    report = {"schema_version": 2, "mode": "live" if args.live else "preflight", "source": source, "versions": versions,
              "runtime_module_files": runtime_manifests(),
              "provider": "anthropic", "model": MODEL, "temperature": 0, "max_output_tokens_per_request": 1024,
              "max_iterations_per_turn": 6, "possible_final_wrapup_requests_per_turn": 1, "turn_timeout_seconds": 120, "turns_per_trial": 2,
              "order": ORDER, "task_protocol_sha256": digest({"tasks": TASKS, "common": COMMON}),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "core_engine_sha256": hashlib.sha256(Path(amplifier_core._engine.__file__).read_bytes()).hexdigest(),
              "scope": "Four exploratory local observations with fictional participants; no statistical or causal guidance-effect claim; no production or human acceptance.", "trials": []}
    runtime = Path(tempfile.mkdtemp(prefix="teamwork-guidance-local-"))
    runtime.chmod(0o700)
    started = time.monotonic()
    try:
        with isolated_environment(runtime):
            for index, (arm, task) in enumerate(ORDER if args.live else ORDER[:1]):
                folder = runtime / str(index)
                folder.mkdir(mode=0o700)
                report["trials"].append(await trial(arm, task, folder, args.live, bundle_source))
                print(json.dumps({"trial": index + 1, "execution": report["trials"][-1]["execution"], "provider_calls": report["trials"][-1]["provider_calls"]}), flush=True)
    finally:
        shutil.rmtree(runtime)
        report["runtime_removed"] = not runtime.exists()
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    report["inputs_unchanged"] = (source["mechanism_files"] == file_manifest(bundle_source, (bundle_source / "modules/hooks-teamwork").rglob("*.py"))
        and source["service_files"] == file_manifest(app_source, (app_source / "backend").rglob("*.py"))
        and source["guidance_files"] == file_manifest(bundle_source, guidance_paths)
        and source["enrollment_files"] == file_manifest(bundle_source, enrollment_paths)
        and source["profile_seed_sha256"] == hashlib.sha256((HERE / "eval-support/seed.py").read_bytes()).hexdigest()
        and source["semantic_rubric_files"] == file_manifest(HERE, HERE.glob("tasks/*/grader.yaml"))
        and report["runtime_module_files"] == runtime_manifests())
    report["mechanism_valid"] = report["runtime_removed"] and report["inputs_unchanged"] and len(report["trials"]) == (4 if args.live else 1) and all(case["execution"] == "completed" and all(case["mechanism_checks"].values()) for case in report["trials"])
    report["semantic_review"] = "pending" if args.live else "not_applicable"
    report["pass_both"] = None
    # Curated fictional assistant text and send bodies support independent review.
    # No codes, session IDs, local paths, tool results, provider errors, or full transcripts.
    fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return 0 if report["mechanism_valid"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-source", type=Path, required=True, help="Clean pinned bundle checkout, distinct from the runner if desired")
    parser.add_argument("--bundle-commit", required=True, help="Required full source commit; moving branch names are refused")
    parser.add_argument("--app-source", type=Path, required=True, help="Local service checkout; never copied or uploaded")
    parser.add_argument("--app-commit", required=True, help="Required full service commit")
    parser.add_argument("--receipt", type=Path, required=True, help="New aggregate JSON path outside either checkout")
    parser.add_argument("--live", action="store_true", help="Authorize four bounded real-provider trials; default only checks local integration")
    args = parser.parse_args()
    args.app_source = args.app_source.expanduser().resolve()
    args.bundle_source = args.bundle_source.expanduser().resolve()
    args.receipt = args.receipt.expanduser().resolve()
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.bundle_commit, args.app_commit)):
        parser.error("Source pins must be full lowercase commit hashes")
    if any(args.receipt.is_relative_to(path) for path in (ROOT, args.bundle_source, args.app_source)) or args.receipt.exists():
        parser.error("Receipt must be a new path outside the measured source trees")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error("ANTHROPIC_API_KEY must already be present; never pass credentials as arguments")
    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(run(args))
    except Exception as error:
        print(json.dumps({"execution": "incomplete", "error_type": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
