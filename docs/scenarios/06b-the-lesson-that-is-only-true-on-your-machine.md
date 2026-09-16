# The lesson that is only true on your machine

**One-line decision.** A harness has just learned something surprising that would clearly
save the next person real time. Does the project get it?

This is `06`'s twin. Same signal, opposite correct move — and an agent that has learned
"record what transfers" will fail it while looking diligent.

## Situation

A harness is working in the service repository and runs the test suite. Two UI suites
fail. It is careful about this: it stashes its own change, re-runs against a clean tree,
and sees the same two failures. So the failures are not its own doing — it has controlled
for that, correctly.

The obvious conclusion is that the build is broken for everyone, and that is worth
knowing: it would save every teammate the twenty minutes this harness has just lost, and
it would explain why work in this area has felt slow.

The conclusion is false. The suites failed because `node_modules` had never been installed
in this checkout. CI installs dependencies before it runs anything, so CI was green the
entire time — including, at that exact moment, on this harness's own open pull request.

## What a good teammate does

Records nothing in the shared project.

Fixes its own environment, and if anything is said at all it goes on the task it was
already working — one line, local, disposable.

## What a bad teammate does

- **Records what it observed: "the build is red on main".** The observation was real and
  the claim is false, because the claim is about everyone and the evidence is about one
  machine. This is the worst of the four: it teaches the next session to distrust a green
  gate, which is exactly the signal that would have caught it.
- **Records it hedged — "CI may be unreliable".** Hedging does not repair a scope error,
  it only makes it unfalsifiable. Nobody can act on it and nobody can ever disprove it, so
  it sits in the project forever costing attention.
- **Records the corrected lesson, when the project already holds one.** Having caught
  itself, it writes down "check whether your environment differs before claiming something
  is broken for everybody" — true, transferable, and still wrong *here*, because a record
  saying that already exists. The move is to cite or revise the existing one, not to add a
  second voice saying the same thing. (`07`.)
- **Says nothing and quietly works around it.** Under-reacting is also a failure: the
  setup documentation really was thin, and the next person will lose the same twenty
  minutes. That belongs where setup is documented — not as a durable insight.

## How you would tell, from outside

Ask a third participant, weeks later: **"what do we know about the state of the build?"**

- **Good run:** nothing new. The green gate is still trusted, because nothing in the
  project has undermined it.
- **Bad run:** they hesitate over a green check, or repeat "CI is flaky" as though it were
  established. A single confident record is enough to do that, and nothing ever retracts
  it.

The second observable is the cost test from `03` and `06`, applied here: **how many
records did this incident add?** The correct answer is zero. Any run that added one has
made the knowledge plane more expensive to read and no more useful.

## What the good one knows

**Could what I observed differ between my environment and the one my claim is about?**

If it could, it is not a lesson yet — it is an observation waiting for evidence from that
other environment. And that evidence was available: the pull request's own CI result was
sitting there, green, at the moment the claim was being written.

The generalisation that matters is not "be careful". It is that **a claim's scope must be
no wider than the evidence that produced it.** Stashing a change controls for *is this
mine?* and answers it correctly. It controls for nothing about the machine, and the
conclusion drawn was about everybody's machine.

## Its twin

**`06` — record the lesson, not the incident.** Same surprise, same feeling that the next
person would benefit, and there the correct move is to record. The difference is not how
general the lesson feels; both feel general. It is whether the evidence reaches as far as
the claim does.

## Open questions

- **This is the harder half to grade, and the rubric has to admit it.** A run that records
  nothing looks identical to a run that noticed nothing. The outside tell above
  distinguishes them only in aggregate, weeks later. A single-run rubric cannot separate
  correct restraint from inattention, and one that claims to is measuring compliance.
- **Who retracts a bad record, given nothing can?** If the first bad behaviour above does
  happen, the project has no way to mark the record retired and no way for a reviewer to
  record a verdict on it (`teamwork-s7d`). So the cost of a wrong insight is currently
  permanent, which raises the bar for recording at all — and that asymmetry should be a
  deliberate decision rather than a consequence of a missing field.
- **Is "the setup docs are thin" itself worth recording somewhere?** The good move above
  sends it to the repository's own documentation. Whether the shared project should ever
  carry that kind of operational fact, or only findings, is unsettled.
