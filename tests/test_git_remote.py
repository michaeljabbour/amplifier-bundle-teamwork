"""Local, no-network git remote reading and canonical repository identity."""
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules/hooks-teamwork"))
from amplifier_module_hooks_teamwork import git_remote


class RepositoryIdentityTests(unittest.TestCase):
    def test_ssh_and_https_remotes_canonicalize_identically(self):
        expected = "https://github.com/owner/repo"
        for value in (
            "git@github.com:owner/repo.git",
            "git@github.com:owner/repo",
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo/",
        ):
            with self.subTest(value=value):
                self.assertEqual(git_remote.repository_identity(value), expected)

    def test_credential_bearing_remote_is_refused(self):
        for value in (
            "https://user:token@github.com/owner/repo",
            "https://token@github.com/owner/repo",
            "ssh://not-git@github.com/owner/repo",
            "ssh://git:password@github.com/owner/repo",
        ):
            with self.subTest(value=value):
                self.assertIsNone(git_remote.repository_identity(value))

    def test_non_github_and_malformed_remotes_are_refused(self):
        for value in (
            "https://github.com@evil.invalid/owner/repo",
            "https://github.com/owner/repo?t=x",
            "https://github.com/owner/repo#fragment",
            "https://gitlab.com/owner/repo",
            "/local/path/to/repo",
            "file:///local/path/to/repo",
            "https://github.com/owner/.",
            "https://github.com/owner/..",
            "not a url at all",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(git_remote.repository_identity(value))

    def test_origin_url_returns_none_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(git_remote.origin_url(cwd=directory))

    def test_origin_url_reads_a_configured_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/owner/repo.git"],
                cwd=directory, check=True,
            )
            self.assertEqual(git_remote.origin_url(cwd=directory), "https://github.com/owner/repo.git")


if __name__ == "__main__":
    unittest.main()
