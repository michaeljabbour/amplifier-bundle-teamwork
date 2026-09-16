#!/usr/bin/env python3
"""Compare routing behaviour across the guidance-contribution eval's 2x2 grid.

Two arms (WITH the guidance layer / WITHOUT it) times two tasks (01
ask-the-expert / 02 owner-is-the-expert) = four trials. This script is the
measurement half of the eval:

  - per (arm, task): read the grader's result and the extracted session to
    find every `teamwork_send` tool call and who it addressed
  - per arm: the HEADLINE is pass-both -- did this arm pass BOTH tasks? A
    per-task score is not the headline; see README.md for why. An agent that
    always asks the owner passes 02 and fails 01; one that never does passes
    01 and fails 02. Both task gates must pass to count this pair as successful; one pair
    does not establish a causal guidance effect.

Usage:
    python compare.py --with-dir <run_dir>/with-guidance \\
                       --without-dir <run_dir>/without-guidance \\
                       --output <run_dir>

Each `--with-dir` / `--without-dir` is expected to contain one subdirectory
per task id (e.g. `01-ask-the-expert/`, `02-owner-is-the-expert/`), each a
trial output directory as produced by the harness / run_trial (containing
`grader/grader_result.json` and an `extraction/` subtree).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

TASK_IDS = ["01-ask-the-expert", "02-owner-is-the-expert"]
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# A score threshold cannot compensate for violating a critical routing rule.
# These gates mirror the explicitly CRITICAL criteria in the two rubrics.
PASS_THRESHOLD = 0.7
CRITICAL_CRITERIA = {
    "01-ask-the-expert": {"sent_to_casey": 55, "never_asked_blair_to_decide": 25},
    "02-owner-is-the-expert": {
        "asks_drew_or_reasons_with_drews_input": 55,
        "did_not_route_to_a_different_teammate": 30,
    },
}


def _number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _grade_status(grader: dict, task_id: str) -> tuple[bool, bool]:
    """Return (valid measurement, critical gates passed); missing data fails closed."""
    overall = grader.get("overall_score")
    evaluations = grader.get("evaluations")
    required = CRITICAL_CRITERIA.get(task_id)
    if not required or not _number(overall) or not 0 <= overall <= 1:
        return False, False
    if not isinstance(evaluations, list) or len(evaluations) != 1:
        return False, False
    evaluation = evaluations[0]
    criteria = evaluation.get("rubric_scores") if isinstance(evaluation, dict) else None
    if not isinstance(criteria, dict):
        return False, False
    passed = True
    for name, expected in required.items():
        score = criteria.get(name)
        if not isinstance(score, dict):
            return False, False
        awarded, possible = score.get("points_awarded"), score.get("points_possible")
        if (
            not _number(awarded)
            or not _number(possible)
            or possible != expected
            or not 0 <= awarded <= possible
        ):
            return False, False
        passed = passed and awarded == possible
    return True, passed


# Which recipient is CORRECT for each task, used only to label the report
# for a human reader -- the grader (running inside the DTU, with the full
# rubric) is what actually scores pass/fail, not this script.
EXPECTED_RECIPIENT = {
    "01-ask-the-expert": "Casey",
    "02-owner-is-the-expert": "Drew (i.e. no teamwork_send needed -- Drew is the owner)",
}


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                yield value
        except json.JSONDecodeError:
            continue


def _find_session_dirs(trial_dir: Path) -> list[Path]:
    root = trial_dir / "extraction"
    if not root.is_dir():
        root = trial_dir
    return [p.parent for p in root.rglob("events.jsonl")] or [
        p.parent for p in root.rglob("transcript.jsonl")
    ]


def _pick_session_dir(trial_dir: Path) -> Path | None:
    dirs = _find_session_dirs(trial_dir)
    if not dirs:
        return None
    # amplifier continue resumes the same session, and this agent never
    # delegates, so there should be exactly one. If more than one somehow
    # appears, the most recently modified is the live one.
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return dirs[0]


def _tool_calls_from_transcript(transcript: Path, tool_name: str) -> list[dict]:
    """Anthropic-style transcript: each line a message with a `content` list
    of blocks; a block with `type` "tool_use" and `name` == tool_name carries
    the call's `input`."""
    calls = []
    for msg in _iter_jsonl(transcript):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == tool_name:
                calls.append(block.get("input") or {})
    return calls


def _tool_calls_from_events(events: Path, tool_name: str) -> list[dict]:
    """events.jsonl fallback: accept a few plausible shapes for a tool-call
    event, since the exact schema depends on the amplifier-core version that
    produced it. This eval was NOT run end to end before authoring this
    parser (see README.md's validation limits) -- if neither shape matches,
    calls will read back empty rather than raising, and the report says so."""
    calls = []
    for ev in _iter_jsonl(events):
        name = ev.get("event", "")
        data = ev.get("data") or {}
        if not isinstance(data, dict):
            continue
        if name == "tool:pre" and data.get("tool_name") == tool_name:
            call = data.get("tool_input", data.get("arguments", data.get("input", {})))
            if isinstance(call, dict):
                calls.append(call)
        elif name == "content_block:end":
            block = data.get("block") or {}
            if (
                block.get("type") in ("tool_use", "tool_call")
                and block.get("name") == tool_name
            ):
                calls.append(block.get("input") or {})
    return calls


def _recipient(call: dict) -> str | None:
    for key in ("to_person", "to_node_label", "to_agent_id"):
        if call.get(key):
            return f"{call[key]} (via {key})"
    return None


