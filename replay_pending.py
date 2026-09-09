"""Replay this connection's pending outbox; prints counts, never record bodies."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import HTTPClient, Journal, SyncError, sha


def unresolved_inputs(state):
    turn = state.get("turn") or {}
    pending = bool(turn.get("prepared_injection") and turn.get("boundary") != "harness_input_accepted")
    return len(state.get("acceptance_unknown", {})) + int(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", default="~/.config/amplifier-teamwork/connection.json")
    args = parser.parse_args()
    path = Path(args.connection_file).expanduser()
    connection = json.loads(path.read_text())
    journal = Journal(path.parent / ("outbox-" + sha(connection["token"])[:16] + ".sqlite3"))
    with journal.connect() as conn:
        sessions = [r[0] for r in conn.execute("SELECT DISTINCT session FROM outbox")]
    failures = []
    for sid in sessions:
        try: journal.flush(sid, HTTPClient(connection))
        except SyncError as error: failures.append({"session_id": sid, "http_status": error.status})
    with journal.connect() as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        unknown = sum(unresolved_inputs(json.loads(row[0])) for row in conn.execute("SELECT body FROM state"))
    print(json.dumps({"pending_requests": remaining, "blocked_sessions": failures, "acceptance_unknown": unknown}))
    if unknown:
        print("Prepared input outcomes need local reconciliation; no receipt is fabricated or retried for them.")


if __name__ == "__main__": main()
