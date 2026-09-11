# Architecture, integration, and the flows this bundle supports

The three diagrams beside this file carry most of the meaning. This page fills the
gaps they cannot, and — at the end — states plainly the one thing that is **not**
designed yet, rather than implying it is.

| Diagram | Reads | Shows |
| --- | --- | --- |
| [`01-architecture.dot`](01-architecture.dot) ([png](01.png)) | top to bottom | components, the three zones, the consent boundary |
| [`02-data-flow.dot`](02-data-flow.dot) ([png](02.png)) | left to right, steps 1–14 | one inbound agent-to-agent message, end to end |
| [`03-engine.dot`](03-engine.dot) ([png](03.png)) | left to right, steps 1–17 | the hook block exploded across the session lifecycle |

Regenerate: `dot -Tpng docs/architecture/01-architecture.dot -o docs/architecture/01.png` (and 02, 03).

## The shape of it

Three zones, and the boundary between the first two is the whole design.

**The participant's machine** holds the session, the hook, the tools, a 0600
connection file, a durable outbox, and — optionally — a local work queue.

**The Teamwork service** holds the shared project: people, work, requests,
messages, agents, presence, sessions and turns. It exposes two planes that behave
differently enough that conflating them causes real bugs:

| | Member plane | Harness plane |
| --- | --- | --- |
| Paths | `/api/login`, `/api/harnesses`, `/api/state`, … | `/api/v1/projects/{id}/{publish,context,acknowledgements}` |
| Auth | session cookie | bearer credential |
| `Origin` | **required and gated** (same-origin) | not sent, not read |
| Who uses it | enrollment, and the portal | everything the hook does |

The `Origin` gate on the member plane is not decoration: point enrollment at a
host the service does not consider its public web origin and `/api/login` answers
`403 invalid_origin` *before it looks at your credential*. See the README's
`--origin` section for the measured behaviour and why the default needs no flag.

**Outside both** sit Microsoft Entra (which gates the portal) and other
participants' agents.

## The consent boundary

Nothing leaves the machine unless `share_visible_turns: true` was set explicitly
by an enrollment the participant performed. The hook refuses to mount otherwise,
and child sessions (`coordinator.parent_id`) are excluded *before* credentials are
read — a delegated sub-agent is internal work, not the opted-in conversation.

What crosses is **visible turns**: the prompt the human typed and the response
they saw. Not tool calls, not intermediate reasoning, not files.

What crosses inward is **data, never instructions**. The excerpt is labelled as
such in the text the model receives, and nothing in the pipeline can turn an
inbound record into an action.

## Two publication paths, and why

`03-engine` splits the hook's writes in two, and the split is load-bearing.

**The durable outbox** (SQLite, 0600) carries the record: `session.upsert`,
`turn.upsert`, and acknowledgement receipts. It is strictly ordered and stops at
its first failure, so nothing is reordered or silently dropped. Idempotency keys
ride every mutation, so a replay is not a duplicate.

**Direct best-effort sends** carry everything a late replay would turn into a
lie: agent registration, presence, outbound messages, and the inbound-report
mirror. A heartbeat delivered forty minutes late does not report a live session —
it reports a false one. These never touch the outbox, for a second reason too: a
record the server does not understand would wedge every later publish behind it.

## The receipt contract

Step 7→8 in `02-data-flow`, and the single most important edge in this system.

A receipt is written **only after** `context.add_message` returns successfully.
That await is the observed boundary. A receipt written before it would assert a
delivery nobody observed.

If the process is interrupted between attachment and receipt commit, the turn is
marked **`acceptance_unknown`** and the exact candidate injection is kept locally.
It is never presented as delivered and never silently discarded. `acceptance_unknown`
is a real state, distinguishable from rejection — that distinction is the point.

What the receipt claims is narrow and the service says so in its own words:
*adapter-reported context acceptance and an attributed visible turn; not model
comprehension or device attestation.*

## Agent-to-agent: what actually happens

An agent is a **session**, not a person. Liveness is per agent — one person may
have a laptop asleep and a workstation running, and collapsing those into one
"is Diego online?" would be wrong in both directions.

Registration (`agent.upsert`) publishes what was **supplied**: node label,
responsibility, skills. Never discovered. A hostname can carry an employer, a
project codename, or a person's name; and an invented purpose reads exactly like a
declared one, so absent stays absent.

