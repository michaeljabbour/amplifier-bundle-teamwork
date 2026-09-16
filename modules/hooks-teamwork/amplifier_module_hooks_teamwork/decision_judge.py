"""An in-process judgment session that classifies one window as RECORD or SKIP.

Specification: docs/scenarios/08-the-decision-the-session-made-itself.md and its
twin 08b-the-path-taken-that-binds-nothing.md. Read those first -- their "What
the good one knows" sections are the decision rule this module encodes into a
prompt, not a re-derivation of it.

NOT WIRED INTO ANY HOOK LIFECYCLE EVENT. This module is a library the hook (or
anything else in-process) can call; it does not subscribe to `prompt:submit`,
`session:end`, or anything else on its own. That wiring -- what fires this, a
watermark so one decision is not re-detected at every later delegation, and the
"recorded by whom" question below -- is out of scope here on purpose (see the
open questions in 08 and 08b).

ASYNC CONTRACT. `judge_window()` performs a real provider call and must never be
awaited inline on a turn's critical path. Callers should fire it with
`asyncio.create_task(judge_window(...))` (or use `spawn_judge_window()` below,
which does exactly that) and inspect the result later -- at the next turn, at
`session:end`, wherever the caller's own lifecycle wiring decides to look. Both
paths are safe: `judge_window()` itself never raises. Every failure -- a
provider error, a coordinator that cannot be read, a response that will not
parse as a verdict -- degrades to `no_verdict(...)`, because a judge that
breaks a turn is worse than no judge.

WHO WRITES. This module never records anything itself. It has no dependency on
`RecordInsightTool` and mounts nothing unless a caller explicitly hands it tools
to mount (`extra_tools`). Per 08's open question: if a separate judgment
session decided a decision was made and then wrote it, the write would carry
the judge's own session identity (`RecordInsightTool` forces
`source_session_id` to `hook.sid`, and `hook.sid` is derived from whichever
session owns the hook instance that is called -- see `__init__.py`'s
`TeamworkHook.sid`). A judge session's identity is synthetic and throwaway; a
record attributed to it would misattribute real project knowledge to a session
nobody can ever ask about it again.

The one way to keep provenance honest -- and the reason `extra_tools` exists at
all -- is for the CALLER to mount its OWN already-bound `RecordInsightTool`
instance (the one built from the parent session's own hook) into this judge
session before asking it to act. Because `RecordInsightTool.__init__` binds to
a *hook instance*, not to whichever session happens to call `.execute()`, a
write made from inside this judge session through that mounted instance still
carries the parent's `hook.sid`. Swapping in a *new* `RecordInsightTool` built
against a hook that belongs to this judge session would silently reintroduce
the misattribution this design exists to avoid -- so `extra_tools` must always
be already-bound instances handed in by the caller, never constructed here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verdict shape
# ---------------------------------------------------------------------------

#: Shaped so a RECORD verdict's fields map straight onto RecordInsightTool's
#: own arguments: claim -> claim, basis -> basis, confidence -> confidence,
#: what_it_does_not_establish -> limitations. `kind` and `reason` are judge
#: metadata, not tool arguments -- `kind` names what sort of thing this is for
#: a human skimming a log, `reason` is the judge's own account of why,
#: referencing the structural-vs-contingent test. Evidence is deliberately
#: absent: the judge saw a window, not the world, and cannot manufacture an
#: evidence reference; a caller that decides to record still has to supply one.
VERDICT_FIELDS = (
    "record",
    "kind",
    "claim",
    "basis",
    "what_it_does_not_establish",
    "confidence",
    "reason",
)

_VALID_BASIS = (None, "observation", "inference")
_VALID_CONFIDENCE = (None, "low", "medium", "high")


def no_verdict(reason):
    """The sentinel returned whenever this module cannot honestly say RECORD or SKIP.

    `record` is False so a caller that only checks `verdict["record"]` degrades
    safely to "do nothing" rather than "do something on bad information."
    """
    return {
        "record": False,
        "kind": None,
        "claim": None,
        "basis": None,
        "what_it_does_not_establish": None,
        "confidence": None,
        "reason": str(reason),
    }


# ---------------------------------------------------------------------------
# Bounded payload construction
# ---------------------------------------------------------------------------

# Per 08's open question: the detector must read the WINDOW of turns leading
# to a trigger, not the triggering instruction alone -- an instruction carries
# the conclusion with none of the alternatives, which is the half that makes a
# decision worth recording. These bounds keep that window a window and never
# the whole transcript.
MAX_WINDOW_TURNS = 10
MAX_TURN_CHARS = 4000
MAX_TOOL_CALLS = 20
MAX_TOOL_FIELD_CHARS = 200


def _clip(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def build_window_payload(
    turns, tool_calls=None, *, max_turns=MAX_WINDOW_TURNS, max_turn_chars=MAX_TURN_CHARS
):
    """Build a bounded window payload: the last `max_turns` visible turns, plus
    consequential tool calls reduced to NAME AND TARGET ONLY -- never arguments,
    never results. A tool call's payload can carry anything a turn's tools
    touched; only what it was and roughly what it acted on belongs in a window
    that may end up quoted back by a judge, or worse, recorded as evidence.

    `turns` is an iterable of `{"user_prompt": str, "agent_responses": [{"text": str}, ...]}`
    -- the same shape the hook's own turn records use. `tool_calls` is an
    iterable of objects or dicts exposing `name` and (optionally) `target`.

    Never raises: a turn or call missing an expected key is skipped rather than
    failing the whole window, because a partially-built window is still far
    more useful than none.
    """
    bounded_turns = []
    for turn in list(turns or [])[-max_turns:]:
        try:
            prompt = _clip(turn.get("user_prompt") or "", max_turn_chars)
            responses = turn.get("agent_responses") or []
            response_text = " ".join(
                str(r.get("text") or "") for r in responses if isinstance(r, dict)
            )
            response_text = _clip(response_text, max_turn_chars)
        except AttributeError:
            continue
        if not prompt and not response_text:
            continue
        bounded_turns.append({"user_prompt": prompt, "agent_response": response_text})

    bounded_calls = []
    for call in list(tool_calls or [])[:MAX_TOOL_CALLS]:
        name = (
            call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        )
        target = (
            call.get("target")
            if isinstance(call, dict)
            else getattr(call, "target", None)
        )
        if not isinstance(name, str) or not name.strip():
            continue
        entry = {"name": _clip(name, MAX_TOOL_FIELD_CHARS)}
        if isinstance(target, str) and target.strip():
            entry["target"] = _clip(target, MAX_TOOL_FIELD_CHARS)
        bounded_calls.append(entry)

    return {"turns": bounded_turns, "tool_calls": bounded_calls}


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------

# The decision rule, distilled from 08 and 08b's "What the good one knows"
# sections. Kept as a module constant rather than reconstructed per call so it
# can be read and reviewed on its own, the same way the rule it encodes is
# meant to be read on its own.
_RULE = """You are judging ONE window of a session's turns to decide whether it contains a
DECISION that should be recorded to a shared project's durable knowledge, or whether
it should be skipped. You will see only this window -- no other cases, no history.

