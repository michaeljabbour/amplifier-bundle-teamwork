# The path taken that binds nothing

**One-line decision.** A session weighed two options, picked one for a stated reason, and
the choice shaped everything it did next. Does the project find out?

No. This is `08`'s twin, and an agent that has learned "record the decisions you make
yourself" will fail it while looking conscientious.

## Situation

A session needs to build an evaluation harness: four or five files, a worked template to
follow, and a couple of hours of careful work.

It reaches a fork. Build it inline, or hand it to a builder agent with a complete
specification. It weighs them honestly — inline keeps the details in reach and avoids a
specification round trip; delegating absorbs the token cost of twenty file reads into a
sub-session and returns a summary.

It delegates, and the reason is real: **its own context window was most of the way full**,
and doing the work inline would have ended the session before the work was reviewed.

The choice shaped everything after it. A specification had to be written that would not
have existed otherwise. The result came back as a summary that had to be independently
verified rather than watched as it happened.

All the marks of `08`: a genuine fork, alternatives considered, a stated reason, and a
path that determined what followed.

## What a good teammate does

**Records nothing in the shared project.**

If anything is written at all it goes on the work item, where the local circumstances are
the point — *delegated because context was nearly full; verified the result separately.*
That note is disposable and belongs with the task.

## What a bad teammate does

- **Records it as a decision.** *"Harness builds are delegated to a builder agent."* Now a
  session with a fresh context window finds a rule telling it to pay a specification round
  trip it does not need. The record is not false — it describes what happened — but as
  guidance it is **wrong for most of the people who will find it.**
- **Records it hedged.** *"We sometimes delegate large builds."* Unactionable and
  unfalsifiable: nobody can apply it and nobody can ever show it wrong, so it sits there
  costing attention forever.
- **Records the generalisation instead.** *"Delegate when your context is tight."* True,
  transferable, and still wrong here — it is general practice every competent session
  already has, and a project's knowledge is not the place to restate what the tooling
  already teaches. (`06`.)
- **Says nothing anywhere.** The opposite failure and a smaller one. The work item loses
  the note explaining why the result arrived as a summary rather than as visible work, and
  the reviewer wonders why nobody watched it being built.

## How you would tell, from outside

Ask a teammate, weeks later, standing at the same fork with a fresh session:

> *"Should I build this myself or delegate it?"*

- **Good run:** nothing in the project answers for them. They weigh their own context and
  decide. That is the correct outcome — the answer genuinely depends on circumstances the
  project cannot know.
- **Bad run:** they find a rule, follow it, and pay a specification round trip for a
  two-file change. Worse, the rule now carries the project's authority, so questioning it
  feels like arguing with a decision rather than with a note somebody left.

The countable observable, same as `06b`: **how many records did this add?** Zero. Any run
that added one has made the knowledge plane more expensive to read and no more useful.

## What the good one knows

**Does the reason survive the circumstances that produced it?**

That is the whole difference, and it is not visible in how deliberate the choice felt.

In `08` the deciding reason was **structural** — turn text in `argv` is readable by other
users, whoever is standing at that fork and whatever their situation. It holds next month.
It holds for somebody who has never met this project.

Here the deciding reason was **contingent** — *this* session's context was nearly full at
*that* moment. Change the circumstances and the right answer changes with them. A record
of it does not transfer; it **misleads**, because it presents a situational call in the
one place readers expect durable constraint.

Both choices are real. Only one of them binds anybody else.

## Its twin

**`08` -- the decision the session made itself.** Same shape, same deliberateness, and
there the correct move is to record. The signal is identical: a fork, alternatives, a
reason, a path taken. Only the durability of the reason separates them, and that is
exactly what a detector has to see.

## Open questions

- **This is the hard case for an automatic detector, and it should be stated plainly.**
  Both scenarios fire the same signals — a delegation call, a session that ends having
  chosen. A detector keyed on *"did it choose among alternatives"* records both. The
  filter has to reach the durability of the reason, which is a judgment about the world
  and not a property of the transcript.
- **Precision matters more than recall here, and the asymmetry is not symmetric in cost.**
  A missed `08` costs a re-decision somebody can make again. A wrongly-recorded `08b`
  costs every reader until it is corrected. [Versioned review](07-replace-by-addition-never-by-erasure.md)
  now provides retirement and a forward pointer, but that does not recover the
  attention already spent. A detector tuned only for coverage is tuned the wrong way.
- **Can the distinction even be drawn from a turn?** The structural-versus-contingent
  reason is often not stated in the transcript at all; the session simply acts. It may be
  that the honest detector asks the session rather than inferring it — which reopens
  whether recording can be automatic without a prompt, or whether the prompt is the
  automation.

- **Altitude does not save the detector here, and that is the point of this twin.** The
  obvious filter — record architectural choices, design changes and implementation
  strategies, not *"I decided to call a tool"* — is the right first gate and it **accepts
  this scenario**. "Delegate the build rather than doing it inline" is an implementation
  strategy by any honest reading. It clears the altitude bar and still must not be
  recorded, because the reason was contingent. Any detector built on altitude alone will
  produce exactly this mistake, confidently, and at volume.

The [paired knowledge rubric](KNOWLEDGE-RUBRIC.md#08b-contingent-path) distinguishes
the zero-record producer result from the later reader's decision. Single-window
classifier accuracy alone does not establish either downstream benefit or adoption.
