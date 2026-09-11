"""Local, no-network git remote reading and canonical GitHub repository identity.

`repository_identity` never transmits a credential-bearing remote: any userinfo
(other than the conventional SSH `git@` account), query, fragment, or non-
`github.com` host returns None rather than a value. Mirrors
`backend/repositories.py::repository_identity` in the service repo.
"""
import re
import subprocess
from urllib.parse import urlsplit

_SCP_LIKE = re.compile(r"^git@github\.com:(?P<path>.+)$")
_OWNER_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}")


def origin_url(cwd=None):
    """Return this directory's `origin` remote URL, or None on any failure. No network."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _owner_repo(path):
    """Split a validated `owner/repo(.git)` path, or None if it does not match."""
    path = path.strip("/").removesuffix(".git")
    if not _OWNER_REPO.fullmatch(path):
        return None
    owner, repo = path.split("/", 1)
    if repo in (".", ".."):
        return None
    return owner, repo


def repository_identity(value):
    """Return the canonical `https://github.com/owner/repo`, or None for anything untrusted."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()

    scp = _SCP_LIKE.match(value)
    if scp:
        owner_repo = _owner_repo(scp.group("path"))
        return f"https://github.com/{owner_repo[0]}/{owner_repo[1]}" if owner_repo else None

    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.query or parsed.fragment:
        return None

    if parsed.scheme == "ssh":
        if parsed.hostname != "github.com" or parsed.username != "git" or parsed.password:
            return None
        owner_repo = _owner_repo(parsed.path)
        return f"https://github.com/{owner_repo[0]}/{owner_repo[1]}" if owner_repo else None

    if parsed.scheme == "https":
        if parsed.hostname != "github.com" or parsed.username or parsed.password:
            return None
        owner_repo = _owner_repo(parsed.path)
        return f"https://github.com/{owner_repo[0]}/{owner_repo[1]}" if owner_repo else None

    return None
