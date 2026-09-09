# Validation evidence and limits

Validated 2026-09-09 using an installed Amplifier CLI environment (Foundation 1.0.0, core 1.6.1).

- Sixteen deterministic tests passed: observed input before receipt, failed input without receipt, durable retry identity across restart, redaction, child-session exclusion, explicit opt-in/disabled mount behavior, authenticated redirect refusal, private setup outputs, existing-file preservation, rollback/revocation after a simulated local write failure, and interruption after input acceptance but before receipt commit with explicit unknown-outcome recovery.
- `scripts/validate_bundle.py` passed actual Foundation schema loading, behavior composition, and local hook preparation without network enrollment or a model call.
- The module wheel built with Hatchling; its packaged hook imported with the real host peer and its `amplifier.modules` entry point resolved. This is packaging evidence, not provider proof.
- A separate isolated real Amplifier CLI session returned the injected synthetic project's exact goal. The service stored one visible prompt/response pair with harness provenance and a context-input delivery receipt. The outbox drained to zero. Its credential was revoked and the synthetic project was removed; the original project and interviews were unchanged. The selected runtime provider/model was `anthropic/claude-fable-5-1`.
- The first isolated attempt published its turn but did not deliver context after early transport failures. It was retained as a failed attempt. A direct scoped API diagnostic succeeded; increasing the request timeout from 8 to 30 seconds was followed by the successful isolated rerun above. Transient network failures remain recoverable through the durable outbox; this does not promise an injection on every failed-network turn.

The private operator evidence contains session and project IDs and remains outside this public repository. No participant profiles, interview transcripts, credentials, raw CLI logs or local user paths are published here.

The expert review and resolution are recorded separately in `expert-review.md` and `REVIEW-RESOLUTION.md`. Other participants' computers, custom orchestrators and combined third-party bundle behavior require their own acceptance runs. A receipt proves input attachment only; it does not establish comprehension, consensus or independently validated expertise.
