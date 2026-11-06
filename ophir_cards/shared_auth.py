"""Shared sign-in with the other Ophir apps on one host (OPHIR_AUTH_MODE). ophir_auth.py holds the
protocol, docs/code/shared-auth.md the whole picture.

The rest of the app keeps asking session["user_id"] who is signed in. In master and slave mode the
hooks here set it from the host-wide cookie on every request, so signing out anywhere signs out
everywhere:

- master: the cookie names one of this app's own accounts. It is renewed while that account
  exists and dropped when the session ends (sign-out) or the account is gone. Under the auth path
  (/auth on the shared host) only the sign-in pages are served.
- slave: the cookie names an account of the master. The matching account here is found, linked
  or created, and takes the master's name, email and role.
"""
from urllib.parse import quote, urlencode

from flask import abort, g, jsonify, redirect, request, session

import ophir_auth

from . import config
from .config import now_iso
from .schema import ensure_user_lists
from .web import app, db

if config.AUTH_MODE not in ophir_auth.MODES:
    raise SystemExit(f"OPHIR_AUTH_MODE must be one of {', '.join(ophir_auth.MODES)}, not {config.AUTH_MODE!r}.")

# What the master serves under its auth path; everything else of the app answers 404 there.
AUTH_ENDPOINTS = frozenset({"login", "logout", "oauth_login", "oauth_callback", "static", "health"})
MANAGED_BY_MASTER = "Konten werden in der Ophir-App verwaltet, die die Anmeldung übernimmt."


def under_auth_path():
    """Whether this request reached the master through its auth path (/auth)."""
    return config.AUTH_MODE == "master" and request.script_root == config.AUTH_PATH


def signs_in_elsewhere():
    """Whether sign-in, sign-out and SSO of this request belong at the auth path: always for a
    slave, and for the master when it is served under a path of the shared host (/cards)."""
    if config.AUTH_MODE == "slave":
        return True
    return config.AUTH_MODE == "master" and bool(request.script_root) and not under_auth_path()


def auth_base():
    """Where sign-in, sign-out and SSO are for this request: the auth path on the shared host, else
    the app's own root."""
    return config.AUTH_PATH if signs_in_elsewhere() or under_auth_path() else request.script_root


app.add_template_global(auth_base)


def start_page():
    """Where a sign-in ends that did not say where to go: this app's start page, or OPHIR_AUTH_HOME
    at the auth path, which serves no app."""
    return config.AUTH_HOME if under_auth_path() else f"{request.script_root}/"


def next_target():
    """The request's validated "next" (query or form), else start_page()."""
    return ophir_auth.safe_next(request.values.get("next"), start_page())


def with_query(url, **params):
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def to_auth_path(endpoint_path, next_path=None):
    """A redirect to <auth path><endpoint_path>, coming back to next_path (default: this app's
    start page). POSTs are redirected as POSTs (307)."""
    target = ophir_auth.safe_next(next_path, f"{request.script_root}/")
    code = 307 if request.method == "POST" else 302
    return redirect(f"{config.AUTH_PATH}{endpoint_path}?next={quote(target, safe='/')}", code=code)


def refuse_when_slave():
    """The answer to an account change a slave leaves to the master (accounts, passwords, SSO
    links); None when the app may make it."""
    if config.AUTH_MODE == "slave":
        return jsonify({"error": MANAGED_BY_MASTER}), 409
    return None


def local_account(identity):
    """This app's account id for a person signed in at the master: the account linked to them,
    else an unlinked one with their email or username (linked from now on), else a new one without
    a password of its own. Display name, email and role follow the master."""
    from .auth import unique_username_from  # auth imports this module

    connection = db()
    subject = identity["sub"]
    role = "admin" if identity["role"] == "admin" else "user"
    name = identity["name"] or identity["username"] or subject
    row = connection.execute("SELECT id FROM users WHERE auth_subject=?", (subject,)).fetchone()
    if row is None:
        if identity["email"]:
            row = connection.execute(
                "SELECT id FROM users WHERE auth_subject='' AND email!='' AND lower(email)=lower(?) ORDER BY id LIMIT 1", (identity["email"],),
            ).fetchone()
        if row is None and identity["username"]:
            row = connection.execute(
                "SELECT id FROM users WHERE auth_subject='' AND lower(username)=lower(?)", (identity["username"],),
            ).fetchone()
        if row is not None:
            connection.execute("UPDATE users SET auth_subject=? WHERE id=?", (subject, row["id"]))
        else:
            username = unique_username_from(identity["username"] or name)
            cursor = connection.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at,email,auth_subject) VALUES(?,?,?,?,?,?,?)",
                (username, name, "", role, now_iso(), identity["email"], subject),
            )
            ensure_user_lists(connection, cursor.lastrowid)
        connection.commit()
        row = connection.execute("SELECT id FROM users WHERE auth_subject=?", (subject,)).fetchone()
    current = connection.execute("SELECT display_name,email,role FROM users WHERE id=?", (row["id"],)).fetchone()
    wanted = {"display_name": name, "role": role, **({"email": identity["email"]} if identity["email"] else {})}
    changes = {column: value for column, value in wanted.items() if current[column] != value}
    if changes:
        connection.execute(f"UPDATE users SET {','.join(f'{column}=?' for column in changes)} WHERE id=?", (*changes.values(), row["id"]))
        connection.commit()
    return row["id"]


@app.before_request
def sign_in_from_shared_cookie():
    if config.AUTH_MODE not in ("master", "slave"):
        return None
    # A slave only takes a recent token; the master renews older ones after checking the account.
    max_age = ophir_auth.MASTER_MAX_AGE_SECONDS if config.AUTH_MODE == "master" else ophir_auth.FRESH_SECONDS
    identity, age = ophir_auth.read(app.config["SECRET_KEY"], request.cookies.get(ophir_auth.COOKIE), max_age)
    g.shared_identity, g.shared_age = identity, age
    if config.AUTH_MODE == "master":
        uid = int(identity["sub"]) if identity and identity["sub"].isdigit() else None
    else:
        uid = local_account(identity) if identity else None
    if uid is None:
        if "user_id" in session:
            session.pop("user_id")
    elif session.get("user_id") != uid:
        session["user_id"] = uid
    # Only now may renew_shared_cookie trust session["user_id"]: a session cookie can still name
    # someone who has signed out since, and a request stopped earlier (by this 404 or by another
    # hook, such as a refused cross-site write) must not hand that out as a new sign-in.
    g.shared_synced = True
    if under_auth_path() and request.endpoint not in AUTH_ENDPOINTS:
        abort(404)
    return None


@app.after_request
def renew_shared_cookie(response):
    """Master: hand the signed-in account to the other apps, renewed when it is older than
    RENEW_AFTER_SECONDS, and take it back once nobody is signed in."""
    if config.AUTH_MODE != "master" or not g.get("shared_synced"):
        return response
    secure = request.is_secure
    uid = session.get("user_id")
    identity, age = g.get("shared_identity"), g.get("shared_age")
    if not uid:
        if request.cookies.get(ophir_auth.COOKIE):
            ophir_auth.clear_cookie(response, secure)
        return response
    if identity and identity["sub"] == str(uid) and age is not None and age < ophir_auth.RENEW_AFTER_SECONDS:
        return response
    row = db().execute("SELECT id,username,display_name,email,role FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        ophir_auth.clear_cookie(response, secure)
        return response
    token = ophir_auth.sign(app.config["SECRET_KEY"], {"sub": row["id"], "username": row["username"], "name": row["display_name"], "email": row["email"], "role": row["role"]})
    ophir_auth.set_cookie(response, token, secure)
    return response
