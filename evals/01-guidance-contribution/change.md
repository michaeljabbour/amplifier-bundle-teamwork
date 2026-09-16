# Guidance arm provenance

The original design compared moving `main` (without guidance) and `skill-wiring`
(with guidance) in separate Gitea mirrors. That was an authoring-time snapshot,
not a permanent experimental control. At the start of review those branches also differed in runtime hook code.
Guidance has since merged into main, erasing the intended branch distinction.
The instrument therefore does not claim that the current branch diff isolates
guidance. Live execution is disabled.

The intended treatment consists of:

- `context/teamwork-awareness.md`, the always-on pointer;
- `skills/teamwork-protocol/SKILL.md`, the on-demand guidance;
- only the corresponding skill/context wiring in `behaviors/teamwork.yaml`.

Before trials can run, construct both arms from one immutable base and apply
only that reviewed treatment. Verify the hook, tools, setup, service and other
runtime inputs have identical bytes across arms. Record base and treatment
commit IDs, resulting trees, exact manifests and the expected wiring delta.
Do not treat scenarios or unrelated branch changes as experimental treatment.

The proposed task rubrics derive from scenarios 01 and 02. Their repeated,
calibrated results would test routing behavior; merely loading the skill or
passing one exploratory pair does not demonstrate a causal guidance effect.
See `README.md` for the secure-execution and integration gaps that remain.
