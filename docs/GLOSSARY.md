# Glossary

Words this project uses in a specific way, and one word it shares with Amplifier
while meaning something else entirely. That collision has already cost a design
decision, which is why this file exists.

---

## The one that matters: **agent**

**In Teamwork, an agent is what registers.** One enrolled app — an Amplifier CLI on
a laptop, or whatever else holds the credential. It has a harness credential, a node
label, declared skills, and liveness. It lives for the length of the enrollment:
weeks.

**In Amplifier, an agent is a persona spawned to do one task** — via `delegate` or
the task tool. It lives for the length of that task: minutes. It has no credential,
no registration, and no identity the project can see.

| | **Amplifier agent** | **Teamwork agent** |
| --- | --- | --- |
| What it is | a persona spawned for one task | an enrolled participant's app |
| Lifetime | the task | the enrollment |
| How many | as many as a session spawns | one per enrolled machine |
| Identity | a `parent_id` tag on events | a harness credential + node label |
| Visible to the project | no | yes — it *is* the participant |

**When this project says *agent*, it always means the second one.** `agent` and
`harness` name the same unit from two angles: `harness` is the credential it holds,
`agent` is how it appears on the project.

---

## What is inside an agent is **opaque** — on purpose

An enrolled app registers. **What happens inside it is not the project's business.**

It may spawn a dozen sub-agents, delegate across five of them, fork sessions, and
discard them all before the turn ends. Teamwork does not see any of that, does not
model it, and **must not start**. The unit of participation is the app, and the
inside of the app is a black box.

This is enforced rather than agreed. The hook reads `coordinator.parent_id` and
returns **before opening any credential**, so a spawned Amplifier agent cannot
register, publish, or be addressed — the question never arises.

**Why opacity is the right boundary, not just a convenient one:** it is the only one
that stays still. Modelling sub-agents would couple this project to one host's
internals — and those internals are free to change, are different in every host, and
are exactly the kind of thing that should be free to change. An app that enrolls is
a stable thing to name. What it does with its own turns is not.

The practical consequence, worth stating because it reads as a limitation and is
not: **you cannot address a sub-agent, and should never want to.** Address the
participant. What they delegate it to is theirs.

---

## The rest

**Harness** — the credential an enrolled app holds, project-scoped and revocable.
Minted at enrollment, carries `context:read` and `session:write`. Same unit as
*agent*, named for the credential rather than the identity.

**Member plane** — the endpoints a *person* uses: `/api/login`, `/api/harnesses`,
`/api/state`. Cookie auth, and `Origin` is required and gated. Pointing enrollment
at the wrong host gets `403 invalid_origin` *before* the credential is examined.

**Harness plane** — the endpoints the *hook* uses:
`/api/v1/projects/{id}/{publish,context,acknowledgements}`. Bearer auth, no
`Origin`. Conflating the two planes causes real bugs; they are listed side by side
in [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md).

**Session** — one conversation on one agent. Ephemeral by construction: it begins,
takes turns, ends. A *root* session is one with no parent — the only kind that can
be a Teamwork agent.

**Turn** — one visible prompt and its response, published as a pair after it
completes.

**Excerpt** — the bounded (≤10 KB) rendering of shared project context injected into
a session at `prompt:submit`. It is how everything the project knows reaches a
session: work, requests, people, messages, direction.

**Receipt** — the record that an excerpt was *accepted* by the session. Written only
after a successful attachment is observed, never before and never instead.

**`acceptance_unknown`** — the honest third state between accepted and rejected, for
when a session was interrupted between attaching an excerpt and committing the
receipt. It is a real state, not an error to be tidied away: losing it would make an
interruption indistinguishable from a refusal.

**Request** — a question or ask addressed to a named person. Carries `urgency`,
`desired_response` (`action` / `context` / `review`), and an answer as
`response` (`act` / `defer` / `context`) with a reason.

**Work item** — a durable unit of work, independent of any session. Unlike a
session, it survives: this is why a blocked question parks the *work* rather than
the session that found it.
