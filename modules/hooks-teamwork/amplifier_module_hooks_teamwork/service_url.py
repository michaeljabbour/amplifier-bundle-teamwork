"""Validation for the Teamwork service recipient boundary."""
from urllib.parse import urlsplit, urlunsplit


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
