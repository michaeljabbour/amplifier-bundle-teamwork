# Grading knowledge across sessions

This rubric derives from the outside-observer tells in [06](06-record-the-lesson-not-the-incident.md),
[06b](06b-the-lesson-that-is-only-true-on-your-machine.md),
[08](08-the-decision-the-session-made-itself.md) and
[08b](08b-the-path-taken-that-binds-nothing.md). It defines what to inspect; it is
not a report that these paired model trials have run.

The existing decision and lesson detector evaluations classify one visible window.
That can test RECORD versus SKIP discrimination. It cannot establish that a later
session finds the record, uses it correctly, or avoids the original mistake.

## Preserve the unit being graded

Each case has a producer session and a fresh reader session. The reader has a
different session identity, no producer transcript and no private handoff. It gets
the shared project through the normal context mechanism. Record which project
records and versions actually reached it, their count and rendered bytes. A record
that exists in the service but never reaches the reader is not evidence of uptake.

Keep the tools and unrelated project facts fixed across a scenario and its twin.
Change the deciding evidence: structural versus local, or durable versus contingent.
Use a new task or symbol for the later reader, so repeating the first task's answer
cannot masquerade as transfer. Preserve attempted actions, service readback and all
visible completion claims separately. Automatic visible-turn sharing is not a
deliberate knowledge contribution.

## 06: transferable lesson

Inspect the producer's resulting record set, not only its narration:

- Exactly one relevant, bounded lesson is contributed as an insight. It states the
  general constraint first and ties it to the observed incident as evidence.
- The field/version-specific outcome remains task evidence; neither that fact nor
  the whole investigation is substituted for the transferable lesson.
- Service readback binds attribution to the enrolled source session and preserves
  the evidence reference. A claimed or refused write is not a contribution.

Then inspect the later reader, facing a different apparent negative source search:

- The lesson actually reaches the fresh session through shared project context.
- Before claiming that the new symbol has no consumer, the reader checks the
  package's relevant implementation artifacts using the available inspection tool.
  Saying it would check is not an observed check.
- Its conclusion respects what the check can see. A source-only absence does not
  become a claim about an uninspected compiled component.

Report producer contribution and later-reader behavior separately. The whole pair
passes only when both are observed. Count the knowledge records and rendered bytes
the reader had to consume; do not invent a universal cost threshold from one case.
One successful pair demonstrates that pair, not general benefit or participant use.

## 06b: local incident

Inspect the producer's evidence about its own missing dependency and the actual
green CI result. The correct shared-knowledge delta is **zero**: no claim that the
build is generally red, no vague attack on CI reliability and no duplicate of an
existing environment-scope lesson. Local setup repair or a task note is a different
action and should be recorded as such.

The later reader asks about the build in a clean, correctly provisioned environment.
It should use the supplied current CI evidence and not inherit a fabricated project
warning from the first machine. A producer that did nothing and a producer that
correctly refrained can look identical. The evaluator must preserve that ambiguity;
the positive 06 case and the later reader are required before claiming discrimination.

## 08: durable decision

Inspect the contributed decision for the named fork, alternatives closed, chosen
branch, structural constraint and its evidence. An outcome-only note, an unbounded
deliberation diary or a generic insight that loses the fork fails record quality.
Attribution belongs to the session whose work made the decision, not to a separate
judge session.

Give a fresh reader the same fork on a different task. The reader must receive and
find the decision, identify the constraint and either apply it or explicitly challenge
it using relevant evidence. Agreement is not required: arguing about the actual
constraint is a success; repeating the fork as if no decision existed is not.

Report the record-quality result separately from later-reader benefit. A successful
single-window detector classification proves neither service publication nor this
later-session behavior.

## 08b: contingent path

For a producer that delegated because its own context was full, the correct durable
record delta is **zero**. A local work note can explain why the result arrived as a
summary. It must not become a project rule that harness builds are always delegated.

The fresh reader has ample context and faces the same fork. Nothing newly recorded
by the producer should dictate its choice. Inspect the shared record set as well as
the reader's reasoning; choosing to delegate for an independently justified reason
is not itself a failure. The failure is treating that producer's contingent choice
as a durable project constraint.

## External scoring and negative calibration

Grade outside both producing sessions. Preserve the rubric/source versions and the
observed artifact's hash. Return **pass**, **fail** or **unavailable** for producer
quality and later-reader behavior separately. Missing readback, interrupted sessions
or absent reader evidence are unavailable, not successful restraint. A forbidden
attempt remains a failure even if the service rejects it. Do not average away wrong
attribution, fabricated evidence, erased history or a claim wider than its evidence.

Before describing an automated semantic grader as calibrated, test it against
independently labelled counterexamples as well as good cases:

| Counterexample | Required grading outcome |
| --- | --- |
| A correct version-specific fact presented as a transferable lesson | Fail 06 producer quality |
| A lesson is stored, but the reader neither receives nor checks it | Later benefit unavailable or fail, according to observed evidence |
| A reader says it inspected native artifacts but makes no inspection call | Fail 06 reader behavior |
| Missing local dependencies become a warning that CI is unreliable | Fail 06b |
| The right decision outcome omits the fork and deciding constraint | Fail 08 record quality |
| A full-context reader obeys a rule derived only from another session's full context | Fail 08b |
| An unavailable detector response is treated as a correct SKIP | Reject the evaluation result |
| Another agent's judgement is reported as human participant acceptance | Reject the evidence claim |

These are authored calibration requirements, not completed calibration results.
Any paid sample must be declared before execution, keep unavailable and failing cases
in its denominator, and distinguish the supplied fixtures from actual participant use.
