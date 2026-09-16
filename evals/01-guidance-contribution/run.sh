#!/usr/bin/env bash
# Teamwork guidance-contribution eval: does the teamwork bundle's guidance
# layer (context pointer + teamwork-protocol skill) change WHO an agent
# addresses when a routing decision arises?
#
# Stands up FOUR independent Gitea mirror repos:
#   admin/amplifier-bundle-teamwork-with-guidance     (this repo's skill-wiring branch)
#   admin/amplifier-bundle-teamwork-without-guidance  (this repo's main branch)
#   admin/amplifier-app-teamwork-service              (the Teamwork service source,
#                                                       fixed across every trial)
#   admin/amplifier-teamwork-eval-support              (this eval's seed.py, fixed)
# -- each carrying its state on `main` -- then hands off to harness.py, which
# runs a 2x2 grid (2 arms x 2 tasks = 4 trials) and writes the pass-both
# comparison.
#
# Two separate repos for the arm (not two branches of one repo) so the
# variants never conflict and both resolve cleanly under Amplifier's
# default-branch (`main`) re-resolution at session start -- same reasoning as
# examples/01-explorer-removal/run.sh.
#
# Idempotent: re-running refreshes the mirrors and writes a fresh dated run.
#
# Usage:
#   ./run.sh
#
# Environment overrides:
#   TEAMWORK_BUNDLE_GIT   git source for this bundle (default: this checkout's
#                         own origin remote, so local edits under evals/ never
#                         leak into the mirrors -- see the mirroring step).
#   APP_TEAMWORK_GIT      git source for amplifier-app-teamwork (default: the
#                         sibling checkout in this workspace, since that repo
#                         is private and this eval was authored alongside it).
#   APP_TEAMWORK_REF      ref to mirror from APP_TEAMWORK_GIT (default: its
#                         current HEAD).
#   AMPLIFIER_GITEA_ID    pick a specific gitea instance instead of the first/new
#   ANTHROPIC_API_KEY     required; falls back to ~/.amplifier/keys.env
#
# Prerequisites:
#   amplifier-digital-twin, amplifier-gitea, git, python3, docker on PATH
#   Docker daemon running
#   amplifier_evaluation importable (the bundle .venv is auto-activated if present)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_ROOT="$(cd "$HERE/../.." && pwd)"

# Default git sources. The bundle source defaults to THIS checkout (not a
# fixed upstream URL) because the two arms are two BRANCHES of this same repo
# (main / skill-wiring) rather than two forks -- see the mirroring step for
# how each arm's content is selected.
TEAMWORK_BUNDLE_GIT="${TEAMWORK_BUNDLE_GIT:-$BUNDLE_ROOT}"
APP_TEAMWORK_GIT="${APP_TEAMWORK_GIT:-$(cd "$BUNDLE_ROOT/../amplifier-app-teamwork" 2>/dev/null && pwd || true)}"

REPO_WITH="amplifier-bundle-teamwork-with-guidance"
REPO_WITHOUT="amplifier-bundle-teamwork-without-guidance"
REPO_APP="amplifier-app-teamwork-service"
REPO_SUPPORT="amplifier-teamwork-eval-support"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---- 0. preflight --------------------------------------------------------
log "preflight checks"
command -v amplifier-digital-twin >/dev/null || die "amplifier-digital-twin not on PATH"
command -v amplifier-gitea >/dev/null || die "amplifier-gitea not on PATH"
command -v git >/dev/null || die "git not on PATH"
command -v python3 >/dev/null || die "python3 not on PATH"
command -v docker >/dev/null || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "Docker is not running"
[ -n "$APP_TEAMWORK_GIT" ] && [ -d "$APP_TEAMWORK_GIT" ] || die "amplifier-app-teamwork checkout not found; set APP_TEAMWORK_GIT"

# Activate the bundle venv so `import amplifier_evaluation` resolves.
if [ -f "$BUNDLE_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . "$BUNDLE_ROOT/.venv/bin/activate"
fi
python3 -c "import amplifier_evaluation" 2>/dev/null \
    || die "amplifier_evaluation not importable; activate the bundle .venv or 'uv pip install -e .' with the amplifier-bundle-evaluation package"

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$HOME/.amplifier/keys.env" ]; then
    set -a; . "$HOME/.amplifier/keys.env"; set +a
fi
[ -n "${ANTHROPIC_API_KEY:-}" ] || die "ANTHROPIC_API_KEY not set and not in ~/.amplifier/keys.env"

# ---- 1. gitea: discover, create, or start -------------------------------
log "ensuring a Gitea instance is running"
GITEA_ID="${AMPLIFIER_GITEA_ID:-}"
if [ -z "$GITEA_ID" ]; then
    GITEA_ID="$(amplifier-gitea list | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"] if d else "")')"
