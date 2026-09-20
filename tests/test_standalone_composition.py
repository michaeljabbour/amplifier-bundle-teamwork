"""Resolve the real root includes while the native behavior shares the registry."""
import json
import tempfile
import unittest
from pathlib import Path

from amplifier_foundation import BundleRegistry


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"


class StandaloneComposition(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        foundation = directory / "foundation.yaml"
        self.providers = [{"module": "provider-fixture", "config": {"default_model": "fixture"}}]
        self.session = {"orchestrator": {"module": "loop-fixture"},
                        "context": {"module": "context-fixture", "config": {"max_tokens": 123}}}
        foundation.write_text(json.dumps({"bundle": {"name": "foundation", "version": "1.0.0"},
                                          "session": self.session, "providers": self.providers}))
        # Only the external Foundation include is substituted. Teamwork's real
        # self-include must resolve through the released registry implementation.
        self.registry = BundleRegistry(home=directory / "home", strict=True,
            include_source_resolver=lambda uri: foundation.as_uri() if uri == FOUNDATION else None)
        self.registry.register({"teamwork-standalone": (ROOT / "bundle.md").as_uri(),
                                "teamwork": (ROOT / "behaviors/teamwork.yaml").as_uri()})

    def assert_composition(self, bundle):
        self.assertEqual(bundle.name, "teamwork-standalone")
        self.assertEqual(bundle.session, self.session)
        self.assertEqual(bundle.providers, self.providers)
        hooks = [h for h in bundle.hooks if h["module"] == "hooks-teamwork"]
        self.assertEqual(len(hooks), 1)
        self.assertFalse(hooks[0].get("config", {}).get("share_visible_turns", False))
        self.assertEqual(len([t for t in bundle.tools if t["module"] == "tool-teamwork"]), 1)

    async def test_standalone_resolves_after_native_behavior_is_loaded(self):
        await self.registry.load("teamwork")
        self.assert_composition(await self.registry.load("teamwork-standalone"))

    async def test_standalone_resolves_before_native_behavior_is_loaded(self):
        standalone = await self.registry.load("teamwork-standalone")
        self.assert_composition(standalone)
        behavior = await self.registry.load("teamwork")
        composed = standalone.compose(behavior)
        self.assertEqual(composed.session, self.session)
        self.assertEqual(composed.providers, self.providers)
        self.assertEqual(len([h for h in composed.hooks if h["module"] == "hooks-teamwork"]), 1)

    async def test_standalone_does_not_require_native_behavior_registration(self):
        self.registry.unregister("teamwork")
        self.assert_composition(await self.registry.load("teamwork-standalone"))
