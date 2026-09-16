#!/usr/bin/env bash
# Live execution is intentionally unavailable until the privacy and arm-isolation
# defects documented in README.md are repaired. Do not read credentials, create
# mirrors, force-push source, or start a DTU from this entry point.
set -euo pipefail
printf '%s\n' 'Live evaluation is unavailable: secure private-mirror transport, cleanup and controlled arm snapshots are not implemented. No credentials were read and no resources were created. See README.md for offline validation and remaining work.' >&2
exit 2
