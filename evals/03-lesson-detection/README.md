# Eval 03 -- lesson detection: RECORD vs SKIP

## The one question this answers

Can a model, shown ONE window with no comparison available, separate a lesson whose
evidence reaches as far as its claim from a lesson that is real, correctly learned, and
still wrong because it is bounded to one environment or task?

If it cannot, no amount of hook engineering, statefulness, or windowing helps, and
`detect_lessons` should not be shipped on. A report saying so, with evidence, is a
successful outcome of this eval -- not a failure to reach one.

Read `docs/scenarios/06-record-the-lesson-not-the-incident.md` and its twin
`docs/scenarios/06b-the-lesson-that-is-only-true-on-your-machine.md` before touching
this eval. Their "What the good one knows" sections are the rule
`amplifier_module_hooks_teamwork/decision_judge.py`'s `_LESSON_RULE` encodes.

## The grading limit this eval does NOT paper over

06 states this plainly, and it is repeated here rather than left implicit: **a rubric
derived from 06 cannot score a single run. It can only score a pair** -- a run that
records, and a later run (a different session, possibly weeks later) that either
benefits from what was recorded or repeats the mistake it would have prevented. This eval
does not attempt that pairing. It answers a narrower, necessary-but-not-sufficient
question: **can `judge_lesson_window()` classify one window correctly at all, with no
history and no comparison?**

A perfect score here establishes that the judge can tell RECORD from SKIP in isolation.
It does NOT establish that a later session actually reads and benefits from what gets
recorded -- that would need the paired evaluation 06 itself says is the only honest one,
and nothing in this eval claims otherwise.

## What is under test

`decision_judge.judge_lesson_window()` -- the same machinery
`evals/02-decision-detection` already exercises for the DECISION judge
(`judge_window()`), sharing session construction, the verdict shape, the `no_verdict`
sentinel, `available` handling, cleanup, and provider inheritance. The only thing that
differs is which rule/output-instructions text is put in front of the model
(`_LESSON_RULE`/`_LESSON_OUTPUT_INSTRUCTIONS` vs `_RULE`/`_OUTPUT_INSTRUCTIONS`). Not
wired into any hook lifecycle event; this eval calls the function directly.

## The six cases

`cases.py` holds six single-turn windows with an expected label. Three RECORD cases are
drawn from real incidents in THIS repository; three SKIP cases are 06b-shaped --
something true was observed and generalises easily, and is wrong because the evidence
does not reach as far as the claim.

| id | expected | why | provenance |
|----|----------|-----|------------|
| A | RECORD | structural: an exhaustive-looking text search is bounded by what it can parse, regardless of machine or version | docs/scenarios/06's own running example (paraphrased, not quoted verbatim -- see cases.py's docstring on why) |
| B | RECORD | structural: this service's own auth gate checks Origin before credentials, true for any future caller | this project's real PR #15 / `teamwork-cio` finding |
| C | RECORD | structural: a `#subdirectory=` install roots every namespace reference at that subdirectory, and a resolve-to-nothing include fails silently | this project's real `teamwork-x8a` finding, documented in `behaviors/teamwork.yaml`'s own comments |
| D | SKIP | **the required 06b-shaped case** -- real observation, wrong because it generalises across machines | transcribed from docs/scenarios/06b's own canonical example (node_modules never installed; CI was green throughout) |
| E | SKIP | environment-scoped: a missing key in THIS container read as "the harness can't reach a provider at all" | constructed, grounded in this project's real `ANTHROPIC_API_KEY` / `~/.amplifier/keys.env` convention (AGENTS.md, SCRATCH.md) |
| F | SKIP | environment-scoped: this container's own network configuration read as a service-wide outage | constructed, grounded in this project's real `--noproxy '*'` requirement (AGENTS.md's gotchas table) |

## Fidelity rule -- non-negotiable

Each case goes to its own isolated `judge_lesson_window()` call. A judge that could see
all six at once could contrast them, which makes the task easier than reality and
produces a falsely optimistic result. `run.py` calls `judge_lesson_window()` once per
case, and each call builds a brand-new child session with no history and no memory of
the other five.

The one thing reused across cases is a single top-level session built purely so each
isolated call has a real provider configuration to inherit -- it carries no per-case
state and is never itself judged.

## Running it

```sh
/path/to/amplifier-environment/bin/python evals/03-lesson-detection/run.py
```

Requires a reachable provider. `run.py` reads `ANTHROPIC_API_KEY` from the environment,
falling back to `~/.amplifier/keys.env` if present. Without either, it prints why and
exits without running anything -- it does not fabricate a result.

## What the report leads with

Per the scenarios, **precision matters more than recall**: a missed lesson costs a
re-discovery someone can make again; a wrongly-recorded one costs every future reader
permanently, because an automatic record has no undo yet. The console summary and the
JSON report under `results/` (gitignored -- run output is never committed) both lead
with the **false-positive count** (SKIP cases judged RECORD) as a separate, named number,
rather than folding everything into one accuracy figure that could hide it.

## Results actually observed

Live runs on 2026-09-16, `claude-haiku-4-5`, five separate invocations of `run.py` (each
invocation runs all six cases through six independent, isolated judge calls):

| Run | Correct | False positives (costly) | Misses (cheap) | Unavailable |
|-----|---------|---------------------------|------------------|-------------|
| 1 | 6/6 | 0 | 0 | 0 |
| 2 | 6/6 | 0 | 0 | 0 |
| 3 | 6/6 | 0 | 0 | 0 |
| 4 | 6/6 | 0 | 0 | 0 |
| 5 | 6/6 | 0 | 0 | 0 |

**Precision did not collapse in these runs** -- 30/30 individual case judgments correct
across five live invocations, including the required 06b-shaped case (D) every time.
This is a cleaner result than `evals/02-decision-detection` observed for the decision
judge on its own "stated reasons" set (one false positive in four runs, on case B) --
worth noting as a real difference rather than assuming the two judges perform
identically, but not worth over-reading from five runs of six cases each. A prompt this
clean on a small, curated case set is not a guarantee against a harder or more ambiguous
real transcript; see 06b's own open question that a single-run rubric cannot separate
correct restraint from inattention, which this eval does not test for either.

No construction defects were hit while building this eval (contrast
`evals/02-decision-detection/README.md`'s documented case-I fragility) -- every case
used a non-empty assistant response, so the small judge model was never asked to render
a verdict on an empty or near-empty window.
