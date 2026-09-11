"""Entra (Azure AD) access tokens via `az login`, for SSO enrollment only.

`azure.identity` is lazy-imported inside the functions below, so a host
without it keeps the member-code path working (see pyproject.toml's optional
`sso` extra). `AzureCliCredential`, not `DefaultAzureCredential`: the bundle
runs on a developer workstation and a silently-selected managed identity
would be an unexplained identity. Tokens are cached per scope in this
process's memory only; never logged, never returned in any error message.
"""
import time

_CACHE = {}  # {scope: (token, expires_on)}
_NEAR_EXPIRY_SECONDS = 300


class EntraUnavailable(RuntimeError):
    """Self-authored message, safe to surface to the user; never service or SDK text."""


def available():
    """Return whether azure.identity is importable on this host."""
    try:
        import azure.identity  # noqa: F401
    except Exception:
        return False
    return True


def access_token(scope):
    """Return a cached or freshly minted Entra access token for `scope`. Never logged."""
    cached = _CACHE.get(scope)
    if cached and cached[1] - time.time() > _NEAR_EXPIRY_SECONDS:
        return cached[0]
    try:
        from azure.identity import AzureCliCredential

        token = AzureCliCredential().get_token(scope)
    except Exception:
        raise EntraUnavailable(
            "Microsoft sign-in is unavailable on this machine. Run `az login`, then try again."
        ) from None
    _CACHE[scope] = (token.token, token.expires_on)
    return token.token
