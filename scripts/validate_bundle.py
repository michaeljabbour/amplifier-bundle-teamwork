"""Validate and prepare the real local hook without credentials or provider calls."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from amplifier_foundation import load_bundle, validate_bundle
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_teamwork import build_overlay


async def main():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if not any(part in (".venv", ".git", "dist") for part in path.relative_to(root).parts):
            compile(path.read_text(), str(path.relative_to(root)), "exec")
    root_bundle = await load_bundle((root / 'bundle.md').as_uri(), auto_include=False, strict=True)
    behavior = await load_bundle((root / 'behaviors/teamwork.yaml').as_uri(), strict=True)
    for bundle in (root_bundle, behavior):
        result = validate_bundle(bundle)
        if not result.valid:
            raise RuntimeError(result.errors)
    with tempfile.TemporaryDirectory() as directory:
        overlay = build_overlay(str(root / 'behaviors/teamwork.yaml'), Path(directory) / 'connection.json')
        # Repeating the behavior as the base is unnecessary for this narrow hook test.
        overlay['includes'] = overlay['includes'][1:]
        path = Path(directory) / 'overlay.yaml'
        path.write_text(json.dumps(overlay))
        composed = await load_bundle(path.as_uri(), strict=True)
        hook = next(h for h in composed.hooks if h['module'] == 'hooks-teamwork')
        assert Path(hook['source']) == root / 'modules/hooks-teamwork'
        assert hook['config']['share_visible_turns'] is True
        prepared = await composed.prepare(install_deps=False, strict=True)
        assert prepared is not None
    print('PASS: root/behavior schema, composed opt-in overlay, local hook preparation. No provider call or enrollment.')


if __name__ == '__main__':
    asyncio.run(main())
