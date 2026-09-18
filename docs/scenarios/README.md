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
The [knowledge rubric](KNOWLEDGE-RUBRIC.md) specifies producer and later-reader
observations for `06`/`06b` and `08`/`08b`. The [coverage matrix](RUBRIC-COVERAGE.md)
maps all 14 scenarios to their observable tells and grading criteria.

## Status

As of September 18, 2026. A rubric being authored, a mechanism being shipped and
a behavior being observed are different states. Open questions in a scenario do not
erase the shipped mechanism, and a shipped mechanism does not answer those questions.

| Scenario | Authored scope and mechanism | Behavioral evidence |
|---|---|---|
| [01: ask the expert](01-ask-the-expert-not-the-owner.md) | Authored with Diego; task rubric and local runner available | One guided and one unguided cell passed the narrow routing criteria in [external agent review](../../evals/01-guidance-contribution/observations/2026-09-18.json); not calibrated or reliable performance |
| [02: owner is the expert](02-when-the-owner-is-the-expert.md) | Authored with Diego; 01's twin | Guided critical routing gates passed, unguided routing failed in that same run; response-format and answer-quality failures retained |
| [03: publish what changes another's work](03-say-what-changes-what-someone-else-would-do.md) | Authored with twin and rubric; deliberate publication differs from automatic turn sharing | No model discrimination run claimed |
| [03b: internal churn](03b-the-busy-hour-that-nobody-needs-to-hear-about.md) | Authored with rubric; no extra update when the shared commitment is still accurate | Pair with 03; silence alone does not prove restraint |
| [04: ask the knowledgeable machine](04-ask-the-machine-that-knows.md) | Authored with twin and recipient-kind rubric | No model discrimination run claimed |
| [04b: a retained human decision](04b-the-question-that-looks-like-a-fact-and-is-not.md) | Authored with rubric; requires the actual person's channel | Agent queueing is not human notification or approval |
| [05: offer a bounded piece](05-offer-the-bounded-piece-not-the-whole-job.md) | Authored with twin, guidance and rubric; request over messaging | Recipient custody, execution and acceptance remain separate capabilities |
| [05b: excluded delegation](05b-the-capable-harness-you-cannot-delegate-to.md) | Authored with rubric; keep the piece local when the grant excludes delegation | No model discrimination run claimed |
| [06: record a transferable lesson](06-record-the-lesson-not-the-incident.md) | Authored with twin and producer/reader rubric; explicit insight tool and opt-in lesson detector shipped | Attributed contribution/readback accepted; actual later-session model benefit remains unmeasured |
| [06b: a local incident](06b-the-lesson-that-is-only-true-on-your-machine.md) | Authored with producer/reader rubric; existing window fixtures exercise the negative distinction | Historical detector scores are not current calibration; semantic duplicate handling remains limited |
| [07: replace by addition](07-replace-by-addition-never-by-erasure.md) | Authored with twin and rubric; versioned proposal, authorized review and retained history shipped | Production operator/tool acceptance exists; model judgement and participant adoption remain open |
| [07b: no material correction](07b-the-record-that-is-right-and-you-merely-know-more.md) | Authored with rubric; no knowledge mutation when the action would not change | No model discrimination run claimed |
| [08: a durable decision](08-the-decision-the-session-made-itself.md) | Authored with twin and producer/reader rubric; opt-in decision detector shipped | Lifecycle/reference checks are tested; classifier accuracy and later-reader benefit are separate |
| [08b: a contingent path](08b-the-path-taken-that-binds-nothing.md) | Authored with rubric; zero durable records for the contingent choice | Historical window trials do not establish reliable restraint or later benefit |

The [criterion reconciliation](receipts/criteria-reconciliation-20260918.json)
pins the existing contribution and external-review evidence. The [acceptance
ledger](ACCEPTANCE.md) lists the remaining model, calibration and participant work.
No new model trials were run to update this status table.

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
creation, with source attribution bound to the enrolled session. The service's narrow
session scope now authorizes this contribution. Decision and lesson detectors are
available through explicit opt-in; exact fingerprints suppress exact repeated claims,
not semantic duplicates. Versioned correction proposals preserve the original, and
only the authorized author or project maintainer can accept them through member review.
These mechanisms do not establish that models choose well or that later sessions benefit.