A decision worth recording is a choice among alternatives that now constrains work
beyond this task. Both halves are load-bearing: "among alternatives" rules out the
many forced moves that only looked like choices; "constrains work beyond this task"
rules out anything local and reversible.

The test that actually separates RECORD from SKIP is NOT how deliberate the choice
felt, and not merely whether it clears an altitude bar (architectural choice vs.
"I decided to call a tool"). A contingent choice can be a real implementation
strategy, stated with a clear reason, and still must be SKIPPED. The test is:

    Does the reason survive the circumstances that produced it?

  - STRUCTURAL reason -> RECORD. It would still be the right call for someone else,
    at another time, regardless of who is asking or what their situation is --
    e.g. a privacy invariant, a permission boundary, a validation rule for how the
    project checks its own guidance.
  - CONTINGENT reason -> SKIP. It depended on THIS session's circumstances at THIS
    moment -- e.g. this session's own context window being nearly full, a
    coincidental resource conflict, an operational detail like a port already in
    use, someone else happening to be mid-review right now. Change the
    circumstances and the right answer changes with them; recording it would
    mislead every future reader whose circumstances differ.

Precision matters more than recall. A missed RECORD costs a re-decision someone can
make again later. A wrong RECORD costs every future reader permanently -- there is
no undo for an automatic record. When genuinely unsure, prefer SKIP."""

_OUTPUT_INSTRUCTIONS = """Respond with ONLY a single JSON object -- no markdown fence, no commentary
before or after it -- with exactly these keys:

  "record": true or false
  "kind": a short free-text label for what this is (e.g. "architectural decision",
          "permission boundary", "validation practice"), or null when record is false
  "claim": the transferable statement -- what stays true after this task is
           forgotten -- naming the fork it settles, or null when record is false
  "basis": "observation" or "inference", or null when record is false
  "what_it_does_not_establish": what this does NOT establish, or null when record
          is false
  "confidence": "low", "medium", or "high"
  "reason": one or two sentences on why, naming whether the deciding reason was
          structural or contingent"""


def _prompt_for(window):
    lines = [_RULE, "", "WINDOW:"]
    turns = window.get("turns") or []
    if not turns and not (window.get("tool_calls") or []):
        lines.append("(empty window)")
    for turn in turns:
        prompt = turn.get("user_prompt")
        response = turn.get("agent_response")
        if prompt:
            lines.append("user: " + prompt)
        if response:
            lines.append("assistant: " + response)
    for call in window.get("tool_calls") or []:
        target = call.get("target")
        lines.append(
            "[tool call: %s -> %s]" % (call["name"], target)
            if target
            else "[tool call: %s]" % call["name"]
        )
    lines.append("")
    lines.append(_OUTPUT_INSTRUCTIONS)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing the judge's response
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_verdict(raw):
    """Strictly parse the judge's text as one verdict object, or return None.

    None is not an error path here -- it is a normal outcome the caller turns
    into `no_verdict(...)`. Never raises.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _FENCE.sub("", raw.strip()).strip()
    data = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None
    if not isinstance(data, dict) or not isinstance(data.get("record"), bool):
        return None
    verdict = {field: data.get(field) for field in VERDICT_FIELDS}
    if verdict["basis"] not in _VALID_BASIS:
        verdict["basis"] = None
    if verdict["confidence"] not in _VALID_CONFIDENCE:
        verdict["confidence"] = None
    for field in ("kind", "claim", "what_it_does_not_establish", "reason"):
        if verdict[field] is not None and not isinstance(verdict[field], str):
            verdict[field] = str(verdict[field])
    return verdict


