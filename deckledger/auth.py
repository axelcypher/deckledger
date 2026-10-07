"""Signing in: local passwords and the optional OAuth/OIDC provider."""

import json
from pathlib import Path

from flask import redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from catalog_provider_contract import slug
from oauth_client import OAuthConfigError, build_authorization_request, exchange_code, extract_identity, fetch_userinfo, id_token_claims

from .config import OAUTH_CONFIG_DEFAULTS, OAUTH_CONFIG_PATH, OAUTH_PROVIDER_KEY, jload, now_iso
from .web import app, db
from .schema import ensure_user_lists


def resolve_oauth_config():
    # File wins whenever it's mounted -- the Admin UI shows those fields read-only in that
    # case (see api_admin_oauth). Without a mounted file, settings live in app_settings and
    # are read fresh on every call (no in-process caching of this dict) so an Admin UI save
    # takes effect on the very next login click, no restart needed.
    path = Path(OAUTH_CONFIG_PATH)
    if path.is_file():
        try:
            file_config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            file_config = {}
        return {**OAUTH_CONFIG_DEFAULTS, **file_config, "source": "file"}
    row = db().execute("SELECT value FROM app_settings WHERE key='oauth_config'").fetchone()
    stored = jload(row["value"], {}) if row else {}
    return {**OAUTH_CONFIG_DEFAULTS, **stored, "source": "database"}


OAUTH_ERROR_MESSAGES = {
    "not_configured": "Single Sign-On ist auf diesem Server nicht aktiviert.",
    "state_mismatch": "Die Anmeldeanfrage ist abgelaufen oder ungültig. Bitte erneut versuchen.",
    "provider_error": "Der Identity Provider hat die Anmeldung abgelehnt oder ist nicht erreichbar.",
    "no_account": "Kein DeckLedger-Konto mit dieser Identität verknüpft. Bitte zuerst mit Benutzername/Passwort anmelden und das Konto in den Einstellungen unter „Single Sign-On“ verbinden.",
    "already_linked": "Diese SSO-Identität ist bereits mit einem anderen Konto verknüpft.",
}


def unique_username_from(candidate):
    base = slug(candidate) or "user"
    username, suffix = base, 1
    while db().execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        suffix += 1
        username = f"{base}-{suffix}"
    return username


def resolve_oauth_identity(config, subject, email, display_name, email_verified=True):
    """Walks the account-matching chain for a login (not link) OAuth callback and
    returns the matched/created users row, or None if no account could be resolved.
    Order is cumulative: exact identity match always applies; email-matching and
    auto-provisioning are opt-in escalations controlled by account_matching."""
    connection = db()
    matched = connection.execute(
        "SELECT * FROM users WHERE oauth_provider=? AND oauth_subject=?", (OAUTH_PROVIDER_KEY, subject),
    ).fetchone()
    if matched:
        return matched
    mode = config.get("account_matching") or "manual"
    if mode in ("email", "auto_provision") and email:
        candidate = connection.execute(
            "SELECT * FROM users WHERE oauth_subject='' AND lower(email)=lower(?)", (email,),
        ).fetchone()
        if candidate and not email_verified:
            # An address the provider itself reports as unverified proves nothing about who is
            # logging in. Neither is the existing account handed over, nor a second account
            # created next to it under the same address.
            return None
        if candidate:
            connection.execute(
                "UPDATE users SET oauth_provider=?, oauth_subject=? WHERE id=?", (OAUTH_PROVIDER_KEY, subject, candidate["id"]),
            )
            connection.commit()
            return connection.execute("SELECT * FROM users WHERE id=?", (candidate["id"],)).fetchone()
    if mode == "auto_provision":
        username = unique_username_from(email.split("@")[0] if email else display_name or subject)
        connection.execute(
            "INSERT INTO users(username,display_name,password_hash,role,created_at,email,oauth_provider,oauth_subject) VALUES(?,?,?,?,?,?,?,?)",
            (username, display_name or username, "", "user", now_iso(), email, OAUTH_PROVIDER_KEY, subject),
        )
        ensure_user_lists(connection, connection.execute("SELECT id FROM users WHERE oauth_provider=? AND oauth_subject=?", (OAUTH_PROVIDER_KEY, subject)).fetchone()["id"])
        connection.commit()
        return connection.execute(
            "SELECT * FROM users WHERE oauth_provider=? AND oauth_subject=?", (OAUTH_PROVIDER_KEY, subject),
        ).fetchone()
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    oauth_config = resolve_oauth_config()
    oauth_context = {
        "oauth_enabled": oauth_config["enabled"], "oauth_provider_name": oauth_config["provider_name"],
    }
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and row["password_hash"] and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            return redirect(url_for("index"))
        return render_template("login.html", error="Benutzername oder Passwort ist nicht korrekt.", **oauth_context), 401
    if session.get("user_id"):
        return redirect(url_for("index"))
    error = OAUTH_ERROR_MESSAGES.get(request.args.get("error"))
    return render_template("login.html", error=error, **oauth_context)


