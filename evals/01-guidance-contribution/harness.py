#!/usr/bin/env python3
"""Custom harness for the teamwork guidance-contribution eval.

Runs a 2x2 grid: two arms (teamwork bundle WITH the guidance layer / WITHOUT
it) times two tasks (01-ask-the-expert / 02-owner-is-the-expert) = four
trials, using the amplifier_evaluation building blocks directly (same shape
as examples/01-explorer-removal/harness.py): launch DTU -> install agent ->
AIUser drives the scenario -> Extractor pulls the session -> Grader scores it
-> destroy DTU. compare.py then reads all four trials' extracted sessions and
grader results and computes the pass-both headline per arm.

The arm is selected per trial via the TEAMWORK_REPO launch variable, which
the task profile's url_rewrites uses to redirect the teamwork bundle clone to
one of two pre-seeded Gitea mirrors. The Teamwork service source
(amplifier-app-teamwork) and this eval's own seed script are mirrored once,
fixed across every trial -- only the bundle differs.

Invoked by run.sh, which provisions the three mirrors and supplies
GITEA_URL / GITEA_TOKEN / the three repo names. See run.sh for the
surrounding setup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from amplifier_evaluation.ai_user import AIUser
from amplifier_evaluation.extractor import Extractor
from amplifier_evaluation.grader import Grader
from amplifier_evaluation.harness.loaders import load_agent, load_task
from amplifier_evaluation.harness.schema import TrialSpec

# compare.py is a sibling module (same directory as this script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare
from amplifier_evaluation.harness.trial import run_trial

log = logging.getLogger("teamwork-guidance-contribution")

# (arm label, Gitea repo name) -- label is also the trial output subdir.
ARMS = [
    ("with-guidance", "amplifier-bundle-teamwork-with-guidance"),
    ("without-guidance", "amplifier-bundle-teamwork-without-guidance"),
]
TASK_IDS = ["01-ask-the-expert", "02-owner-is-the-expert"]


async def run(args: argparse.Namespace) -> int:
    agent_dir = Path(args.agents_dir) / args.agent_id
    agent = load_agent(agent_dir)
    tasks = {task_id: load_task(Path(args.tasks_dir) / task_id) for task_id in TASK_IDS}
    log.info("agent=%s tasks=%s", agent.id, list(tasks))

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    log.info("setting up AIUser / Grader / Extractor sessions")
    ai_user, grader, extractor = AIUser(), Grader(), Extractor()
    await ai_user.setup()
    await grader.setup()
    await extractor.setup()

    repo_override = {
        "with-guidance": args.with_repo,
        "without-guidance": args.without_repo,
    }
    summary: dict[str, dict] = {}
    for arm_label, default_repo in ARMS:
        repo = repo_override[arm_label] or default_repo
        for task_id in TASK_IDS:
            trial_dir = output / arm_label / task_id
            spec = TrialSpec(
                agent=agent,
                task=tasks[task_id],
                trial_number=0,
                launch_variables={
                    "GITEA_URL": args.gitea_url,
                    "GITEA_TOKEN": args.gitea_token,
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
            try:
                result = await run_trial(
                    spec, trial_dir, ai_user=ai_user, grader=grader, extractor=extractor
                )
                summary[key] = {
                    "state": result.state,
                    "grader_overall": (result.grader or {}).get("overall_score"),
                    "error": result.error,
                }
                log.info("trial %s finished: state=%s", key, result.state)
            except (
                Exception
            ) as exc:  # keep going so the other trials + comparison still run
                summary[key] = {"state": "FAILED", "error": repr(exc)}
                log.exception("trial %s raised", key)

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
        if isinstance(v, dict) and v.get("state") == "FAILED"
    ]
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--agents-dir", default=str(here / "agents"), type=str)
    ap.add_argument("--tasks-dir", default=str(here / "tasks"), type=str)
    ap.add_argument("--agent-id", default="amplifier-teamwork")
    ap.add_argument(
        "--output",
        required=True,
        help="run output dir (gets with-guidance/ + without-guidance/)",
    )
    ap.add_argument("--gitea-url", required=True)
    ap.add_argument("--gitea-token", required=True)
    ap.add_argument("--with-repo", default="amplifier-bundle-teamwork-with-guidance")
    ap.add_argument(
        "--without-repo", default="amplifier-bundle-teamwork-without-guidance"
    )
    ap.add_argument("--app-repo", default="amplifier-app-teamwork-service")
    ap.add_argument("--support-repo", default="amplifier-teamwork-eval-support")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
