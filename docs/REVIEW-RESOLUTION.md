# Expert review resolution

The bundle was reviewed through a real Amplifier CLI session driven by Forge, with `amplifier:amplifier-expert` and `foundation:foundation-expert`. This resolution records implementation-owner verification of the reported findings; it does not turn an offline expert review into a live-provider claim.

| Finding | Resolution and verification |
| --- | --- |
| Automatic hostname disclosed during enrollment | Removed machine discovery. The default label is `Amplifier harness`; an optional `--label` is explicitly supplied by the participant. Enrollment test checks the generic label. |
| Remote credential issued before local destinations were secured | Both destinations are expanded, parent directories created, and private files reserved exclusively before any remote call. Existing output prevents enrollment. A later write failure revokes the newly issued credential and removes only this attempt's files; failed revocation leaves recovery information and a harness ID. Failure-injection tests verify preservation and rollback. |
| Disabled hook looked like a module-load failure | Explicit `false` now mounts as a successful no-op. Missing/invalid opt-in remains a hard error; enabled mode still requires private credentials. Both paths have tests. |
| Validation link missing during extraction | Added `VALIDATION.md` with separate test, package, local-loader, live-provider and remaining participant boundaries. |
| Crash after input attachment could discard acceptance uncertainty | The exact prepared input survives in private journal state as `acceptance_unknown` when durable acceptance was not committed. Closing an interrupted turn retains it. Replay reports unresolved candidates, including before resume. No speculative receipt or accepted injection is emitted. A fault-injection test interrupts after context attachment and before receipt persistence, then recreates the hook from the journal. |

Additional checks cover child-session exclusion before credentials are read, authenticated redirect refusal, private file permissions, and absence of login codes/tokens from overlays. Sixteen deterministic tests and actual Foundation loading/preparation passed after these resolutions. The separate live run and its earlier failed transport attempt are documented in `VALIDATION.md`.

Release readiness is for the reviewed source and one local installation. Each original participant must still enroll and produce their own new attributable session, context-input receipt and visible prompt/response pair. This repository includes no participant-specific evidence or private transcripts.
