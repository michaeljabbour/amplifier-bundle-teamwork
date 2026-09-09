# Teamwork for Amplifier

Teamwork is an **explicit, per-session opt-in** Amplifier bundle for sharing one selected project's visible conversation and bounded project context. It does not install a central agent, publish tool/internal-loop output, claim tasks, or change Amplifier's defaults.

## Before you enroll

When you run an enabled Teamwork overlay, the selected project can receive:

- visible prompts and final responses;
- session metadata and stable correlation IDs; and
- receipts for bounded, derived project-context excerpts that Amplifier's context manager accepted before a turn.

Credential-shaped strings are redacted with patterns before they are stored or sent. Pattern redaction is **not** a guarantee that arbitrary secrets or sensitive prose will be detected. Do not opt in a session that contains secrets or content you do not intend to share.

## Prerequisites

Use macOS, Linux, or WSL with Python 3.11+, Git, and [uv](https://docs.astral.sh/uv/). You also need an existing, configured Amplifier installation and a provider you can already use.

If Amplifier is not installed yet, its normal first-time setup is:

```sh
uv tool install git+https://github.com/microsoft/amplifier
amplifier init
amplifier --version
```

`amplifier init` configures providers and routing. If Amplifier is already working, do not reinstall or reconfigure it for Teamwork.

## Connect one project

### 1. Keep a persistent local checkout

Clone the public, canonical repository to a location that will remain present while you use its generated overlays:

```sh
TEAMWORK_CHECKOUT="$HOME/.local/share/amplifier/teamwork"
git clone https://github.com/michaeljabbour/amplifier-bundle-teamwork "$TEAMWORK_CHECKOUT"
cd "$TEAMWORK_CHECKOUT"
```

The generated overlay uses this checkout as a local hook source. Do not move or delete the checkout while an overlay made from it is in use.

### 2. Identify the base bundle you actually use

These commands are read-only source-discovery aids:

```sh
amplifier bundle current
amplifier bundle show <name>
```

Use their output to preserve your actual base bundle. Do not guess a registry alias or substitute a generic bundle name. For portable local setup, supply an **existing absolute filesystem path** to that bundle (for example, `/absolute/path/to/my-bundle.yaml`); an explicit Git URI is also valid only when you intentionally want the remote source resolved.

### 3. Enroll and create project-private files

Sign in at [team.amplifier.run](https://team.amplifier.run) with your name/email and private member code, and select the project you were added to. Then choose a project-specific private directory outside the checkout. The explicit paths below avoid relying on any installer default:

```sh
BASE_BUNDLE="/absolute/path/to/your/existing/bundle.yaml"
TEAMWORK_HOME="$HOME/.config/amplifier-teamwork/teamwork"
mkdir -p "$TEAMWORK_HOME"

python3 setup_teamwork.py \
  --project teamwork \
  --bundle "$BASE_BUNDLE" \
  --connection-file "$TEAMWORK_HOME/connection.json" \
  --output "$TEAMWORK_HOME/teamwork-overlay.yaml"
```

The script asks for the member code without saving it, enrolls a separate project-scoped harness credential, writes a private connection file, and writes the overlay. Existing output files are not overwritten. Keep the connection file, SQLite journal, and overlay outside the repository and out of source control.

By default enrollment uses `https://team.amplifier.run`. You may explicitly select a trusted custom HTTPS service URL, but that does **not** establish that the service is Teamwork-compatible. Deceptive URLs containing userinfo, a query, or a fragment are rejected. HTTP is for literal loopback test hosts only (`localhost`, `127.0.0.1`, or `::1`), never a production service.

### 4. Start a new opted-in session

Setup prints the exact file URI to use. Copy that URI into a **new** session, for example:

```sh
amplifier run --bundle file:///absolute/path/to/teamwork-overlay.yaml
```

Use the URI setup printed rather than a bare path: the CLI may interpret a bare path as a bundle name. This command does not run `amplifier bundle add`, `amplifier bundle use`, change an app setting, or change a default bundle. A normal future run that omits this overlay does not itself enable Teamwork; Teamwork makes no claim about unrelated pre-existing defaults.

Ask a short, non-sensitive project prompt and confirm the expected project activity. Each participant must enroll and opt in on their own machine.

## What is shared and retained

The hook responds only to visible lifecycle events: `session:start`, `prompt:submit`, `prompt:complete`, and `session:end`. Child/delegated sessions are excluded.

Before a visible prompt, it flushes durable pending work, retrieves selected project context, derives a bounded excerpt, and awaits Amplifier context acceptance. Only that successful acceptance can produce a `harness_input_accepted` receipt. A receipt proves the context manager accepted a message; it does not prove provider submission, model attention, comprehension, agreement, or completion.

A private SQLite journal records selected shared turn data, synchronization state, and its durable outbox before requests are sent. The context cache is sanitized before it is persisted: recognized credential strings are redacted while the source-content hash is retained for correlation. This is a logical scrub of the current cache, not an assertion that backups, exported copies, or old SQLite pages have been erased.

Requests use stable idempotency keys. Delivery order is retained; a conflict blocks later work until you reconcile it. A visible prompt or response over the sharing limit is omitted. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the exact delivery and acceptance boundary.

## Recovery, updates, and removal

### Replay queued work

The replay helper is a standalone standard-library Python path. Give it the specific private connection file:

```sh
python3 replay_pending.py \
  --connection-file "$TEAMWORK_HOME/connection.json"
```

It reports pending requests, blocked HTTP statuses, and `acceptance_unknown` outcomes without printing content or credentials. `acceptance_unknown` means interruption left uncertainty between input attachment and durable receipt recording. Reconcile it from the private journal; replay never turns it into an invented receipt. Conflicts and revoked or expired credentials likewise require explicit reconciliation.

### Update the local checkout

Overlays point at the local checkout, so update a reviewed revision deliberately:

1. Stop sessions launched with the overlay.
2. Replay and reconcile pending work before changing the checkout.
3. Update this checkout to the reviewed revision, then start a new overlay session using the printed file URI.

A Git URL alone does not pin the nested hook module. If remote composition is necessary, pin both the behavior include and the hook source to the **same reviewed full commit SHA**. The local-checkout setup above is the recommended route.

```yaml
# Template only — replace both placeholders with one reviewed full commit SHA.
includes:
  - bundle: git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@<REVIEWED_FULL_COMMIT_SHA>#subdirectory=behaviors/teamwork.yaml
hooks:
  - module: hooks-teamwork
    source: git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@<REVIEWED_FULL_COMMIT_SHA>#subdirectory=modules/hooks-teamwork
    config:
      connection_file: /absolute/path/outside/the-checkout/connection.json
      share_visible_turns: true
```

This template is not copy-ready: replace both placeholders only with the same published, reviewed revision that contains the required fixes. No current reviewed revision is implied here.

### Retire an enrollment

Stop overlay sessions, then replay and reconcile their queued work. Revoke the harness in the Teamwork service portal, and **only then** remove the connection file, overlay, journal, and checkout if no longer needed. Deleting local files does not revoke a harness credential.

## Local validation and limits

For the `uv tool install` route, use uv's tool environment rather than deriving a Python executable from a wrapper shebang:

```sh
AMP_PY="$(uv tool dir)/amplifier/bin/python"
"$AMP_PY" -m unittest discover -s tests -v
"$AMP_PY" scripts/validate_bundle.py
```

For another installation method, set `AMPLIFIER_PY` to the known Python executable from that installation's Amplifier environment:

```sh
AMPLIFIER_PY="/path/to/amplifier-environment/bin/python"
AMP_PY="$AMPLIFIER_PY"
"$AMP_PY" -m unittest discover -s tests -v
"$AMP_PY" scripts/validate_bundle.py
```

Current and historical validation evidence is in [`docs/VALIDATION.md`](docs/VALIDATION.md). A clean current CLI smoke and an external-service smoke remain unverified. The current local-fixture checks do not prove a live Teamwork service, an external provider, another participant's machine, or every custom orchestrator. The bundle is intentionally narrow and is not a compatibility promise for every custom orchestrator.

## Bundle structure

- `bundle.md` is the Foundation-based entry point.
- `behaviors/teamwork.yaml` adds an explicitly disabled hook behavior.
- `modules/hooks-teamwork/` is the independently packaged hook; it requires Python 3.11+ and uses host-supplied `amplifier-core` for lifecycle integration.
- `setup_teamwork.py` composes the selected base bundle with an enrolled, enabled local overlay.

No service databases, transcripts, member codes, provider keys, private connection files, journals, or local validation artifacts belong in this repository. See [`docs/PUBLISHING.md`](docs/PUBLISHING.md) for release checks.
