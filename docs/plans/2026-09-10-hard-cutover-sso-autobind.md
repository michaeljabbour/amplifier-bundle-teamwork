# Hard cutover to Amplifier Online + Entra SSO enrollment + git-remote auto-bind

Plan date: 2026-09-10. Status: implementation-ready. Written from the bundle repo (the only
writable sandbox). **PR-B and PR-C must be executed from a session rooted in
the `amplifier-app-teamwork` repository.**

## 0. Summary

- **PR-A (bundle, this repo)** — new default origin, Entra `az login` enrollment, git-remote
  auto-bind, `shared:write` in the minted scopes with updated consent copy, retired-host hint, 0.2.0.
- **PR-B (service)** — Entra JWT validation, Entra-authenticated `POST /api/harnesses` with optional
  `repository_url`, `/api/config` advertising `{tenant_id, api_app_id}`, roster email backfill.
- **PR-C (cutover + cleanup)** — sever `team.amplifier.run`, retire Vercel, refresh docs.

### Decisions (settled — do not reopen)

| # | Decision |
|---|---|
| 1 | Hard cut. `team.amplifier.run` is severed; everyone re-enrolls after `amplifier update`. No alias, no redirect. |
| 2 | Token source is `az login` via `azure.identity` (`AzureCliCredential`), mirroring Team Pulse / context-intelligence. No device code, no new public client. |
| 3 | Member codes remain a working fallback throughout the transition. |
| 4 | One consent checkbox mints all three scopes. The copy must say plainly that the session may publish visible turns **and** shared knowledge / work requests to the project. |

### Two corrections to the brief (verified in source — please confirm)

1. **`api/index.py` is no longer a Vercel artifact and must not be deleted.** It is the container
   entrypoint: `backend/devserver.py:5`, `tests/test_http_smoke.py:14` and `tests/test_health.py:10`
   all `from api.index import handler`, and `deploy/api/Dockerfile` copies `api/`. Deleting it breaks
   the api container. PR-C rewrites its Vercel-era comment instead (C5).
2. **A harness credential cannot discover a project other than its own.**
   `directory.list_projects:47` skips any `pid not in actor["projects"]` for harness actors, and a
   harness is minted with `projects=[one project]`. So "git remote → `/api/projects/discover`" can
   only ever confirm the project the credential already holds — it cannot move a session between
   projects. Auto-bind therefore resolves **locally first** (A7): the canonical `repository_url` is
   stored in `connection.json` at enrollment and matched against the working directory's remote
   across saved connections. Network `discover` is retained as the confirmation/diagnostic path.

### Non-goals

Custom domain binding (external ask), device-code or redirect auth, migrating existing harness
credentials (all re-minted), multi-tenant EasyAuth, changing the `person_id` uuid5 namespace,
publishing knowledge from the hook (only the scope is granted now), harness renewal.

---

## 1. Sequenced work

### PR-A — bundle (`amplifier-bundle-teamwork`)

`AGENTS.md` gates apply to every task: `python -m unittest discover -s tests -v` and
`python scripts/validate_bundle.py` from an Amplifier Python environment; wheel build + import check
(module packaging changes); one bounded isolated live session (connection flow changes); fresh
`bundle.dot` / `bundle.png`; no credential, connection file, or local path committed.

#### A1. Default origin becomes one named constant (5 min)

- **Test first** — `tests/test_native.py::DefaultsTests::test_default_base_url_is_the_azure_web_origin`:
  `service_url.DEFAULT_BASE_URL == "https://amplifier-teamwork-web.livelysea-7d934004.westus2.azurecontainerapps.io"`
  and survives `validate_service_url` unchanged.
- **Change** — add `DEFAULT_BASE_URL` to `service_url.py`; `tool_teamwork/__init__.py:317` and
  `setup_teamwork.py:80` import it instead of literals.
- **Success** — no `team.amplifier.run` literal remains outside `RETIRED_HOSTS` (A6).

#### A2. Git remote reader (10 min, 3 tasks)

New `amplifier_module_hooks_teamwork/git_remote.py`:

```python
def origin_url(cwd=None) -> str | None       # `git remote get-url origin`, 5s timeout, None on any failure
def repository_identity(value) -> str | None # canonical "https://github.com/owner/repo", else None
```