def oauth_failure_redirect(link_flow, error_code):
    # An already-authenticated user hitting this from the Settings "Verbinden" button never sees
    # /login (it immediately bounces anyone with a session straight to "/") -- routing those
    # failures there would silently swallow the error. Send them to "/" with a query flag the SPA
    # itself reads and toasts (see init() in static/js/main.js) instead. A genuine logged-out login attempt
    # still gets the server-rendered banner on /login as before.
    if link_flow:
        return redirect(url_for("index", oauth_error=error_code))
    return redirect(url_for("login", error=error_code))


@app.get("/oauth/login")
def oauth_login():
    config = resolve_oauth_config()
    link_flow = bool(session.get("user_id"))
    if not config["enabled"]:
        return oauth_failure_redirect(link_flow, "not_configured")
    try:
        auth_url, state, code_verifier = build_authorization_request(config, url_for("oauth_callback", _external=True))
    except OAuthConfigError as error:
        app.logger.warning("SSO login could not be started: %s", error)
        return oauth_failure_redirect(link_flow, "provider_error")
    session["oauth_state"] = state
    session["oauth_code_verifier"] = code_verifier
    # Only set (and only ever trusted) when the browser already carries an authenticated
    # session -- marks this round trip on the callback as "link this identity to my
    # current account" (started from Settings) rather than "log me in as whoever this
    # identity resolves to" (started from the logged-out /login page).
    session["oauth_link_user_id"] = session.get("user_id")
    return redirect(auth_url)


@app.get("/oauth/callback")
def oauth_callback():
    config = resolve_oauth_config()
    link_user_id = session.pop("oauth_link_user_id", None)
    expected_state = session.pop("oauth_state", None)
    code_verifier = session.pop("oauth_code_verifier", None)
    if not config["enabled"] or not expected_state or request.args.get("state") != expected_state:
        return oauth_failure_redirect(link_user_id, "state_mismatch")
    code = request.args.get("code")
    if not code:
        return oauth_failure_redirect(link_user_id, "provider_error")
    # The login page only says "the provider refused or cannot be reached". Which step failed and
    # what the provider answered goes to the log -- without it a wrong client secret, an
    # unreachable token endpoint and a missing claim all look the same.
    step = "token exchange"
    try:
        redirect_uri = url_for("oauth_callback", _external=True)
        token = exchange_code(config, redirect_uri, code, code_verifier)
        step = "userinfo request"
        # An OIDC provider states who signed in twice: in the ID token that comes with the tokens,
        # and at its userinfo endpoint. Either is enough; a provider that refuses the second
        # request (Authentik does for some provider setups) must not make the login fail.
        claims = id_token_claims(config, token)
        try:
            claims = {**claims, **fetch_userinfo(config, token)}
        except OAuthConfigError as error:
            if not claims.get(config.get("subject_claim") or "sub"):
                raise
            app.logger.warning("SSO: the userinfo request failed, using the ID token's claims instead: %s", error)
        step = "reading the identity"
        subject, email, display_name = extract_identity(config, claims)
    except OAuthConfigError as error:
        app.logger.warning("SSO login failed at the %s (redirect URI %s): %s", step, url_for("oauth_callback", _external=True), error)
        return oauth_failure_redirect(link_user_id, "provider_error")

    connection = db()
    if link_user_id:
        conflict = connection.execute(
            "SELECT id FROM users WHERE oauth_provider=? AND oauth_subject=? AND id!=?", (OAUTH_PROVIDER_KEY, subject, link_user_id),
        ).fetchone()
        if conflict:
            return oauth_failure_redirect(link_user_id, "already_linked")
        connection.execute(
            "UPDATE users SET oauth_provider=?, oauth_subject=?, email=CASE WHEN email='' THEN ? ELSE email END WHERE id=?",
            (OAUTH_PROVIDER_KEY, subject, email, link_user_id),
        )
        connection.commit()
        session["user_id"] = link_user_id
        return redirect(url_for("index", linked="1"))

    matched = resolve_oauth_identity(config, subject, email, display_name, email_verified=claims.get("email_verified") is not False)
    if not matched:
        return redirect(url_for("login", error="no_account"))
    session.clear()
    session["user_id"] = matched["id"]
    return redirect(url_for("index"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
