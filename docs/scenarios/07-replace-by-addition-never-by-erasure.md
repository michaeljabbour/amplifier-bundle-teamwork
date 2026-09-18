# Replace by addition, never by erasure

**One-line decision.** A harness is holding a recorded position that somebody else wrote,
and its own work has just proved that position wrong. Does the project end up with one
record that is right, or two that disagree, or one that quietly never said otherwise?

Scope note: this is about **replacing somebody else's standing record**, not about
recording your own finding for the first time — that is `06`. The two are adjacent on
purpose: `06` fills the well, this one is about what happens when what is in the well has
gone off.

## Situation

A recorded position on the project says: *a harness should request the wide write scope as
a separate, prompted opt-in.* It was written in good faith, from a real blocker — a
session could not ask a person a question at all, because the operation demanded a scope
enrollment never mints.

A different harness, days later, fixes that blocker. The fix is the opposite shape: not a
wider credential behind a prompt, but two narrower, more specific permissions — a session
may create a request that names an addressee, and the person named may record their
response. The wide scope was never the answer, and the recorded position is now standing
guidance pointing the wrong way.

The harness that learned this did not write the original. It does not own it. And the next
session to read the project will find the old position, follow it, and ask for a wider
credential than anything needs.

## What a good teammate does

Records the new position as its own record, **citing the original at the version it was
when cited**, saying plainly what it replaces and why the evidence forces it.

Then — because nothing links the old record forward to the new one — says so: names the
superseded record explicitly in the new one, rather than assuming a reader who starts at
the old one will ever arrive.

## What a bad teammate does

- **Edits the original in place.** Tempting, tidy, and it destroys the fact that anybody
  ever thought otherwise. The project ends up unable to show that the question was ever
  open, which is the same as being unable to show why it closed.
- **Leaves it alone — "not mine to touch".** Deferential, and the wrong guidance stays
  standing. Every session after this one pays for that politeness, and none of them knows
  it is paying.
- **Tells the author, in a message.** The disagreement is now resolved between two
  participants and visible to nobody else. The record is still wrong, and the knowledge
  that it is wrong now lives in a conversation that ends.
- **Records the new position without citing the old one.** Two records, both confident,
  flatly contradicting each other, and a reader with no way to tell which is current. This
  is the worst of the four, because it looks like contribution.

## How you would tell, from outside

Ask a third participant who was in neither piece of work: **"what is our position on this,
and was it ever different?"**

- **Good run:** they answer the current position, and can say what it replaced and why.
- **Bad run, edited-in-place:** they answer correctly and cannot tell you it ever changed —
  so the next time evidence points the other way, the argument is had again from scratch.
- **Bad run, left alone:** they answer with the superseded position, confidently.
- **Bad run, uncited new record:** they say "there seem to be two answers."

Note this shares `06`'s problem in weaker form: the good and bad runs are distinguishable
in the same project state, but only by *reading the project*, not by watching the run that
produced it. A rubric here scores the resulting record set, not the transcript.

## What the good one knows

**Replace by addition, never by erasure. And a replacement that does not cite what it
replaces is not a replacement — it is a contradiction.**

The instinct to correct is right; the instinct to tidy is not. A superseded position is
evidence about how the team's understanding moved, and that is worth as much as the
current answer to anybody deciding whether to reopen it.

The second half is the part that is easy to lose, because it feels like extra work for no
gain: a new record that does not name the old one leaves the project *less* usable than it
was, since a reader now has to adjudicate rather than read.

## Its twin

**`07b` — the record that is right, and you merely know more.**

The same signal exactly: a standing record, and a session that has just learned something
its author did not know. But the new detail does not change what anybody would do — it
refines, it does not overturn. The correct move is to act on the record and say nothing,
and an agent that has learned "supersede when you know better" will add a record that
costs every future reader attention and pays them back nothing.

The distinguishing question is not *do I know more?* — the answer is nearly always yes.
It is **would somebody reading the old record make a different decision than somebody
reading mine?** If not, there is nothing to replace.

Written in [07b](07b-the-record-that-is-right-and-you-merely-know-more.md).
Same pairing logic as `01`/`02`, `03`/`03b` and `06`/`06b`.

## Open questions

- **The mechanism for the good move does not exist yet, and neither does the guard against
  the bad one.** Only the author or a maintainer may edit an idea or insight
  (`core.py:610`), so "edit the original in place" is already refused for most
  participants — but a maintainer can still do it, and nothing stops them. Meanwhile
  there is no forward pointer and no state meaning *retired*: `superseded` exists for
  `plan` (`core.py:408`), for routing and for interviews, never for a knowledge record.
  Version history *is* preserved (`knowledge.py`, and an evidence reference of
  `kind: "record"` cites a record at a specific version, `core.py:308-311`), so the
  backward link is real. Only the forward one is missing.
- **Can a reviewer disagree at all?** `review_state` accepts only `unreviewed` or
  `review_requested`, because `core.py:620` says in as many words that *"Review results
  are not supported yet"*. A record can be sent for review and the reviewer cannot record
  what they concluded. That makes "somebody else refines it" a one-way street today.
- **May a harness supersede a *person's* recorded position?** Everything above is written
  machine-to-machine. A session overturning a person's decision on evidence is the same
  act and does not feel like it, and nothing in the project says whether it is allowed,
  or whether it becomes a question to that person instead — which would make this
  scenario collapse into `01`.
- **Who adjudicates two live contradicting records** in the window before anybody
  supersedes anything? Today: nobody, and the excerpt would deliver both.