Mirrors `backend/repositories.py::repository_identity`: accept `git@github.com:o/r(.git)`,
`ssh://git@github.com/o/r(.git)`, `https://github.com/o/r(.git)`; strip `.git`; require
`re.fullmatch(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}")` with a second segment that is not
`.`/`..`. **Return `None` — never a value — for any remote carrying userinfo, a query, a fragment, or
a non-`github.com` host**: `https://user:token@github.com/o/r` must never be transmitted. No network.

- **Tests** — `tests/test_git_remote.py`:
  `test_ssh_and_https_remotes_canonicalize_identically`;
  `test_credential_bearing_remote_is_refused`;
  `test_non_github_and_malformed_remotes_are_refused` (`https://github.com@evil.invalid/o/r`,
  `https://github.com/o/r?t=x`, file paths); `test_origin_url_returns_none_outside_a_repository`.

#### A3. Entra token helper (10 min, 3 tasks)

New `amplifier_module_hooks_teamwork/entra.py`:

```python
class EntraUnavailable(RuntimeError): ...    # self-authored message, never service text
def available() -> bool                      # azure.identity importable
def access_token(scope) -> str               # AzureCliCredential().get_token(scope).token
```

Lazy-import inside the function. Module cache `{scope: (token, expires_on)}`, reused while
`expires_on - time.time() > 300` (the context-intelligence pattern, `context_intelligence/auth.py`).
`AzureCliCredential`, not `DefaultAzureCredential`: the bundle runs on a developer workstation and a
silently-selected managed identity would be an unexplained identity. Any failure raises
``EntraUnavailable("Microsoft sign-in is unavailable on this machine. Run `az login`, then try again.")``.
Never log or return the token.

- **Tests** — `tests/test_entra.py`, monkeypatching `sys.modules["azure.identity"]` with a stub:
  `test_token_is_cached_per_scope_until_near_expiry`; `test_expired_cache_entry_is_refetched`;
  ``test_missing_azure_identity_raises_a_run_az_login_message`` (asserts `az login` present, no
  traceback text leaks).

#### A4. SSO-aware consent form (15 min, 4 tasks)

`page.py::form_page(..., sso=False, repository=None)`:

- `Grants` row → `context:read, session:write and shared:write, for this project only`.
- Consent label → *"Share subsequent visible prompts and final responses in this session with this
  project, publish shared knowledge and work requests to it on my behalf, and receive its bounded
  context."*
- `sso=True`: a `<dt>Sign-in</dt><dd>Your Microsoft account (az login)</dd>` row; `name`/`code`
  relabelled *"member-code fallback only"*; `project` marked optional when `repository` is set.
- `repository` set: a `<dt>Repository</dt><dd>{url}</dd>` row, so the user sees exactly what will be
  sent **before** consenting.

- **Tests** — `tests/test_native.py::ConsentCopyTests`:
  `test_consent_copy_names_shared_write_publishing` (contains `shared:write` and
  `publish shared knowledge and work requests`);
  `test_sso_form_shows_the_detected_repository_and_makes_project_optional`;
  `test_form_never_renders_a_member_code` (existing invariant, extended to the SSO branch).

#### A5. Enrollment with SSO + auto-bind (25 min, 6 tasks)

`tool_teamwork/__init__.py::connect(form, base, home, repository=None)`.
**No network call happens before the form is submitted with consent.** The git remote read (A2) is a
local subprocess and runs before the form so it can be displayed. Ordering after consent:

1. If `form.get("code")` is empty and `entra.available()`:
   1. `GET {base}/api/config` — unauthenticated, carries no user data, the only pre-mint read. If it
      omits a non-empty `api_app_id`, raise `ConsentError("This service is not configured for
      Microsoft sign-in.", "code", "Enter your name and private member code below, then submit
      again.")` — a retryable form error with kept values, not a silent fallback.
   2. `bearer = entra.access_token("api://" + api_app_id + "/.default")`.
   3. `POST {base}/api/harnesses` with `Authorization: Bearer <entra>`, body
      `{"label": "Amplifier native", "scopes": ["context:read","session:write","shared:write"]}`
      plus `{"repository_url": repository}` when `project` is blank, or header
      `X-Teamwork-Project: <project>` when the user filled it in.
   4. `409 ambiguous_repository` → `ConsentError` naming the returned project ids, asking the user to
      type one into Project ID. Nothing stored, nothing minted.
   5. `403 entra_unmapped` / `401` → `ConsentError("Microsoft sign-in did not match a Teamwork
      member.", "name", "Ask a maintainer to set your Teamwork email, or enroll with your name and
      private member code below.")` — the member-code fields stay live in the same form.
   6. `project = credential["projects"][0]`.
