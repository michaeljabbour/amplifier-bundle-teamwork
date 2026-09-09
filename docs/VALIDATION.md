# Validation evidence and limits

**Recorded 2026-09-09.** This page separates current, local evidence from an earlier historical live attestation. It does not claim a current external Teamwork-service or provider success.

## Current independent checks

- **Fresh-home test suite:** all 30 tests passed independently with a fresh `HOME`. The fixture drives real loopback HTTP through setup login and harness enrollment, verifies the requested scopes, authenticated requests, `Origin` handling, and private output modes, and exercises compensating harness revocation.
- **Hook and recovery boundaries:** tests perform real hook mount registration; verify context acceptance happens before an acknowledgement and that the acknowledgement is linked to the accepted identity; and verify a `503` leaves durable work that standalone replay submits with the identical idempotency key and body. The replay path was run with `python -I -S` and without `amplifier_core`. Session completion is also covered.
- **Security and regression cases:** baseline-versus-fixed checks cover the mount return contract, standalone replay, cache sanitization before persistence, deceptive service URLs, and default output handling. They demonstrate the specified behavior in controlled fixtures; they do not make a claim about arbitrary services or user content.
- **Wheel isolation:** a wheel was freshly built with `uv`, installed with `--no-deps` into an empty virtual environment, and imported there. An enabled mount was exercised without `amplifier_core`, confirming the package keeps the host-only lifecycle dependency out of its standalone import path.
- **Foundation composition:** the Foundation validator independently **PASSED** in a macOS sandbox with deny-all-network policy, a fresh home/XDG/cache, and no remote/cache reuse. It used an explicit local include registry and temporary minimal base bundle, verified preservation of session/context configuration, and prepared only the local hook. It also created fresh 145-byte local install-state metadata.
- **Expert review:** structural, code-contract, and security reviews reported no remaining blockers; the conformance review reported no findings. These reviews do not close the runtime integration gate.

## Bounded CLI integration result

A first partial fixture run from a fresh CLI installation of the public `amplifier` package (version `2026.09.09-fee2529`, Core `1.6.1`) reached a synthetic streaming OpenAI loopback once. The loopback observed the Teamwork context marker in the provider request and observed a receipt, final turn, and completed session. That run then exited with status 1, reporting `Session not found`, after the completion events.

A latest normal-Foundation follow-up recognized the selection but the module failed to mount and reported `Error: No providers available`. It exited 0 but made zero completion, context, or acknowledgement requests. Neither run is a clean CLI smoke pass, and neither is evidence of an external live service or provider integration. The runtime result remains unresolved; no further CLI run is planned in this task.

## Historical, pre-change attestation

An earlier isolated live Amplifier CLI run on the same date, documented before the changes covered by this page, recorded a visible prompt/response pair, harness provenance, a context-input receipt, an empty outbox, and credential revocation. That is historical evidence only; it has not been re-verified against the current source and must not be read as current validation.

## Remaining release gate

Before release, run an authorized fresh test against a real Teamwork service and provider, and record only public-safe outcome evidence. Also verify a fresh remote composition resolves the exact reviewed behavior include and nested hook source at the intended pinned revision. The current local checks cannot establish another participant's installation, a custom orchestrator, an arbitrary custom HTTPS service, or a live external provider/service. A Digital Twin Universe attempt was blocked by host capacity before launch; no container source verification occurred in this task. The temporary Gitea environment was torn down and supplies no release evidence.

The private operator evidence remains outside this public repository. No session or project IDs, local paths, usernames, credentials, fixture payloads, raw logs, participant profiles, or interview transcripts are published here.
