# Scenarios

The bundle ships eight tools and no judgment. A tool description says what a tool
does; none of them says when interrupting a person is worth their attention. These
scenarios are where that judgment gets worked out, before any of it is written as
guidance.

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

## Status

| scenario | state |
|---|---|
| `01-ask-the-expert-not-the-owner` | drafted with Diego; open questions recorded |
| `02-when-the-owner-is-the-expert` | drafted with Diego; 01's twin |
| `03-say-what-changes-what-someone-else-would-do` | drafted; twin `03b` named, unwritten |
| `04-ask-the-machine-that-knows` | drafted; twin `04b` named, unwritten; depends on `03` |
| `05` -- handing work to another harness | named, unwritten |
