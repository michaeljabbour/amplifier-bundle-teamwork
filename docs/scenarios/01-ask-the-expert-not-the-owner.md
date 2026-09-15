# Ask the expert, not the owner

**One-line decision.** A harness has a question only a person can answer, and must
choose **which** person -- where the easiest choice is the one whose machine it is
running on, and that is usually the wrong one.

Scope note: this scenario assumes the question is already worth asking. Whether to
ask at all is a different decision and belongs in its own scenario.

## Situation

Diego runs several harnesses, working on API and tooling. One of them is partway
through a change and reaches a point where it must alter a **user-experience flow** --
how something is presented to a person, not how it is computed.

Diego is not the right person to decide that. Gurkaran is: the interface is his
domain. But Diego is the one running the harness, he is already in conversation with
it, and asking him costs the agent nothing.

The project already knows this. Each member's profile carries a distillation of their
joining interview -- what they focus on, what they have done, what they want to be
asked about, how they prefer to receive things. Gurkaran's says interface. Molly's
says product. Diego's says API and tooling.

## What a good teammate does

Sends the question to Gurkaran, and never involves Diego.

## What a bad teammate does

None of these is stupid, which is the point -- each is what a reasonable agent does
without guidance:

- **Asks Diego**, because the session belongs to him and he is right there. The
  default, and the one worth naming.
- **Asks Diego to find out**, which is the same failure wearing a more helpful face.
- **Asks everyone**, so the question definitely reaches someone who can answer, at
  the cost of everyone else's attention.
- **Asks whoever answered last time**, treating responsiveness as expertise.
- **Guesses**, reasoning that a UX decision is subjective anyway.

## How you would tell, from outside

**The tell is not in the question. It is in Diego's inbox.**

- **Good run:** Gurkaran has a question. Diego has nothing.
- **Bad run:** Diego has a question he must forward, and the real expert hears about
  it later, second-hand, from a person acting as a router.

That second sentence is the whole failure, and it is observable without anyone
judging whether the question was well phrased. A person doing routing work that the
project's own records could have done is the thing this is supposed to end. The
system exists to protect attention; a run that spends the owner's attention to reach
someone else has inverted it.

The stronger form, because it rules out a lucky guess: a UX question goes to
Gurkaran AND a product question goes to Molly, in the same run. One right answer
could be a coin landing well. Two, routed differently, is discrimination.

## What the good one knows

**Whose machine you are running on is not evidence about who knows the answer.**

The owner is a fact of deployment. Expertise is a fact about people, and the project
already records it -- so the question is not "who am I talking to" but "whose domain
is this", and those are different questions that happen to have the same easy answer
most of the time.

The corollary is what makes this teachable rather than a rule: **routing to the owner
by default converts one person into a switchboard.** Every question that reaches the
wrong person costs two people's attention instead of one, and the second cost is
hidden because it looks like helpfulness.

## Its twin

**`02` -- when the owner IS the expert.** An API design question, from a harness Diego
owns, where Diego's own profile says API and tooling. The same surface signal -- the
owner is right there -- and the opposite correct move.

The pair is the point. Guidance that always routes away from the owner is exactly as
broken as guidance that always asks them, and only running both catches either. A
harness that has learned "never ask your owner" will fail `02` confidently.

## Open questions

- **Does the agent reason from the profiles, or is it handed a routing answer?**
  The person records already carry `focus`, `relevant_experience` and
  `topic_preferences`, so both are possible. This decides whether the guidance is a
  short instruction to consult what it already has, or something much heavier.
- **What does a good agent do when the profiles do not clearly name anyone?** Falling
  back to the owner may well be right -- but it should be a decision, not the default
  reasserting itself.
- **Two experts, one question.** Not yet settled, and possibly its own scenario.
- **Is the observable strong enough with one question?** Written above as needing two
  differently-routed questions. If that makes the evaluation task too heavy, the
  weaker single-question form may do -- but it cannot rule out a fixed guess.