2. Otherwise the member-code path runs unchanged (`/api/login` → cookie → `/api/harnesses`), with
   `scopes` widened to all three and `project` from the form.

**File ordering.** The directory key `sha(base + "\0" + project)` is unknowable before an SSO mint, so
that path inverts today's order: mint, then `os.open(path, O_WRONLY|O_CREAT|O_EXCL, 0o600)`. On
`FileExistsError` the new harness is revoked via `POST /api/harnesses/revoke` with the same Entra
bearer and the saved connection is validated and reused exactly as today (`__init__.py:57-69`); if
revocation fails, the existing `recovery-<uuid>.json` breadcrumb is written (`:98-100`). The
member-code path keeps reserve-then-mint. Either way the invariant holds: **an existing saved
connection is never overwritten.**

Stored `connection.json` gains one optional field: `"repository_url"` (the canonical URL, or absent).
This is what makes A7 work without a network call.

`TeamworkConnect.execute` computes
`repository = git_remote.repository_identity(git_remote.origin_url())` and threads it through
`private_browser_connect` → `form_page` → `connect`.

- **Tests** — `tests/test_native.py`:
  `test_sso_enrollment_mints_all_three_scopes_and_stores_project_and_repository` (stub opener asserts
  body scopes; `connection.json` gets `project_id` from the mint response and the canonical
  `repository_url`; mode 0600);
  `test_sso_enrollment_sends_only_the_canonical_repository_url`;
  `test_ambiguous_repository_is_a_retryable_form_error` (409 → form re-renders, no file written);
  `test_service_without_api_app_id_asks_for_the_member_code`;
  `test_unmapped_entra_identity_falls_back_to_the_member_code_fields`;
  `test_sso_conflict_revokes_the_new_credential_and_reuses_the_saved_one`;
  `test_member_code_path_is_unchanged_and_now_requests_shared_write`.

#### A6. Retired-host hint (10 min, 2 tasks)

```python
RETIRED_HOSTS = ("team.amplifier.run",)
RETIRED_MESSAGE = "Teamwork moved — run `amplifier update`, then `teamwork_connect`."
```

- `resolve_connection` raises `ValueError(RETIRED_MESSAGE)` when the resolved `base_url` host is
  retired. Deterministic, no network, cannot spin.
- `mount` catches **that one case**, registers a one-shot `prompt:submit` handler returning
  `hook_result(RETIRED_MESSAGE)` that unregisters itself, and returns `None` (inert). This is
  required, not decorative: the module's own `verbosity()` docstring records that a mount exception
  is absorbed by the host, leaving the hook silently absent — so raising alone would say nothing.
- `TeamworkBind`/`TeamworkConnect` return the same sentence verbatim in `ToolResult.error`.
- A `410` from the service maps to the same message via `SyncError(410)` handling in `on_submit`.

- **Tests** — `tests/test_hook.py`:
  `test_retired_host_connection_stays_inert_and_says_so_once` (mount returns None; one notice on the
  first prompt, none on the second; no HTTP performed);
  `test_retired_host_bind_returns_the_update_hint`.

#### A7. Optional `project_id` on `teamwork_bind` (15 min, 3 tasks)

`input_schema` drops `"required": ["project_id"]`; description gains *"Omit `project_id` to bind the
project linked to this working directory's git remote."*

`execute` when `project_id` is absent:

1. `identity = git_remote.repository_identity(git_remote.origin_url())`. `None` → *"This directory
   has no usable GitHub remote. Pass `project_id`."*
2. **Local match (primary).** Scan `~/.config/amplifier-teamwork/native/*/connection.json` (mode-0600
   files only, malformed skipped) for entries whose `repository_url` equals `identity`. Exactly one →
   `rebind(project_id, connection)`, which swaps both project and credential (`hooks/__init__.py:196`).
   Several → error naming the candidate project ids. Zero → step 3.
