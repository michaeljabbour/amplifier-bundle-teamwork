"""Exercise guidance through Foundation's real behavior loader/composer."""
import unittest
from pathlib import Path

from amplifier_foundation import Bundle, load_bundle


class GuidanceComposition(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.behavior = await load_bundle(
            (self.root / "behaviors/teamwork.yaml").as_uri(),
            auto_include=False,
            strict=True,
        )

    async def test_file_installed_behavior_resolves_the_awareness_pointer(self):
        paths = [self.behavior.resolve_context_path(key) for key in self.behavior.context]
        self.assertEqual(len(paths), 1)
        self.assertIsNotNone(paths[0])
        self.assertEqual(paths[0].resolve(), self.root / "context/teamwork-awareness.md")
        self.assertIn("load_skill", paths[0].read_text())

    async def test_guidance_appends_skills_and_preserves_primary_provider(self):
        primary = Bundle(
            name="primary",
            providers=[{"module": "provider-fixture"}],
            tools=[{"module": "tool-skills", "source": "fixture", "config": {
                "skills": ["existing-skills"],
            }}],
        )
        composed = primary.compose(self.behavior)
        skills = next(tool for tool in composed.tools if tool["module"] == "tool-skills")
        self.assertEqual(skills["source"], "fixture")
        self.assertEqual(skills["config"]["skills"], [
            "existing-skills",
            "git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@main#subdirectory=skills",
        ])
        self.assertEqual(composed.providers, primary.providers)
        self.assertFalse(self.behavior.hooks[0].get("config", {}).get("share_visible_turns", False))
