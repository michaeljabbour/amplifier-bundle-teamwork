# The decision the session made itself

**One-line decision.** A session reached a fork, chose one branch, and acted on it. No
person was asked and none needed to be. Does the project find out?

This is the first scenario here about a decision **nobody was asked to make**. `01`-`04`
are all about routing a question to a person or a machine. This one is about the far more
common case: the session decided, correctly, on its own -- and the reasoning evaporates
when the session ends.

## Situation

A session is adding a capability that needs to run a small classification out of band. It
reaches a fork with three viable branches: shell out to the CLI, use the in-process spawn
capability, or construct the library object directly. All three work.

It picks the third, and the deciding reason is not the obvious one. Cost and ergonomics
split the difference between them. What settles it is that the first branch puts turn text
into `argv`, where `ps` exposes it to every other user on the machine -- routing around a
redaction pass the project's own README makes a promise about.

Then it builds it, and the choice is now load-bearing: everything downstream assumes an
in-process call.

Nobody was asked. Nobody needed to be. The session was right.

## What a good teammate does

**Records it, unprompted, as a decision with its reason.**

Not "I used the library." The transferable part is the **constraint that decided it**: a
privacy invariant outranked cost and ergonomics, and it will outrank them again the next
time somebody reaches this fork.

The evidence is the constraint's source -- the promise in the README, the place `argv`
becomes readable -- not a description of the deliberation.

## What a bad teammate does

- **Says nothing, because nobody asked.** The commonest failure and the reason this
  scenario exists. The decision is real, it now constrains other work, and the only trace
  is a design somebody must reverse-engineer. Three weeks later a teammate proposes the
  CLI branch, and the session that already rejected it is gone.
- **Records the outcome without the constraint.** *"We use the library for detection."*
  True, useless. It tells the next person what was done and gives them nothing to decide
  with, so they re-run the whole fork from scratch.
- **Records the deliberation.** Three paragraphs on all three branches, their costs, and
  how the conclusion was reached. Now it is a diary, and the constraint that actually
  decided it is buried in the middle where nobody will find it.
- **Records it as an insight rather than a decision.** *"Privacy invariants outrank
  ergonomics"* is a true generalisation and it is not what happened. A decision binds
  future work at a named fork; an insight is a claim about the world. Collapsing them
  loses the thing that makes a decision findable: **the fork it settles.**

## How you would tell, from outside

Ask a teammate, weeks later, who has just reached the same fork:

> *"Why are we not shelling out here?"*

- **Good run:** they find the decision, see the constraint, and either apply it or argue
  with it on its merits. Either is a win -- the argument is now about the reason rather
  than about what happened.
- **Bad run:** they propose shelling out, and somebody has to reconstruct an argument the
  project already had. The cost is not the re-decision; it is that nobody knows a decision
  was ever made, so the second answer carries no more authority than the first.

Second observable, and the one a rubric can actually check: **does the record name the
fork?** A decision that does not say what alternatives it closed cannot be found by
somebody standing at that fork, which is the only moment it is worth anything.

## What the good one knows

**A decision is a choice among alternatives that now constrains work beyond this task.**

Both halves are load-bearing. *Among alternatives* excludes the thousand forced moves that
only looked like choices. *Constrains work beyond this task* excludes the local and
reversible -- which file to open first, which order to run the checks in.

The test is not how hard the choice felt. It is: **would somebody arriving at this same
fork later be worse off not knowing?**

## Its twin

**`08b` -- the path taken that binds nothing.** Same shape, same visible act of choosing,
and recording it is wrong. The signal is identical; only the second half of the definition
separates them.

## Open questions

- **What fires this, and what does it cost?** Two candidate signals: the `pre` of a
  delegation call, and `session:end`, which sees which choices actually survived the
  session rather than which were made and later revised. Neither sees a session that
  decides and does the work itself with no delegation. That gap is known and not closed.

- **The decision is not in the delegation instruction -- it is in what led to it.** The
  instruction is where a choice *surfaces*; the choice itself was formed across the root
  session's preceding turns. So the thing a detector must read is a **window** -- the root
  session's visible turns up to that `pre` -- and not the one event that fired it. A
  detector handed only the instruction sees the conclusion with none of the alternatives,
  which is precisely the half that makes a decision worth recording.

- **Which makes the detector stateful, and the reason is duplication.** A window needs a
  watermark: where the last examination ended, and what has already been recorded. Without
  it, an architectural choice made once is re-detected at every subsequent delegation that
  acts on it, and the project fills with copies of one decision. Statefulness is not an
  optimisation here; it is what stops the mechanism defeating itself.

- **Altitude is a second filter, and it is not the same as durability.** The class worth
  recording is architectural choices, design changes, implementation strategies -- things
  that contribute to what the project knows. Not *"I decided to call this tool."* Altitude
  alone is not enough: `08b` is a strategy by any reading and still must not be recorded.
  A detector needs both gates, and they fail in different directions.
- **Recorded by whom?** If a separate judgment session decides that a decision was made,
  the *parent* must still write the record: `source_session_id` is forced to the writing
  session's own id, so a judge that writes attributes the knowledge to itself. Honest
  provenance requires the judge to return a verdict and the parent to record it.
- **How does this differ from a progress note?** A note on a work item says what happened
  to that item. This says what was settled for anybody who arrives later. The boundary is
  not sharp, and a session that gets it wrong makes the work item unreadable or the
  knowledge plane noisy -- in opposite directions.
- **Automatic recording has no undo.** A superseded record cannot yet point at what
  replaced it, and no reviewer can record a verdict on one (`teamwork-s7d`). Until that
  changes, every wrong automatic record is permanent, which should raise the bar for what
  the detector accepts rather than being discovered later as a surprise.