# ---------------------------------------------------------------------------
# Provider inheritance
# ---------------------------------------------------------------------------

_GLOB_CHARS = re.compile(r"[*?\[\]]")


def _bare_module(module_id):
    module_id = module_id or ""
    return (
        module_id[len("provider-") :]
        if module_id.startswith("provider-")
        else module_id
    )


async def _resolve_providers(parent_coordinator, model_role):
    """Build this session's provider list, inherited from the calling session.

    Returns `(providers, note)`. `note` explains what happened -- a `fast`
    substitution, or plainly why one was not attempted -- for a caller that
    wants to log it; nothing here guesses. Never raises: any failure degrades
    to the parent's own provider config, byte-for-byte, which is always a
    valid providers list because it is the one the parent session itself
    already runs on.

    Deliberately duck-typed and import-free with respect to
    `amplifier_foundation.ProviderPreference` -- this module has zero
    third-party dependencies, matching the rest of this package.
    """
    try:
        parent_config = getattr(parent_coordinator, "config", None) or {}
        providers = [dict(spec) for spec in (parent_config.get("providers") or [])]
    except Exception:
        providers = []
    if not providers:
        return providers, "the calling session has no provider configured to inherit"
    if not model_role:
        return (
            providers,
            "no model_role requested; using the calling session's provider unchanged",
        )

    try:
        resolver = parent_coordinator.get_capability("model_role_resolver")
    except Exception:
        resolver = None
    if resolver is None:
        return providers, (
            "no model_role_resolver capability is registered on the calling session; "
            "using its provider unchanged"
        )

    try:
        preferences = await resolver.resolve(model_role)
    except Exception as error:
        return (
            providers,
            "model_role_resolver.resolve(%r) raised %r; using the calling session's provider unchanged"
            % (
                model_role,
                error,
            ),
        )
    if not preferences:
        return (
            providers,
            "model_role %r resolved to no candidates; using the calling session's provider unchanged"
            % model_role,
        )

    preference = preferences[0]
    if isinstance(preference, dict):
        pref_provider, pref_model = preference.get("provider"), preference.get("model")
    else:
        pref_provider = getattr(preference, "provider", None)
        pref_model = getattr(preference, "model", None)
    if not pref_provider or not pref_model:
        return providers, (
            "model_role_resolver returned a preference with no provider/model; "
            "using the calling session's provider unchanged"
        )
    if _GLOB_CHARS.search(pref_model):
        return providers, (
            "model_role %r resolved to a glob pattern (%r) that needs live model-list "
            "resolution this judge does not perform; using the calling session's provider unchanged"
            % (model_role, pref_model)
        )

    for spec in providers:
        if _bare_module(spec.get("module", "")) == pref_provider:
            spec["config"] = dict(spec.get("config") or {})
            spec["config"]["default_model"] = pref_model
            return providers, "model_role %r resolved to %s/%s" % (
                model_role,
                pref_provider,
                pref_model,
            )

    return providers, (
        "model_role %r resolved to provider %r, which is not among the calling session's "
        "configured providers; using its provider unchanged"
        % (model_role, pref_provider)
    )


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------