3. **Network confirm (fallback).** With the currently configured credential,
   `POST {base}/api/projects/discover` `{"repository_url": identity}`. `selected_project_id` → bind.
   Otherwise *"No Teamwork project you can reach is linked to this repository. Link it in Teamwork,
   or pass `project_id`."* A harness only ever sees its own project here (see correction 2), so this
   step confirms or diagnoses; it never moves the session.

- **Tests** — `tests/test_native.py`:
  `test_bind_without_project_uses_a_saved_connection_matching_the_git_remote`;
  `test_bind_reports_candidates_when_two_saved_connections_match`;
  `test_bind_falls_back_to_discover_when_no_saved_connection_matches`;
  `test_bind_without_a_git_remote_asks_for_a_project_id`.

#### A8. Version, packaging, docs (10 min)

| File | Change |
|---|---|
| `bundle.md`, `behaviors/teamwork.yaml` | `version: 0.2.0` |
| `modules/hooks-teamwork/pyproject.toml` | `version = "0.2.0"`; add `[project.optional-dependencies] sso = ["azure-identity>=1.17,<2"]`. `dependencies` stays `[]` — `amplifier-core` is host-supplied and `azure.identity` is lazy-imported, so a host without it keeps the member-code path. |
| `README.md:73,77,178,194` | New origin; SSO-first steps; member code as fallback; `az login` prerequisite. |
| `docs/PROTOCOL.md:5` | New default origin; `/api/config` discovery of `{tenant_id, api_app_id}`; the three minted scopes; the `repository_url` mint contract and its 409; the new `repository_url` connection field. |
| `docs/VALIDATION.md:5` | Replace with new live-session evidence (public-safe only). |
| `bundle.dot`, `bundle.png` | Regenerate. |

---

### PR-B — service (`amplifier-app-teamwork`, run from that repo)

Gate: `uv run python -m unittest discover -s tests -v` (124 today; all must still pass).

#### B1. `pyjwt[crypto]` (5 min)

Add `"pyjwt[crypto]>=2.9,<3"` to `pyproject.toml` and `requirements.txt`. **Why a library:** RS256
verification needs RSA PKCS#1 v1.5 checking and Python's stdlib has no RSA primitive, so hand-rolling
means writing security-critical crypto. `azure-identity` already pulls `cryptography` transitively
(via msal), so this adds only PyJWT itself. Squarely the "complex, well-solved problem" case.

#### B2. `backend/entra.py` (20 min, 4 tasks)

```python
def config() -> dict           # {"tenant_id": AZURE_TENANT_ID, "api_app_id": AZURE_API_CLIENT_ID}
def signing_key(token)         # PyJWKClient against the tenant JWKS, module-level cache, 1h TTL
def verify(token) -> dict      # claims
def identity(claims) -> str    # casefold(preferred_username or upn or email); "" when absent
```

`verify` calls `jwt.decode(token, key, algorithms=["RS256"],
audience=[api_app_id, "api://"+api_app_id],
issuer=["https://login.microsoftonline.com/{tid}/v2.0", "https://sts.windows.net/{tid}/"])`,
which also enforces `exp`/`nbf`.

| Condition | Status | Code |
|---|---|---|
| `AZURE_TENANT_ID` / `AZURE_API_CLIENT_ID` unset | 503 | `entra_unconfigured` |
| JWKS unreachable | 503 | `entra_unavailable` |
| Bad signature / audience / issuer / expiry | 401 | `entra_invalid` |

Messages are generic ("Microsoft sign-in could not be verified"); no claim values are echoed.
`amplifier-online.yaml:24` documents only `AZURE_API_CLIENT_ID`; **confirm the exact tenant variable
name against the Amplifier Online README before relying on it**, and fall back to reading the `tid`
claim's issuer only after the audience check if it is not injected.

- **Tests** — `tests/test_entra_auth.py`. Generate an RSA key with `cryptography` in `setUp`, sign
  real tokens with PyJWT, monkeypatch the JWKS fetch to return the matching public JWK:
  `test_valid_token_yields_claims`; `test_wrong_audience_is_rejected`; `test_wrong_issuer_is_rejected`;
  `test_expired_is_rejected`; `test_token_signed_by_another_key_is_rejected`;
  `test_identity_prefers_preferred_username_then_upn_then_email_and_casefolds`;
  `test_unconfigured_tenant_is_503_not_401`.

