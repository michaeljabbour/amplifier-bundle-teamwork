"""An in-process judgment session that classifies one window as RECORD or SKIP.

Two judges share this module: a DECISION judge and a LESSON judge. Same
machinery throughout -- session construction, the verdict shape, the
no-verdict sentinel, `available` handling, cleanup, provider inheritance --
differing only in which rule/output-instructions text is put in front of the
model. See `_RULE`/`_OUTPUT_INSTRUCTIONS` (decision) and
`_LESSON_RULE`/`_LESSON_OUTPUT_INSTRUCTIONS` (lesson) below.

Decision specification: docs/scenarios/08-the-decision-the-session-made-itself.md
and its twin 08b-the-path-taken-that-binds-nothing.md.

Lesson specification: docs/scenarios/06-record-the-lesson-not-the-incident.md
and its twin 06b-the-lesson-that-is-only-true-on-your-machine.md. Read those
first -- their "What the good one knows" sections are the lesson rule
`_LESSON_RULE` encodes, not a re-derivation of it. 06 itself names a grading
limit that also bounds this module: a rubric derived from it cannot score a
single run, only a pair (a run that records, and a later run that benefits or
does not) -- `judge_lesson_window()` classifies one window and cannot, by
itself, prove the lesson transferred; see evals/03-lesson-detection/README.md.

NOT WIRED INTO ANY HOOK LIFECYCLE EVENT. This module is a library the hook (or
anything else in-process) can call; it does not subscribe to `prompt:submit`,
`session:end`, or anything else on its own. That wiring -- what fires each
judge, a watermark so nothing is re-detected at every later trigger, and the
"recorded by whom" question below -- is out of scope here on purpose (see the
open questions in 08/08b and 06/06b).

ASYNC CONTRACT. `judge_window()` performs a real provider call and must never be
awaited inline on a turn's critical path. Callers should fire it with
`asyncio.create_task(judge_window(...))` (or use `spawn_judge_window()` below,
which does exactly that) and inspect the result later -- at the next turn, at
`session:end`, wherever the caller's own lifecycle wiring decides to look. Both
paths turn operational failures into unavailable verdicts. Cancellation propagates
after session cleanup. An operational failure -- a
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

THE TALLY. Both judges are also shown a bounded, most-recent-first TALLY of
knowledge already recorded for this project (`build_tally()` below) -- claim/
title only, never whole records. This exists because a live DTU run recorded
the SAME lesson twice in different words ("whatever you mock, you are not
testing" vs. "a test only covers the defects that could make it fail"): claim-
text fingerprinting (see `__init__.py`'s `_record_verdict`) only catches an
exact repeat, never a paraphrase, and a judge shown nothing but its own window
has no way to notice the project already knows this. The tally is the judge's
half of that fix -- it can decline a semantic duplicate and, when a claim
genuinely builds on something already recorded, say so via the optional
`links` verdict field (see `_parse_links()` below), which `__init__.py`'s
`_record_verdict` carries through as `{"kind": "record", ...}` evidence
alongside the window's own `external` reference. The judge is otherwise
strictly less informed than a model that reads the project directly and cites
what it extends by hand -- the tally narrows, but does not close, that gap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)
CLEANUP_TIMEOUT_SECONDS = 5


async def _cleanup_session(session):
    """Bound cooperative cleanup without retaining provider failure details."""
    try:
        await asyncio.wait_for(session.cleanup(), timeout=CLEANUP_TIMEOUT_SECONDS)
    except Exception as error:
        logger.warning("decision judge: session cleanup failed (%s)", type(error).__name__)

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
#:
#: `links` (see "THE TALLY" in this module's docstring) is deliberately NOT in
#: this tuple: every field here must be PRESENT in the model's JSON or the
#: whole verdict is unusable (see `_parse_verdict` below), and `links` is the
#: one field this module is lenient about -- a model that omits it, or gets its
#: shape wrong, should not lose an otherwise-valid verdict over one optional
#: field. `_parse_links()` reads it separately and always produces a list.
VERDICT_FIELDS = (
    "record",
    "kind",
    "claim",
    "basis",
    "what_it_does_not_establish",
    "confidence",
    "reason",
)

def no_verdict(reason):
    """The sentinel returned whenever this module cannot honestly say RECORD or SKIP.

    `available` is False so evaluation cannot mistake a failure for a valid SKIP.
    `record` is False so a caller that only checks `verdict["record"]` degrades
    safely to "do nothing" rather than "do something on bad information."
    """
    return {
        "available": False,
        "record": False,
        "kind": None,
        "claim": None,
        "basis": None,
        "what_it_does_not_establish": None,
        "confidence": None,
        "reason": str(reason),
        "links": [],
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

# The tally (see this module's docstring, "THE TALLY") is claim/title-only and
# bounded the same way the window itself is: a count cap and a per-entry char
# cap, named here rather than reused from the window's own constants because a
# tally entry is a different shape (one line of already-recorded knowledge, not
# a turn of conversation) and the two may need to move independently. A tally
# that grows with the project would turn a cheap per-window classification
# into one whose cost scales with how much the project has ever recorded --
# these bounds keep it constant regardless of project size.
MAX_TALLY_ENTRIES = 20
MAX_TALLY_CLAIM_CHARS = 200

# The only record types read into the tally: durable knowledge, matching the
# server's own INCLUDES grouping and the two record kinds RecordInsightTool
# writes (see docs/scenarios/06's open questions on why nothing wrote one
# until now). Work/request/message/etc. are project *activity*, not knowledge
# a judge should be checking a claim against for duplication.
TALLY_RECORD_TYPES = ("insight", "idea")

# The same fields describe() and TITLE_FIELDS-style projection already favor
# for "what is this record about" -- kept local rather than imported from
# __init__.py because decision_judge.py is deliberately dependency-free of the
# hook module (see this file's own docstring on why it is a library, not a
# subscriber). Order matters: the first present, non-empty string wins.
_TALLY_CLAIM_FIELDS = ("claim", "title", "text", "summary", "body")


def _clip(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def build_window_payload(
    turns, tool_calls=None, tally=None, *,
    max_turns=MAX_WINDOW_TURNS, max_turn_chars=MAX_TURN_CHARS,
    max_tally_entries=MAX_TALLY_ENTRIES, max_tally_chars=MAX_TALLY_CLAIM_CHARS,
):
    """Build a bounded window payload: the last `max_turns` visible turns, plus
    consequential tool calls reduced to NAME AND TARGET ONLY -- never arguments,
    never results. A tool call's payload can carry anything a turn's tools
    touched; only what it was and roughly what it acted on belongs in a window
    that may end up quoted back by a judge, or worse, recorded as evidence.

    `turns` is an iterable of `{"user_prompt": str, "agent_responses": [{"text": str}, ...]}`
    -- the same shape the hook's own turn records use. `tool_calls` is an
    iterable of objects or dicts exposing `name` and (optionally) `target`.

    `tally` is an already-built list of entries from `build_tally()` below (or
    None) -- this function re-bounds it defensively (count and per-entry claim
    length) rather than trusting the caller, the same "never raises, never
    trusts an upstream bound" posture the rest of this function already takes
    for turns and tool_calls. A malformed entry (not a dict, or missing one of
    record_type/record_id/version/claim) is dropped, not fatal.

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

    bounded_tally = []
    for entry in list(tally or [])[:max_tally_entries]:
        if not isinstance(entry, dict):
            continue
        record_type, record_id = entry.get("record_type"), entry.get("record_id")
        version, claim = entry.get("version"), entry.get("claim")
        if record_type not in TALLY_RECORD_TYPES:
            continue
        if not isinstance(record_id, str) or not record_id.strip():
            continue
        if not isinstance(version, int) or isinstance(version, bool):
            continue
        if not isinstance(claim, str) or not claim.strip():
            continue
        bounded_tally.append({
            "record_type": record_type, "record_id": record_id,
            "version": version, "claim": _clip(claim, max_tally_chars),
        })

    return {"turns": bounded_turns, "tool_calls": bounded_calls, "tally": bounded_tally}


