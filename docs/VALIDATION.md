# Validation evidence and limits

## Hard cutover to the Azure web origin + Entra SSO + auto-bind (2026-09-10)

This PR changes the default `base_url` to the Amplifier Online web origin
(`https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io`),
adds Entra (`az login`) enrollment alongside the member-code path, adds local
git-remote auto-bind for `teamwork_bind`, and retires `team.amplifier.run`.

**A fresh, authorized live session against the new default origin (with a real
`az login` mint, a real member-code mint, and a real `repository_url` mint) has
not been run as part of this PR.** It is a required manual step before this
bundle is tagged and released -- see the cutover order in the plan
(`docs/plans/2026-09-10-hard-cutover-sso-autobind.md`, section 2, step 3-4) --
and it cannot be attested here without fabricating evidence. What follows below
is the complete local, mocked, and structural evidence gathered for this PR;
it establishes the code paths are correct in isolation, not that a live `az
login` mint or a live repository-scoped mint has ever succeeded end to end
against a running service.

### Local evidence for this PR

All 111 unit tests pass (`tests/`), covering: the new `DEFAULT_BASE_URL`
constant; local git-remote reading and canonical repository identity
(`git_remote.py`, no network); the Entra token helper (`entra.py`, with
`azure.identity` itself stubbed -- no real `az` CLI call is made in any test);
the SSO-aware consent form copy and repository preview; the full Entra
enrollment flow (mint-then-reserve ordering, 409/401/403 handling, conflict
revocation and reuse) against a fixture HTTP opener; the retired-host inert
hint (mount stays inert, no HTTP call, notice fires once); and `teamwork_bind`'s
local-first auto-bind (single match, ambiguous match, network-discover
fallback, no-git-remote case) against a fixture native connection directory.
`scripts/validate_bundle.py` passes (schema, composition, isolated local
prepare, standalone replay).

**The 111-test result was reproduced in two environments**, to guard against
the `sys.modules["azure.identity"]` stub only working because a real
`azure-identity` package (and therefore a real `azure` parent module) was
already importable:

1. The Amplifier CLI's own Python environment (`azure-identity` genuinely
   installed, via `amplifier_module_provider_azure_openai`) -- `Ran 111 tests
   in 10.590s / OK`.
2. A throwaway venv holding only `amplifier-core==1.6.1` (installed from
   PyPI) and the stdlib, with **no `azure` namespace package at all**
   (`import azure` raises `ModuleNotFoundError`) -- `Ran 111 tests in
   10.393s / OK`.

The second environment is what actually exercises the "azure.identity is not
installed" and "azure.identity is installed but unusable" branches
faithfully: `entra.py`'s `import azure.identity` first imports the parent
`azure` package, so a test stub that patches only `sys.modules["azure.identity"]`
silently passes on a host where `azure` happens to already be importable, and
would instead raise `ModuleNotFoundError: No module named 'azure'` on a truly
clean host -- masking exactly the failure `EntraUnavailable` exists to catch
cleanly. `tests/test_entra.py`'s stub now seeds both `sys.modules["azure"]`
and `sys.modules["azure.identity"]` for this reason.

None of this exercises a real Entra tenant, a
real Azure Container Apps deployment, or a real GitHub repository beyond
string-level canonicalization.

## Live service and provider run (2026-09-09, pre-cutover)

**This section predates the hard cutover above and describes the retired
`team.amplifier.run` origin and the member-code-only enrollment flow. It is
kept as historical evidence of the underlying delivery/receipt mechanism,
which this PR does not change; it does not establish anything about the new
default origin, Entra enrollment, or auto-bind.**

An authorized operator run against the default hosted service (`https://team.amplifier.run`) with a real LLM provider, from a fresh `amplifier bundle update` of the `@main` behavior at merge commit `882f6b9`, observed:

- **Native connection completed.** In a new interactive session, "Connect this session to Teamwork" invoked `teamwork_connect`; the private browser form accepted the member login and project selection; the tool returned the selected project name and `sharing: enabled for subsequent prompts in this session`. No credential appeared in tool output, chat, or terminal.
- **Context delivery reached the model.** The next visible prompt carried the `[Teamwork shared project context …]` excerpt (project record plus work items) as an attached message. This is the injection contract described in `docs/PROTOCOL.md`, observed end to end for the first time against the hosted service.
- **Round trip is clean.** After that turn, the replay helper reported `{"pending_requests": 0, "blocked_sessions": [], "acceptance_unknown": 0}`; the journal held one active shared session at version 1, one open turn, an empty outbox, and a populated context cache with a cursor.
- **Composition resolved the reviewed revision.** A fresh session started after the cache refresh listed both `tool-teamwork` and `hooks-teamwork` in its configuration and exposed `teamwork_connect` and `teamwork_bind` to the model, with no module load errors.

Two earlier findings from the same day, fixed before this run: the cached `@main` behavior had been frozen at a pre-native-onboarding revision until `amplifier bundle update` was run (README now documents the refresh), and the consent form refused its own submission because `Referrer-Policy: no-referrer` makes browsers send `Origin: null` on a same-origin POST (fixed in the enrollment-form change; the origin check now relies on the unguessable csrf field).

This closes the "real Teamwork service and provider" item of the release gate below. It remains a single-operator, single-project observation: it does not establish another participant's installation, a custom orchestrator, or an arbitrary custom HTTPS service. No session, project, harness, or person identifiers, local paths, or journal contents are published here.

## Native enrollment PR checks (2026-09-09)

- **40 tests passed** in the installed Amplifier Python environment. New focused checks cover native tool mounting and child exclusion, consent and project selection, private credential files, saved-connection reuse, enrollment failure cleanup and compensating revocation, local-browser origin checks, secret-free tool output, and no retroactive turn publication.
- **Foundation validator passed:** actual Markdown/YAML entry files load, app behavior composition preserves explicit overlay opt-in and session configuration, and both module sources prepare locally. The primary bundle is not replaced.
- **Wheel and mount protocol passed:** a freshly built Hatchling wheel contains both `amplifier.modules` entry points; each imports and mounts from an isolated extraction. Core's `ToolValidator` passes all eight checks for `teamwork_connect`. `mount()` returns `None`, not metadata.
- Bundle diagrams regenerated from source.

These are structural, mocked enrollment, and local loopback-form checks. No extended CLI/provider connection experiment or external-service enrollment was run for this PR. Local browser availability and complete live connection remain unverified. The user's reported `Unknown bundle format` error came without its failing command/path, so it was **not reproduced or claimed fixed**. The supplied behavior uses an accepted `.yaml` entry file; module directories are module sources, not bundle entry points. README's `@main` command describes the flow once this PR is merged; an outer branch reference alone would still resolve nested module sources from `main`.

## Earlier published revision evidence

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

The authorized fresh test against a real Teamwork service and provider is recorded above, together with a fresh remote composition of the reviewed behavior include and nested module sources at `@main`; a composition pinned to an exact commit SHA has not been separately exercised. The current local checks cannot establish another participant's installation, a custom orchestrator, an arbitrary custom HTTPS service, or a live external provider/service. A Digital Twin Universe attempt was blocked by host capacity before launch; no container source verification occurred in this task. The temporary Gitea environment was torn down and supplies no release evidence.

The private operator evidence remains outside this public repository. No session or project IDs, local paths, usernames, credentials, fixture payloads, raw logs, participant profiles, or interview transcripts are published here.
