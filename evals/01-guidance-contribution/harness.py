#!/usr/bin/env python3
"""Offline-tested orchestration for the teamwork guidance-contribution eval.

The proposed live design is a 2x2 grid: two arms (teamwork bundle WITH the guidance layer / WITHOUT
it) times two tasks (01-ask-the-expert / 02-owner-is-the-expert) = four
trials, using the amplifier_evaluation building blocks directly (same shape
as examples/01-explorer-removal/harness.py): launch DTU -> install agent ->
AIUser drives the scenario -> Extractor pulls the session -> Grader scores it
-> destroy DTU. compare.py then reads all four trials' extracted sessions and
grader results and computes the pass-both headline per arm.

The retained draft profiles use TEAMWORK_REPO to select a mirror. They do
not construct or verify controlled, immutable arms. A future live runner
must do that and provide secure source transport before using these profiles.

The live CLI is deliberately disabled. run.sh creates no resources. The run()
function is retained for offline contract tests with synthetic trial runners.
Secure private-source transport and immutable arm construction remain unresolved;
see README.md before implementing any supported live entry point.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# compare.py is a sibling module (same directory as this script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare

log = logging.getLogger("teamwork-guidance-contribution")

# (arm label, Gitea repo name) -- label is also the trial output subdir.
ARMS = [
    ("with-guidance", "amplifier-bundle-teamwork-with-guidance"),
    ("without-guidance", "amplifier-bundle-teamwork-without-guidance"),
]
TASK_IDS = ["01-ask-the-expert", "02-owner-is-the-expert"]


async def run(
    args: argparse.Namespace,
    *,
    trial_runner=None,
    agent_loader=None,
    task_loader=None,
    trial_spec_factory=None,
) -> int:
    """Compare a synthetic 2x2 fixture through explicitly supplied offline callables.

    No evaluation SDK is imported and no AIUser, Grader, Extractor, provider,
    or DTU is constructed here. Callers own the injected fixture functions.
    """
    if not all(
        callable(item)
        for item in (trial_runner, agent_loader, task_loader, trial_spec_factory)
    ):
        raise RuntimeError(
            "Live evaluation is unavailable; an offline runner, loaders, and "
            "trial-spec factory are required"
        )
    agent_dir = Path(args.agents_dir) / args.agent_id
    agent = agent_loader(agent_dir)
    tasks = {
        task_id: task_loader(Path(args.tasks_dir) / task_id) for task_id in TASK_IDS
    }
    log.info("agent=%s tasks=%s", agent.id, list(tasks))

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    repo_override = {
        "with-guidance": args.with_repo,
        "without-guidance": args.without_repo,
    }
    summary: dict[str, dict] = {}
    for arm_label, default_repo in ARMS:
        repo = repo_override[arm_label] or default_repo
        for task_id in TASK_IDS:
            trial_dir = output / arm_label / task_id
            spec = trial_spec_factory(
                agent=agent,
                task=tasks[task_id],
                trial_number=0,
                launch_variables={
                    "GITEA_URL": args.gitea_url,
                    "TEAMWORK_REPO": repo,
                    "APP_REPO": args.app_repo,
                    "SUPPORT_REPO": args.support_repo,
                },
            )
            log.info(
                "=== trial arm=%s task=%s (TEAMWORK_REPO=%s) ===",
                arm_label,
                task_id,
                repo,
            )
            key = f"{arm_label}/{task_id}"
            trial_dir.mkdir(parents=True, exist_ok=True)
            state_path = trial_dir / "execution-state.json"
            # Invalidate any old grade before starting this fixture. An interrupted
            # runner leaves pending metadata, which standalone comparison rejects.
            state_path.write_text(json.dumps({"state": "pending"}))
            try:
                result = await trial_runner(spec, trial_dir)
                summary[key] = {
                    "state": result.state,
                    "grader_overall": (result.grader or {}).get("overall_score"),
                    "error_present": bool(result.error),
                }
                log.info("trial %s finished: state=%s", key, result.state)
            except (
                Exception
            ) as exc:  # keep going so the other trials + comparison still run
                summary[key] = {"state": "failed", "error_type": type(exc).__name__}
                log.warning("trial %s raised (%s)", key, type(exc).__name__)
            state = summary[key]["state"]
            state_path.write_text(
                json.dumps(
                    {
                        "state": state
                        if state in ("completed", "failed", "cancelled")
                        else "incomplete"
                    }
                )
            )

    log.info("computing pass-both comparison")
    comparison = compare.compare(output / "with-guidance", output / "without-guidance")
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2))
    md = compare.render_markdown(comparison)
    (output / "comparison.md").write_text(md)

    summary["pass_both"] = comparison.get("pass_both", {})
    (output / "summary.json").write_text(json.dumps(summary, indent=2))

    print("\n" + md)
    log.info("results: %s", output)

    failed = [
        k
        for k, v in summary.items()
        if isinstance(v, dict) and "state" in v and v["state"] != "completed"
    ]
    invalid_grades = any(
        not trial["measurement_valid"]
        for arm in ("with_guidance", "without_guidance")
        for trial in comparison[arm].values()
    )
    return 1 if failed or invalid_grades else 0


def main() -> int:
    print(
        "Live evaluation is unavailable: private mirror credentials cannot be "
        "passed safely through the current DTU launch-variable contract. "
        "Controlled arm snapshots and private mirror cleanup also need repair. "
        "Use the offline regression suite; see README.md. No trial was started.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