def _execution_state(trial_dir: Path) -> str:
    """Read explicit trial metadata only; missing metadata means grading only."""
    states = []
    for name in ("execution-state.json", "state.json"):
        path = trial_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            state = data.get("state") if isinstance(data, dict) else None
        except (OSError, ValueError):
            state = None
        states.append(
            state if state in ("completed", "failed", "cancelled") else "incomplete"
        )
    if not states:
        return "unverified"
    return (
        "completed"
        if all(state == "completed" for state in states)
        else next(state for state in states if state != "completed")
    )


def collect_trial(trial_dir: Path, task_id: str | None = None) -> dict:
    trial_dir = Path(trial_dir)
    grader_path = trial_dir / "grader" / "grader_result.json"
    grader = {}
    if grader_path.exists():
        try:
            grader = json.loads(grader_path.read_text())
        except json.JSONDecodeError:
            grader = {}
    if not isinstance(grader, dict):
        grader = {}

    session_dir = _pick_session_dir(trial_dir)
    calls: list[dict] = []
    if session_dir is not None:
        calls = _tool_calls_from_transcript(
            session_dir / "transcript.jsonl", "teamwork_send"
        )
        if not calls:
            calls = _tool_calls_from_events(
                session_dir / "events.jsonl", "teamwork_send"
            )

    overall = grader.get("overall_score")
    grader_valid, critical_pass = _grade_status(grader, task_id or trial_dir.name)
    execution_state = _execution_state(trial_dir)
    measurement_valid = grader_valid and execution_state in ("completed", "unverified")
    evaluations = grader.get("evaluations", []) if grader_valid else []
    return {
        "trial_dir": str(trial_dir),
        "session_dir": str(session_dir) if session_dir else None,
        "overall_score": overall,
        "rubric": [
            {
                "name": e.get("name"),
                "criteria": {
                    k: {
                        "points_awarded": v.get("points_awarded"),
                        "points_possible": v.get("points_possible"),
                        "reasoning": ANSI.sub("", str(v.get("reasoning", "") or "")),
                    }
                    for k, v in (e.get("rubric_scores") or {}).items()
                    if isinstance(v, dict)
                },
            }
            for e in evaluations
        ],
        "teamwork_send_calls": [
            {"recipient": _recipient(c), "body": ANSI.sub("", c.get("body", "") or "")}
            for c in calls
        ],
        "pass": measurement_valid and critical_pass and overall >= PASS_THRESHOLD,
        "grader_valid": grader_valid,
        "measurement_valid": measurement_valid,
        "execution_state": execution_state,
        "completion_verified": execution_state == "completed",
        "critical_criteria_pass": critical_pass,
        "grader_missing": not grader_path.exists(),
    }


def collect_arm(arm_dir: Path) -> dict:
    return {task_id: collect_trial(arm_dir / task_id, task_id) for task_id in TASK_IDS}


def compare(with_dir: Path, without_dir: Path) -> dict:
    with_arm = collect_arm(Path(with_dir))
    without_arm = collect_arm(Path(without_dir))
    pass_both_with = all(with_arm[t]["pass"] for t in TASK_IDS)
    pass_both_without = all(without_arm[t]["pass"] for t in TASK_IDS)
    return {
        "with_guidance": with_arm,
        "without_guidance": without_arm,
        "pass_both": {
            "with_guidance": pass_both_with,
            "without_guidance": pass_both_without,
        },
        "expected_recipient": EXPECTED_RECIPIENT,
        "pass_threshold": PASS_THRESHOLD,
    }


def render_markdown(result: dict) -> str:
    lines = ["# Teamwork guidance-contribution eval -- pass-both comparison", ""]
    lines += [
        "Comparison labels: teamwork bundle WITH vs WITHOUT the guidance layer",
        "(context pointer + teamwork-protocol skill). This report scores supplied",
        "artifacts; it does not verify arm provenance, identical setup, or a causal effect.",
        "Without execution-state metadata, PASS means grading only; completion is unverified.",
        "",
        "**The headline is pass-both, not either task alone.** Task 01 rewards",
        "routing AWAY from the owner; task 02 rewards routing (or deciding) WITH",
        "the owner. A rule based only on whether someone owns the session cannot pass",
        "both task gates.",
        "",
        "| Arm | 01-ask-the-expert | 02-owner-is-the-expert | PASS BOTH |",
        "|---|---|---|---|",
    ]
    for label, key in (
        ("WITH guidance", "with_guidance"),
        ("WITHOUT guidance", "without_guidance"),
    ):
        arm = result[key]
        cells = []
        for t in TASK_IDS:
            trial = arm[t]
            score = trial["overall_score"]
            mark = (
                "INCOMPLETE"
                if not trial["measurement_valid"]
                else ("PASS" if trial["pass"] else "FAIL")
            )
            if trial["measurement_valid"] and not trial["completion_verified"]:
                mark += " (grading only)"
            recipients = (
                ", ".join(c["recipient"] or "?" for c in trial["teamwork_send_calls"])
                or "(none sent)"
            )
            cells.append(f"{mark} ({score}) -- sent to: {recipients}")
        headline = "YES" if result["pass_both"][key] else "no"
        lines.append(f"| {label} | {cells[0]} | {cells[1]} | **{headline}** |")

    lines += [
        "",
        "Expected correct recipient per task (for a human reading this report;",
        "the grader, not this line, is what actually scores pass/fail):",
    ]
    for t in TASK_IDS:
        lines.append(f"- `{t}`: {result['expected_recipient'][t]}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-dir", required=True, type=Path)
    ap.add_argument("--without-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    result = compare(args.with_dir, args.without_dir)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "comparison.json").write_text(json.dumps(result, indent=2))
    md = render_markdown(result)
    (args.output / "comparison.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
