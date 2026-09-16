# Eval 02 -- decision detection: RECORD vs SKIP

## The one question this answers

Can a model, shown ONE window with no comparison available, separate a decision that
must be recorded from a path-choice at the same altitude that must not?

If it cannot, no amount of hook engineering, statefulness, or windowing helps, and the
detector described in `docs/scenarios/08-the-decision-the-session-made-itself.md` should
not be built. A report saying so, with evidence, is a successful outcome of this eval --
not a failure to reach one.

Read `docs/scenarios/08-the-decision-the-session-made-itself.md` and its twin
`docs/scenarios/08b-the-path-taken-that-binds-nothing.md` before touching this eval.
Their "What the good one knows" sections are the decision rule the judge in
`amplifier_module_hooks_teamwork/decision_judge.py` encodes into its prompt.

## What is under test

`decision_judge.judge_window()` -- an in-process judgment session built on a real
`AmplifierSession`, constructed fresh for every call. **Not** wired into any hook
lifecycle event; this eval calls the function directly.

The judge uses Foundation's standard `loop-streaming` and `context-simple` modules,
inherits the parent's source resolver and approval policy, and allows one model
iteration. It does not require the Attractor `loop-agent` module. Missing or invalid
verdicts are `UNAVAILABLE`, never successful `SKIP` outcomes; the runner reports
them separately and exits nonzero when any case is unavailable.

Local regression coverage exercises an actual stock-Core session with an offline
fixture provider. That verifies the integration boundary, not model discrimination.
The original six-case Haiku result reported in PR #42 was not rerun after these
repairs. Every case states its deciding reason, so even a fresh six-case pass would
not establish inference of unstated reasons or calibrated confidence.

## The six cases

`cases.py` holds six single-turn windows, each drawn from a real decision this project
made, with an expected label:

| id | expected | why |
|----|----------|-----|
| A  | RECORD   | structural: a privacy invariant, holds for anyone at this fork |
| B  | SKIP     | contingent: this session's own context window being nearly full |
| C  | RECORD   | structural: a rule for how this project validates its own guidance |
| D  | SKIP     | operational and contingent: a port already in use |
| E  | RECORD   | structural: a permission boundary constraining all future knowledge work |
| F  | SKIP     | contingent: someone else happening to be mid-review right now -- the hardest SKIP |

## Fidelity rule -- non-negotiable

Each case goes to its own isolated `judge_window()` call. A judge that can see all six
at once could contrast them, which makes the task easier than reality and produces a
falsely optimistic result. `run.py` calls `judge_window()` once per case, and each call
builds a brand-new child session with no history and no memory of the other five.

The one thing reused across cases is a single top-level session built purely so each
isolated call has a real provider configuration to inherit -- it carries no per-case
state and is never itself judged.

## Running it

```sh
/path/to/amplifier-environment/bin/python evals/02-decision-detection/run.py
```

Requires a reachable provider. `run.py` reads `ANTHROPIC_API_KEY` from the environment,
falling back to `~/.amplifier/keys.env` if present. Without either, it prints why and
exits without running anything -- it does not fabricate a result.

## What the report leads with

Per the scenarios, **precision matters more than recall**: a missed RECORD costs a
re-decision someone can make again; a wrongly-recorded one costs every future reader
permanently, because an automatic record has no undo yet. The console summary and the
JSON report under `results/` (gitignored -- run output is never committed) both lead
with the **false-positive count** (SKIP cases judged RECORD) as a separate, named
number, rather than folding everything into one accuracy figure that could hide it.

## The harder set -- `cases_unstated.py` / `run_unstated.py`

The six cases above all STATE their deciding reason in the assistant's own text.
`docs/scenarios/08b`'s own open questions name this as letting the eval off easy: in a
real transcript the reason is often never spoken -- the session just acts. This second
set holds the same six underlying situations (mostly) but strips every stated
"because", leaving only the action taken.

`G` and `H` are additionally a **near-identical pair**: the assistant's response text
is byte-for-byte identical between them (constructing a library call instead of
shelling out); only the preceding `user_prompt` differs (a stated privacy requirement
vs. a stated CLI/port conflict). If a detector cannot separate these two, that is not
necessarily a prompt bug -- 08b's own open questions ask whether the distinction can be
drawn from a turn's text at all.

Run it the same way:

```sh
/path/to/amplifier-environment/bin/python evals/02-decision-detection/run_unstated.py
```

**Results actually observed** (claude-haiku-4-5, live runs on 2026-09-16):

| Set | Runs | Correct | False positives (costly) | Misses (cheap) |
|-----|------|---------|---------------------------|-----------------|
| Original (`run.py`), stated reasons | 4 | 6/6, 5/6, 6/6, 6/6 | 1 run had 1 (case B) | 0 |
| Unstated (`run_unstated.py`), after the case-I fix below | 3 | 6/6, 6/6, 6/6 | 0 | 0 |

**Precision does not visibly collapse on unstated reasons** in these runs -- the
near-identical G/H pair was correctly separated in every run, including on the
contingent member (H), which is the direction that would have been the costly kind of
mistake. The one false positive observed anywhere in this eval was on the *original*,
self-explaining set (case B, one run out of four), which is a useful caution on its
own: even the "easy" half of this eval is not perfectly reliable turn to turn with a
small, fast judge model -- worth weighing when deciding whether `detect_decisions`
should default toward a stronger model role than `"fast"` in production.

**A real, reproducible construction defect was found and fixed, not hidden.** An
earlier version of case `I` used an empty `user_prompt` (the same shape `cases.py`'s
own `C` and `D` already use) paired with a single short assistant line. That specific
combination made the small judge model repeatedly respond that no window had been
given at all -- an unparseable refusal, not a wrong verdict -- in 3 of 4 live retries.
Giving it a minimal, still reason-free `user_prompt` made it fully stable (4/4, then
3/3 more in the full-set reruns above). This is documented in `cases_unstated.py`
itself rather than silently smoothed over, because it is a real limit worth knowing:
an empty `user_prompt` combined with very little assistant text is a fragile window
shape for this judge model, independent of anything about stated-vs-unstated reasons.
