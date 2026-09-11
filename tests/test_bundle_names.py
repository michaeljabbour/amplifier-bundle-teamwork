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


if __name__ == "__main__":
    unittest.main()
