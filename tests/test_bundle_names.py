"""The installable behavior's shortname and the root standalone bundle's name."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _front_matter(markdown_text: str) -> dict:
    # bundle.md is Markdown with a YAML front-matter block delimited by "---" lines.
    _, front_matter, _rest = markdown_text.split("---", 2)
    return yaml.safe_load(front_matter)


class BundleNameTests(unittest.TestCase):
    def test_behavior_installs_under_the_short_name_teamwork(self):
        # `amplifier bundle add ".../behaviors/teamwork.yaml" --app` must register the
        # installed behavior as "teamwork" -- that is the name users see and reference
        # (e.g. `amplifier bundle update teamwork -y`), not an internal "-behavior" suffix.
        behavior = yaml.safe_load(
            (ROOT / "behaviors/teamwork.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(behavior["bundle"]["name"], "teamwork")

    def test_root_standalone_bundle_has_a_distinct_name(self):
        # The root bundle.md is used only by validate_bundle.py, the DTU tests, and the
        # legacy overlay flow -- it must never collide with the installable behavior's
        # "teamwork" registry name.
        root_bundle = _front_matter((ROOT / "bundle.md").read_text(encoding="utf-8"))
        self.assertEqual(root_bundle["bundle"]["name"], "teamwork-standalone")
        self.assertNotEqual(root_bundle["bundle"]["name"], "teamwork")


def _version(text):
    return tuple(int(part) for part in text.split("."))


class BundleVersionTests(unittest.TestCase):
    def versions(self):
        import re
        behavior = yaml.safe_load((ROOT / "behaviors/teamwork.yaml").read_text(encoding="utf-8"))
        root_bundle = _front_matter((ROOT / "bundle.md").read_text(encoding="utf-8"))
        module = re.search(r'^version = "([^"]+)"',
                           (ROOT / "modules/hooks-teamwork/pyproject.toml").read_text(encoding="utf-8"),
                           re.MULTILINE).group(1)
        return behavior["bundle"]["version"], root_bundle["bundle"]["version"], module

    def test_the_three_declared_versions_agree(self):
        behavior, root_bundle, module = self.versions()
        self.assertEqual(behavior, root_bundle)
        self.assertEqual(behavior, module)

    def test_the_queue_and_waiting_contract_ships_as_at_least_0_4_0(self):
        # Queue observations and declared waiting are new wire fields; a host on
        # 0.3.0 must be able to tell from the version alone that it has them.
        behavior, _, _ = self.versions()
        self.assertGreaterEqual(_version(behavior), (0, 4, 0))


if __name__ == "__main__":
    unittest.main()