#### B3. Roster email backfill + `find_member` extraction (10 min, 3 tasks)

`directory.initialize:11-17` copies only `name`, `token_sha256`, `active`, `is_admin` from the roster
— **`email` never reaches `_directory`**, which is what `login:53` and `member_actor:41` read. Without
this task, no Entra identity can ever map.

- `auth.py::members()` — additionally assert roster `email` values are unique (casefold), so backfill
  cannot manufacture an `email_conflict`.
- `auth.py` — extract the inline matcher at `login:53` into `find_member(s, identifier)`; `login`
  calls it. Behaviour identical.
- `directory.initialize` — **outside** the `"_directory" not in root` guard, for each roster member
  carrying `email`, set it on the matching `_directory` entry if that entry has none. Never
  overwrites. Runs every request; `PostgresStore.read` is `SET TRANSACTION READ ONLY` and never
  UPDATEs, so a read discards the mutation and the first write persists it. No operational script.

- **Tests** — `tests/test_connections.py`: `test_login_matching_is_unchanged_after_extraction`
  (refactor guard); `test_roster_email_backfills_an_initialized_directory_without_overwriting`;
  `test_duplicate_roster_emails_are_refused_as_unconfigured` (503).

#### B4. Entra actors in `authenticate` (15 min, 3 tasks)

`auth.py::authenticate:62` — discriminate before the length guard:

```python
if bearer:
    token = bearer[len("Bearer "):] if bearer.startswith("Bearer ") else ""
    if token.count(".") == 2:                 # compact JWS -> Entra
        require(len(token) <= 8192, "unauthorized", "Invalid bearer credential", 401)
        return entra_actor(s, token)
    require(len(bearer) <= 600, ...)          # existing opaque-harness path, unchanged
```

Harness tokens are `secrets.token_urlsafe(40)` and contain no dots, so the discriminator is exact.
The 600-byte cap must not apply to JWTs (Entra access tokens run 1–2.5 KB).

`entra_actor(s, token)`: `claims = entra.verify(token)`; `ident = entra.identity(claims)`;
`member = find_member(s, ident)`; require `member` **and** `member.get("email","").casefold() == ident`
(identity maps only through an explicit email, never a display name), else
`APIError(403, "entra_unmapped", "This Microsoft account is not linked to a Teamwork member. Ask a
maintainer to set your Teamwork email, or enroll with your member code.")`. Return
`member_actor(s, member) | {"credential_kind": "entra"}`.

`http.py`, immediately after the harness restriction at `:58-59`:

```python
if actor.get("credential_kind") == "entra":
    core.require(path in ("/api/harnesses", "/api/harnesses/revoke", "/api/me", "/api/projects/discover"),
                 "forbidden", "Microsoft sign-in may only enroll and manage harness credentials", 403)
```

Least privilege: an SSO bearer is an **enrollment** credential, not a general member session. Revoke
is allowlisted because the bundle's conflict path must clean up an orphaned credential.

- **Tests** — `tests/test_entra_auth.py`:
  `test_entra_bearer_enrolls_a_harness_for_the_mapped_member`;
  `test_entra_bearer_is_refused_on_state_and_action` (403 on `/api/state`, `/api/action`);
  `test_unmapped_identity_is_403_entra_unmapped`;
  `test_opaque_harness_tokens_still_authenticate` (regression against the discriminator).

#### B5. `repository_url` on `POST /api/harnesses` (15 min, 3 tasks)

`http.py`, placed **before** `selected = headers.get("X-Teamwork-Project", ...)` at `:82` so discovery
chooses the project (the default `project_id` would otherwise 404 in `scoped_actor` for a member who
is not in it):

```python
if path == "/api/harnesses" and method == "POST" and body.get("repository_url"):
    chosen = repositories.discover(s, actor, {"repository_url": body["repository_url"]})
    core.require(chosen["selected_project_id"], "ambiguous_repository",
                 "That repository does not identify exactly one project", 409,
                 projects=chosen["matches"], reason=chosen["reason"])
    actor = directory.scoped_actor(s, actor, chosen["selected_project_id"])
    return 201, auth.enroll(s, actor, {k: v for k, v in body.items() if k != "repository_url"}), {}
```

