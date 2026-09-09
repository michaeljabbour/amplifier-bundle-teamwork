# Teamwork for Amplifier

This composable Amplifier bundle shares one explicitly selected project's visible user prompts, final responses, session IDs, and actual shared-context injections. It retrieves project context and records the observed Amplifier context-input acceptance boundary. It never runs a central agent or publishes tool/internal-loop dumps. It does not accept tasks or publish insights automatically. Delegated child sessions are excluded so internal agent instructions are not treated as human-visible conversation.

**Verified on one real local Amplifier CLI session on 2026-09-09:** the configured provider returned the shared project's exact goal; the service stored a prompt/response pair with correct harness origin and a delivery receipt, and the local outbox drained. The temporary test credential was revoked. Other participants' machines have not been verified by this test.

## Connect your own installation

1. Sign in at **https://team.amplifier.run** using your name/email and private member code. Choose the project you were added to. New projects do not inherit your other interviews or profile context.
2. Clone the bundle (`git clone https://github.com/michaeljabbour/amplifier-bundle-teamwork.git`) and `cd amplifier-bundle-teamwork`, or download/extract the Teamwork adapter archive. Ensure your existing Amplifier installation works. Run `amplifier bundle current` and `amplifier bundle show <your-bundle>` to find the bundle you already use; do not guess a bundle name.
3. From this bundle directory run:

   ```sh
   python3 setup_teamwork.py --project teamwork --bundle /absolute/path/to/your/existing/bundle
   ```

   The script prompts for name/email and a hidden member code, then enrolls a **separate project-scoped harness credential** with only context-read/session-write scopes. It stores that credential in a private mode-600 connection file and creates `teamwork-overlay.yaml`. The member code is not stored. The default harness label is generic; use `--label` for a label you choose. Setup reserves both private output files before enrollment and revokes the new harness if a later write fails. If revocation also fails, it reports the harness ID for recovery in the portal. The overlay includes only the connection file path, not a credential.
4. Start a **new** session explicitly using the generated overlay:

   ```sh
   amplifier run --bundle file:///absolute/path/to/teamwork-overlay.yaml
   ```

   Use the exact file URI printed by setup; the inspected CLI treats a bare filesystem path as a registry name. This opts that new session into sharing visible turns with the selected project. It changes neither the active/default bundle nor existing sessions. If you use another project, enroll a separate connection using `--project`, `--connection-file`, and `--output` to keep the credentials and overlay separate.
5. Ask a short non-sensitive prompt about the project. Confirm the actual session and prompt/response appear in the shared room. Other participants must each perform their own run; one local proof cannot establish five-machine acceptance.

Revoke the harness in the website's harness controls when finished. Existing connection/overlay files are never overwritten by setup. The credential expires after 30 days. For a new credential, enroll again to a new connection file; old queued work belongs to the old credential and needs reconciliation rather than blindly relabeling ownership.

## What the hook does

- Source-verified lifecycle events: `session:start`, `prompt:submit`, `prompt:complete`, `session:end`.
- On prompt submission, flushes durable pending requests and fetches project-scoped snapshot/delta context. It selects a bounded **derived excerpt** (up to 10,000 UTF-8 bytes) and calls the mounted context manager's async `add_message` method.
- Only after that await succeeds does it enqueue `harness_input_accepted` receipts. This is an observable harness boundary, not proof of provider submission, comprehension, or agreement. A failed input attachment produces no receipt.
- The excerpt is a persistent user-context message, not ephemeral provider-only injection. This deliberate choice provides a directly observable acceptance boundary. The context manager may later compact its provider view; repeated turns add bounded messages to history. Receipts do not claim immunity from compaction.
- A `prompt:complete` event links the original prompt, visible response, and exact injection under stable IDs. If a host never emits that event, final-response capture is unsupported for that host: an interrupted/incomplete turn remains explicit. The inspected CLI emits it after `session.execute` returns.
- A private local SQLite journal stores selected turn content and request keys before transmission. After interruption/restart, stable keys are reused. The hook uses only the enrolled project API, no global directory endpoints. One process should own a given native session at a time.
- Known credential-shaped strings and this connection's exact token are redacted before journal storage. This is not a comprehensive content classifier. Only opt in sessions whose visible prompts/responses you intend to share; never put secrets in project conversations.

To retry queued work from older closed sessions, run the replay helper using the Amplifier Python environment (where `amplifier_core` is installed):

```sh
python3 replay_pending.py --connection-file /path/to/private/connection.json
```

If your ordinary Python lacks `amplifier_core`, use the Python executable from your Amplifier environment. The helper prints pending counts and HTTP status, not content or credentials. It also reports `acceptance_unknown` inputs after an interrupted attachment/receipt commit; their exact candidate excerpts remain in the private journal for reconciliation, and are never converted into fabricated receipts. A 409/410 remains blocked for explicit reconciliation; it never fabricates an acknowledged delivery or silently overwrites a conflict.

## Evidence and limits

Focused adapter tests cover accepted-input-before-receipt ordering, input failure without receipt, durable retry-key reuse across hook restart, and credential redaction. Tests import the real installed `HookResult` type but use a fake context/client for deterministic boundary failures. The original extracted adapter passed a separate real Amplifier CLI acceptance run. See `docs/VALIDATION.md` for the standalone bundle validation and its limits; unit tests alone are not provider proof.

Source inspection used the installed `loop-streaming` implementation's `prompt:submit` event, `context-simple.add_message`, the CLI's `prompt:complete` emission, and the hook module entrypoint/mount convention. The adapter is a small candidate for those interfaces, not a claim of compatibility with every custom orchestrator. No user bundle name, provider/model, or remote participant installation is assumed.

## Bundle structure and development

- `bundle.md` is a thin Foundation-based entry point.
- `behaviors/teamwork.yaml` adds only the hook; it does not select a provider, replace an orchestrator, or add agent authority.
- `modules/hooks-teamwork/` is the independently packaged hook. Python 3.11+ is required. `amplifier-core` is a peer supplied by the Amplifier host; the hook has no third-party runtime dependencies of its own. Hatchling builds its wheel.
- `setup_teamwork.py` composes the behavior onto your chosen existing bundle and supplies an absolute local module path, so a checkout/archive works without publishing first.
- The behavior defaults to sharing disabled. Enrollment is the supported way to create an explicit opt-in overlay. Loading the bare root bundle does not enroll you or enable sharing.

Run deterministic tests with the Python executable from your working Amplifier environment:

```sh
/path/to/amplifier-environment/bin/python -m unittest discover -s tests -v
/path/to/amplifier-environment/bin/python scripts/validate_bundle.py
```

No service database, transcripts, login codes, provider keys, or operator artifacts belong in this repository. Connection files and SQLite journals live outside it by default. See `docs/PROTOCOL.md` for the API and delivery boundary and `docs/PUBLISHING.md` for publication checks.

To compose into an existing bundle manually after enrollment:

```yaml
includes:
  - bundle: git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@main#subdirectory=behaviors/teamwork.yaml
hooks:
  - module: hooks-teamwork
    config:
      connection_file: ~/.config/amplifier-teamwork/connection.json
      share_visible_turns: true
```

The hook source is independent of your bundle's directory. The setup-generated overlay uses the local checkout instead, which is useful for development and offline packaging. Use a reviewed commit SHA instead of `main` when you need a fixed revision.
