# Inbound messages as queued work

A message from another agent in your shared project does not run anything in your
session. It arrives as data in that turn's excerpt, exactly as it always has. If
this machine also runs a local work queue, the same message is additionally
**filed there as a report**: the sender's words, attributed and unedited, sitting
in your own backlog until you decide what to do with it.

Nothing runs because a stranger asked. Delivery is not agreement and not action.

## Reports and issues are different objects

This is [amplifier-work-tracker][wt]'s own distinction, and the whole consent
model rests on it.

| | A **report** | An **issue** |
|---|---|---|
| Whose words | The sender's, verbatim | Yours |
| Who wrote it | The harness, on their behalf | Your triage step |
| What it commits you to | Nothing | What you wrote in it |

An inbound message is always a report. It becomes an issue only if **you** decide
so, by writing a separate item — and the two are linked rather than merged, so the
sender's words are never edited into a spec they did not write.

Declining is a complete outcome. Resolve the report saying so.

## You do not need any of this

The queue is optional. With no work tracker installed, a session receives its
messages exactly as before, no turn is blocked, and the absence is stated once in
the session's own arrival notice:

```
1 inbound message(s) reached this turn but no local work queue took them: no
amplifier-work-tracker command is installed on this machine. They remain in the
shared project; nothing was queued on this machine.
```

That is a status line, not an error. The probe is retried on later turns, so a
queue you set up mid-session starts working without restarting anything.

## Setting one up, from a clean machine

**1. Install the tracker.** Follow the install instructions in
[microsoft/amplifier-work-tracker][wt]. It brings its own `bd` (Beads) and Dolt.

**2. Prove the install behaves as this bundle assumes.**

```sh
amplifier-work-tracker doctor
```

`doctor` runs the tracker's contract suite — executable assertions of every
behaviour it depends on, checked against the live binary. Run it again after any
`bd` upgrade. It is slow; `--quick` skips the concurrency check and proves less.

**3. Install the background service.**

```sh
amplifier-work-tracker service install
amplifier-work-tracker service status
```

This runs the shared Dolt server plus the sweeps. The sweeps matter here: an
agent that claims a report and then dies keeps holding it until `reap` reclaims
it, and **an unrenewed hold is only reclaim-eligible, not reclaimed** — where no
sweep runs, a dead hold persists indefinitely.

**4. Create the queue for your project, under the name the bundle will look for.**

The name is derived from your teamwork project id by a published rule (below).
For a project id of `design-review`:

```sh
amplifier-work-tracker new design_review
```

The bundle does **not** create this for you. Creating a queue nobody asked for is
provisioning infrastructure on someone's behalf; when the project is missing, the
notice names the exact command instead.

**5. Start a session bound to that teamwork project.** Filing is on by default
once both halves exist. Nothing else to configure.

> **Do not use `--root` to keep an experiment away from a real queue.** It changes
> only the local project directory — the Dolt server is global. `new --root
> /tmp/... <name>` for a name that already exists on the server *adopts* that
> project, re-identifies it, and leaves the original checkout unable to write to
> it. Reads keep working, so you find out at the first write. (Recorded as
> `teamwork-vbf`.)

## Which queue: the name rule, and the refusal

Work-tracker project names must match `^[a-z][a-z0-9_]{1,30}$`. Teamwork project
ids need not, so the mapping is lossy — and lossy mappings collide.
`design-review`, `design_review` and `Design Review` all reduce to the same name.

So `queue_name.normalise()` is **published**, not private: both sides apply the
same rule independently, and a rule only one side knows is not a join. The result
is then **remembered** in a small registry (`queue-names.json`, mode 0600, beside
your connection file). The first project to claim a tracker name keeps it, and a
second project that would take the same name is **refused**, loudly, rather than
merged into it:

```
Tracker project 'design_review' is already bound to teamwork project
'https://team.example/design-review', so 'https://team.example/design_review'
cannot use it. Choose an explicit queue name for one of them rather than sharing
a backlog.
```

