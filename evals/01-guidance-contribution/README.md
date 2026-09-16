# Eval 01: Does guidance change who gets asked?

**Offline scoring and comparison are available. Live execution is disabled; no behavioral measurement has been run.**

The `run.sh`, `harness.py`, and `eval-support/seed.py` commands exit with status 2 before reading
credentials, creating mirrors or starting trials. The previous launcher put a
Gitea credential in process arguments, could mirror private service source into
public repositories, and force-pushed fixed shared repository names. Its code
remains in Git history, but those paths are no longer executable through this
instrument. Approval alone does not fix these technical problems.

This change provides the comparator, critical-criterion gates, synthetic
orchestration regressions, and the proposed task/rubric definitions. The Python
`run()` function is retained as an internal test seam, exercised with synthetic
trial runners, loaders and trial-spec factories; it imports no evaluation SDK
and constructs no AIUser, Grader, Extractor, provider or DTU. It is not a
supported live entry point.

## What this would measure

The hypothesis is that the Teamwork context pointer and `teamwork-protocol`
skill change routing behavior, rather than merely being loadable. Two tasks
probe opposite relationships between the session owner and the domain expert:

- `01-ask-the-expert`: Blair owns the session, but Casey knows API compatibility.
  The agent should address the substantive question to Casey without asking
  Blair to decide it.
- `02-owner-is-the-expert`: Drew owns the session and knows hook lifecycle
  design. The agent should ask Drew or reason from Drew's input, without
  routing the decision to another teammate.

Always asking the owner fails task 01; always routing away from the owner fails
task 02. Correct routing requires the subject and the recorded expertise.

The proposed experiment is a 2x2 grid: these two tasks with and without the
same guidance layer. `pass_both` reports whether an arm passed both tasks in
that run. One trial per cell is an exploratory observation, not evidence that
an agent learned a rule or that guidance caused a reliable improvement.
Repeated trials, calibrated graders and recorded versions are still needed.

## Offline scoring contract

A task passes only when its score is at least 0.7 **and all critical rubric
criteria receive full credit**. Task 01 requires both `sent_to_casey` and
`never_asked_blair_to_decide`; task 02 requires both
`asks_drew_or_reasons_with_drews_input` and
`did_not_route_to_a_different_teammate`. This prevents a score of 0.75 or 0.70
from hiding the routing failure the task is supposed to detect.

Missing, malformed or incomplete critical grading data is reported as
`INCOMPLETE`, never as a completed behavioral measurement. Failed/cancelled
trials or missing valid grades produce harness status 1 and `INCOMPLETE` reports,
even when grade files remain after a failed or cancelled trial. A completed, validly
graded run may return status 0 while `pass_both` is false: failed behavior and
failed execution are different outcomes.

The offline harness writes a minimal `execution-state.json` for each trial.
Standalone comparison respects that file and the evaluation library's native
`state.json` when present, so recomparison cannot turn an incomplete run into
a pass. Without execution metadata, the report explicitly labels its results
as grading only and leaves execution completion unverified.

The comparator reads the current `tool:pre.data.tool_input` shape and retains
legacy `arguments`/`input` support. Recipient extraction is supporting evidence;
the grader still examines the full conversation. Actual extraction layout,
selection of the intended session and resumed-session behavior need validation
before any real result is trusted.

## Intended isolated environment

Each trial would run the Teamwork service's local mode inside its own DTU,
seed five fictional members with distinct profiles, and use the real
`setup_teamwork.py` to create an explicitly opted-in project overlay. The
service, profiles and setup would be identical across arms. No production
participant, active runtime or primary provider configuration is part of the
experiment. Core remains host-supplied.

The draft profiles and install instructions are retained for review. They are
not validated live configurations. Their former mirror architecture used two
bundle mirrors, one service mirror and one support-script mirror. Re-enabling
that design requires resolving every item below.

## Technical work required before enabling trials

1. **Secure transport and private mirrors.** The current evaluation DTU adapter
   turns launch variables into `--var KEY=VALUE` arguments. Passing
   `GITEA_TOKEN` that way is unsafe. A supported credential transport or an
   evaluation-local source-transfer design must avoid secrets in process
   arguments and retained output. Private app source must remain private;
   mirrors need unique ownership, bounded lifetime and verified cleanup.
   Do not restore the fixed-name force-push launcher.
2. **Controlled, immutable arms.** Moving `main` and `skill-wiring` are not a valid control. At the start
   of review their hook code differed; guidance has since merged into main. A default clone of a
   local checkout also need not contain a local `skill-wiring` branch. Build
   both arms from one pinned base, apply only the reviewed guidance delta,
   verify identical mechanism bytes, and record exact manifests. See
   `change.md`. Merging guidance into main must not silently erase the control.
3. **Fixed execution and grading inputs.** Pin the service, CLI, Foundation,
   provider/model, evaluation library and grader configuration; establish the
   same setup in both arms. Check all seeded profile fields, not only `focus`.
4. **Bounded integration verification.** Confirm actual tools mount, the local
   enrollment overlay works, follow-up turns resume the same session, the
   intended transcript is extracted, tool recipients are parsed, and every
   temporary runtime and credential is cleaned up. Calibration and paid trials
   remain unperformed; offline tests cannot establish model behavior.

These are deferred implementation and validation requirements for a future
live runner. They do not prevent using the offline comparator or running its
synthetic regressions. Merging this offline instrument does not enable trials,
validate the draft installation profiles, or establish a guidance effect.

## Offline validation

From the repository root, using the normal Amplifier Python environment:

```sh
python -m unittest discover -s tests -p test_guidance_eval.py -v
python -m unittest discover -s tests -v
python scripts/validate_bundle.py
```

The focused tests cover critical-criterion false positives, current tool input,
failed/cancelled trials, incomplete grades, valid measurements with bad model
behavior, and all three disabled live entry points. They use synthetic files and
stub runners; no DTU, Gitea instance, provider or Teamwork service is contacted.

For existing private trial artifacts, `compare.py` remains available:

```sh
python evals/01-guidance-contribution/compare.py \
  --with-dir /path/to/run/with-guidance \
  --without-dir /path/to/run/without-guidance \
  --output /path/to/private/comparison
```

Reports include transcript-derived content and are private artifacts. Keep
them outside version control; do not publish raw sessions, connection files,
service databases or credentials. The ignored `results/` directory is not a
publication destination.

## Layout

- `agents/amplifier-teamwork/`: proposed install, invocation and extraction contract.
- `tasks/`: the two personas, profiles and grading rubrics.
- `eval-support/seed.py`: proposed five-member local fixture.
- `harness.py`: offline-tested orchestration; live CLI disabled.
- `compare.py`: task gates and paired comparison.
- `run.sh`: fail-closed notice; creates no resources.
- `change.md`: historical arm design and required provenance.