Scoping explicitly (rather than moving the whole `/api/harnesses` block up) keeps `enroll`'s
`actor["role"] != "viewer"` guard meaningful — an unscoped actor has `role=None`, which would pass it
vacuously. `APIError(**details)` already flows into the envelope (`http.py:224`), so the 409 body
carries `projects` (only `matches`, i.e. the actor's own projects — never the full listing) and
`reason` (`ambiguous_repository` | `no_repository_match`).

`auth.enroll:89` — widen to `set(body) <= {"label", "scopes", "repository_url"}` so a direct call with
the key is not a 422; the http layer strips it first.

- **Tests** — `tests/test_project_discovery.py`:
  `test_harness_mint_with_repository_url_selects_the_unique_project`;
  `test_ambiguous_repository_mint_is_409_with_candidate_projects`;
  `test_unmatched_repository_mint_is_409_and_mints_nothing`;
  `test_viewer_cannot_mint_through_the_repository_path`.

#### B6. `/api/config` advertises the audience (5 min)

`http.py:26-27` — add `**entra.config()` to the payload. Both values are public app-registration
identifiers; both are empty strings when unset, which is the bundle's signal to skip SSO. The
endpoint is already served before authentication.

- **Test** — `tests/test_health.py::test_config_advertises_tenant_and_api_app_id` (present when set,
  empty when unset; `auth_required` / `project_id` / `share_url` unchanged).

#### B7. uuid5 namespace guard (5 min)

`core.py:43` keeps `https://team.amplifier.run/people/` **forever** — it is an ID namespace, not a
URL. Add that sentence as a comment plus
`tests/test_connections.py::test_person_id_namespace_is_frozen`, asserting a literal expected uuid for
a fixed name.

---

### PR-C — cutover runbook and cleanup (service repo)

| # | Task |
|---|---|
| C1 | `TEAMWORK_PUBLIC_ORIGIN` in `amplifier-online.yaml:44-45` is already the web FQDN; run `amplifier-online up` — no env change takes effect without it. |
| C2 | Confirm `/api/config` and `/api/v1/projects/{id}/publish` work through nginx at the web origin (see R1). `deploy/web/default.conf.template` proxies `location /api/` and sets `Authorization` explicitly; `Origin` passes through unmodified, so `origin_check` matches `TEAMWORK_PUBLIC_ORIGIN`. |
| C3 | Turn the Vercel project `amplifier-teamwork` off in the dashboard (manual; no CLI step in this repo). |
| C4 | Delete `vercel.json`, `.vercelignore`. |
| C5 | **Keep `api/index.py`** (see correction 1); replace its Vercel-era comment with "container entrypoint handler". Update the `deploy/api/Dockerfile:19` comment that refers to `vercel.json`'s `includeFiles`. |
| C6 | `backend/store.py:134` — drop the `VERCEL` check, keep `TEAMWORK_HOSTED`. Update `tests/test_store_config.py::ENV_KEYS` and `tests/test_http_smoke.py:28,56`. Also `http.py:54` (`TEAMWORK_COOKIE_INSECURE` … `and not os.environ.get("VERCEL")`). |
| C7 | Docs: `README.md`, `public/connection-guide.html`, `public/app.js`, `docs/harness-api-implemented.md` — replace `team.amplifier.run` with the new origin and the Vercel deployment prose with Amplifier Online. |
| C8 | Announce: new origin, `amplifier update`, then `teamwork_connect`. |

---

## 2. Cutover order, preconditions, rollback

| Step | Precondition | Rollback |
|---|---|---|
| 1. Merge PR-B; CI builds and deploys the api image | 124 + new tests green; `pyjwt[crypto]` in the image | Revert commit; push-to-deploy rolls back |
| 2. `amplifier-online secret set members-json` (emails added), then `up` for it and `TEAMWORK_PUBLIC_ORIGIN` | Roster emails unique, casefolded | Restore the previous secret value; `up` again |
| 3. Verify on the temp FQDN: `/api/config` returns `tenant_id`/`api_app_id`; `az login` then a real SSO mint; a member-code mint; one `repository_url` mint | Step 2 complete; backfill observed | Nothing user-visible yet — fix forward |
| 4. Merge PR-A; tag bundle 0.2.0 | Bundle gates green + one bounded live session against the new origin | Revert tag; `@main` still resolves the previous behavior |
| 5. Announce | Steps 3–4 complete | Announcement correction only |
| 6. Merge PR-C; turn the Vercel project off | 48 h after step 5, or once every active member has re-enrolled | Re-enable from the dashboard; the DB connection string is unchanged |

Steps 1–4 are reversible. Step 6 is the irreversible one and is deliberately last and time-gapped.

---

## 3. External asks (Amplifier Online team)

| Ask | Blocks | Testable today without it |
|---|---|---|
| Bind custom domain `teamwork.amplifier.ms` (no manifest/CLI support today) | Only the final origin swap | Yes — everything runs against the temp `*.azurecontainerapps.io` FQDN. The origin is one constant in `service_url.py` plus one manifest value; swapping is a one-line change plus `up`. |
| Pre-authorize the az CLI client `04b07795-8ddb-461a-bbee-02f9e1bf7b46` for `access_as_user` on the platform-managed `ao-amplifier-teamwork-api` registration | Only the real end-to-end `az login` mint | Mostly. B2/B4 are fully testable with locally minted RSA-signed tokens; A3/A5 with a stubbed `azure.identity`. |

**Fallback if pre-authorization is refused:** set `auth.api_app_id` in `amplifier-online.yaml`
(the BYO block at lines 7-12) to a registration that already pre-authorizes the az CLI. No code
changes — the bundle uses whatever `/api/config` advertises. Ship member codes as the sole enrollment
route until either lands; nothing in PR-A/PR-B is gated on it.

---

## 4. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | `auth_exclude: ["/api","/api/"]` is literal-prefix matching with a ~60-minute auth-proxy cache. A first-time `/api/v1/...` call or a path-shape change may hit an EasyAuth redirect, which `NoRedirect` refuses outright. | Verify `/api/config` and `/api/v1/projects/{id}/publish` through the web origin **before** step 4. Any `auth_exclude` edit needs `up` plus up to an hour before it is observable — budget for it; do not retry-loop. |
| R2 | `core.person_id` uuid5 namespace is `https://team.amplifier.run/people/`. Changing it silently re-IDs every person and orphans memberships, harnesses and records. | Frozen by comment + regression test (B7). Never "clean up" this string, including during PR-C's grep-and-replace. |
| R3 | Harness tokens expire after 30 days (`auth.py:93`). Every 0.2.0 enrollment expires together, ~30 days after cutover. | Out of scope here; the 401 path already surfaces failure. Track a renewal flow separately and diarize the date. |
| R4 | Roster email hygiene: SSO maps only through an exact casefolded email. Missing or mistyped → `entra_unmapped`. | Backfill (B3), uniqueness asserted in `members()`, and the error text names exactly what a maintainer must fix. Member codes still work. |
| R5 | EasyAuth is single-tenant; a guest or personal Microsoft account yields a token the API rejects. | `entra_unmapped` names the member-code fallback. Do not attempt multi-tenant. |
| R6 | A stolen `az` token with full member authority could mutate project state. | The `credential_kind == "entra"` allowlist (B4) restricts SSO bearers to enroll / revoke / me / discover. |
| R7 | A credential-bearing git remote could be transmitted. | `git_remote.repository_identity` returns `None` for any remote with userinfo, and the form displays the exact URL before consent (A2, A4). |
| R8 | The SSO mint-then-reserve ordering can leave an orphaned credential if the process dies between mint and revoke. | Revoke on conflict, `recovery-<uuid>.json` breadcrumb on revoke failure, and the credential is project-scoped and 30-day bounded. |
| R9 | The api container must reach `login.microsoftonline.com` for JWKS. | Container Apps allows egress by default; JWKS is cached for 1 h and failure is a loud 503 `entra_unavailable`, never a silent accept. |

---

## 5. Follow-up

Once a `shared:write` credential exists (i.e. after the first 0.2.0 enrollment), publish
`docs/research-a2a-and-coordination-frameworks-20260910.md` (amplifier-app-teamwork) into the
project as a Knowledge item through the harness publish path. That is the first real exercise of the
third scope and the smallest honest proof it works end to end.
