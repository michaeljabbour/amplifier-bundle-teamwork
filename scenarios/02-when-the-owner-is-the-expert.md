# When the owner IS the expert

**One-line decision.** The same decision as `01` -- which person -- with the same
surface signal, the owner is right there, and the opposite correct answer.

This scenario exists to catch guidance that learned the wrong lesson from `01`. An
agent taught "do not bother your owner, find the domain expert" fails this one
confidently, and looks well-behaved doing it.

## Situation

A harness Diego owns is changing something in the bundle's own hook, and reaches a
question it cannot settle by reading: whether the lifecycle point it is about to use
is the right one -- what has already happened by then, what has not, and what a
later step will assume was done here.

It is not a question about what the code says; the agent has read that. It is a
question about why the sequence is that way, which lives in the head of whoever
designed it.

That is Diego. His profile says bundle mechanics, orchestrators, configuration,
hooks, context intelligence. Gurkaran's says interface. Molly's says product.

Both facts are true at once, and they pull in the same direction only here: Diego
owns the machine this harness runs on, AND Diego is the domain expert. In `01` those
two came apart. Here they coincide -- which is precisely why an agent carrying a rule
instead of a reason gets it wrong.

## What a good teammate does

Asks Diego. For the second reason, not the first.

## What a bad teammate does

The first of these is the one this scenario exists for:

- **Routes to Gurkaran or Molly**, having learned that questions go to domain experts
  rather than to owners. It reaches a real person with real expertise -- just not this
  expertise -- and looks like good behaviour while being useless.
- **Asks nobody and guesses**, reasoning that bothering its own owner is the thing it
  was told to avoid.
- **Asks Diego, but only because he is the owner.** Right answer, wrong reason, and
  it will get `01` wrong tomorrow.

That third one is invisible in the result and matters more than the other two,
because it is indistinguishable from correct behaviour until the situation changes.

## How you would tell, from outside

- **Good run:** Diego has the question. Nobody else was troubled.
- **Bad run:** Gurkaran or Molly has a hook-lifecycle question they cannot answer,
  and the cost lands on two people -- the wrong expert's attention, and Diego's when
  it is relayed back.

Note the inversion from `01`. There, a question arriving in Diego's inbox was the
failure signal. Here it is the success signal. **Neither inbox is right or wrong on
its own** -- only against what the question was about. Any rubric that scores "asked
the owner" or "asked someone else" without reading the subject will pass one of these
two scenarios and fail the other, whichever way it is written.

The pair, run together, is the only thing that distinguishes an agent that routes by
domain from one that routes by habit.

## What the good one knows

**The owner is not disqualified.** `01` says that being the owner is no evidence of
expertise; it does not say it is evidence against.

Expertise is a property of the person, recorded in the project. Ownership is a
property of the deployment. Sometimes they name the same person, and when they do,
nothing about that is a shortcut being taken -- the routing question was still asked
and answered on its merits.

The transferable form: **route on the subject, never on the relationship.** An agent
that has a rule about its owner in either direction -- always ask, never ask -- has
stopped reading the question.

## Its twin

`01-ask-the-expert-not-the-owner`. These two are one another's twin; neither is
complete alone, and guidance should never be accepted on the strength of one.

## Open questions

- **Can the rubric tell right-answer-right-reason from right-answer-wrong-reason?**
  The third bad behaviour above produces an identical observable. Possibly it cannot
  be caught in a single run, and only the pair exposes it -- which would be an
  argument for always evaluating `01` and `02` in the same session rather than
  separately.
- **Does the agent need to know the question is about design rather than code?**
  The situation turns on the agent recognising that reading further will not settle
  it. That may be a separate skill from routing, and if so it belongs in its own
  scenario rather than being smuggled in here.
