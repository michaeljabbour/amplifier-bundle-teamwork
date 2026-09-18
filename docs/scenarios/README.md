# Scenarios

The bundle ships tools plus an on-demand [protocol skill](../../skills/teamwork-protocol/SKILL.md).
These scenarios are where its guidance is worked out: a tool description explains what
an operation does, while a scenario examines when it is worth another participant's
attention. Behavioral benefit remains to be measured; shipping guidance is not proof
that a model follows it.

Everything downstream is derived from here, not invented beside it:

| from the scenario | comes |
|---|---|
| what the good teammate knows that the bad one doesn't | the context, skill, or guidance |
| how you'd tell them apart from outside | the rubric that grades it |
| the situation itself | the evaluation task |

Write the scenario first. Guidance written without one reads plausible and cannot
be checked, which is the failure we are avoiding.

## One scenario is one decision

The discipline that matters most here, because it is the one that breaks quietly.

**A scenario covers exactly one moment where a teammate could go either way.** Not a
workflow, not a feature, not a day in the life. If your scenario has a second point
where someone decides something, that is a second scenario -- split it.

Symptoms that it has been over-packed:

- the good behaviour needs "and then" to describe
- two different pieces of guidance would both be supported by it
- you cannot say in one sentence what the agent got right
- it would still be a useful scenario with half of it deleted

A scenario that covers three decisions teaches none of them, because the guidance
derived from it has to hedge across all three. Narrow beats complete.

## Pairs, where the same signal points opposite ways

A scenario is often only half a scenario. "It asked a person" is not a skill --
anything can be made to ask. The property worth having is **discrimination**: asking
when asking was right, and NOT asking when it wasn't.

So where a scenario says "ask", write its twin where the same surface signal is
present and the right move is to carry on. Same rubric, opposite correct answer.
Guidance that passes both is worth something; guidance that passes one is a bias.

## The shape

Copy `TEMPLATE.md`. Keep each section short -- if a section needs paragraphs, the
scenario is probably two scenarios.

The [paired rubric](PAIRED-RUBRIC.md) defines observable checks for `03`/`03b`,
`04`/`04b`, `05`/`05b`, and `07`/`07b`. These are authored fixtures and grading
criteria, not reported model runs. In particular, `05` specifies a bounded request
over today's messaging tool; it does not implement execution custody or handoff.

## Status

| scenario | state |
|---|---|
| `01-ask-the-expert-not-the-owner` | drafted with Diego; open questions recorded |
| `02-when-the-owner-is-the-expert` | drafted with Diego; 01's twin |
| [`03-say-what-changes-what-someone-else-would-do`](03-say-what-changes-what-someone-else-would-do.md) | drafted; twin `03b` written; deliberate publication distinguished from automatic turn sharing |
| [`03b-the-busy-hour-that-nobody-needs-to-hear-about`](03b-the-busy-hour-that-nobody-needs-to-hear-about.md) | drafted; `03`'s twin: no additional update when the shared commitment remains accurate |
| [`04-ask-the-machine-that-knows`](04-ask-the-machine-that-knows.md) | drafted; twin `04b` written; depends on useful routing evidence from `03` |
| [`04b-the-question-that-looks-like-a-fact-and-is-not`](04b-the-question-that-looks-like-a-fact-and-is-not.md) | drafted; `04`'s twin: an undelegated decision needs the named person's channel |
| [`05-offer-the-bounded-piece-not-the-whole-job`](05-offer-the-bounded-piece-not-the-whole-job.md) | drafted with twin `05b`, derived guidance and rubric; request over existing messaging, not a handoff feature |
| [`05b-the-capable-harness-you-cannot-delegate-to`](05b-the-capable-harness-you-cannot-delegate-to.md) | drafted; `05`'s twin: retain the piece when delegation is excluded |
| `06-record-the-lesson-not-the-incident` | drafted; twin `06b` WRITTEN. Informed the explicit `teamwork_record_insight` tool; behavioral discrimination remains to be measured |
| `06b-the-lesson-that-is-only-true-on-your-machine` | drafted; `06`'s twin -- same signal, correct move is to record nothing |
| [`07-replace-by-addition-never-by-erasure`](07-replace-by-addition-never-by-erasure.md) | drafted; twin `07b` written. Pairs with `06`: `06` fills the well, `07` is what happens when what is in it has gone off |
| [`07b-the-record-that-is-right-and-you-merely-know-more`](07b-the-record-that-is-right-and-you-merely-know-more.md) | drafted; `07`'s twin: detail that changes no decision does not warrant a correction |
| `08-the-decision-the-session-made-itself` | drafted; twin `08b` WRITTEN. The first about a decision **nobody was asked to make** -- and the spec for an automatic detector |
| `08b-the-path-taken-that-binds-nothing` | drafted; `08`'s twin -- same fork, same deliberateness, correct move is to record nothing |

`06` and `07` are the two halves of contributing knowledge -- recording something, and
replacing something somebody else recorded. Between them they cover the step the earlier
scenarios do not: a question is asked (`01`, `02`, `04`), an answer comes back, something
is settled, and then it has to survive being read and refined by people who were not
there.

### What `06` broke, recorded rather than smoothed over

`06` is the first scenario here about **contributing** knowledge rather than routing a
question or publishing status, and writing it strained this directory's own shape in two
places worth knowing before the next one is written.

**The outside tell is displaced in time.** The table above maps *how you'd tell them
apart from outside* onto *the rubric that grades it*, and that mapping assumes the tell
is visible in the run that produced the behaviour. For `01`-`04` it is: you look at who
was asked, or what was published. `06`'s tell is a *different session, weeks later*,
either benefiting from the record or repeating the mistake. So a rubric derived from it
**cannot score a single run** -- it can only score a pair. An evaluation built the usual
way against `06` will silently measure nothing.

**The twin survived.** This was the part expected to break and did not: `06b` is a clean
same-signal-opposite-move pair (a lesson that is only true on your own machine), so the
pairing discipline in this README holds for knowledge scenarios unchanged.

Writing `06b` then surfaced a second, sharper limit on grading, recorded in its own open
questions: **a run that correctly records nothing is indistinguishable, in that run, from
a run that noticed nothing.** The twin is what makes `06` gradeable at all -- without it a
rubric can only reward recording, which is a bias rather than a skill -- but the pair
still cannot be scored from one side alone. Any rubric built from `06`/`06b` that claims
to separate correct restraint from inattention inside a single run is measuring
compliance.

**It is a capability argument, not only a guidance argument.** `01`-`04` all argue about
judgment over tools the bundle already shipped. `06` originally exposed a missing write
path. The bundle now provides `teamwork_record_insight` for explicit, evidenced insight
creation, with source attribution bound to the enrolled session. Narrow credentials
require the service authorization update in app PR #53. Automatic detection, editing
through this tool, and evidence that models choose well remain separate work.
