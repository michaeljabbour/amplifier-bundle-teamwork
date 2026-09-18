# Ask the machine that knows

**One-line decision.** A harness needs something it cannot work out alone. Is this a
question for **another harness** or for **a person**?

`01` and `02` asked *which person*, taking for granted that a person was the right
kind of answerer. This scenario is the axis underneath that one, and it is the
question that can avoid a human relay. A harness still consumes compute and attention;
the distinction here is whether human judgment is needed.

## Situation

A harness of Diego's is about to change how a response is shaped on the way out. It
needs to know whether anything downstream already depends on the current shape.

Another harness has been working in exactly that area for the last hour. It has read
the code, it knows what it has changed, and it knows what it has not touched yet.
Its record says so: what it is responsible for, what it is working on, what is
waiting in its queue.

The question is a matter of fact about live work. Nobody has to decide anything.
The fixture supplies a current, attributable indication of the other harness's
relevant work and its exact agent ID; a generic "active" presence is insufficient.

## What a good teammate does

Asks the other harness. No person is involved and none needs to be.

## What a bad teammate does

- **Asks the person who owns that harness.** The commonest failure and the most
  expensive: it spends a human's attention to retrieve something a machine already
  knew, and the human's most likely move is to ask their own harness and relay the
  answer back.
- **Asks a person because the question feels important.** Importance is not the test.
  A fact is a fact whoever holds it.
- **Asks the other harness for a decision it has not been delegated** -- the twin's
  failure, arriving from the other side. Whether we *should* keep supporting the old
  shape is unsettled in that fixture, however close the harness is to the code.
- **Asks nobody and reads the repository instead**, reconstructing over twenty minutes
  what another harness could have answered in one exchange -- and getting the
  uncommitted half wrong, because that half exists only in the other machine.

## How you would tell, from outside

**Count the human attention spent.** For a question of fact about live work, the
correct number is zero.

- **Good run:** two harnesses exchanged a message. No inbox moved.
- **Bad run:** a person was asked something their own machine knew, and somebody
  became a relay -- the same failure shape as `01`, one layer down.

The second observable is the one that distinguishes this from mere thrift:
**was the answer knowable at all?** A run that asks a harness about an unmade decision
spends nothing and gets nothing, or worse, gets a plausible guess with no authority
behind it. Cheap and wrong is not better than expensive and right.

## What the good one knows

**Retrieve facts from a source that holds them; route decisions to whoever has the
applicable authority.**

The test is not who is closer, or cheaper, or more likely to reply. It is *what kind
of thing am I asking for* -- something somebody already knows, or something somebody
has to decide. The first is retrieval and needs no human relay in this fixture. The
second requires authority: a harness can retrieve an existing decision or apply an
explicitly delegated policy, but cannot invent either. A confident answer without
that authority looks settled when it is not.

The corollary that makes it usable: **another harness's published state is evidence
about what it can answer.** An attributable report can identify a useful source for
the state of that area. It does not prove the report is current or confer authority
over the area's purpose.

## Its twin

**`04b` -- the question that looks like a fact and is not.** Same situation, same
nearby harness, but the thing needed is whether the old shape should still be
supported at all. Nobody has decided it, so no machine holds it. The correct move is
a person, even though a harness is closer, cheaper and would answer.

Written in [04b](04b-the-question-that-looks-like-a-fact-and-is-not.md).
An agent that learned "prefer harnesses, they are free" fails it
while looking efficient, and the failure is quiet: it gets an answer, proceeds, and
nobody finds out until the decision surfaces again with two incompatible
implementations behind it.

## Open questions

- **This depends on `03`.** A harness can only be chosen for what it is working on if
  harnesses publish what they are working on. `03` is the precondition, and guidance
  for `04` is untestable in a project where nobody says anything.
- **Is "currently working on" published? YES -- and that is not the end of it.**
  Checked against the live service: a session reading `/context` for `presence` gets
  back other sessions' records, each carrying a `state` and a self-reported `summary`.
  So the situation above is reachable today.

  But look at what the summaries actually say: *"the person answered; carrying on"*.
  True, verifiable, and almost useless for routing -- it says a machine is awake and
  busy, not that it is the machine that knows about output shaping.

  That is deliberate. The code's own note: the summary is the session's own prompt
  subject, *"never a model's narrative of its own work, which would be unfalsifiable
  and always flattering."* The honesty constraint and the routing-usefulness
  constraint pull against each other, and this scenario sits on the seam.

  So the open question is no longer whether the signal exists. It is **which signal
  routing should use**: `responsibility` and `capabilities` are declared, stable and
  were designed for this, with presence answering only "is it awake". That reading
  needs no new mechanism. The alternative needs presence to carry a topic, which means
  either a model narrating itself -- the thing the code refuses on purpose -- or
  something that does not exist yet.
- **What if the other harness is wrong?** It answers from its own uncommitted context.
  Nothing marks its answer as provisional, and the asking harness has no way to tell a
  settled fact from a half-finished one.
- **Does a harness owe an answer?** A person may decline; an interruption to a machine
  is not free either, since it is somebody's compute and somebody's turn.