def build_tally(cache, *, max_entries=MAX_TALLY_ENTRIES, max_chars=MAX_TALLY_CLAIM_CHARS):
    """Build the tally `build_window_payload()` above bounds and both prompts
    render: a most-recent-first list of this project's already-recorded
    knowledge (insight/idea records), claim/title only -- never a whole
    record. See this module's docstring, "THE TALLY", for why this exists.

    `cache` is the hook's own already-synchronized cache
    (`self.state["cache"]`, keyed `"<record_type>:<id>"` ->
    `{"record": {...}, "delivery_id": ...}` -- see `__init__.py`'s
    `TeamworkHook.retrieve()`). No new network call is made or needed here;
    this reads what the hook already fetched on this turn's `prompt:submit`.

    "Most recent" is read from each record's own `updated_at` (present inside
    `content` for every record the service returns -- see
    `without_audit_trail()`'s AUDIT_FIELDS in `__init__.py`), falling back to
    `created_at` when a record has never been edited, and to the empty string
    (sorting last) when neither is a usable string -- a record with no
    resolvable timestamp is still shown, just not preferentially.

    Never raises: a malformed cache entry -- not a dict, no usable
    record/content, no string record_id, no integer version, no usable claim
    text -- is skipped, not fatal to the rest of the tally. An empty or
    missing cache degrades to an empty tally, which is exactly today's
    behavior (no "ALREADY RECORDED" section in the prompt) -- callers do not
    need to special-case "detection has never synchronized yet".
    """
    candidates = []
    for source in (cache or {}).values():
        if not isinstance(source, dict):
            continue
        record = source.get("record")
        if not isinstance(record, dict):
            continue
        record_type = record.get("record_type")
        if record_type not in TALLY_RECORD_TYPES:
            continue
        record_id = record.get("id")
        version = record.get("version")
        if not isinstance(record_id, str) or not record_id.strip():
            continue
        if not isinstance(version, int) or isinstance(version, bool):
            continue
        content = record.get("content") if isinstance(record.get("content"), dict) else {}
        claim = next(
            (content[key] for key in _TALLY_CLAIM_FIELDS
             if isinstance(content.get(key), str) and content[key].strip()),
            None,
        )
        if not claim:
            continue
        recency = content.get("updated_at") or content.get("created_at")
        recency = recency if isinstance(recency, str) else ""
        candidates.append({
            "record_type": record_type, "record_id": record_id, "version": version,
            "claim": _clip(claim, max_chars), "_recency": recency,
        })
    candidates.sort(key=lambda entry: entry["_recency"], reverse=True)
    return [
        {key: value for key, value in entry.items() if key != "_recency"}
        for entry in candidates[:max_entries]
    ]


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
no undo for an automatic record. When genuinely unsure, prefer SKIP.

