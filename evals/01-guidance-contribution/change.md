# Change: the guidance layer (WITH vs WITHOUT)

## Summary

Unlike `examples/01-explorer-removal/change.md` in the evaluation bundle
(which documents a destructive edit made FOR the eval), this eval's
independent variable already exists as two real branches of this repo. No
edit is made here; this file documents what the diff between them actually
is, so the eval's WITH/WITHOUT claim can be checked against real content
rather than taken on faith.

- **WITHOUT** = `main` branch: the teamwork bundle with its hook and tools
  only. No context, no skill.
- **WITH** = `skill-wiring` branch: `main` plus a context pointer and a
  skill, wired into `behaviors/teamwork.yaml`.

## The actual diff (`git diff --stat main skill-wiring`, captured 2026-09-16)

```
 behaviors/teamwork.yaml                            |  23 +++
 context/teamwork-awareness.md                      |  25 ++++
 .../06-record-the-lesson-not-the-incident.md       | 144 +++++++++++++++++++
 .../07-replace-by-addition-never-by-erasure.md     | 118 +++++++++++++++
 docs/scenarios/README.md                           |  32 +++++
 skills/teamwork-protocol/SKILL.md                  | 160 +++++++++++++++++++++
 6 files changed, 502 insertions(+)
```

Scenarios 06/07 and the scenarios README are new documentation on the WITH
branch not otherwise relevant to this eval (they document a different,
not-yet-buildable capability -- see `context/teamwork-awareness.md`'s
provenance table). The three files that matter for THIS eval's independent
variable:

- `context/teamwork-awareness.md` -- a ~250-token always-on pointer: "you may
  be enrolled", what the signal is, when to load the skill.
- `skills/teamwork-protocol/SKILL.md` -- the actual rules, loaded on demand.
  Its "Who to ask" section is a DIRECT restatement of
  `docs/scenarios/01-ask-the-expert-not-the-owner.md` and
  `docs/scenarios/02-when-the-owner-is-the-expert.md`, including the
  "routing to the owner by default converts one person into a switchboard"
  line quoted in both scenarios and both task graders.
- `behaviors/teamwork.yaml` -- wires the above two in: adds a `tool-skills`
  config entry pointing at `skills/` (WITH only), and a `context.include`
  entry for `../context/teamwork-awareness.md` (WITH only).

## Why this eval can trust that diff is the whole difference

The hook and tool modules (`modules/hooks-teamwork/`) are IDENTICAL on both
branches -- `git diff main skill-wiring -- modules/` is empty (verified
2026-09-16). `teamwork_send`'s behavior, the person-field projection
(`PERSON_FIELDS` in `modules/hooks-teamwork/amplifier_module_hooks_teamwork/__init__.py`),
and the context-excerpt rendering are all mechanism, not the thing under
test. Only the guidance layer (context + skill + the one wiring block) is
the independent variable.
