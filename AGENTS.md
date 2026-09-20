# Teamwork bundle conventions

## Required validation

Run these from an Amplifier Python environment before calling a change done:

```sh
/path/to/amplifier-environment/bin/python -m unittest discover -s tests -v
/path/to/amplifier-environment/bin/python scripts/validate_bundle.py
```

For hook packaging changes, also build and import-check the Hatchling wheel. For setup,
connection, or lifecycle changes, run a bounded isolated live session; unit tests do not
prove provider behavior. Record only public-safe evidence.

## Boundaries and pitfalls

- Sharing is disabled by default. Enable it only through an explicit enrolled overlay or native browser consent;
  never replace the primary bundle/provider or silently enroll a user. Adding the
  inert behavior with `bundle add --app` is the documented native installation.
- Keep each enrollment project-scoped and keep connection/overlay outputs separate.
  Existing private output files are never overwritten.
- `amplifier-core` is host-supplied; do not add it as a runtime dependency or add
  `tool.uv.sources` wiring.
- Never commit credentials, member codes, connection files, SQLite journals, service
  databases, transcripts, raw CLI logs, local paths, or private validation evidence.

## The trust boundary moves when you publish a handoff

Every defect found in the detection-event review (`d453dc8`, `4168468`, `537cce0`) was
one mistake wearing different hats: an internal handoff became a **public event**, and
its payload kept being treated as if it were still private. A hook bus is shared,
untrusted and mutable — anyone composed into the session can emit on it, anyone can
change what passes, and anything on it is effectively logged. Check these every time a
value crosses out of the function that produced it:

- **Anyone can emit your event.** Receiving `teamwork:decision_detected` is not evidence
  your detector sent it. Match the carrier against something you issued (the
  `_detected_candidates` handshake), and require the event name and the payload's `kind`
  to agree. An unsolicited carrier must publish nothing.
- **The payload is shared and mutable.** A subscriber that runs before you can rewrite
  the claim, or replay a carrier you already handled. Hand subscribers a copy and act on
  the canonical value you kept.
- **Scrub before the first await, while the original credential is still bound.** Run
  free text through `clean_json` at entry, not at publish time.
- **Re-check the binding after every await, by identity.** Pin `sid`, `client` and
  `connection` and compare the objects. A generation counter is not enough: rebinding
  away and back restores the numbers, and a stale verdict must still not publish.
- **A truncated read is not a complete read.** Paged lookups return whether they
  finished; refuse on an incomplete page set even when one candidate matched, because the
  match that would have made it ambiguous may be on a page you never read.
- **Never log payload text.** No `exc_info` on a path carrying claims, questions or
  evidence.
- **Keep uncertain delivery uncertain.** Preserve the attempted record id and report the
  ambiguous outcome; never collapse "may have landed" into success or refusal.

## Review your own change through these lenses, every time

Before asking for review, run the change past the three questions that produced the list
above — they are cheap, and each one has already caught a real defect here:

1. **Who else can reach this?** New event, capability, tool or public method: name every
   caller that is now possible, not just the one you wrote.
2. **What survives an await?** List the awaits on the path, and for each say what could
   have changed underneath — binding, credential, state, the record you read.
3. **What does absence mean?** For every empty result, short read or silent skip, say how
   a caller tells "nothing there" from "I could not look". If it cannot, say so in the
   result.

A green suite does not answer any of these. It also does not prove the file is
well-formed: a mistaken edit once duplicated ~170 lines inside a class and every test
still passed, because duplicate definitions shadow each other.

## Publication gate

Before publishing, inspect every staged path for private data, run the required
validation, resolve expert-review blockers, and verify the repository URL, visibility,
commit, and remote behavior source. Publish only to the agreed owner and visibility.

## Done

A typical documentation or bundle-structure change is done when both required commands
pass, `bundle.dot` and `bundle.png` are fresh, behavior remains explicitly opt-in, and
no private artifact is staged.