async def build_judge_session(
    parent_coordinator, *, extra_tools=None, model_role="fast"
):
    """Construct and initialize an in-process judgment session.

    `parent_id` is set to the calling session's own id, which is what keeps
    this from ever bootstrapping a second copy of the teamwork hook: mount()
    (see `__init__.py`) returns None whenever `coordinator.parent_id` is set,
    unconditionally, before it reads any connection or config. No second guard
    is added here -- the existing one is the mechanism, not a inconvenience to
    route around.

    No tools by default. `extra_tools` mounts whatever the caller hands in --
    already-constructed instances, never built here. See this module's
    docstring for why that distinction matters for `RecordInsightTool`
    specifically.

    Returns `(session, note)`. The caller owns the session and must call
    `await session.cleanup()` when done with it; `judge_window()` below does
    this for you.
    """
    from amplifier_core import AmplifierSession  # lazy: amplifier-core is host-supplied

    parent_id = getattr(parent_coordinator, "session_id", None)
    providers, note = await _resolve_providers(parent_coordinator, model_role)
    config = {
        "session": {"orchestrator": "loop-agent", "context": "context-simple"},
        "providers": providers,
        "tools": [],
        "hooks": [],
    }
    session = AmplifierSession(config, parent_id=parent_id)
    await session.initialize()
    for tool in extra_tools or []:
        name = getattr(tool, "name", None)
        if not name:
            continue
        await session.coordinator.mount("tools", tool, name=name)
    return session, note


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------


async def judge_window(parent_coordinator, window, *, extra_tools=None):
    """Classify ONE window as RECORD or SKIP. Never raises.

    See this module's docstring for the async contract: this performs a real
    provider call and must not be awaited inline on a turn's critical path.

    On any failure -- session construction, the provider call, or a response
    that will not parse as a verdict -- returns `no_verdict(reason)` rather
    than raising, because a judge that breaks a turn is worse than no judge.
    """
    try:
        session, note = await build_judge_session(
            parent_coordinator, extra_tools=extra_tools
        )
    except Exception as error:
        logger.warning("decision judge: could not build a judge session", exc_info=True)
        return no_verdict("could not build a judge session: %r" % (error,))
    logger.debug("decision judge: provider inheritance -- %s", note)

    try:
        try:
            raw = await session.execute(_prompt_for(window))
        except Exception as error:
            logger.warning("decision judge: the provider call failed", exc_info=True)
            return no_verdict("the judge's provider call failed: %r" % (error,))
    finally:
        try:
            await session.cleanup()
        except Exception:
            logger.warning("decision judge: session cleanup failed", exc_info=True)

    verdict = _parse_verdict(raw)
    if verdict is None:
        return no_verdict("the judge's response could not be parsed as a verdict")
    return verdict


def spawn_judge_window(parent_coordinator, window, *, extra_tools=None):
    """Fire-and-not-await convenience: schedule `judge_window()` as a Task.

    Returns the `asyncio.Task` immediately without awaiting it, so a caller on
    a turn's critical path (e.g. a hook handler) can call this and return
    right away. `judge_window()` itself never raises, so the returned task is
    safe to leave unawaited (its result is simply never collected) or to await
    later for its verdict -- either way nothing here can surface an exception
    into the event loop's default unhandled-exception handler.
    """
    return asyncio.create_task(
        judge_window(parent_coordinator, window, extra_tools=extra_tools)
    )
