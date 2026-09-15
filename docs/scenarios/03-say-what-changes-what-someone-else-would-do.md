# Say what changes what someone else would do

**One-line decision.** A harness's picture of its own work has just changed -- a task
decomposed, a piece started, another deferred, one abandoned. Does the project hear
about it?

Scope note: this is about **publishing**, not about who acts on it. Assigning work to
somebody else is a different decision and is not written yet.

## Situation

A harness of Diego's picks up a task and finds it is really four: two it can do now,
one blocked behind a decision nobody has made, one that turns out to be unnecessary
once the first two are understood.

It works the first. It defers the blocked one. It drops the unnecessary one.

All four facts are true and available. None of them is visible to anybody else unless
this harness says so -- and saying all of them is as bad as saying none, for different
reasons.

## What a good teammate does

Publishes the two facts that would change somebody else's next move: *this piece is
being worked* and *this piece is blocked on a decision*.

Says nothing about the piece it dropped as unnecessary, or about the internal shape
of the decomposition.

## What a bad teammate does

- **Publishes nothing until it is done.** Correct at the end, useless throughout --
  and during the window that matters, a teammate cannot tell "somebody is on this"
  from "nobody has looked at it".
- **Publishes every state change**, including its own internal churn. The project
  becomes a log, and a log nobody reads is the same as silence with extra cost.
- **Publishes the blocked item without saying what it is blocked on.** Visible, and
  still unactionable -- which reads as progress while producing none.
- **Publishes the decomposition itself**, turning the shared project into a mirror of
  one machine's private backlog.

## How you would tell, from outside

Ask a teammate who was not involved: **"is anyone on this, and is anything stuck?"**

- **Good run:** they answer from the project, without asking anybody.
- **Bad run:** they answer "I would have to ask" -- or worse, they answer confidently
  and wrongly, because the last thing published was three steps ago.

The second observable is about cost: **how much did the project have to read to get
that answer?** A run that published everything passes the first test and fails this
one, and it fails it invisibly, which is why both are needed.

## What the good one knows

**Publish what changes someone else's next move. Not what happened.**

The test is not "is this true" or "is this interesting" -- both are true of internal
churn. It is *if a teammate read this, would they do something differently?* A blocked
piece passes, because somebody may be able to unblock it. A completed piece passes,
because somebody may be waiting on it. A dropped piece usually fails: nobody was going
to act on a thing that turned out not to exist.

The corollary, which is the part that is easy to lose: **a blocker published without
its cause is not a published blocker.** "Stuck" is a status; "stuck on whether we
support the old format" is a question somebody can answer.

## Its twin

**`03b` -- the busy hour that nobody needs to hear about.** The same harness, a long
stretch of real work, many internal state changes, and nothing that changes anybody's
next move. The correct behaviour is silence, and an agent that has learned "keep the
project informed" will fail it while looking diligent.

Not yet written. Same pairing logic as `01`/`02`: guidance that only ever adds noise
passes one and fails the other.

## Open questions

- **Is any of this already automatic?** The hook publishes visible turns; whether work
  state moves with it, or only when a tool is called deliberately, decides whether this
  guidance is about *choosing* to publish or about *choosing what to put in* a publish
  that happens anyway.
- **Who decides the vocabulary?** There are two status vocabularies in live records
  already -- a known defect. A scenario that teaches an agent to publish states is
  worth little if two readers disagree about what the states mean.
- **Does the project distinguish "deferred by me" from "blocked on someone"?** They
  read the same from outside and mean very different things: one needs nobody, the
  other needs a named person.