If, and only if, an ALREADY RECORDED tally is shown to you below (this project's
own knowledge, most recent first, claim/title only), use it for ONE thing only.
No tally section means none of this applies -- judge the window on its own merits
as above. The tally never changes your RECORD/SKIP call: judge that on the window.

When what you record builds on something the tally already states, cite the entry it builds on in "links" using its exact
record_type/record_id/version as shown -- never invent one, never cite an entry
not actually shown to you."""

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
          structural or contingent
  "links": a list of ALREADY RECORDED entries this decision builds on, each exactly
          {"record_type": ..., "record_id": ..., "version": ...} copied from the
          tally shown to you -- or an empty list when it builds on nothing shown.
          Omit or use an empty list when record is false."""

# The lesson rule, distilled from 06 and 06b's "What the good one knows"
# sections -- quoted, not paraphrased, where 06/06b state the test in their
# own words. A lesson is a different question from a decision: not "a choice
# among alternatives that constrains future work" but "something learned that
# generalises past this task, for teammates who were never in this session."
# 06b's twin is about REACH, not durability -- a lesson can be real, correctly
# learned, and still fail to transfer because the evidence for it does not
# reach as far as the claim would. That is a different failure from the
# decision judge's structural-vs-contingent test above, so this is its own
# rule text rather than the same one with nouns swapped.
_LESSON_RULE = """You are judging ONE window of a session's turns to decide whether it contains a
LESSON that should be recorded to a shared project's durable knowledge, or whether it
should be skipped. You will see only this window -- no other cases, no history.

A lesson worth recording is something learned that generalises past this task, for
teammates who were never in this session -- not a narration of what happened, and not
a fact about this task alone. The test is not "is this true" or "did I just learn it" --
both are true of a fact that is only about this task and of the whole investigation that
produced it. It is: record what will still be true when this task is forgotten -- would
this change what somebody does on a DIFFERENT task? The incident is the evidence, not
the lesson: a lesson with no incident attached is an opinion and must be refused; a
lesson that is only its incident has not been generalised and will not be found by
anybody who did not live it.

The test that actually separates RECORD from SKIP is NOT how surprising the discovery
felt, and not merely whether it sounds like a rule someone else could reuse. Something
learned in this session can be real, correctly learned, and still not transfer, because
its evidence does not reach as far as the claim it would license. The test is:

    Could what I observed differ between my environment and the one my claim is about?

  - NO, the evidence reaches as far as the claim -> RECORD, stated so it is usable by
    somebody who was never near this task -- e.g. "a negative result from a search is
    bounded by what the search can see" holds regardless of whose machine, whose
    version, whose task this was.
  - YES, the evidence does not reach that far -> SKIP. It is an observation awaiting
    evidence from that other environment, not a lesson yet -- e.g. two suites failing
    on THIS checkout does not establish the build is broken for everyone. A claim's
    scope must be no wider than the evidence that produced it; recording it anyway
    would teach the next reader to distrust a signal (a green gate, an exhaustive-
    looking search) that was fine all along -- which is worse than recording nothing.

Precision matters more than recall. A missed lesson costs a re-discovery someone can
make again later. A wrongly-recorded one costs every future reader permanently -- there
is no undo for an automatic record. When genuinely unsure, prefer SKIP.

If, and only if, an ALREADY RECORDED tally is shown to you below (this project's
own knowledge, most recent first, claim/title only), use it two ways. No tally
section means none of this applies -- judge the window on its own merits as above.

When what you record builds on something the tally already states, cite the entry it builds on in "links" using its exact
record_type/record_id/version as shown -- never invent one, never cite an entry
not actually shown to you. A genuinely NEW lesson, on a different mechanism or
limitation than anything shown, stays RECORD even when the tally is full of
near-miss entries on the same general topic -- on-topic is not duplicate."""

