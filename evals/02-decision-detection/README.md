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
