"""Entra (Azure AD) access tokens via `az login`, for SSO enrollment only.

`azure.identity` is lazy-imported inside the functions below, so a host
without it keeps the member-code path working (see pyproject.toml's optional
`sso` extra). `AzureCliCredential`, not `DefaultAzureCredential`: the bundle
runs on a developer workstation and a silently-selected managed identity
would be an unexplained identity. Tokens are cached per scope in this
process's memory only; never logged, never returned in any error message.
"""
import shutil
import time

_CACHE = {}  # {scope: (token, expires_on)}
_NEAR_EXPIRY_SECONDS = 300


class EntraUnavailable(RuntimeError):
    """Self-authored message, safe to surface to the user; never service or SDK text."""


def _cli_present():
    """Whether the Azure CLI this module's only credential source shells out to exists."""
    return shutil.which("az") is not None


def available():
    """Check the SDK and executable needed to attempt Microsoft sign-in.

    This form-time probe makes no subprocess or token request. It does not
    establish that an account is signed in or a token can be obtained.
    """
    try:
        from azure.identity import AzureCliCredential  # noqa: F401
    except Exception:
        return False
    return _cli_present()


def access_token(scope):
    """Return a cached or freshly minted Entra access token for `scope`. Never logged."""
    cached = _CACHE.get(scope)
    if cached and cached[1] - time.time() > _NEAR_EXPIRY_SECONDS:
        return cached[0]
    try:
        from azure.identity import AzureCliCredential
    except Exception:
        raise EntraUnavailable(
            "Microsoft sign-in is unavailable because the optional Azure identity support "
            "is not installed or could not load. Install Teamwork's SSO dependencies, "
            "or attach a project agent credential created in Teamwork."
        ) from None
    if not _cli_present():
        raise EntraUnavailable(
            "Microsoft sign-in is unavailable on this machine: the Azure CLI is not installed. "
            "Install the Azure CLI to use Microsoft sign-in here, or sign in to Teamwork "
            "on another machine, create a project agent credential, and supply it to this machine."
        )
    try:
        token = AzureCliCredential().get_token(scope)
    except Exception:
        raise EntraUnavailable(
            "Microsoft sign-in could not obtain a token from the Azure CLI. "
            "Check `az account show`; if sign-in is needed, run `az login`, then try again. "
            "If already signed in, check connectivity and access to the requested service. "
            "You can also attach a project agent credential created in Teamwork."
        ) from None
    _CACHE[scope] = (token.token, token.expires_on)
    return token.token
