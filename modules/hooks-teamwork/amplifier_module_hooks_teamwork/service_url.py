"""Validation for the Teamwork service recipient boundary."""
from urllib.parse import urlsplit, urlunsplit


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# This deployment's own domain, and the value it publishes at /api/config as
# `share_url` -- which is the same value its member plane gates the Origin on.
# One named constant so every caller shares the default instead of repeating it.
#
# It replaced a platform-generated Azure Container Apps host, which was a fact
# about where the containers ran rather than about where the project lives. When
# the deployment moved, that default did not, and every new joiner was refused
# 403 before their credential was read. A domain the project controls can follow
# the project; and enrollment now ASKS the service for its origin as well
# (setup_teamwork.discover_origin), so this is a starting point, not a guess
# anything depends on being current.
DEFAULT_BASE_URL = "https://teamwork.amplifier.ms"


def validate_service_url(value):
    """Return a normalized service URL or reject an ambiguous recipient."""
    if not isinstance(value, str) or not value:
        raise ValueError("Teamwork service URL is required")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Teamwork service URL cannot contain whitespace or control characters")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Teamwork service URL has an invalid port") from error

    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Teamwork service URL must be an unambiguous HTTP(S) origin")
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("Teamwork requires HTTPS except literal loopback test hosts")

    netloc = parsed.hostname
    if ":" in parsed.hostname and not parsed.hostname.startswith("["):
        netloc = f"[{parsed.hostname}]"
    if port is not None:
        netloc += f":{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def service_origin(value):
    """Return the scheme and authority for the browser-style Origin header."""
    parsed = urlsplit(validate_service_url(value))
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
