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
    """Whether Microsoft sign-in can actually be attempted on this host.

    IT IS NOT ENOUGH THAT azure.identity IMPORTS, and the difference is not
    academic. Measured in a cold container: the library imported, this function
    returned True, the enrollment form offered Microsoft sign-in, and the
    attempt then failed with `AzureCliCredential.get_token failed: Azure CLI not
    found on path`. Every container, CI runner and headless harness is in that
    state -- library present, no way to sign in.

    An import succeeding is a PROXY for a credential existing, not the thing
    itself. So this also requires the `az` binary that AzureCliCredential shells
    out to, which is the check that decisively separates a laptop from a
    container.

    A host with `az` installed but nobody signed in still returns True here and
    fails at access_token(). That is deliberate: distinguishing those two costs
    a subprocess and a network round trip on a path that runs before a form is
    drawn, and both states are ACTIONABLE BY THE SAME PERSON AT THAT KEYBOARD --
    unlike the container case, where no remedy exists on that machine at all.
    access_token() names which of the two it is.
    """
    try:
        import azure.identity  # noqa: F401
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

        token = AzureCliCredential().get_token(scope)
    except Exception:
        # Two different machines, two different remedies. Telling someone to run
        # `az login` on a host with no `az` is advice they cannot take, and it
        # leaves them no way to learn what would actually help -- which is how a
        # container user reads "unavailable" as "broken" and stops.
        if _cli_present():
            raise EntraUnavailable(
                "Microsoft sign-in is unavailable on this machine: the Azure CLI is installed "
                "but no account is signed in. Run `az login`, then try again."
            ) from None
        raise EntraUnavailable(
            "Microsoft sign-in is unavailable on this machine: the Azure CLI is not installed, "
            "so no Microsoft identity can be obtained here. On a machine without a browser -- a "
            "container, a CI runner, a remote harness -- sign in where you do have one, create a "
            "credential there, and supply it to this machine instead."
        ) from None
    _CACHE[scope] = (token.token, token.expires_on)
    return token.token
