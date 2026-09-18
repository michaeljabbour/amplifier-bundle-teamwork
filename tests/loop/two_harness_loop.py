"""Bounded clock/answer check using real hooks and a synthetic service fixture.

Runs both answer and no-answer controls. This exercises the shipped wait, tasks,
answer and lifecycle code; it does not prove a deployed service or model obeys.
Use live_two_harness.py only with explicit isolated QA credentials for that layer.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules/hooks-teamwork"))
import amplifier_module_hooks_teamwork as tw


class Host:
    parent_id = None
    mount_points = {}
    def __init__(self, sid): self.session_id = sid
    def get_capability(self, name): return None


class Service:
    def __init__(self):
        self.records = {"request:req-alpha": {"id": "req-alpha", "version": 1,
            "title": "Fixture question", "requested_person_id": "fixture-recipient"}}
    def request(self, endpoint, body, key=None):
        if endpoint == "publish":
            for op in body["operations"]:
                kind = op["op"].split(".")[0]
                record = self.records.setdefault(kind + ":" + op["id"], {"id": op["id"]})
                record.update(op["data"], version=op["expected_version"] + 1)
                if kind == "agent": record["owner_person_id"] = "fixture-recipient"
            return {"stored": True}
        if endpoint == "context":
            return {"items": [{"id": r["id"], "record_type": k.split(":")[0],
                "key": k + ":" + str(r["version"]), "version": r["version"],
                "change": "upsert", "content": dict(r)} for k, r in self.records.items()],
                "next_cursor": "fixture", "delivery_id": "fixture", "has_more": False, "truncated": False}
        return {}


async def run(answering):
    service = Service()
    connection = {"base_url": "https://fixture.invalid", "project_id": "qa", "token": "fixture-only"}
    with tempfile.TemporaryDirectory() as work:
        hooks = [tw.TeamworkHook(Host(name), connection, tw.Journal(Path(work) / (name + ".db")), service)
                 for name in ("a", "b")]
        a, b = hooks
        writer = None
        try:
            for hook in hooks: await hook.on_start("session:start", {})
            async def reply():
                await asyncio.sleep(.05)
                listed = await tw.TasksTool(b).execute({})
                assert any(r["id"] == "req-alpha" for r in listed.output["assigned"])
                result = await tw.AnswerTool(b).execute({"request_id": "req-alpha", "response": "context", "note": "Fixture reply"})
                assert result.success
            if answering: writer = asyncio.create_task(reply())
            with patch.object(tw, "WAIT_POLL_SECONDS", .01), patch.object(tw, "WAIT_MAX_SECONDS", .25):
                result = await tw.WaitTool(a).execute({"reason": "fixture", "request_id": "req-alpha"})
            if writer: await writer
            assert result.success
            if answering:
                assert result.output["answer"]["note"] == "Fixture reply"
                assert result.output["state"] == "answered"
            else:
                assert result.output["state"] == "waiting" and "answer" not in result.output
            return {"control": "answer" if answering else "no-answer", "passed": True}
        finally:
            if writer:
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            for hook in hooks: await hook.cleanup()


async def main():
    print(json.dumps([await run(True), await run(False)]))


if __name__ == "__main__":
    asyncio.run(main())
