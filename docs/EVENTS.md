# Teamwork events

`hooks-teamwork` emits six named events on the kernel's hook bus, covering the
outcome of decision and lesson detection. The module contributes its catalogue to
the `observability.events` discovery channel, so consumers subscribe without
hard-coding names.

## Why these exist

Detection's only observable result used to be a successful publish:
`_judge_and_record` ends in a `RecordInsightTool` write, and every other outcome
went to a local SQLite table with no subscriber. Ten distinct signals about
whether the detector works, already computed, unreadable by anyone.

That is why "do we detect decisions?" had no answer. A mechanism whose failures
are invisible can only be said to have shipped, not to work.

These events do not change what is detected or what is published. They make the
outcomes that were always being computed observable by something other than a
person opening a SQLite file.

## The catalogue

| Event | Meaning |
|---|---|
| `teamwork:decision_recorded` | A decision was judged and written to the shared project. |
| `teamwork:decision_skipped` | The detector ran and deliberately produced nothing. |
| `teamwork:decision_failed` | The detector did not complete, or its write did not land. |
| `teamwork:lesson_recorded` | As above, for lesson detection. |
| `teamwork:lesson_skipped` | |
| `teamwork:lesson_failed` | |

Three categories per detector, because three is what a consumer does something
different about: something new exists, nothing was produced on purpose, or the
mechanism did not work.

### Payload

Identical for all six:

```python
{
    "session_id": str,          # the shared session id, not the native one
    "kind": str,                # "decision" | "lesson"
    "outcome": str,             # the raw outcome -- see the table below
    "tally_size": int | None,   # records considered, when known
    "link_count": int | None,   # evidence links on the record, when known
}
```

The raw `outcome` always travels, so categorising loses nothing: a dashboard can
count `*_failed`, and an audit can still read exactly which failure it was.

### Outcomes, and the category each maps to

| Outcome | Category | What happened |
|---|---|---|
| `recorded` | recorded | The insight was written to the shared project. |
| `skipped_deliberate` | skipped | The window was recorded by hand; automatic detection stood aside. |
| `skip_verdict` | skipped | The judge declined to record this window. |
| `unavailable` | failed | No provider was available for the judgment. |
| `no_claim` | skipped | The judge found nothing worth saying. |
| `duplicate_fingerprint` | skipped | Already recorded, or reserved by a concurrent attempt. |
| `binding_changed` | skipped | The session rebound to another project mid-detection. |
| `judge_failed` | failed | The judge call raised. |
| `judge_cancelled` | failed | The judge call was cancelled. |
| `unusable_shape` | failed | The judge returned a verdict that cannot be recorded. |
| `write_cancelled` | failed | The write was cancelled; its reservation is retained. |
| `write_raised` | failed | The write raised. |
| `refused` | failed | The service refused the record. |
| `acceptance_unknown` | failed | The write may or may not have landed, and we could not find out. |

`acceptance_unknown` is deliberately distinct from `refused`, for the same reason
the sharing path keeps that distinction: a blind retry after an ambiguous write
duplicates a record that already exists, and dropping it loses a real one.

## Consuming them

Discover the names rather than hard-coding them:

```python
contributions = await coordinator.collect_contributions("observability.events")
names = [name for names in contributions for name in names]
for name in [n for n in names if n.startswith("teamwork:")]:
    coordinator.hooks.register(name, self._on_teamwork_event)
```

`hook-context-intelligence` already does exactly this — it collects the channel,
unions it with its own catalogue, and registers a handler per discovered name. A
session running both bundles therefore captures these events with no change on
the consumer side.

## Guarantees, and the limits of them

- **Emission never breaks detection.** The journal write happens first, the emit
  is guarded, and a subscriber that raises is logged and ignored. A host whose
  coordinator offers no hook bus detects exactly as before.
- **One funnel.** Every outcome goes through `TeamworkHook.detection_outcome`,
  which journals *and* emits. A new outcome cannot be added that records locally
  and announces nothing — and a test parses this module's own call sites to
  prove the table still covers them.
- **Nothing is emitted that was not advertised.** An outcome missing from the
  table emits nothing rather than being guessed into a category; the test above
  is what stops that being silent.
- **Off is off.** Detection is opt-in (`detect_decisions`, `detect_lessons`) and
  default off. No detection means no outcomes and therefore no events.

**Not claimed:** that the detector's judgments are correct. These events report
what the detector did, not whether it was right to do it. At the time of writing,
decision detection had not been observed running end-to-end against the hosted
project — which is precisely the gap this surface exists to close.


Outcome observation is asynchronous and best effort. The local journal remains
the durable outcome record. At most 16 observer deliveries are in flight, each
with a one-second deadline; slow or failing subscribers cannot hold the detector's
publication lock. Shutdown drains deliveries for at most one second, then cancels
pending work. Subscriber exception text is never logged. `skip_verdict` is a
skipped outcome; provider `unavailable` is a failed outcome.