_LESSON_OUTPUT_INSTRUCTIONS = """Respond with ONLY a single JSON object -- no markdown fence, no commentary
before or after it -- with exactly these keys:

  "record": true or false
  "kind": a short free-text label for what this is (e.g. "search-method limit",
          "environment-scoped observation", "process rule"), or null when record is false
  "claim": the transferable statement -- what stays true after this task is forgotten,
           usable by someone who was never in this session -- or null when record is false
  "basis": "observation" or "inference", or null when record is false
  "what_it_does_not_establish": what this does NOT establish, or null when record
          is false
  "confidence": "low", "medium", or "high"
  "reason": one or two sentences on why, naming whether the evidence reaches as far as
          the claim or is bounded to this environment/task -- and, when declining a
          why
  "links": a list of ALREADY RECORDED entries this lesson builds on, each exactly
          {"record_type": ..., "record_id": ..., "version": ...} copied from the
          tally shown to you -- or an empty list when it builds on nothing shown.
          Omit or use an empty list when record is false."""


def _build_prompt(rule, output_instructions, window):
    """Shared prompt assembly for both judges -- only `rule` and
    `output_instructions` differ between the decision and lesson prompts.
    """
    lines = [rule, ""]
    tally = window.get("tally") or []
    if tally:
        lines.append(
            "ALREADY RECORDED (this project's own knowledge, most recent first; "
            "claim/title only, not full records):"
        )
        for entry in tally:
            lines.append(
                "  - %s:%s@%s -- %s"
                % (entry["record_type"], entry["record_id"], entry["version"], entry["claim"])
            )
        lines.append("")
    lines.append("WINDOW:")
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
    lines.append(output_instructions)
    return "\n".join(lines)


def _prompt_for(window):
    return _build_prompt(_RULE, _OUTPUT_INSTRUCTIONS, window)


def _prompt_for_lesson(window):
    return _build_prompt(_LESSON_RULE, _LESSON_OUTPUT_INSTRUCTIONS, window)


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
    if (not isinstance(data, dict) or not isinstance(data.get("record"), bool)
            or not all(field in data for field in VERDICT_FIELDS)):
        return None
    verdict = {field: data.get(field) for field in VERDICT_FIELDS}
    if (verdict["confidence"] not in ("low", "medium", "high")
            or not isinstance(verdict["reason"], str) or not verdict["reason"].strip()):
        return None
    if verdict["record"]:
        if verdict["basis"] not in ("observation", "inference"):
            return None
        if any(not isinstance(verdict[field], str) or not verdict[field].strip()
               for field in ("kind", "claim", "what_it_does_not_establish")):
            return None
    elif any(verdict[field] is not None
             for field in ("kind", "claim", "basis", "what_it_does_not_establish")):
        return None
    verdict["links"] = _parse_links(data.get("links")) if verdict["record"] else []
    verdict["available"] = True
    return verdict


