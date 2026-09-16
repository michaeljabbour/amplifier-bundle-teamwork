"""Runner for eval 02: can a single-window judge separate RECORD from SKIP?

The question this spike answers, and the only one (see
docs/scenarios/08-the-decision-the-session-made-itself.md and its twin 08b):
can a model, shown ONE window with no comparison available, separate a
decision that must be recorded from a path-choice at the same altitude that
must not? If it cannot, no amount of hook engineering, statefulness, or
windowing helps, and the detector should not be built -- a report saying so
is a success.

FIDELITY RULE, non-negotiable: each of the six cases in cases.py goes to its
own isolated `decision_judge.judge_window()` call. A judge that sees several
at once can contrast them, which makes the task easier than reality and
produces a falsely optimistic result. One window, one call, no shared memory
between cases. (One real, top-level session is built once and reused only to
give each judge call something to inherit a provider from -- exactly what a
`judge_window()` caller in production would hand it. It carries no
per-case state; `build_judge_session()` constructs a brand-new child session,
with no history, for every one of the six calls.)

Usage:
    /path/to/amplifier-environment/bin/python evals/02-decision-detection/run.py

Requires a real provider (ANTHROPIC_API_KEY, read from ~/.amplifier/keys.env
if not already in the environment). Writes a timestamped JSON report under
results/ (gitignored -- run output is never committed) and prints a summary
that leads with the false-positive count: per the scenarios, precision beats
recall here -- a missed decision costs a re-decision someone can make again,
a wrongly-recorded one costs every future reader permanently.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "modules/hooks-teamwork"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from amplifier_module_hooks_teamwork import decision_judge

from cases import CASES

JUDGE_MODEL = "claude-haiku-4-5"


def _load_keys():
    """Best-effort load of ~/.amplifier/keys.env into the environment.

    Never overrides a key already set; never raises if the file is absent.
    """
    path = Path("~/.amplifier/keys.env").expanduser()
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def _build_root_session():
    """A real, top-level session, built once, used only so each isolated judge
    call has a real provider config to inherit -- see this module's docstring
    for why reusing it across all six cases does not violate the fidelity
    rule.
    """
    from amplifier_core import AmplifierSession

    config = {
        "session": {"orchestrator": "loop-streaming", "context": "context-simple"},
        "providers": [
            {"module": "provider-anthropic", "config": {"default_model": JUDGE_MODEL}}
        ],
        "tools": [],
        "hooks": [],
    }
    session = AmplifierSession(config)
    try:
        await session.initialize()
    except BaseException:
        await decision_judge._cleanup_session(session)
        raise
    return session


def assess_case(case, verdict):
    """Unavailable or malformed model output cannot count as a correct SKIP."""
    available = isinstance(verdict, dict) and verdict.get("available") is True
    judged = ("RECORD" if verdict.get("record") else "SKIP") if available else "UNAVAILABLE"
    return {
        "id": case["id"], "expected": case["expected"], "judged": judged,
        "correct": available and judged == case["expected"],
        "note": case["note"], "verdict": verdict,
    }


async def run():
    _load_keys()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set (checked the environment and "
            "~/.amplifier/keys.env). Cannot reach a real provider -- see this "
            "script's docstring. Nothing was run."
        )
        return 1

    root = await _build_root_session()
    results = []
    try:
        for case in CASES:
            verdict = await decision_judge.judge_window(
                root.coordinator, case["window"]
            )
            results.append(assess_case(case, verdict))
    finally:
        await decision_judge._cleanup_session(root)

    false_positives = [
        r for r in results if r["expected"] == "SKIP" and r["judged"] == "RECORD"
    ]
    misses = [r for r in results if r["expected"] == "RECORD" and r["judged"] == "SKIP"]
    unavailable = [r for r in results if r["judged"] == "UNAVAILABLE"]
    correct = sum(1 for r in results if r["correct"])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": JUDGE_MODEL,
        "correct": correct,
        "total": len(results),
        "false_positive_count": len(false_positives),
        "false_positives": [r["id"] for r in false_positives],
        "miss_count": len(misses),
        "misses": [r["id"] for r in misses],
        "unavailable_count": len(unavailable),
        "unavailable": [r["id"] for r in unavailable],
        "results": results,
    }

    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("Decision-detection spike -- %d/%d correct" % (correct, len(results)))
    print("Unavailable verdicts (never counted as correct): %d" % len(unavailable))
    print(
        "False positives (SKIP judged RECORD), the costly direction: %d -- %s"
        % (len(false_positives), [r["id"] for r in false_positives])
    )
    print(
        "Misses (RECORD judged SKIP), the cheap direction: %d -- %s"
        % (len(misses), [r["id"] for r in misses])
    )
    for r in results:
        mark = "OK" if r["correct"] else "XX"
        print(
            "  [%s] %s expected=%-6s judged=%-6s reason=%s"
            % (
                mark,
                r["id"],
                r["expected"],
                r["judged"],
                (r["verdict"] or {}).get("reason"),
            )
        )
    print("Report written to %s" % out_path)
    return 1 if unavailable else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
