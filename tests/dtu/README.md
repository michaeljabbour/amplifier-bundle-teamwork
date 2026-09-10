# Digital Twin end-to-end check

An isolated container that installs this bundle **from a remote source** and runs
real Amplifier sessions against a loopback stub of the harness plane. It exists
because the unit suite cannot show provider behaviour, and `AGENTS.md` requires a
bounded isolated session for setup, connection, or lifecycle changes.

Nothing here contacts the hosted Teamwork service. No member code, harness
credential, project data, or participant identity is involved.

## Contents

| Path | Purpose |
| --- | --- |
| `profiles/teamwork-e2e.yaml` | DTU profile: Ubuntu, uv, Amplifier CLI, Anthropic provider, `url_rewrites` pointing the GitHub origin at a local Gitea mirror |
| `stub_service.py` | Loopback stub of the member plane (`/api/login`, `/api/harnesses`, `/api/harnesses/revoke`) and the harness plane (`/context`, `/publish`, `/acknowledgements`), logging every request as JSONL |
| `run_e2e.py` | Clones the bundle, runs the real enrollment script against the stub, then runs one opted-in and one opted-out session and asserts the enrollment contract and the delivery boundary |

## Running it

Requires `amplifier-digital-twin` and `amplifier-gitea` on the host, and
`ANTHROPIC_API_KEY` exported. The run makes one real provider call per session.

```sh
# 1. Mirror this repository into a local Gitea instance
amplifier-gitea create --port 10160 --name teamwork-gitea
amplifier-gitea mirror-from-github <gitea-id> \
  --github-repo https://github.com/michaeljabbour/amplifier-bundle-teamwork

# 2. Launch the twin
amplifier-digital-twin launch tests/dtu/profiles/teamwork-e2e.yaml \
  --name teamwork-e2e \
  --var GITEA_URL=http://localhost:10160 \
  --var GITEA_TOKEN=<token>

# 3. Copy this directory in and run it
amplifier-digital-twin file-push teamwork-e2e tests/dtu/ /root/dtu/
amplifier-digital-twin exec teamwork-e2e -- python3 /root/dtu/run_e2e.py

# 4. Tear down
amplifier-digital-twin destroy teamwork-e2e
amplifier-gitea destroy <gitea-id>
```

To exercise an unmerged branch, push it to the Gitea mirror and pass
`--ref <branch>`; the rewrite makes the hook install from that ref by URL, the
same code path a participant uses against GitHub.

By default the run performs **real enrollment**: it clones this repository the way a
participant does (the twin rewrites the GitHub origin to the Gitea mirror) and runs
`setup_teamwork.py` against the stub's member plane, so the credential the hook uses is
one the service issued rather than a hand-written fixture. Pass `--no-enroll` to skip
that and supply the connection file directly.

## Which service the run points at

The assertions do not depend on the stub. `--service-url` points the same run at a
service that is already running, and `--request-log` supplies that service's own request
log when it produces one.

| Mode | Command | Coverage |
| --- | --- | --- |
| Built-in stub (default) | `run_e2e.py` | every check |
| Crowded fixture | `run_e2e.py --crowded` | adds many records of one kind and asserts no kind is starved out of the excerpt |
| External service, log available | `run_e2e.py --service-url URL --request-log PATH` | every check |
| Opaque service | `run_e2e.py --service-url URL --no-canary` | client-side checks; traffic-derived ones report `SKIP`, never `PASS` |

For a deployed service, pass `--member-name`, `--member-code`, `--prompt` and `--no-canary`;
enrollment then mints a real credential, which must be revoked afterwards in the service's
own harness controls.

## Host-configuration mode (settings.yaml + keys.env)

The hook can take its connection from ordinary Amplifier host configuration
instead of a private file. Verified in the twin, with no connection file on disk:

```yaml
# ~/.amplifier/settings.yaml
overrides:
  hooks-teamwork:
    # A behavior pins its module source to @main, so testing a branch needs
    # the source override too -- config alone would exercise main's code.
    source: git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@<ref>#subdirectory=modules/hooks-teamwork
    config:
      share_visible_turns: true
      base_url: http://127.0.0.1:8901
      project_id: teamwork
      token: ${TEAMWORK_HARNESS_TOKEN}
```

```sh
printf 'TEAMWORK_HARNESS_TOKEN=...\n' > ~/.amplifier/keys.env && chmod 600 ~/.amplifier/keys.env
```

Observed: the session returned the fixture canary and the stub logged
`session.upsert -> context -> acknowledgements -> turn.upsert -> session.upsert`,
with `ls ~/.config/amplifier-teamwork/connection.json` reporting nothing.

Only the URL and the credential are persisted. With no project configured the hook
mounts inert and `teamwork_bind` binds the running session; verified in the twin, a bind
turn mounted the hook and queued its session records without a project ever appearing in
settings. `amplifier run --resume` starts a fresh process, so a resumed session mounts
inert again -- the harness therefore cannot assert cross-resume sharing, and does not
pretend to.

## What a PASS establishes

- Enrollment signs in before minting, presents the session cookie, requests only
  `context:read` and `session:write`, writes a mode-600 connection file, keeps the
  member code out of that file, keeps the credential out of the overlay, and refuses to
  overwrite an existing enrollment.
- The bundle and its hook install from the configured remote source.
- Shared project context reaches the provider: a fixture canary present only in
  the stub's `/context` response is returned in the visible answer. A receipt
  alone cannot show this, and the protocol documentation is explicit that it
  does not claim to.
- No acknowledgement is sent before the observed context attachment, and the
  acknowledged injection hash equals the injection published with the turn.
- The person projection withholds fields outside the hook's whitelist.
- A disabled overlay runs a normal session and emits no requests at all.

## What a PASS does not establish

- **The deployed service contract.** The stub's response shapes are written from
  the hook's own reader, not captured from the hosted service. A schema change
  upstream would not be detected here.
- Provider comprehension, agreement, or attention. Only that the excerpt was
  present in the conversation the provider received.
- Credential scope enforcement, expiry, revocation, or any real conflict
  semantics. The stub issues and accepts its own credentials; it does not enforce the
  scopes it records, so a least-privilege claim about the deployed service is not
  established here.
- Any other participant's installation, host, or orchestrator.

The `--crowded` check is red-on-violation, not decorative: run against the code before
the seating fix it reports `missing: person`, and against the fix it reports all offered
kinds seated. The kinds it compares against come from the stub's own fixture rather than
from the receipt, because the receipt lists only what was already chosen and would agree
with the excerpt by construction.

## Scenarios not yet covered

Deterministic fault injection is the reason a stub is preferable to the live
service for iteration, and these remain open:

- `acceptance_unknown` after interruption between attachment and receipt commit
  (unit-tested; not yet exercised through a real session)
- outbox replay and idempotency-key reuse after a transport failure
- 409 conflict blocking later work, and 410 resetting the context baseline
- child-session exclusion driven through a real delegated turn
- prompt/response omission at the 60,000-byte boundary, and fragment truncation
  at 1,600 characters
- enrollment rollback: revocation of a freshly issued credential when a later local
  write fails, and the recovery message when revocation itself fails
- the native enrollment tool (`tool-teamwork`), whose browser consent flow is not
  driven here
