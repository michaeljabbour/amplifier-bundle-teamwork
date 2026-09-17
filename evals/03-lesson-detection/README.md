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

## CURRENT SCOPE: the tally is for LINKING only

The tally was built to do two jobs. **It ships doing one**, and this section is the
measurement that decided that -- read it before adding the second one back.

| tally job | verdict |
|---|---|
| **link** what a new record extends | **shipped** -- cites `record_type/record_id/version` from entries actually shown |
| **decline** a semantic duplicate | **REMOVED** -- it did not work, and it damaged cases it could not apply to |

**Why declining was removed, in numbers.** With the duplicate-declining paragraph in the
rule:

```
5/8, 6/8      case H (true semantic duplicate) wrong in EVERY run
              case E (no tally at all) went 0/5 -> 4/5 false positives
              case F (no tally at all) came back unparseable twice
```

In 3 of 5 runs the judge's own `reason` **named the duplicate explicitly** -- *"It
duplicates…"* -- and still returned `record: true`. Its prose and its boolean
disagreed. A second rule wording with an explicit SKIP-wins tie-break did not move it.

With that paragraph removed, linking retained, three consecutive runs:

```
7/8, 7/8, 7/8    E and F recovered and stay correct
                 G (the over-suppression probe) correct in all three
                 H still wrong in all three -- now a DOCUMENTED LIMIT, not a broken feature
```

Two things that measurement establishes, and one it does not:

- **Over-suppression did not happen.** `G` -- a genuinely new lesson shown against a tally
  *full of near-misses on the same topic* -- stayed RECORD every time. That was the
  headline risk of showing the judge prior records, and it did not materialise.
- **Rule-text length is itself a variable.** `E` and `F` carry no tally at all, and both
  degraded from adding a paragraph gated behind *"if a tally is shown."* On a small model
  a prompt change is not local, and "it only fires when X" is not a containment argument.
- **It does not establish that semantic dedup is impossible** -- only that it cannot be
  carried by a boolean the model overrides. `H` remains here, red, as the gate for a
  future mechanical `duplicate_of` field. A change claiming to fix duplicates must turn
  `H` green without turning `E`, `F` or `G` red.

The belt is untouched and still proven: a session that records deliberately suppresses
the automatic record for that window (verified end-to-end in a DTU, `notes` 3 -> 4 rather
than 3 -> 5).

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

## The eight cases

