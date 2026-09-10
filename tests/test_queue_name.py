"""The teamwork project to work-tracker project mapping, and its refusal."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules" / "hooks-teamwork"))

from amplifier_module_hooks_teamwork.queue_name import (VALID, QueueNameConflict, bind, normalise)


class Normalise(unittest.TestCase):
    def test_awkward_ids_still_produce_a_legal_name(self):
        for project_id in ("design-review", "Design Review", "teamwork", "a/b/c",
                           "2026-planning", "UPPER_CASE", "  spaced  "):
            name = normalise(project_id)
            self.assertTrue(VALID.match(name), "%r produced %r" % (project_id, name))

    def test_the_same_id_always_produces_the_same_name(self):
        # Both sides of the join apply this rule independently; if it were not
        # deterministic it would not be a join.
        self.assertEqual(normalise("design-review"), normalise("design-review"))

    def test_a_leading_digit_is_prefixed_rather_than_dropped(self):
        # Dropping it would map "2026-planning" and "planning" together.
        self.assertNotEqual(normalise("2026-planning"), normalise("planning"))

    def test_an_id_with_nothing_usable_is_refused(self):
        for empty in ("", "   ", "---", "///"):
            with self.assertRaises(ValueError):
                normalise(empty)


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.registry = Path(self.tmp.name) / "queues.json"

    def test_the_first_project_to_claim_a_name_keeps_it(self):
        self.assertEqual(bind("design-review", self.registry), "design_review")
        self.assertEqual(json.loads(self.registry.read_text()), {"design_review": "design-review"})

    def test_rebinding_the_same_project_is_not_a_conflict(self):
        first = bind("design-review", self.registry)
        self.assertEqual(bind("design-review", self.registry), first)

    def test_a_second_project_that_would_share_the_queue_is_refused(self):
        # "design-review" and "design_review" normalise identically. Sharing the
        # backlog silently is the exact failure this mapping exists to prevent.
        bind("design-review", self.registry)
        with self.assertRaises(QueueNameConflict) as caught:
            bind("design_review", self.registry)
        self.assertIn("already bound", str(caught.exception))

    def test_an_unreadable_registry_refuses_rather_than_starting_over(self):
        # Treating a corrupt file as empty would hand away a name another project
        # is already using -- a silent merge produced by an unrelated failure.
        self.registry.write_text("{not json")
        with self.assertRaises(QueueNameConflict):
            bind("design-review", self.registry)

    def test_the_same_project_id_on_two_services_is_two_projects(self):
        # A project id is unique only within a service. Two deployments that both
        # call a project "teamwork" are different projects with different members,
        # and quietly handing them one backlog is the failure this refusal exists
        # to prevent -- arriving by a different door than a normalisation clash.
        bind("teamwork", self.registry, "https://team.example.invalid")
        with self.assertRaises(QueueNameConflict) as caught:
            bind("teamwork", self.registry, "http://localhost:8090")
        self.assertIn("already bound", str(caught.exception))

    def test_rebinding_the_same_project_on_the_same_service_is_not_a_conflict(self):
        first = bind("teamwork", self.registry, "https://team.example.invalid/")
        self.assertEqual(bind("teamwork", self.registry, "https://team.example.invalid"), first)

    def test_the_registry_is_not_world_readable(self):
        bind("design-review", self.registry)
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