Identity is the **service and the id together**. A project id is unique only
within a service, so two deployments that both call a project `teamwork` are two
different projects with different members — and quietly handing them one backlog
would be the same failure arriving by a different door.

A refusal files nothing. It is reported in the arrival notice like any other
absence, because a naming clash between two projects must not be the thing that
breaks a turn.

## What a filed report looks like

```
TITLE:  Message from Molly: CANARY-INDIGO-7731 Please add jitter to the relay…
STATUS: open

Filed by the Amplifier Teamwork harness from a message addressed to this
session. It arrived as data. Nothing was executed and nothing was agreed.

  Sender: Molly (person bc32f2c8-…)
  Sending harness: f4d7bc74-…
  Sent at: 2026-09-10T22:19:35.573230+00:00
  Shared project: relay-probe
  Message id: d2acf41c-…
  Delivered to agent: 6f5276e9-…

The sender's words, verbatim as this harness received them (credential-shaped
strings are redacted before anything is stored, and nothing else is altered):

----- begin message -----
CANARY-INDIGO-7731 Please add jitter to the relay backoff before Thursday; we
saw three consecutive drops in prod and the retries all landed together.
----- end message -----
```

The **title is derived** and may be shortened; the words between the fences are
not. Attribution comes from what the service recorded — it stamps `from_person_id`
and `from_harness_id` from the credential and refuses to take either from the
client, so a message cannot be filed under someone else's name. The service
records no sending *agent* id, so a report attributes to a person and a harness
and stops there. A sender whose person record was not delivered is identified by
id rather than by an invented name.

## Triaging one

Claim it the way you claim anything else — **`bd ready --claim` / `work_claim`,
never read-then-claim**, which double-claims silently under contention with every
loser exiting 0.

Then, if it should become work, write a separate item and link it:

```sh
amplifier-work-tracker add --project design_review "Jitter the relay backoff" \
    --description "Our scope, our words." \
    --acceptance "Given three consecutive drops, When the relay retries, Then attempts are jittered."
amplifier-work-tracker dep --project design_review --id <new-id> \
    --depends-on <report-id> --type discovered-from
```

`discovered-from` is non-blocking: the new item is ready immediately and the
report stays readable beside it. From a session that holds the report,
`work_file` does the same thing in one call.

The report itself is never edited. Verified: its description is byte-identical
before and after triage.

## Configuration

All optional. Set under the hook's module config.

| Key | Default | What it does |
|---|---|---|
| `file_inbound_reports` | `true` | Set `false` to never file, even with a tracker present |
| `work_tracker_command` | `amplifier-work-tracker` | The CLI to run |
| `work_tracker_root` | the CLI's own default | Passed through as `--root` (read the warning above) |
| `queue_registry_path` | `<connection dir>/queue-names.json` | Where the name binding is remembered |

Every write goes through the `amplifier-work-tracker` CLI, never `bd` directly.
That CLI is the sanctioned seam and owns the contention/retry contract; reaching
past it to Beads is how a coordination layer stops coordinating.

## Bounds, and the two states that are not success

Filing is **bounded per turn** — at most five messages, each with its own timeout
— so a burst of inbound mail cannot stretch one prompt. The remainder is filed on
a later turn; a report delivered late is still true, unlike a replayed heartbeat.

Two outcomes are deliberately not reported as success:

**Too large to file whole.** A body over the filing limit is refused, not
shortened. A truncated body is no longer the sender's words, and filing it as
though it were is the one thing this path must never do.

**`acceptance_unknown`.** A failed write does not prove the write failed — that is
the tracker's own stated contention contract. So an ambiguous `add` is followed by
a read-back that looks for the message id in existing descriptions. If the read-back
cannot *prove* absence (a truncated listing says nothing about what it did not
show), the message is marked unknown and **not retried**, and the notice names it
for a human to check. A blind retry would duplicate a teammate's request; dropping
it would lose one. Neither is acceptable, so the ambiguity is carried and named —
the same shape, and the same reasoning, as the injection path's
`acceptance_unknown`.

[wt]: https://github.com/microsoft/amplifier-work-tracker
