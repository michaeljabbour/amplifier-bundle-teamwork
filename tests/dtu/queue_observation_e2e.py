"""Phase 2 queue observation, against the REAL work-tracker in this container.

Not a stub: this drives amplifier-work-tracker/bd/dolt as installed, so
Queue.status() is exercised against a tracker that can actually answer.
"""
import json, os, subprocess, sys, glob, uuid

cache = sorted(glob.glob("/root/.amplifier/cache/amplifier-bundle-teamwork-*"))[-1]
sys.path.insert(0, os.path.join(cache, "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork.reports import Queue, sanitize_outbound  # noqa: E402

TRACKER = "/root/.local/bin/amplifier-work-tracker"
project = "p2probe" + uuid.uuid4().hex[:6]
registry = "/tmp/p2-queues.json"
if os.path.exists(registry):
    os.remove(registry)

checks, failed = [], 0
def check(name, ok, detail=""):
    global failed
    checks.append((name, ok, detail))
    if not ok:
        failed += 1

# 1. a real queue, created through the real CLI
new = subprocess.run([TRACKER, "new", project], capture_output=True, text=True, timeout=120)
check("real tracker created a project", new.returncode == 0, new.stderr.strip()[:120])

q = Queue(project, registry, service="https://team.example.invalid")
first = q.status()
check("status() reports ready against a real tracker",
      first.get("queue_status") == "ready", json.dumps(first)[:160])
check("integration names the real CLI",
      first.get("integration") == "amplifier-work-tracker", str(first.get("integration")))
check("observed_at is stamped", bool(first.get("observed_at")), str(first.get("observed_at")))
check("ready_count is a real int", isinstance(first.get("ready_count"), int), str(first.get("ready_count")))

# 2. add one real item and see the count move
add = subprocess.run([TRACKER, "add", "--project", project, "A real probe item",
                      "--description", "created by the phase 2 DTU probe"],
                     capture_output=True, text=True, timeout=120)
second = Queue(project, registry, service="https://team.example.invalid").status()
check("a real added item is counted",
      add.returncode == 0 and second.get("ready_count", 0) >= 1,
      "rc=%s count=%s %s" % (add.returncode, second.get("ready_count"), add.stderr.strip()[:80]))

# 3. the sanitizer is the only door out
leaky = dict(second, queue_path="/root/.beads/queue.db",
             command="%s list --project %s" % (TRACKER, project),
             hostname=os.uname().nodename, token="tok-secret")
clean = sanitize_outbound(leaky)
check("sanitizer drops path/command/hostname/token",
      set(clean) <= {"queue_status", "observed_at", "ready_count", "integration", "reason_code"},
      json.dumps(sorted(clean)))
blob = json.dumps(clean)
check("no path, command, hostname or token survives",
      not any(s in blob for s in ("/root", TRACKER, os.uname().nodename, "tok-secret", project)),
      blob[:160])

# 4. no tracker at all is `unavailable`, never an invented reading
absent = Queue(project, registry, command="/nonexistent/tracker",
               service="https://team.example.invalid").status()
check("an absent tracker is unavailable", absent.get("queue_status") == "unavailable",
      json.dumps(absent)[:160])
check("unavailable invents no count", "ready_count" not in absent, json.dumps(absent)[:120])

# 5. a read that succeeded then failed is `stale`, keeping the ORIGINAL time
q2 = Queue(project, registry, service="https://team.example.invalid")
good = q2.status()
q2.command = "/nonexistent/tracker"
stale = q2.status()
check("a failed probe after a good one is stale", stale.get("queue_status") == "stale",
      json.dumps(stale)[:160])
check("stale keeps the ORIGINAL observed_at",
      stale.get("observed_at") == good.get("observed_at"),
      "%s vs %s" % (stale.get("observed_at"), good.get("observed_at")))

subprocess.run([TRACKER, "remove", project, "--yes"], capture_output=True, text=True, timeout=120)

for name, ok, detail in checks:
    print("%-4s %-52s %s" % ("PASS" if ok else "FAIL", name, detail if not ok else ""))
print(json.dumps({"checks": len(checks), "passed": len(checks) - failed, "failed": failed}))
sys.exit(1 if failed else 0)