#: The only evidence-kind record types RecordInsightTool's own `record`
#: evidence kind accepts (see __init__.py's RecordInsightTool._evidence_refs).
#: Kept identical and separate rather than imported, matching this module's
#: existing choice to stay import-free of the hook module.
_LINK_RECORD_TYPES = ("work", "request", "idea", "insight")


def _parse_links(raw):
    """Defensively validate the judge's optional `links` field.

    Each surviving entry matches RecordInsightTool's own `record` evidence-kind
    shape exactly: `record_type` in work/request/idea/insight, a non-empty
    string `record_id`, an integer `version`. An entry that does not match is
    DROPPED, not fatal to the rest of the verdict or the rest of `links` --
    per this module's docstring, the judge should emit nothing rather than a
    malformed ref, but this is the backstop for when it doesn't. `raw` that is
    not a list (absent, null, a string, ...) degrades to no links at all.

    Never raises.
    """
    if not isinstance(raw, list):
        return []
    links = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        record_type = item.get("record_type")
        record_id = item.get("record_id")
        version = item.get("version")
        if record_type not in _LINK_RECORD_TYPES:
            continue
        if not isinstance(record_id, str) or not record_id.strip():
            continue
        if not isinstance(version, int) or isinstance(version, bool):
            continue
        links.append({"record_type": record_type, "record_id": record_id, "version": version})
    return links


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
        return providers, "the calling session has no provider configured to inherit", False
    if not model_role:
        return (
            providers,
            "no model_role requested; using the calling session's provider unchanged",
            False,
        )

    try:
        resolver = parent_coordinator.get_capability("model_role_resolver")
    except Exception:
        resolver = None
    if resolver is None:
        return providers, (
            "no model_role_resolver capability is registered on the calling session; "
            "using its provider unchanged"
        ), False

    try:
        preferences = await resolver.resolve(model_role)
    except Exception as error:
        return (
            providers,
            "model_role_resolver.resolve(%r) failed (%s); using the calling session's provider unchanged"
            % (
                model_role,
                type(error).__name__,
            ),
            False,
        )
    if not preferences:
        return (
            providers,
            "model_role %r resolved to no candidates; using the calling session's provider unchanged"
            % model_role,
            False,
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
        ), False
    if _GLOB_CHARS.search(pref_model):
        return providers, (
            "model_role %r resolved to a glob pattern (%r) that needs live model-list "
            "resolution this judge does not perform; using the calling session's provider unchanged"
            % (model_role, pref_model)
        ), False

    for spec in providers:
        if _bare_module(spec.get("module", "")) == pref_provider:
            spec["config"] = dict(spec.get("config") or {})
            spec["config"]["default_model"] = pref_model
            # The loop chooses the lowest provider priority. Restrict this
            # child to the exact resolved provider so a parent's higher-priority
            # expensive provider cannot silently override the fast-role choice.
            return [spec], "model_role %r resolved to %s/%s" % (
                model_role,
                pref_provider,
                pref_model,
            ), True

    return providers, (
        "model_role %r resolved to provider %r, which is not among the calling session's "
        "configured providers; using its provider unchanged"
        % (model_role, pref_provider)
    ), False


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

    Uses Foundation's standard loop-streaming/context-simple modules, reusing
    the parent's module-source resolver when available. The loop is bounded to
    one model iteration. Host approval policy is carried into the child.

    Returns `(session, note)`. The caller owns the session and must call
    `await session.cleanup()` when done with it; `judge_window()` below does
    this for you.
    """
    from amplifier_core import AmplifierSession  # lazy: amplifier-core is host-supplied

    parent_id = getattr(parent_coordinator, "session_id", None)
    providers, note, role_honored = await _resolve_providers(parent_coordinator, model_role)
    config = {
        "session": {
            "orchestrator": {"module": "loop-streaming", "config": {"max_iterations": 1}},
            "context": "context-simple",
        },
        "providers": providers,
        "tools": [],
        "hooks": [],
    }
    # Follow Foundation's create_session seam: mount its source resolver before
    # initialization. The child retains its own loader, coordinator, and state.
    session = AmplifierSession(
        config, parent_id=parent_id,
        approval_system=getattr(parent_coordinator, "approval_system", None),
    )
    try:
        get_mount = getattr(parent_coordinator, "get", None)
        resolver = get_mount("module-source-resolver") if callable(get_mount) else None
        if resolver is not None:
            await session.coordinator.mount("module-source-resolver", resolver)
        await session.initialize()
        for tool in extra_tools or []:
            name = getattr(tool, "name", None)
            if not name:
                continue
            await session.coordinator.mount("tools", tool, name=name)
    except BaseException:
        await _cleanup_session(session)
        raise
    return session, note, role_honored


# ---------------------------------------------------------------------------
# The public entry points
# ---------------------------------------------------------------------------


async def _judge(parent_coordinator, window, prompt_builder, *, extra_tools=None, log_label="decision judge"):
    """Shared body for `judge_window()` and `judge_lesson_window()`: classify
    ONE window using whichever `prompt_builder(window)` the caller supplies;
    operational failures return an unavailable verdict.

    See this module's docstring for the async contract: this performs a real
    provider call and must not be awaited inline on a turn's critical path.

    On an operational failure -- session construction, the provider call, or a response
    that will not parse as a verdict -- returns `no_verdict(reason)` rather
    than raising. Cancellation propagates after owned-session cleanup.
    """
    try:
        session, note, role_honored = await build_judge_session(
            parent_coordinator, extra_tools=extra_tools
        )
    except Exception as error:
        logger.warning("%s: could not build a judge session (%s)", log_label, type(error).__name__)
        return no_verdict("could not build a judge session (%s)" % type(error).__name__)
    # A requested role that could NOT be honored is a COST regression -- this
    # judge then runs on the calling session's own (often frontier) model, on
    # every triggered turn. Logged at DEBUG it is invisible, which is how a
    # silent fallback to an expensive model survives a green DTU run. Warn.
    if not role_honored:
        logger.warning("%s: requested model role was NOT honored -- %s", log_label, note)
    else:
        logger.debug("%s: provider inheritance -- %s", log_label, note)

    try:
        try:
            raw = await session.execute(prompt_builder(window))
        except Exception as error:
            logger.warning("%s: the provider call failed (%s)", log_label, type(error).__name__)
            return no_verdict("the judge's provider call failed (%s)" % type(error).__name__)
    finally:
        await _cleanup_session(session)

    verdict = _parse_verdict(raw)
    if verdict is None:
        return no_verdict("the judge's response could not be parsed as a verdict")
    return verdict


async def judge_window(parent_coordinator, window, *, extra_tools=None):
    """Classify ONE window as a DECISION verdict (docs/scenarios/08, 08b)."""
    return await _judge(
        parent_coordinator, window, _prompt_for, extra_tools=extra_tools, log_label="decision judge"
    )


async def judge_lesson_window(parent_coordinator, window, *, extra_tools=None):
    """Classify ONE window as a LESSON verdict (docs/scenarios/06, 06b).

    Same contract as `judge_window()` in every respect but the prompt: the
    verdict shape, `no_verdict` sentinel, and failure handling are identical.
    """
    return await _judge(
        parent_coordinator, window, _prompt_for_lesson, extra_tools=extra_tools, log_label="lesson judge"
    )


def spawn_judge_window(parent_coordinator, window, *, extra_tools=None):
    """Fire-and-not-await convenience: schedule `judge_window()` as a Task.

    Returns the `asyncio.Task` immediately without awaiting it, so a caller on
    a turn's critical path (e.g. a hook handler) can call this and return
    right away. The caller owns the task and should retain it, collect its
    verdict, and cancel/await it during caller shutdown. Operational failures
    return unavailable verdicts; cancellation remains observable to the caller.
    """
    return asyncio.create_task(
        judge_window(parent_coordinator, window, extra_tools=extra_tools)
    )


def spawn_lesson_judge_window(parent_coordinator, window, *, extra_tools=None):
    """Fire-and-not-await convenience for `judge_lesson_window()` -- see
    `spawn_judge_window()`'s docstring; identical contract, lesson prompt."""
    return asyncio.create_task(
        judge_lesson_window(parent_coordinator, window, extra_tools=extra_tools)
    )
