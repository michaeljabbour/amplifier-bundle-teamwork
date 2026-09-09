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

## Publication gate

Before publishing, inspect every staged path for private data, run the required
validation, resolve expert-review blockers, and verify the repository URL, visibility,
commit, and remote behavior source. Publish only to the agreed owner and visibility.

## Done

A typical documentation or bundle-structure change is done when both required commands
pass, `bundle.dot` and `bundle.png` are fresh, behavior remains explicitly opt-in, and
no private artifact is staged.