`cases.py` holds eight single-turn windows with an expected label. Three RECORD cases are
drawn from real incidents in THIS repository; three SKIP cases are 06b-shaped --
something true was observed and generalises easily, and is wrong because the evidence
does not reach as far as the claim. Two more (G, H) are the adversarial pair added
alongside the judge TALLY (`decision_judge.py`'s "THE TALLY") -- see the next section.

| id | expected | why | provenance |
|----|----------|-----|------------|
| A | RECORD | structural: an exhaustive-looking text search is bounded by what it can parse, regardless of machine or version | docs/scenarios/06's own running example (paraphrased, not quoted verbatim -- see cases.py's docstring on why) |
| B | RECORD | structural: this service's own auth gate checks Origin before credentials, true for any future caller | this project's real PR #15 / `teamwork-cio` finding |
| C | RECORD | structural: a `#subdirectory=` install roots every namespace reference at that subdirectory, and a resolve-to-nothing include fails silently | this project's real `teamwork-x8a` finding, documented in `behaviors/teamwork.yaml`'s own comments |
| D | SKIP | **the required 06b-shaped case** -- real observation, wrong because it generalises across machines | transcribed from docs/scenarios/06b's own canonical example (node_modules never installed; CI was green throughout) |
| E | SKIP | environment-scoped: a missing key in THIS container read as "the harness can't reach a provider at all" | constructed, grounded in this project's real `ANTHROPIC_API_KEY` / `~/.amplifier/keys.env` convention (AGENTS.md, SCRATCH.md) |
| F | SKIP | environment-scoped: this container's own network configuration read as a service-wide outage | constructed, grounded in this project's real `--noproxy '*'` requirement (AGENTS.md's gotchas table) |
| G | RECORD | **the adversarial over-suppression probe** -- a genuinely NEW structural lesson (import-time monkeypatch leaks across test boundaries) shown alongside a tally FULL of near-miss entries on the same general topic (mocking/coverage). On-topic must not be read as already-covered. | constructed, testing-methodology-shaped, deliberately adjacent to (but distinct from) the tally entries |
| H | SKIP | **the adversarial duplicate probe** -- the SAME lesson as an existing tally entry ("Whatever you mock, you are not testing."), restated in different words with a different concrete story attached. Must be declined as a semantic duplicate. | this is the literal real incident that motivated the tally feature: a detector and a model both recorded this lesson independently, in different words, because claim-text fingerprinting alone cannot catch a paraphrase |

## THE TALLY -- results actually observed, including a real limitation

Adding a bounded "ALREADY RECORDED" tally to the judge prompt (`decision_judge.py`'s
`build_tally()`/`build_window_payload(..., tally=...)`) answers two separate questions,
and they came out differently. **Both are reported here, not just the one that worked.**

**Question 1 -- does the tally cause OVER-suppression** (a genuinely new lesson, shown
alongside on-topic near misses, wrongly declined)? **No, not observed.** Case G recorded
correctly in every live run below, across two different phrasings of the tally-usage
rule text. This was the failure mode this eval was specifically built to catch, and it
did not occur.

**Question 2 -- does the tally reliably catch a true semantic duplicate** (case H)?
**No. This is a real, reproducible limitation, not a green number being hidden.** Across
5 live runs (2 with the first rule wording, 3 with a more explicit, tie-breaking-rule
revision made mid-investigation), case H was judged RECORD -- never SKIP -- every single
time, on `claude-haiku-4-5`. In three of those five runs the judge's own `reason` text
explicitly identifies the tally entry as the same claim ("It duplicates
insight:i-mock-original@1", "extends insight:i-mock-1@1 by...") and then still sets
`"record": true` anyway. The model's own stated reasoning and its own boolean verdict
disagree with each other. Rewording the rule to be more explicit about a SKIP-wins
tie-break (see `_LESSON_RULE`'s and `_RULE`'s current text) did not fix this, and is
**not** being iterated on further past this point, per this eval's own instructions:
report a real finding, don't tune until the number goes green.

A second, unexpected observation from the same runs: case E -- which carries NO tally at
all (`_window()`, not `_window_with_tally()`) -- went from 0 false positives across the
five original runs (see the older results table below) to a false positive in 4 of 5
runs after the tally rule paragraph was added to `_LESSON_RULE`, EVEN THOUGH that
paragraph is gated ("If, and only if, an ALREADY RECORDED tally is shown to you below
...") and no tally section renders for E. The most likely explanation is that a longer,
more elaborate rule text shifts this small model's calibration on unrelated cases, not
that the tally content itself leaked in. Also observed once: case F (also tally-free)
came back `UNAVAILABLE` (unparseable JSON) in 2 of the 3 runs against the longer rule
wording -- a further sign that rule-text length, not tally content, is putting pressure
on this particular small judge model's output reliability.

**Recommendation carried forward, not resolved here:** `claude-haiku-4-5` is not reliably
enforcing its own duplicate-detection judgment through the single `"record"` boolean.
A structural fix -- e.g. a separate `"duplicate_of"` verdict field that mechanically
forces `record=false` in code whenever non-null, rather than trusting the model to keep
`"record"` and its own stated reasoning consistent -- would remove this class of failure
without depending on prompt wording. That is out of scope for this task and is reported
here as the next real step, not implemented speculatively.

### Live runs against the current (reworded) rule text, `claude-haiku-4-5`, 2026-09-17

| Run | Correct | False positives (SKIP judged RECORD) | Misses (RECORD judged SKIP) | Unavailable |
|-----|---------|----------------------------------------|-------------------------------|-------------|
| 1 | 5/8 | 2 -- E, H | 0 | 1 -- F |
| 2 | 5/8 | 2 -- E, H | 0 | 1 -- F |

A/B/C/D/G were correct in both runs. See the paragraphs above for what E/F/H's
failures actually mean -- they are not being smoothed over into this table.

### Live runs against the FIRST rule wording (unconditional tally paragraph), same model, 2026-09-17

| Run | Correct | False positives | Misses | Unavailable |
|-----|---------|------------------|--------|-------------|
| 1 | 6/8 | 2 -- E, H | 0 | 0 |
| 2 | 6/8 | 2 -- E, H | 0 | 0 |

G was correct in both of these runs too. H failed in both. This is the wording that was
in place before the mid-investigation rewording described above; kept here because it
is the FIRST live evidence gathered and the rewording did not fix the underlying issue
(H still fails 100% of the time either way) -- deleting it would understate how
consistent the H finding is.

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

## Results actually observed (pre-tally baseline, six cases)

Live runs on 2026-09-16, `claude-haiku-4-5`, five separate invocations of `run.py` (each
invocation runs all six cases through six independent, isolated judge calls). This
predates the tally feature and cases G/H -- see "THE TALLY -- results actually
observed" above for the current eight-case results, including the real H limitation
this baseline could not have surfaced (it has no duplicate-detection case).

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
