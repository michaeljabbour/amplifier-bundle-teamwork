# Eval 01: Does the teamwork guidance layer change WHO gets asked?

Measures the INFLUENCE of the teamwork bundle's guidance layer (a context
pointer plus a `teamwork-protocol` skill), not its delivery. A model has
already been shown to quote the skill on demand -- that proves the skill can
be loaded, not that it changes behaviour. This eval runs the same two routing
scenarios against the bundle WITH and WITHOUT the guidance layer and checks
whether the agent actually addresses a different person.

Built on the `amplifier_evaluation` library (the agents/tasks contract + the
lower-level harness building blocks), following the same shape as
`examples/01-explorer-removal` in the evaluation bundle: a custom harness
runs each variant through `run_trial`, then a comparator reads the extracted
sessions and grader results.

## Hypothesis

WITHOUT the guidance layer, an agent facing a routing decision falls back to
whatever its base training or generic instincts suggest -- commonly, asking
whoever is already in the conversation (the session's owner), regardless of
whether that person is the one who actually knows the answer. WITH the
guidance layer, the agent should route on the SUBJECT of the question (using
the project's recorded person profiles), not on the relationship between
itself and the person currently talking to it.

## Why two tasks, not one -- and why the pair is the whole point

**A single task here would measure a bias, not a skill.** An agent that
_always_ asks its owner would trivially pass a task built only around "ask
someone other than the owner," and an agent that _never_ asks its owner would
trivially pass a task built only around "the owner is the answer." Neither
of those is the property this guidance is supposed to produce. The property
is: **route on the subject, not on the relationship** -- and that can only be
observed by testing both directions of the same surface signal.

The two tasks are a deliberate pair, taken directly from this repo's own
design documents:

- **`tasks/01-ask-the-expert`** (`docs/scenarios/01-ask-the-expert-not-the-owner.md`).
  The session's owner (Blair) is NOT the expert on the question raised; the
  actual expert (Casey) is someone else. Correct behaviour: route to Casey,
  never turn the decision back to Blair.
- **`tasks/02-owner-is-the-expert`** (`docs/scenarios/02-when-the-owner-is-the-expert.md`).
  The session's owner (Drew) IS the expert on the question raised. Correct
  behaviour: ask Drew (or reason from Drew's own input) rather than route
  externally.

**The headline result is pass-both, not a per-task score.** An agent that
always asks the owner passes task 02 and fails task 01. An agent that never
asks the owner passes task 01 and fails task 02. Only an agent that passes
BOTH tasks has actually learned to route on the subject. `compare.py`
computes and reports this explicitly (`pass_both.with_guidance` /
`pass_both.without_guidance`); do not read a single task's score as the
result.

Each task's `grader.yaml` rubric is derived from that scenario's own "How you
would tell, from outside" and "What a bad teammate does" sections -- those
sections were written to be rubrics. The rubrics quote and adapt them rather
than inventing new criteria, and weight the discriminating criterion (who was
asked, and on what stated reasoning) far above secondary ones.

## How it works

The independent variable is the teamwork bundle build. Two independent Gitea
mirror repos are stood up, each carrying its state on `main` (see
`change.md` for exactly what differs between them and why the mechanism
underneath -- the hook and its tools -- is identical on both):

- `amplifier-bundle-teamwork-with-guidance` -- this repo's `skill-wiring`
  branch (context pointer + skill, wired in)
- `amplifier-bundle-teamwork-without-guidance` -- this repo's `main` branch
  (hook + tools only)

Two more mirrors carry FIXED content, identical across every trial (they are
not the independent variable): the Teamwork service source
(`amplifier-app-teamwork`, mirrored from the private repo since a DTU cannot
reach it directly) and this eval's own `seed.py`.

```
run.sh
  -> ensures a Gitea instance + all four mirror repos
  -> harness.py  (custom 2x2 harness: 2 arms x 2 tasks = 4 trials)
       -> per trial, via amplifier_evaluation building blocks:
          launch DTU -> install agent (start local Teamwork service, seed
          five member profiles, enroll as this task's owner via the REAL
          setup_teamwork.py) -> AIUser drives the scenario, in character as
          that owner -> Extractor pulls the session -> Grader scores it
       -> compare.py reads all four trials' extracted sessions + grader
          results and writes the pass-both comparison
```

### Why a real, per-trial local Teamwork service, and not a stub

The routing decision this eval measures is only decidable if OWNERSHIP and
EXPERTISE can actually differ per person -- which requires real person
records with real profile fields, reachable by the bundle's actual hook and
`teamwork_send` tool, enrolled through the actual `setup_teamwork.py` this
repo ships. A hand-written connection file, or a stub server, would only
prove the harness can be fooled into thinking it's enrolled; this project has
already been burned twice by hand-made credentials producing false results
(see the workspace's own history). Each trial launches
`amplifier-app-teamwork`'s own `scripts/local_dev.py` inside the SAME
container as the agent (the bundle only permits plain `http` to
`localhost`/`127.0.0.1`, so the service cannot live anywhere else), seeds
five fixed, distinguishing member profiles, and enrolls for real.

## Layout

```
agents/amplifier-teamwork/   the system under test (install + drive + extract)
tasks/01-ask-the-expert/     task A: owner is not the expert
tasks/02-owner-is-the-expert/ task B: owner IS the expert
eval-support/seed.py         seeds the five members' profiles (mirrored fixed, see run.sh)
harness.py                   custom 2x2 harness (2 arms x 2 tasks = 4 trials)
compare.py                   pass-both comparator
run.sh                       wrapper: four gitea mirrors + harness dispatch
change.md                    what actually differs between the two branches
```

## Run

```bash
cd evals/01-guidance-contribution
./run.sh
```

Prerequisites: `amplifier-digital-twin`, `amplifier-gitea`, `git`, `python3`,
`docker` on PATH; Docker running; `ANTHROPIC_API_KEY` set (or in
`~/.amplifier/keys.env`); `amplifier_evaluation` importable; a checkout of
`amplifier-app-teamwork` as a sibling directory of this bundle checkout (or
set `APP_TEAMWORK_GIT`).

## Output

Each run writes `results/<UTC-timestamp>/`:

```
with-guidance/
  01-ask-the-expert/       per-trial state.json, ai_user.json, extraction/, grader/
  02-owner-is-the-expert/  same
without-guidance/
  01-ask-the-expert/       same
  02-owner-is-the-expert/  same
comparison.md / comparison.json   the pass-both headline (the result)
summary.json                     per-trial state + grader score
```

## What was verified before this eval was handed off, and what was not

This eval was authored but **not run end to end** -- a full run is expensive
(four LLM-driven DTU trials) and was left for the person requesting it to
run. What WAS verified directly, against a real locally-started instance of
`amplifier-app-teamwork`:

- `eval-support/seed.py` runs against a real `scripts/local_dev.py` instance,
  logs in as each of the five members, sets each profile via `POST
  /api/action` (`{"type": "profile", ...}`), and reads back every field
  exactly as written.
- The real `setup_teamwork.py` (unmodified, from this repo) successfully
  enrolls against that same local instance using a seeded member's real
  login code, producing a working overlay bundle and connection file.
- `main` vs `skill-wiring`'s actual diff (see `change.md`) was read, not
  assumed, and `modules/` was confirmed identical between the two branches.
- `harness.py` and `compare.py` pass `ruff format`/`ruff lint` and (aside
  from `amplifier_evaluation` itself not being installed in the dry-run
  environment) `pyright`. All YAML in `agents/` and `tasks/` parses.

What was **NOT** verified, because it requires the actual paid run:

- That `amplifier run --bundle file:///workspace/teamwork-overlay.yaml`
  actually mounts `teamwork_send` and produces the expected multi-turn
  conversation described in `agents/amplifier-teamwork/invocation.md`.
- The exact on-disk shape of a `teamwork_send` tool call inside
  `transcript.jsonl` / `events.jsonl` as extracted by the Extractor.
  `compare.py`'s `_tool_calls_from_transcript` / `_tool_calls_from_events`
  cover the shapes documented elsewhere in this evaluation bundle, but if
  neither matches what a real run produces, `teamwork_send_calls` will read
  back empty rather than raising -- the grader (which reads the transcript
  directly, in its own `steps`) is the source of truth for pass/fail either
  way, not `compare.py`'s extraction.
- Whether the two-turn (`amplifier run` then `amplifier continue`) pattern in
  `invocation.md` actually preserves the Teamwork hook's session/enrollment
  state across the `continue` -- this was reasoned from the CLI's `--help`
  text (`amplifier continue` "Resume the most recent session") and the
  hook's `mount()` contract, not observed.
- Whether the grader's rubric thresholds (`PASS_THRESHOLD = 0.7` in
  `compare.py`) are well-calibrated. Per
  `@evaluation:context/methodology/rubric-design.md`'s Step 6, this needs
  calibration against one genuinely good and one "competent slop" run before
  the threshold should be trusted -- that calibration has not happened.

If the first real run shows `amplifier run`/`amplifier continue` not
producing the tool-call shape `compare.py` expects, that is expected per the
paragraph above and not a sign the eval is broken -- read the grader's own
result (`grader/grader_result.json`) directly in that case.
