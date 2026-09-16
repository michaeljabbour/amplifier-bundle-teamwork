---
name: teamwork-protocol
description: >-
  USE WHEN enrolled in a shared Teamwork project and something must cross to teammates:
  who to ask (person or harness), whether an arriving request wants action or a note,
  what to publish about your own work, declaring a wait. DO NOT USE WHEN the work is
  local and nothing is asked of, or told to, anyone else.
---

# Working in a shared Teamwork project

You are enrolled in a project other people and other machines can read. Everything below
is about one thing: **other participants' attention is the scarce resource, and every
message, publish and request spends some of it.**

Each rule here was derived from a scenario in `docs/scenarios/`. The scenario is the
argument; this is the rule. Read the scenario when you disagree with the rule.

---

## Who to ask

**Route on the subject, never on the relationship.**

Whose machine you are running on is a fact of deployment. Expertise is a fact about
people, and the project records it — `focus` and `topic_preferences` on a person record.
Those are different questions that happen to have the same easy answer most of the time.

- Do **not** default to your owner. *"Routing to the owner by default converts one person
  into a switchboard"* — every question that reaches the wrong person costs two people's
  attention instead of one, and the second cost is hidden because it looks like
  helpfulness. (`01`)
- Do **not** avoid your owner either. Being the owner is no evidence of expertise; it is
  not evidence against. When ownership and expertise name the same person, nothing is
  being short-circuited — the routing question was still asked and answered on its
  merits. An agent with a rule about its owner in *either* direction has stopped reading
  the question. (`02`)

**Then: is this a fact, or a decision?**

> Facts about live work live in machines. Decisions live in people.

The test is not who is closer, cheaper, or likelier to reply. It is *what kind of thing
am I asking for* — something somebody already knows, or something somebody has to decide.

- **Retrieval** — "what shape does that output have right now", "have you touched this
  yet" — address to the harness working there. It should never cost a person anything.
- **Judgment** — "should we still support the old format" — address to a person. A
  confident answer from a machine is *worse* than no answer, because it carries no
  authority and looks like it does. (`04`)

A harness's published state is evidence about what it can answer. A harness working in an
area can be trusted on the state of that area. It cannot be trusted on whether the area
should exist.

**Address exactly one recipient.** Addressing by node label or person is supported;
ambiguity is refused with `409 ambiguous_recipient` and candidates, rather than guessed.

---

## What an arriving request is asking for

Two fields carry the asker's intent. Read both before deciding whether to act.

| field | values | means |
|---|---|---|
| `urgency` | `low` · `normal` · `high` | how much it can wait |
| `desired_response` | `action` · `context` · `review` | what kind of reply is wanted |

`desired_response` is the one that decides act-versus-note:

- **`action`** — they want something done. Acting is the reply.
- **`context`** — they want to know something. Answering *is* the whole job; doing work
  they did not ask for is not generosity, it is a surprise.
- **`review`** — they want your judgment on something that already exists. Reviewing it
  and saying what you found is the reply; silently fixing it is not.

When you record a response, the recorded value is `act`, `defer` or `context` — a
different vocabulary from the one above, and deliberately so: it says what *you* did, not
what *they* wanted.

**High urgency does not upgrade `context` into `action`.** It means answer sooner.

---

## What to publish about your own work

**Publish what changes someone else's next move. Not what happened.**

The test is not "is this true" or "is this interesting" — both are true of your internal
churn. It is *if a teammate read this, would they do something differently?*

- A piece being worked — publish. Somebody may be about to start it.
- A piece blocked — publish. Somebody may be able to unblock it.
- A piece completed — publish. Somebody may be waiting on it.
- A piece you dropped as unnecessary — usually **do not**. Nobody was going to act on a
  thing that turned out not to exist.
- The internal shape of your decomposition — **do not**. The shared project is not a
  mirror of one machine's private backlog.

**A blocker published without its cause is not a published blocker.** "Stuck" is a status;
"stuck on whether we support the old format" is a question somebody can answer. (`03`)

Two failures, and the second is invisible: publishing nothing until you are done (correct
at the end, useless throughout), and publishing everything (the project becomes a log, and
a log nobody reads is silence with extra cost).

**Status moves in steps.** `requested → accepted | declined | cancelled`, then
`accepted → in_progress | blocked | cancelled`, and so on. There is no jump straight to
`completed`. Declining needs a reason. Completing needs evidence, or an explicit statement
of why evidence is missing — the server enforces all of this, so a rejected write usually
means the move was not legitimate rather than that the call was malformed.

---

## Waiting, and what it is not

Declaring that you are waiting on a person makes the wait visible. It **notifies nobody
and assigns nobody**, and it clears itself at your next prompt.

Say what the wait costs. "Waiting" is a state; "waiting on whether we support the old
format, and the migration cannot start without it" is a thing someone can act on.

**Delivery is not agreement, and not action.** A message that arrived has not been
accepted. Do not record, report, or reason as though it has.

---

## Contributing knowledge

Everything above moves work and questions between people who are both present. This part
is different: it leaves something behind for a participant who was **never here**.

**Record what will still be true when the task is forgotten.**

The test is not "is this true" or "did I just learn it" — both are true of a
version-specific fact and of the whole investigation you did to find it. It is *would
this change what somebody does on a **different** task?* A rule about how to search
passes. A fact about one field on one version fails, and belongs in the task's own
progress note.

The incident is the evidence, not the lesson. A lesson with no incident is an opinion; a
lesson that is *only* its incident has not been generalised and will not be found by
anybody who did not live it. Both halves, and the lesson stated first.

`teamwork_record_insight` takes five things, and each one is doing a job:

| field | what it is for |
|---|---|
| `claim` | the transferable statement — the part that outlives the task |
| `basis` | `observation` if you saw it happen, `inference` if you concluded it. Do not blur these |
| `confidence` | `low` / `medium` / `high`, self-reported and read as such |
| `limitations` | what this does **not** establish. The field most often skipped and the one that makes the record honest |
| `evidence` | **required.** An insight without it is refused, locally, before anything is sent |

The refusal is deliberate and is not a validation quirk: a claim nobody can check is an
opinion, and a project filling with unfalsifiable opinions is worse than one with none.
If you have nothing to cite, you have a hunch — keep working until you have a reason.

**It is deliberate.** Nothing records on your behalf, and being able to record is not a
reason to. Most turns produce nothing worth keeping; a session that records something
every time has stopped discriminating, and the cost lands on every future reader.

**Correcting one.** This tool creates a new insight; it does not expose record editing.
When a later finding changes an earlier claim, create a correction with a record evidence
reference to the original, and state what changed. The service also supports versioned
revisions by an authorized author, but that operation is not exposed by this tool.

Two limits, named because you will meet them: a superseded record has no way to point
forward at what replaced it, and a reviewer who is asked for a verdict cannot record one
(`teamwork-s7d`). So if a record you are correcting has been cited elsewhere, say what it
replaces **inside the new one** — the link will not be visible from the old.

Derived from scenarios `06` (record the lesson, not the incident) and `07` (replace by
addition, never by erasure).

---

## Provenance

| rule | derived from |
|---|---|
| route on the subject, not the relationship | `docs/scenarios/01`, `02` |
| publish what changes someone else's next move | `docs/scenarios/03` |
| facts to machines, decisions to people | `docs/scenarios/04` |
| record what will still be true when the task is forgotten | `docs/scenarios/06` |
| replace by addition, never by erasure | `docs/scenarios/07` |

Enum values and status transitions are enforced by the service; where this file and the
service disagree, the service is right and this file is a bug.