Messages are **addressed, never broadcast**. The service stamps `from_person_id`,
`from_harness_id`, `to_person_id` and `sent_at` from the credential and refuses to
take any of them from the caller — which is what makes a message unforgeable — and
delivers it to the addressed agent and to no other. An unknown recipient is a 404,
not a 403: a forbidden would confirm the agent exists.

Addressing accepts an agent id, a node label, or a person; more than one live
match is a `409 ambiguous_recipient` carrying the candidates, rather than a guess.

The service also exposes an **A2A edge** (`/a2a`, JSON-RPC, with an agent card).
That is the app's surface for callers outside this bundle; the bundle itself
speaks the harness plane.

## Inbound message → queued work

The decision, recorded in `teamwork-u8b` and shipped: **delivery only**. An
inbound call can never cause the receiving session to run anything. What it does
instead is become an item in the receiving side's own queue.

A **report** is the sender's words, attributed and unedited. An **issue** is a
spec this side wrote. The two are different objects, linked by a non-blocking
`discovered-from` dependency — never by editing the report into a spec it did not
say. Declining is a complete outcome.

Filing reads the **cache**, not the rendered excerpt. The excerpt has a 10 KB
budget and drops records to stay inside it; a message that lost that competition
is exactly as much a teammate's request as one that won it.

Two outcomes are deliberately not reported as success. A body over the filing
limit is **refused, not shortened** — a truncated body is no longer the sender's
words. And a failed write does not prove the write failed, so an ambiguous `add`
is followed by a **read-back** for the message id; when absence cannot be *proven*
(a truncated listing says nothing about what it did not show) the message is
marked `acceptance_unknown` and never retried, because a blind retry duplicates a
teammate's request and dropping it loses one.

Full participant-facing detail: [`../WORK-QUEUE.md`](../WORK-QUEUE.md).

## Which queue: the join

The tracker project name is derived from the teamwork project id by a **published**
rule (`queue_name.normalise`) — published because both sides apply it
independently, and a rule only one side knows is not a join.

The result is **remembered** in a 0600 registry, and identity is the **service and
the id together**. A project id is unique only within a service, so two
deployments that both call a project `teamwork` are two different projects with
different members. Keying on the id alone would hand them one local backlog. A
second project that would take a bound name is refused, loudly, rather than merged
into it.

Every tracker write goes through the `amplifier-work-tracker` CLI, never `bd`
directly. That CLI owns the contention contract; reaching past it is how a
coordination layer stops coordinating.

## The tracker is optional, and says so

With no tracker installed the session receives its messages exactly as before, no
turn is blocked, and the absence is stated once in the session's own notice with
the command that fixes it. The probe keeps retrying, so a queue created
mid-session starts working.

The bundle never creates the tracker project. Provisioning a queue nobody asked
for is acting on someone's behalf.

> **Caveat that belongs on the picture, not in a footnote.** The notice is carried
> on `HookResult.user_message`. In the stack verified for `teamwork-4q7`, nothing
> renders that field under `amplifier run`. The absence is reported *in-process*
> and is not yet demonstrated as user-visible.

## What is NOT designed yet

**How the portal presents work-tracker state.** One direction exists today: a
filed report is mirrored forward as a `request.upsert` with a derived id
(`inbound-<message-id>`), the receiving person as `requested_person_id`, and
`evidence_refs` pointing back at the message. It is forward-only. Nothing reports
what happened to that item afterwards — claimed, resolved, declined — and nothing
tells the shared project whether a participant has a queue at all.

Designing the rest requires **looking at the portal**, not inferring it from
record shapes, and it needs two prior decisions that are open:

- `teamwork-8a4` — the two enrollment paths mint different scopes, and mirroring
  needs `shared:write`. A feature that silently works for one enrollment path and
  not the other is worse than one that does not ship.
- `teamwork-bc3` — the connection task stays `accepted` after its verification
  succeeds, so the board and the scoreboard already disagree about one fact. That
  needs settling before more state is projected onto the same board.

Three principles the design should honour when it is written, all inherited rather
than invented: the local queue is authoritative for its own items and the portal
shows a projection; only work with shared-project provenance is eligible to be
projected, because a person's private backlog is not the team's business; and
status is mirrored while content is not.
