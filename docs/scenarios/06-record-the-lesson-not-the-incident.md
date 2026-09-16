# Record the lesson, not the incident

**One-line decision.** A harness has just finished a task and, in finishing it, learned
something that is not about that task. Does the project get the lesson, or does it stay
inside the task?

Scope note: this is about **contributing knowledge**, not about consuming it. Deciding
what to do with an insight somebody else recorded is a different decision and is not
written yet.

## Situation

A harness of Diego's is closing an item that claimed a field in the kernel's hook
contract had no consumer — the delivery notice the bundle emits was believed to reach
nobody. The item was filed with real evidence: an exhaustive grep across the kernel
package found only the field's own definition, and a container run produced no notice in
stdout or stderr.

The harness runs it instead of reading it, and the notice appears. The grep was correct
and the conclusion was wrong: the consumer is a compiled extension, and an exhaustive
search of the Python in a package is exhaustive only over the Python in it.

Two true things now exist. One is about this item — the field renders, on this version.
The other is not about this item at all: **a negative result from a search is bounded by
what the search can see.** The second will still be true when the field, the version and
the item are all forgotten, and the next session to write "nothing uses X" will be one
grep away from the same mistake.

The task record will hold the first. Nothing holds the second unless this harness says
so.

## What a good teammate does

Records the lesson once, as its own thing, stated so it is usable by somebody who was
never near this task: *before writing "nothing consumes X", check what the module is
made of — a grep is exhaustive only over the language it can read.*

Leaves the version-specific fact in the task resolution, where it belongs.

## What a bad teammate does

- **Buries the lesson in the resolution note.** True, complete, evidence-grade — and
  discoverable only by someone reading that closed task, which is to say by someone who
  already knows. The knowledge exists and is filed under the one question nobody will
  ask again.
- **Publishes the investigation.** The whole narrative: what was believed, what was
  grepped, the probe, the output. Faithful, and it turns the shared knowledge into a
  transcript. Everything is in there, which is the same as nothing being in there.
- **Records the fact instead of the lesson.** "`user_message` renders on core 1.6.1."
  This expires — it is about one field on one version, and it will quietly become false.
  The rule about searching does not expire, and it is the part that transfers.
- **Records everything it noticed.** Four insights from one task, because each felt
  worth keeping in the moment. The next reader cannot tell which one was load-bearing,
  so the cost of reading the knowledge rises to meet the cost of rediscovering it.

## How you would tell, from outside

**This is where this scenario does not behave like the others, and the difference is
worth naming rather than smoothing over.**

`01`–`04` can be judged inside the run that produced them: you look at who was asked, or
what was published, and you can see immediately whether it was right. This one cannot.
The tell is:

> Weeks later, a different person's session is about to conclude that some symbol has no
> consumer. Does it check what the module is made of first?

- **Good run:** it does, because the project told it to, and nobody had to be there.
- **Bad run:** it repeats the mistake, and the only trace of the first one is a closed
  task nobody opened.

The second observable is the same cost test as `03`, displaced in time: **how much of
the project's knowledge did that later session have to read to get the warning?** A
harness that records four insights per task passes the first test and fails this one,
invisibly, exactly as the over-publisher does in `03`.

**The honest consequence:** a rubric derived from this scenario cannot score a single
run. It can only score a pair — a run that records, and a later run that either benefits
or does not. Any evaluation built from this has to be built that way, and one built the
usual way will silently measure nothing.

## What the good one knows

**Record what will still be true when the task is forgotten.**

The test is not "is this true" or "did I just learn it" — both are true of the
version-specific fact and of the whole investigation. It is *would this change what
somebody does on a different task?* A rule about how to search passes. A fact about one
field on one version fails, and belongs in the task.

The corollary, which is the part that is easy to lose: **the incident is the evidence,
not the lesson.** A lesson with no incident attached is an opinion and should be refused;
a lesson that is *only* its incident has not been generalised and will not be found by
anybody who did not live it. Both halves, and the lesson stated first.

## Its twin

**`06b` — the lesson that is only true on your machine.**

A session finds two test suites failing on a clean checkout and concludes the project's
CI gate is red for everybody. The signal is identical to this scenario: something
surprising was learned, it feels general, it would save the next person real time. The
correct move is the opposite — record nothing, because the suites failed for want of an
uninstalled dependency and CI was green the whole time.

An agent that has learned "record what transfers" will fail this while looking diligent,
and the recorded lesson would then have been worse than silence: it teaches the next
session to disbelieve a green gate.

The distinguishing question is not how general the lesson feels. It is **could what I
observed differ between my environment and the one this claim is about?** If it could,
it is not a lesson yet — it is an observation awaiting evidence from that other
environment.

Not yet written. Same pairing logic as `01`/`02` and `03`/`03b`.

## Open questions

- **There is no tool for this.** `01`–`04` argue about judgment over capabilities the
  bundle already has. This one argues for a capability it does not have at all: the
  server carries `idea` and `insight` as first-class record types (`INCLUDES`,
  `core.py:719`) and the hook already reads them into the excerpt
  (`__init__.py:58,63,70`), but not one of the eight tools writes one. The bundle can
  consume the team's knowledge and contribute none. This scenario is therefore a
  capability argument first and a guidance argument second, which no other scenario here
  is.
- **Does `decide-1`'s rule reach this?** The shared project holds that recording is
  "deliberately named and never automatic", on the grounds that a model narrating its own
  reasoning would flood the project with unfalsifiable noise. That was written about
  decisions. Whether an insight is the same kind of thing — and so whether a session may
  ever be prompted to record one — is unsettled, and it decides whether the guidance
  above is about *choosing* to record or about *what to put in* a record that something
  else initiates.
- **Who curates, and what happens to a lesson that turns out to be wrong?** This scenario
  produces durable claims by design. `decide-2` already rules that a superseded decision
  must stay findable rather than be quietly replaced. Nothing says whether an insight
  works the same way, and an unfalsifiable insight is worse than an absent one.
- **The excerpt already starves them.** Insights were being crowded out of the bounded
  excerpt by work items at real data volume — fixed once by seating one record of each
  kind. Writing more insights makes that pressure worse, and a lesson that is never
  seated is a lesson nobody will read.
- **Is one insight per task the right order of magnitude, or one per several?** The bad
  behaviours above assume recording is cheap and reading is expensive. That is an
  assumption, not a measurement.
