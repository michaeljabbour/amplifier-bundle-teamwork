"""Validate and prepare the real local hook without credentials or provider calls."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from amplifier_foundation import BundleRegistry, load_bundle, validate_bundle
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from setup_teamwork import build_overlay
from amplifier_module_hooks_teamwork import Journal, sha


async def main():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if not any(part in (".venv", ".git", "dist") for part in path.relative_to(root).parts):
            compile(path.read_text(encoding="utf-8"), str(path.relative_to(root)), "exec")
    root_bundle = await load_bundle((root / "bundle.md").as_uri(), auto_include=False, strict=True)
    behavior = await load_bundle((root / "behaviors/teamwork.yaml").as_uri(), auto_include=False, strict=True)
    for bundle in (root_bundle, behavior):
        result = validate_bundle(bundle)
        if not result.valid:
            raise RuntimeError(result.errors)
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        base = directory / "local-base.yaml"
        base.write_text(
            "\n".join((
                "bundle:",
                "  name: local-base",
                "  version: 0.1.0",
                "session:",
                "  raw: true",
                "  context:",
                "    max_tokens: 123",
                "",
            )),
            encoding="utf-8",
        )
        connection = directory / "connection.json"
        connection.write_text(
            json.dumps({
                "base_url": "http://127.0.0.1:9",
                "project_id": "validator",
                "token": "validator-token",
            }),
            encoding="utf-8",
        )
        connection.chmod(0o600)
        overlay = build_overlay(str(base), connection)
        path = directory / "overlay.yaml"
        path.write_text(json.dumps(overlay), encoding="utf-8")
        local_sources = {
            overlay["includes"][0]["bundle"]: base.as_uri(),
            overlay["includes"][1]["bundle"]: (root / "behaviors/teamwork.yaml").as_uri(),
        }
        registry = BundleRegistry(
            home=directory / "isolated-amplifier-home",
            strict=True,
            include_source_resolver=local_sources.get,
        )
        composed = await registry.load(path.as_uri())
        assert composed.session == {"raw": True, "context": {"max_tokens": 123}}
        hooks = [hook for hook in composed.hooks if hook["module"] == "hooks-teamwork"]
        assert len(hooks) == 1
        hook = hooks[0]
        assert Path(hook["source"]) == root / "modules/hooks-teamwork"
        assert hook["config"]["share_visible_turns"] is True
        prepared = await composed.prepare(install_deps=False, strict=True)
        assert prepared is not None
        Journal(connection.parent / ("outbox-" + sha("validator-token")[:16] + ".sqlite3"))
        replay = subprocess.run(
            [sys.executable, "-I", "-S", str(root / "replay_pending.py"), "--connection-file", str(connection)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert replay.returncode == 0, replay.stderr
        assert '"pending_requests": 0' in replay.stdout
    print("PASS: root schema without includes, behavior schema, isolated local composition preserving "
          "nonempty session/context, one enabled local hook, bounded local prepare, and standalone "
          "empty replay. No provider call, enrollment, remote Foundation include, or cache reuse.")


if __name__ == '__main__':
    asyncio.run(main())
