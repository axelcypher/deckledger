"""Shared sign-in of the Ophir apps on one host (ophir.<domain>/cards, /figures, ...).

The same file is in every Ophir app (Ophir Cards, Ophir Figures); change it in all of them at once
and bump VERSION when the token changes. docs/code/shared-auth.md describes the protocol.

Every app runs in one of three modes (OPHIR_AUTH_MODE):

- standalone: the app signs its users in itself, as before.
- master: the app also signs people in for the others, under a path of the shared host
  (OPHIR_AUTH_PATH, /auth): /auth/login, /auth/logout, /auth/oauth/login, /auth/oauth/callback.
  After a sign-in it sets the cookie COOKIE for the whole host.
- slave: the app signs nobody in. It reads COOKIE -- the master signed it with the SECRET_KEY all
  Ophir apps share -- and sends whoever has none, or one older than FRESH_SECONDS, to
  <auth path>/login?next=<where they were>. Sign-out goes to <auth path>/logout.

The token says who is signed in at the master; the slaves keep an account of their own for that
person (linked by "sub") and take name, email and role from the token.
"""
from datetime import datetime, timezone

from itsdangerous import BadSignature, URLSafeTimedSerializer

MODES = ("standalone", "master", "slave")
COOKIE = "ophir_auth"
SALT = "ophir-auth-v1"
VERSION = 1
# The identity in a token: the master's account id ("sub"), its username, display name, email
# and role ("admin" or "user").
FIELDS = ("sub", "username", "name", "email", "role")
# How long a slave accepts a token after the master signed it. The master signs it again on its
# own pages and on every pass through <auth path>/login, each time after checking that the account
# still exists, so an account deleted or demoted there stops working at the slaves within this time.
FRESH_SECONDS = 12 * 3600
# The master renews a token older than this on its own pages, so the slaves keep getting fresh ones.
RENEW_AFTER_SECONDS = 3600
# The oldest token the master still renews without asking for the password again.
MASTER_MAX_AGE_SECONDS = 30 * 24 * 3600


def _serializer(secret_key):
    return URLSafeTimedSerializer(secret_key, salt=SALT)


def sign(secret_key, identity):
    """The cookie value for an identity (a mapping with the keys in FIELDS)."""
    return _serializer(secret_key).dumps({"v": VERSION, **{field: str(identity.get(field) or "") for field in FIELDS}})


def read(secret_key, value, max_age):
    """(identity, age in seconds) of a token signed with this key within max_age seconds;
    (None, None) for anything else -- no cookie, a forged or foreign one, an outdated one."""
    if not value:
        return None, None
    try:
        payload, signed_at = _serializer(secret_key).loads(value, max_age=max_age, return_timestamp=True)
    except BadSignature:  # also SignatureExpired
        return None, None
    if not isinstance(payload, dict) or payload.get("v") != VERSION or not payload.get("sub"):
        return None, None
    age = max(0.0, (datetime.now(timezone.utc) - signed_at).total_seconds())
    return {field: str(payload.get(field) or "") for field in FIELDS}, age


def set_cookie(response, value, secure):
    # For the whole host: every Ophir app on it reads the same sign-in. A browser-session cookie,
    # like the apps' own sessions.
    response.set_cookie(COOKIE, value, path="/", httponly=True, samesite="Lax", secure=secure)


def clear_cookie(response, secure):
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="Lax", secure=secure)


def safe_next(value, default):
    """A path on this host to go to after signing in or out. Anything else -- another host, a
    scheme, a protocol-relative //host, backslashes or control characters -- gives the default."""
    value = str(value or "")
    if value.startswith("/") and not value.startswith("//") and "\\" not in value and all(ord(char) >= 32 for char in value):
        return value
    return default