fi
if [ -z "$GITEA_ID" ]; then
    log "no gitea instance, creating one on port 10170"
    GITEA_ID="$(amplifier-gitea create --port 10170 | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
fi
STATUS_JSON="$(amplifier-gitea status "$GITEA_ID")"
RUNNING="$(echo "$STATUS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["container_running"])')"
if [ "$RUNNING" != "True" ]; then
    log "starting stopped gitea container amplifier-gitea-$GITEA_ID"
    docker start "amplifier-gitea-$GITEA_ID" >/dev/null
    sleep 4
fi
GITEA_PORT="$(echo "$STATUS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["port"])')"
GITEA_TOKEN="$(amplifier-gitea token "$GITEA_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
GITEA_URL="http://localhost:$GITEA_PORT"
log "gitea: $GITEA_URL  id=$GITEA_ID"

# ---- 2. build the four mirror repos --------------------------------------
ensure_repo() {
    local name="$1" code
    code="$(curl -sS -H "Authorization: token $GITEA_TOKEN" \
        "$GITEA_URL/api/v1/repos/admin/$name" -o /dev/null -w '%{http_code}')"
    if [ "$code" != "200" ]; then
        log "creating admin/$name"
        curl -sS -X POST "$GITEA_URL/api/v1/admin/users/admin/repos" \
            -H "Authorization: token $GITEA_TOKEN" -H "Content-Type: application/json" \
            -d "{\"name\":\"$name\",\"default_branch\":\"main\",\"auto_init\":false}" -o /dev/null
    fi
}
push_main() {  # local_repo_dir gitea_repo_name
    git -C "$1" -c credential.helper= push --force \
        "http://admin:$GITEA_TOKEN@localhost:$GITEA_PORT/admin/$2.git" \
        "HEAD:refs/heads/main" >/dev/null 2>&1
}

log "mirroring amplifier-bundle-teamwork -> $REPO_WITH (skill-wiring) and $REPO_WITHOUT (main)"
ensure_repo "$REPO_WITH"
ensure_repo "$REPO_WITHOUT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# Full history (NOT --depth 1): Gitea rejects pushing a shallow clone.
git clone --quiet "$TEAMWORK_BUNDLE_GIT" "$WORK/bundle"
(
    cd "$WORK/bundle"
    git fetch --quiet origin main skill-wiring 2>/dev/null || true
    git checkout --quiet main 2>/dev/null || git checkout --quiet origin/main -B main
    push_main "$WORK/bundle" "$REPO_WITHOUT"
    git checkout --quiet skill-wiring 2>/dev/null || git checkout --quiet origin/skill-wiring -B skill-wiring
    push_main "$WORK/bundle" "$REPO_WITH"
)

log "mirroring amplifier-app-teamwork -> $REPO_APP"
ensure_repo "$REPO_APP"
git clone --quiet "$APP_TEAMWORK_GIT" "$WORK/app"
if [ -n "${APP_TEAMWORK_REF:-}" ]; then
    (cd "$WORK/app" && git checkout --quiet "$APP_TEAMWORK_REF")
fi
push_main "$WORK/app" "$REPO_APP"

log "mirroring this eval's seed script -> $REPO_SUPPORT"
ensure_repo "$REPO_SUPPORT"
mkdir -p "$WORK/support"
cp "$HERE/eval-support/seed.py" "$WORK/support/seed.py"
(
    cd "$WORK/support"
    git init --quiet -b main
    git -c user.email=eval@local -c user.name=eval add -A
    git -c user.email=eval@local -c user.name=eval commit --quiet -m "seed.py for the teamwork guidance-contribution eval"
)
push_main "$WORK/support" "$REPO_SUPPORT"
log "mirrors ready"

# ---- 3. run the custom 2x2 harness ---------------------------------------
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="$HERE/results/$RUN_ID"
mkdir -p "$OUTPUT_DIR"

log "running harness, output=$OUTPUT_DIR"
python3 "$HERE/harness.py" \
    --output "$OUTPUT_DIR" \
    --gitea-url "$GITEA_URL" \
    --gitea-token "$GITEA_TOKEN" \
    --with-repo "$REPO_WITH" \
    --without-repo "$REPO_WITHOUT" \
    --app-repo "$REPO_APP" \
    --support-repo "$REPO_SUPPORT"
HARNESS_EXIT=$?

log "harness exit: $HARNESS_EXIT"
log "results: $OUTPUT_DIR"
log "  - with-guidance/  without-guidance/  -- each with 01-ask-the-expert/ + 02-owner-is-the-expert/"
log "  - comparison.md / comparison.json    -- pass-both headline"
log "  - summary.json                       -- per-trial state + grader score"
exit "$HARNESS_EXIT"
