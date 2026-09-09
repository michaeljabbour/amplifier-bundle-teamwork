# Amplifier expert review

Date: 2026-09-09. This report summarizes the completed responses from two named experts invoked by a real Amplifier CLI session through Forge. It is compiled by the implementation owner; it is not an independent approval of later fixes. Raw terminal/session evidence stays with the operator because it contains local paths and operational metadata. The coordinating session was stopped after both expert responses had returned rather than continuing repeated source-readback delegation.

## Reviewers and scope

- `amplifier:amplifier-expert`: publication, enrollment, packaging and CLI composition.
- `foundation:foundation-expert`: bundle/behavior composition, hook contracts, context-input receipts, durable recovery and project/session scope.

Both performed source-based offline reviews. The Foundation expert also ran the then-current 14 tests and `scripts/validate_bundle.py` successfully against installed Foundation/core. Neither expert claimed live service, provider, remote-source or multi-machine verification.

## Findings at review time

The CLI expert identified two release blockers: automatic hostname disclosure in the enrollment label, and issuing a harness credential before ensuring local connection/overlay files could be written. It recommended a generic label plus explicit optional labeling, preflighted destinations, safe writes and recovery/revocation on a failed enrollment write. It also identified the disabled hook's expected error path and a validation document that had not yet been added during extraction.

The Foundation expert's verdict was **BLOCK** for a recovery gap: a process could stop after `context.add_message` succeeded but before receipt persistence. The stored prepared injection was then discarded when closing the interrupted turn, losing the distinction between rejected and uncertain acceptance. It required preserving that candidate as `acceptance_unknown` without manufacturing a receipt, plus a test for interruption after attachment and before durable commit.

## Confirmed properties

The experts confirmed the narrow behavior composition, separate module packaging and entry point, host-provided core dependency, explicit sharing opt-in, early child-session exclusion, normal input-before-receipt ordering, durable request-key reuse, and the documented distinction between context-manager acceptance and model comprehension. The disabled-hook criticism was stale by the Foundation expert's later read: explicit disabled mode had already become a successful no-op.

## Resolution boundary

The reported blockers were corrected after or during review. `REVIEW-RESOLUTION.md` maps each finding to the fix and verification. The implementation owner ran the resulting 16-test suite, bundle loading/preparation and wheel checks. These owner checks resolve the findings but are not represented as a second expert approval. Separate real-provider acceptance is documented in `VALIDATION.md`; original participants still require their own post-assignment installation/session evidence.
