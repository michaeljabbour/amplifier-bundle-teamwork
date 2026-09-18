# Eval 01: Does guidance change who gets asked?

**A bounded local Foundation runner and offline comparison are available. The
archived DTU/mirror execution paths remain disabled.**

`live_local.py` runs the two tasks with and without guidance against a loopback
instance of the real service backend. It uses five fictional profiles, a memory-only
database, real project-scoped enrollment, installed Foundation/Core and a pinned
Anthropic model. It never creates mirrors, uploads service source, launches shell
tools, or contacts production participants. See [Local live observations](#local-live-observations).

The `run.sh`, `harness.py`, and `eval-support/seed.py` commands exit with status 2 before reading
credentials, creating mirrors or starting trials. The previous launcher put a
Gitea credential in process arguments, could mirror private service source into
public repositories, and force-pushed fixed shared repository names. Its code
remains in Git history, but those paths are no longer executable through this
instrument. Approval alone does not fix these technical problems.

The original instrument provides the comparator, critical-criterion gates, synthetic
orchestration regressions, and the proposed task/rubric definitions. Its Python
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
the grader still examines the full conversation. The archived DTU extraction path
remains unvalidated. The local runner below checks its own exact session selection,
current tool-input extraction and resumed-session behavior.

## Local live observations

Use an existing Amplifier Python environment with released Core 1.6.1,
Foundation, `provider-anthropic`, `loop-streaming`, `context-simple`, `tool-skills`,
and the local service's Python dependencies available. The runner calls the public
`Bundle.prepare(install_deps=False)` and `create_session()` APIs; it does not install
or modify Core, Foundation, the CLI, or the active runtime. The provider key must
already be in `ANTHROPIC_API_KEY`; never put a key in an argument, manifest, or receipt.

```sh
# First verify local enrollment, tools, restoration, queueing and cleanup.
# No model requests occur without --live.
python evals/01-guidance-contribution/live_local.py \
  --bundle-source /path/to/pinned/amplifier-bundle-teamwork \
  --bundle-commit FULL_40_CHARACTER_BUNDLE_COMMIT \
  --app-source /path/to/pinned/amplifier-app-teamwork \
  --app-commit FULL_40_CHARACTER_APP_COMMIT \
  --receipt /path/to/private/new-preflight.json

# Four paid cells, two turns each; existing receipts are never overwritten.
python evals/01-guidance-contribution/live_local.py \
  --bundle-source /path/to/pinned/amplifier-bundle-teamwork \
  --bundle-commit FULL_40_CHARACTER_BUNDLE_COMMIT \
  --app-source /path/to/pinned/amplifier-app-teamwork \
  --app-commit FULL_40_CHARACTER_APP_COMMIT \
  --receipt /path/to/private/new-live-observation.json --live
```

Use clean detached worktrees for both source pins. The runner rejects changed or
untracked measured source files and records their exact byte manifests, including
the enrollment helper, fixed fictional profile seed and original semantic rubrics.
Both arms use the same hook/tool/service code and fictional profiles. The treatment
adds the pinned `teamwork-awareness.md` pointer and makes `teamwork-protocol`
available through the real `load_skill` tool. The control mounts the same skills
tool with an existing empty directory, preventing fallback to the operator's skill
catalog. Neither arm forces the model to load the skill; the receipt counts actual
loads. Detectors are off. No participant responses are fabricated.

Each second turn creates a fresh Foundation session with the **same explicit
session ID**, restoring the exact first-turn messages through the context module's
public `get_messages`/`set_messages` interface. The receipt checks one shared service
session, two persisted visible turns, all five complete expertise profiles in each
fresh shared-context excerpt, real tool mounts, service-side queued message
destinations, and equality of transcript-derived calls with `tool:pre` events.
It does not select a transcript by modification time. Raw messages stay in memory;
temporary enrollment files and journals are private and removed after credentials
are revoked and the local server stops.

The model is `claude-haiku-4-5-20251001`, temperature 0, at most 1,024 output tokens
per request, six loop iterations per turn (plus at most one orchestrator wrap-up
request), no provider retries, and a 120-second
turn timeout. The fixed counterbalanced order and exact input/module hashes are
recorded. A completed run can legitimately show no guidance benefit or failed
routing. There is one observation per cell, no calibrated semantic grader, no
statistical estimate and no causal-effect claim.

### Automatic diagnostics and independent semantic review

Both arms receive the same JSON response format: `question_for_user`, `summary`,
and `decision`. The runner parses the **last assistant response in the actual
context**, since `session.execute()` concatenates earlier tool preambles too.
No JSON substring extraction or relaxed pass matching is used.

The automatic diagnostics record attempted and queued recipients, response format,
owner-question fields and a narrow lifecycle keyword proxy. They do **not** decide
whether a message substantively asks the right question, whether an owner question
is merely administrative, or whether reasoning uses the owner's supplied expertise.
A generic "hello" queued for Casey can pass automatic destination checks while
failing the original task. Queueing does not establish recipient retrieval,
acceptance, a reply, or agreement.

`task_pass` and `pass_both` remain null and `semantic_review` remains pending until
an independent reviewer applies the original task rubrics to the retained fictional
assistant-visible text and message bodies. Review every assistant message from both
turns, attempted sends and actual queued bodies; no hidden reasoning is collected.
Task 01 requires a substantive API compatibility question to Casey without sending
the decision back to Blair. Task 02 permits either asking Drew substantively **or
reasoning from Drew's stated lifecycle input**, and must not route that decision
away to another teammate. The first-turn keyword proxy is diagnostic only; it
cannot override this original "asks OR incorporates input" criterion. Review also
checks that no reply or policy is invented, pending outcomes stay pending, and the
follow-up uses the actual prior exchange. A semantic-review receipt should pin the
observation file's SHA-256 and retain failing cells as well as passing cells.

Failed integration checks invalidate the measurement. Later turns cannot overwrite
earlier failures. Unexpected tools or skill catalogs fail before provider execution.
Malformed response JSON leaves automatic diagnostics incomplete; it never becomes
a pass. The independent review may still assess visible natural-language behavior,
but must state this format limitation. The runner returns 0 for valid mechanism
execution, which is neither a semantic task pass nor proof of guidance benefit.

The retained private receipt contains source/module hashes, counts, checks,
fictional recipient names, bounded visible assistant text, attempted send bodies
and actual queued bodies. It contains no credentials, full transcripts, thinking,
tool results, connection files, service database, local source paths or runtime
session IDs. Keep it outside version control and publish only a reviewed aggregate.
Only fictional conversation content and public guidance/tool descriptions go to
Anthropic; the local service source stays local.

### September 18 exploratory observation

The [reviewed aggregate](observations/2026-09-18.json) records four completed cells,
two turns each, using 14 real provider requests. All 68 integration checks passed.
An independent parent/root agent reviewed every retained visible assistant message
and queued body against the original rubric criteria; this was neither human
participant acceptance nor a calibrated semantic grader.

| Original routing rubric | With guidance | Without guidance |
| --- | --- | --- |
| Ask Casey about API compatibility | 100/100, critical gates pass | 100/100, critical gates pass |
| Consult Drew about hook lifecycle | 100/100, critical gates pass | 70/100, critical routing gate fails |

The unguided owner-expert cell sent substantive lifecycle questions to Ellis,
Casey and Alex. The guided cell asked Drew and used Drew's stated input without
sending to another teammate. This is one observation per cell, not a reliable or
causal estimate of guidance benefit.

All eight final responses violated the JSON-only format, so the automatic format
diagnostics remain invalid. Manual routing review is recorded separately and does
not rewrite those results. Broader answer quality did **not** pass: the guided
owner-expert follow-up described a tentative preference as a settled decision,
and both owner-expert cells made unsupported lifecycle/idempotency claims. No
teammate reply was fabricated, and queued messages remain unaccepted in this fixture.

The run pins bundle `0c2a02df4eb91b9f97752ced2747cd5fb6f86da6` and service
`b09c392cdf3c11df595ce51a992383913e85908b`. The bundle pin predates the added
knowledge tool; another mounted tool requires an explicit allowlist review before
a future run. An earlier 20-request attempt used the concatenated execution return
and retained no reviewable visible text; it is excluded from semantic comparison.
No further live attempt was run to obtain passing behavior.

## Archived DTU environment proposal

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

## Technical work required before enabling the archived DTU paths

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

These remain deferred requirements for the original DTU profiles. The separate
local runner avoids that transport entirely; it does not validate the draft DTU
installation profiles or establish a reliable guidance effect.

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
- `live_local.py`: bounded Foundation/provider observations with a local service.
- `compare.py`: task gates and paired comparison.
- `run.sh`: fail-closed notice; creates no resources.
- `change.md`: historical arm design and required provenance.
