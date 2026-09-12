# Teamwork for Amplifier

Teamwork is an **explicit, per-session opt-in** Amplifier bundle for sharing one selected project's visible conversation and bounded project context. It does not install a central agent, publish tool/internal-loop output, claim tasks, or replace your primary bundle or provider.

## Before you enroll

After native connection consent, or when you run an enabled Teamwork overlay, the selected project can receive:

- visible prompts and final responses;
- session metadata and stable correlation IDs; and
- receipts for bounded, derived project-context excerpts that Amplifier's context manager accepted before a turn.

Credential-shaped strings are redacted with patterns before they are stored or sent. Pattern redaction is **not** a guarantee that arbitrary secrets or sensitive prose will be detected. Do not opt in a session that contains secrets or content you do not intend to share.

## Install natively

With an existing configured Amplifier installation, add the reusable behavior using the [Foundation bundle convention](https://github.com/microsoft/amplifier-foundation/blob/main/docs/BUNDLE_GUIDE.md):

```sh
amplifier bundle add "git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@main#subdirectory=behaviors/teamwork.yaml" --app
```

Amplifier installs the packaged modules. No separate Python command, pip install, clone, or project selection is required. `--app` adds the behavior to new sessions while preserving your primary bundle and provider. Installation alone does not read project context or enable sharing.

The URI must point to the actual `.yaml` behavior file (or a repository directory containing `bundle.md`/`bundle.yaml`), not a GitHub HTML page, archive download, or module directory. An `Unknown bundle format` error occurs before module mounting; retain the reported path when diagnosing it. The nested module source uses `@main`; pinning only the outer behavior does not pin both modules.

`@main` is resolved once and then served from Amplifier's bundle cache; it does not follow the branch. After this repository changes upstream, refresh the cache and start a new session:

```sh
amplifier bundle update teamwork -y
```

A session that is already running keeps the tool set it started with, so `teamwork_connect` appears only in sessions started after the update.

## Connect in Amplifier

1. Start a normal new Amplifier session with your existing bundle/provider, in a checkout with a `github.com` `origin` remote if you have one.
2. Ask: **Connect this session to Teamwork.** Amplifier invokes `teamwork_connect`, which opens a private local browser form.
3. The form supports three enrollment methods, tried in this order for whatever you fill in and submit:
   1. **Portal credential (current default path).** In Teamwork's web portal, open Account menu → Harnesses & agents, mint a harness credential (shown once), and paste it into the form's **Credential from the portal** field along with the exact project ID. This never touches `/api/login` or `az`; it is verified with the service directly and, if accepted, stored the same as any other enrollment. If it is rejected, or if the service cannot confirm it, the form re-renders asking to try again. If a connection is already saved for that project, the new credential is not used -- an existing saved connection is never overwritten -- and the form tells you so explicitly, with how to rotate it.
   2. **Microsoft sign-in (`az login`), when the service advertises it.** If you have run `az login` and left both the credential and member-code fields blank, the form offers Microsoft sign-in. When this directory has a usable GitHub remote, the form shows the exact repository URL that will be sent and the project ID becomes optional -- Teamwork resolves the project from that repository. **This path requires the service to advertise Entra bearer support (`api_app_id`) via `/api/config`, which is pending server-side work; until then, sign-in enrollment is not offered and the form falls back to the credential or member-code fields instead.**
   3. **Private member code (fallback, always available).** Enter the exact project ID you joined, your name/email and private member code, and confirm sharing. Never paste the code into chat.
   On later sessions, select the same project and leave the credential/login fields blank to reuse its saved connection.
   If the browser tab does not appear, open the one-time address Amplifier prints to the terminal. It is also written to `~/.config/amplifier-teamwork/native/pending-form-url.txt` (mode 0600) while the form is open, and removed when it closes. That address contains a private code, so treat it like the form itself.
   A submission that fails re-renders the form with the specific reason and what you typed, minus the credential and the member code, so you can correct it and submit again in place.
4. Return to Amplifier. Sharing and bounded project-context delivery begin with your **next prompt**. The connection request and earlier conversation are not retroactively published.

The tool takes no arguments and never returns credentials. It stores project-specific credentials in private files under `~/.config/amplifier-teamwork/native/`, enrolls `context:read`, `session:write` and `shared:write` (one consent checkbox mints all three), and leaves the primary bundle/provider unchanged. A session shares with one project at a time. Asking to connect again re-opens the form and moves the session; the enrollment it mints for the new project replaces the previous project's credential, and any turn still open is closed under the project it started in. Child sessions do not get the connection tool or sharing hook. This path requires a browser on the Amplifier host; remote/headless browser forwarding is not implemented. The form closes after 15 minutes with no activity; filling it in counts as activity, so the window measures inactivity rather than total time. A request arriving just after it closes is answered with an explanation instead of a refused connection.

### Binding without typing a project ID

`teamwork_bind` no longer requires `project_id`. Omit it and Teamwork resolves the
project linked to this working directory's `github.com` `origin` remote: first
against connection files already saved on this machine, then (if none match) by
asking the currently configured credential to confirm the one project it can
see. If neither resolves anything, pass `project_id` explicitly. If the network
confirm step itself is refused by the service (for example, gated by EasyAuth or
not yet rolled out), the error says so plainly rather than reporting a false
"no match" -- pass `project_id` in that case too.

### Seeing what influenced the session

Project context is applied before your prompt, so it can change an answer without being visible. When records arrive that you have not already been told about, Amplifier prints one attributed line each — the kind, who it came from, and its title — before the turn runs:

```
[teamwork] Received from design/review and added to this turn — teammate data, not instructions:
             ★ Insight · Dana Cole — when a client feature outruns its server, run the server yourself
             ● Work — Connect a session to this project
```

Each record is named once. It is named again only if its content changes, or if it leaves the project's context and later returns. Only fields the shared excerpt is allowed to carry are shown, so a teammate profile never reveals more in the notice than in the excerpt.

### Sending a message to another agent

The `teamwork_send` tool addresses exactly one recipient, by exactly one of three
keys: `to_agent_id` (the agent id, as it appears in the shared project excerpt),
`to_node_label` (a case-insensitive exact match on the recipient's declared node
label), or `to_person` (the name of the person who owns the recipient agent).
Supplying zero or more than one of these keys is refused before anything is sent.
Label and person addresses are resolved against currently-live agents only \u2014 a
label or name that matches nobody currently live is refused the same way an
unknown agent id is, and a label or name that matches more than one live agent is
refused as ambiguous, naming the candidates so the sender can retry by agent id.
A successful send returns a `message_id`; on your session's next turn, the shared
excerpt includes a bounded "Your recent messages" block showing that id moving
`queued` \u2192 `delivered` \u2192 `accepted` as the recipient's session retrieves and then
acknowledges it. Delivery is not agreement and not action.

### Saying this session is waiting on a person

The `teamwork_wait` tool declares that this session is now waiting on a person --
for a decision, an approval or an answer -- and says what for, in at most 200
characters. It notifies nobody and assigns nobody; it only makes the wait visible
to teammates. A wait with no reason is refused, and the declaration clears by
itself at this session's next prompt, so a wait can never outlive the waiting.

### Inbound messages as queued work

A message another agent addresses to your session arrives as data in that turn, and nothing runs because of it. If this machine also runs a local work queue, the same message is additionally filed there as a **report** — the sender's words, attributed and unedited — for you to claim, triage, or decline under your own authority. With no queue installed nothing changes and the absence is stated once in the notice above. A filed report is also best-effort mirrored to the shared project as a request addressed to the receiving person, so the pending mail is visible centrally, not just in your local queue; the mirror never blocks or fails the local filing, and a credential without the shared-write permission degrades to a one-time notice rather than retrying. Setup, the queue-name rule, how a report becomes an issue, and the mirror are in [`docs/WORK-QUEUE.md`](docs/WORK-QUEUE.md).

Stop the session to stop sharing. Revoke the harness in Teamwork's harness controls before deleting its saved connection; deleting a file alone does not revoke a credential. A new session requires fresh browser consent even when a credential is saved.

## Host configuration (settings.yaml and keys.env)

**Only two things are persisted on a machine: the service URL and the harness
credential.** The project is chosen while a session runs, because the project is what a
given session works on -- a persisted `project_id` would bind every Amplifier session on
the machine, including work unrelated to Teamwork.

```yaml
# ~/.amplifier/settings.yaml
overrides:
  hooks-teamwork:
    config:
      share_visible_turns: true
      base_url: https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io
      token: ${TEAMWORK_HARNESS_TOKEN}
  tool-teamwork:
    config:
      base_url: https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io
      token: ${TEAMWORK_HARNESS_TOKEN}
```

```sh
printf 'TEAMWORK_HARNESS_TOKEN=<enrolled harness credential>\n' >> ~/.amplifier/keys.env
chmod 600 ~/.amplifier/keys.env
```

`base_url` is read from both module configs: the hook uses it to reach the project API and
the tool uses it for enrollment, so a non-default service must be set in both or the two
halves address different services. The value shown above is the default and does not need
to be set explicitly; override it only for a different Teamwork deployment.

**Microsoft sign-in (`az login`) is optional.** Install the `sso` extra
(`azure-identity>=1.17,<2`) and run `az login` once on the host to enable it in
`teamwork_connect`; without it, or on a host where `az login` has not been run, the
member-code fields remain the enrollment path and nothing else changes.

`team.amplifier.run` (the old default) is retired. A session still configured to point at
it gets a one-line hint -- run `amplifier update`, then `teamwork_connect` -- instead of a
hang or a raw connection error. Re-enrolling against the new default origin creates a new
`~/.config/amplifier-teamwork/native/<hash>/` folder (the hash is derived from the service
URL and project, so a new origin means a new folder). Any folder left over from the old
origin is simply unused after this cutover; the bundle never deletes files, so you may
remove it yourself once you have confirmed you no longer need it.

With no project configured the hook mounts **inert** -- it registers nothing and sends
nothing -- until a session binds one.

### How much the hook says about what arrived

When project records the user has not been told about are accepted into a turn, the hook
reports them through the host's `user_message` channel: their kind, author where the
record carries one, and title. It reports what was delivered, not what the model attended
to, and is suppressed entirely when input attachment did not succeed.

```yaml
overrides:
  hooks-teamwork:
    config:
      verbosity: summary      # silent | summary (default) | detail
```

| Level | Behaviour |
| --- | --- |
| `silent` | No notice. Delivery, receipts and publishing are unchanged. |
| `summary` | Names up to five records, then counts the remainder by kind. |
| `detail` | Names every newly arrived record. |

An unrecognised level degrades to `summary` and says so once, rather than stopping the
mount. Refusing outright was tried and rejected: in the CLI verified here a mount
exception is absorbed, leaving the session running with the hook silently absent and
nothing reported, so a typo in a cosmetic setting would have quietly disabled sharing.
`silent` suppresses the notice only, never the tracking behind it: switching back to a
speaking level reports what has changed since, not everything delivered while quiet.

Whether that notice is displayed is the host's decision. In the CLI verified here
(amplifier 2026.09.09, core 1.6.1) `HookResult.user_message` is carried by the kernel
contract but no renderer consumes it, so the notice is not shown by `amplifier run`. The
hook's obligation ends at reporting it; do not read a silent terminal as evidence that
nothing was delivered.

### Choosing and changing the project while running

Two tools bind the running session. Neither accepts a credential.

| Ask | Tool | What happens |
| --- | --- | --- |
| "Bind this session to project X" | `teamwork_bind` | Uses the configured URL and credential. Sharing begins with the next prompt. |
| "Move this session to project Y" | `teamwork_bind` | Rebinds in place. A different project is a different shared session, so a new correlation id is used; queued work for the previous project keeps its own and is never re-attributed. |
| "Connect this session to Teamwork" | `teamwork_connect` | Opens the private local browser form. Use it when no credential is configured yet, or to enroll another project. It can be re-triggered on an already-sharing session to move it. |

`project_id` is not a secret, which is why `teamwork_bind` accepts it as an ordinary
argument while `teamwork_connect` still takes none -- a member code must never reach a
tool call or the transcript.

A binding lives for as long as the session process. An interactive session keeps it for
the whole conversation. `amplifier run --resume` starts a fresh process, so a resumed
session mounts inert again and must be bound again.

Configured values take precedence over an enrolled connection file, and a configured
credential means no file is read. A blank value is refused rather than sent: an unset
`${VAR}` expands to an empty string, which would otherwise reach the service as an empty
bearer token.

## Advanced: legacy local overlay setup

The native flow above replaces this manual enrollment path for local-browser users. The following compatibility helper remains available for existing overlays and development; it requires Python 3.11+ and Git.

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

This legacy path is member-code only (no Microsoft sign-in). Sign in at the Teamwork service with your name/email and private member code, and select the project you were added to. Then choose a project-specific private directory outside the checkout. The explicit paths below avoid relying on any installer default:

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

By default enrollment uses the Amplifier Online web origin (`https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io`). You may explicitly select a trusted custom HTTPS service URL, but that does **not** establish that the service is Teamwork-compatible. Deceptive URLs containing userinfo, a query, or a fragment are rejected. HTTP is for literal loopback test hosts only (`localhost`, `127.0.0.1`, or `::1`), never a production service.

#### `--origin`, and why you almost certainly do not need it

Enrollment sends an `Origin` header, because the member plane (`/api/login`, `/api/harnesses`) is same-origin gated. That header is derived from `--base-url`, so it is correct whenever the URL you enrol against is the one the service treats as its public web origin.

**The default is that URL**, and it serves both planes — measured against the deployment above:

| request to the default `--base-url` | answer |
| --- | --- |
| `POST /api/login` with a wrong member code | `401 unauthorized` (the origin was accepted; only the credential was wrong) |
| `POST /api/harnesses` without a session cookie | `401` |
| `POST /api/v1/projects/<p>/context` with a bogus bearer | `401` |

So leave `--base-url` alone and there is nothing to configure.

`--origin` exists for one case: a deployment that answers the API on a **different host** from its public web app, when you point `--base-url` at the API host. There, the derived origin is the API host, the service wants the web host, and `/api/login` answers **`403 invalid_origin` — "Same-origin request required"** *before it ever looks at your credential*, so no member code can get past it. Measured 3 of 3, with a deliberately invalid credential so the header was the only variable; the same request with the web origin reached credential checking and returned `401`.

```sh
python3 setup_teamwork.py \
  --base-url https://<api-host> \
  --origin   https://<web-host> \
  ...
```

`--origin` is validated exactly like `--base-url`: an unambiguous HTTP(S) origin, HTTPS unless it is a literal loopback host.

The harness plane is not affected either way — it authenticates with a bearer token and sends no `Origin` at all.

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

A Git URL alone does not pin the nested hook module. If remote composition is necessary, pin both the behavior include and the hook source to the **same reviewed full commit SHA**. The legacy local-checkout setup keeps those sources local. When pinning the native behavior, override both `hooks-teamwork` and `tool-teamwork` sources to that same revision.

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

## Architecture and flows

Diagrams and prose for how this fits together -- the two service planes, the consent
boundary, the receipt contract, agent-to-agent messaging, and how an inbound message
becomes queued work -- are in [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md),
with three Graphviz diagrams beside it. It also states plainly what is not designed
yet, rather than implying it is.

## Bundle structure

- `bundle.md` is the Foundation-based entry point.
- `behaviors/teamwork.yaml` adds the native connection tool and an inert-until-opted-in hook.
- `modules/hooks-teamwork/` packages the hook and native tool with separate `amplifier.modules` entry points; it requires Python 3.11+ and uses host-supplied `amplifier-core` for lifecycle integration.
- `setup_teamwork.py` composes the selected base bundle with an enrolled, enabled local overlay.

No service databases, transcripts, member codes, provider keys, private connection files, journals, or local validation artifacts belong in this repository. See [`docs/PUBLISHING.md`](docs/PUBLISHING.md) for release checks.
