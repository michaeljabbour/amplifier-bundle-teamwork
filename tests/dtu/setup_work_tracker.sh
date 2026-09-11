#!/bin/sh
# Stand up a local work queue inside a DTU container, so the inbound-report path
# can be exercised where it actually runs rather than replayed on the host.
#
# Run INSIDE the container:
#   amplifier-digital-twin file-push <dtu> tests/dtu/setup_work_tracker.sh /root/setup_work_tracker.sh
#   amplifier-digital-twin exec <dtu> -- sh /root/setup_work_tracker.sh <queue-name>
#
# <queue-name> is the tracker project the bundle will look for: the teamwork
# project id put through queue_name.normalise() (lowercase, non-alphanumerics to
# underscores). For a project id of "teamwork" that is "teamwork".
#
# THE VERSION PINS LIVE UPSTREAM, NOT HERE. amplifier-work-tracker's own
# prereqs module carries the pinned bd/dolt versions and mirrors its CI; this
# script installs the tracker first and then asks it what to fetch. Copying the
# pins here would create a second one to keep in step, and a stale copy of a
# version pin fails as a mysterious behaviour difference rather than as an error.
set -eu

QUEUE="${1:?usage: setup_work_tracker.sh <queue-name>}"
export PATH="$HOME/.local/bin:$PATH"

echo "== giving this user a systemd session bus =="
# A container entered by `exec` has no login session, so there is no
# /run/user/<uid> and `systemctl --user` cannot reach a bus -- which is where the
# tracker's service and its reap/notify sweeps live. Lingering creates the bus
# without a login. Done BEFORE doctor, because doctor checks for exactly this and
# a red check here is a real finding about the container, not noise to skip past.
loginctl enable-linger "$(id -un)" || true
XDG_RUNTIME_DIR="/run/user/$(id -u)"
export XDG_RUNTIME_DIR
for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -d "$XDG_RUNTIME_DIR" ] && break
    sleep 1
done
[ -d "$XDG_RUNTIME_DIR" ] || { echo "no session bus at $XDG_RUNTIME_DIR" >&2; exit 1; }

echo "== installing amplifier-work-tracker =="
uv tool install --force \
    --with 'amplifier-work-tracker[web]' \
    'git+https://github.com/microsoft/amplifier-work-tracker@main' >/dev/null

TOOL_PY="$HOME/.local/share/uv/tools/amplifier-work-tracker/bin/python"
[ -x "$TOOL_PY" ] || { echo "no tool venv at $TOOL_PY" >&2; exit 1; }

echo "== installing the bd and dolt builds it pins =="
# Printed by the tracker itself; running them is the only step this script owns.
"$TOOL_PY" -c 'from amplifier_work_tracker import prereqs; print(prereqs.bd_install_command())' | sh
"$TOOL_PY" -c 'from amplifier_work_tracker import prereqs; print(prereqs.dolt_install_command())' | sh

echo "== proving the install behaves as the bundle assumes =="
# --quick skips the concurrency check. A container standing up one queue for one
# agent is not the contended case, and the full suite is minutes long; the
# contended path is the tracker's own contract to prove, not this bundle's.
amplifier-work-tracker doctor --quick

echo "== shared dolt server plus the reap/notify sweeps =="
# The sweeps are not decoration: without reap, a hold whose agent died is only
# reclaim-ELIGIBLE and never actually reclaimed, so the queue wedges quietly.
amplifier-work-tracker service install
amplifier-work-tracker service status

echo "== creating queue '$QUEUE' =="
# The bundle deliberately does not create this. Creating a queue nobody asked for
# is provisioning on someone's behalf; when it is missing the session says so and
# names this command.
amplifier-work-tracker new "$QUEUE"
amplifier-work-tracker list --project "$QUEUE" --limit 1 --json >/dev/null

echo "== ready: queue '$QUEUE' is writable in this container =="
