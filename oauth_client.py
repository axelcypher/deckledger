"""Generic OAuth2/OIDC client mechanics for DeckLedger's optional SSO login.

Deliberately built on Authlib (github.com/lepture/authlib) instead of hand-rolled
PKCE/state/token-exchange code -- reimplementing the OAuth2 authorization-code +
PKCE dance and safely consuming token/userinfo responses is exactly the kind of
security-sensitive plumbing a vetted library should own, not this app.

Every function here takes the resolved config dict as a plain argument (see
app.py's resolve_oauth_config()) and has no Flask/DB coupling of its own, so it
behaves identically whether the config came from the mounted file or the
database, and stays independently exercisable (e.g. against a mocked HTTP
response) without spinning up the whole app.
"""
import functools
import time

import requests
from authlib.common.security import generate_token
from authlib.integrations.requests_client import OAuth2Session

_REQUEST_TIMEOUT = 10
_DISCOVERY_TTL_SECONDS = 300
_discovery_cache = {}


class OAuthConfigError(Exception):
    """A disabled/incomplete config, an unreachable/misbehaving IdP, or a
    response missing what we need -- callers turn this into a single
    user-facing message instead of a requests/authlib stack trace."""


def _as_oauth_error(func):
    # Boundary between "anything can go wrong talking to an external IdP over
    # HTTP" (DNS failure, timeout, non-2xx status, malformed JSON, an OAuth2
    # protocol-level error response) and this module's one exposed error type.
    # Deliberately catches broadly -- every failure mode here ends up meaning
    # the same thing to a caller: show a generic "provider unreachable/refused"
    # message rather than leak transport details to the login page.
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OAuthConfigError:
            raise
        except Exception as exc:
            raise OAuthConfigError(f"Kommunikation mit dem Identity Provider fehlgeschlagen: {exc}") from exc
    return wrapped


@_as_oauth_error
def discover_endpoints(discovery_url):
    # Cached per discovery_url (not per config) -- a config edit that changes any
    # OTHER field never serves a stale entry, and this only ever saves a round
    # trip to the SAME IdP's own well-known document between login clicks.
    cached = _discovery_cache.get(discovery_url)
    if cached and time.monotonic() - cached[0] < _DISCOVERY_TTL_SECONDS:
        return cached[1]
    response = requests.get(discovery_url, timeout=_REQUEST_TIMEOUT)
    response.raise_for_status()
    document = response.json()
    endpoints = {
        "authorize_url": document.get("authorization_endpoint") or "",
        "token_url": document.get("token_endpoint") or "",
        "userinfo_url": document.get("userinfo_endpoint") or "",
    }
    if not all(endpoints.values()):
        raise OAuthConfigError("Das Discovery-Dokument des Identity Providers enthält nicht alle benötigten Endpunkte.")
    _discovery_cache[discovery_url] = (time.monotonic(), endpoints)
    return endpoints


def resolved_endpoints(config):
    """Manually configured endpoints win; OIDC discovery only fills in whichever
    of the three are still missing after that."""
    endpoints = {
        "authorize_url": config.get("authorize_url") or "",
        "token_url": config.get("token_url") or "",
        "userinfo_url": config.get("userinfo_url") or "",
    }
    if config.get("discovery_url") and not all(endpoints.values()):
        discovered = discover_endpoints(config["discovery_url"])
        endpoints = {key: value or discovered[key] for key, value in endpoints.items()}
    if not all(endpoints.values()):
        raise OAuthConfigError("OAuth ist nicht vollständig konfiguriert: Endpunkte fehlen (Discovery-URL oder manuelle Endpunkte angeben).")
    if not config.get("client_id") or not config.get("client_secret"):
        raise OAuthConfigError("OAuth ist nicht vollständig konfiguriert: Client-ID oder Client-Secret fehlt.")
    return endpoints


def _session_for(config, redirect_uri):
    return OAuth2Session(
        config["client_id"], config["client_secret"],
        scope=config.get("scopes") or "openid email profile",
        redirect_uri=redirect_uri, code_challenge_method="S256",
    )


@_as_oauth_error
def build_authorization_request(config, redirect_uri):
    """Returns (auth_url, state, code_verifier). The caller must keep state and
    code_verifier in the Flask session and feed both back into exchange_code()
    on the callback -- this module never holds server-side flow state itself."""
    endpoints = resolved_endpoints(config)
    session = _session_for(config, redirect_uri)
    code_verifier = generate_token(48)
    auth_url, state = session.create_authorization_url(endpoints["authorize_url"], code_verifier=code_verifier)
    return auth_url, state, code_verifier


@_as_oauth_error
def exchange_code(config, redirect_uri, code, code_verifier):
    endpoints = resolved_endpoints(config)
    session = _session_for(config, redirect_uri)
    return session.fetch_token(endpoints["token_url"], code=code, code_verifier=code_verifier)


@_as_oauth_error
def fetch_userinfo(config, token):
    endpoints = resolved_endpoints(config)
    access_token = token.get("access_token") if isinstance(token, dict) else token
    response = requests.get(
        endpoints["userinfo_url"], headers={"Authorization": f"Bearer {access_token}"}, timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def extract_identity(config, claims):
    """Pulls (subject, email, display_name) out of a userinfo claims dict using
    the configured claim names, with sane fallbacks for providers that only
    populate a subset of them."""
    subject = str(claims.get(config.get("subject_claim") or "sub") or "").strip()
    if not subject:
        raise OAuthConfigError("Der Identity Provider hat keine eindeutige Nutzerkennung (sub) geliefert.")
    email = str(claims.get(config.get("email_claim") or "email") or "").strip()
    username_claim = config.get("username_claim") or "preferred_username"
    display_name = (
        str(claims.get(username_claim) or "").strip()
        or str(claims.get("name") or "").strip()
        or email or subject
    )
    return subject, email, display_name
