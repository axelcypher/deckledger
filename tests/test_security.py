"""Session handling, cross-site writes, access to images and SSO account matching."""
import json

from conftest import EMBER8, deckledger, query

WRITE = json.dumps({"variant_id": EMBER8, "delta": 1})


def test_session_cookie_is_samesite_lax(anonymous):
    response = anonymous.post("/login", data={"username": "demo", "password": "deckledger"})
    cookie = response.headers["Set-Cookie"]
    assert "SameSite=Lax" in cookie and "HttpOnly" in cookie


def test_cross_site_writes_are_rejected(client):
    # What a <form> on another website can send: no JSON content type, cookies attached.
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Origin": "https://evil.example"}):
        response = client.post("/api/collection", data=WRITE, content_type="text/plain", headers=headers)
        assert response.status_code == 403, headers
    assert query("SELECT COUNT(*) n FROM collection_entries")[0]["n"] == 0


def test_own_and_non_browser_writes_pass(client):
    for headers in ({"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {"Origin": "http://localhost"}, {}):
        assert client.post("/api/collection", data=WRITE, content_type="application/json", headers=headers).status_code == 200, headers


def test_api_requires_a_session(anonymous):
    assert anonymous.get("/api/bootstrap").status_code == 401
    assert anonymous.post("/api/collection", json={"variant_id": EMBER8, "delta": 1}).status_code == 401
    assert anonymous.get("/api/admin/providers").status_code == 401


def test_admin_api_is_closed_to_normal_users(client):
    assert client.get("/api/admin/providers").status_code == 403


def test_card_images_need_a_login(anonymous, client):
    for url in (f"/art/{EMBER8}.svg", f"/foil-mask/{EMBER8}.webp", "/set-logo/vcard-test", "/game-logo/vcard"):
        assert anonymous.get(url).status_code == 302, url
    assert client.get(f"/art/{EMBER8}.svg").status_code == 200


def test_public_endpoints_stay_public(anonymous):
    for url in ("/health", "/login", "/service-worker.js", "/static/manifest.json"):
        assert anonymous.get(url).status_code == 200, url


def test_image_placeholder_is_not_cached_as_the_real_image(client):
    """Regression: the stand-in for an image that could not be fetched was served immutable for a
    year, which pinned it in browsers and the service worker."""
    response = client.get(f"/art/{EMBER8}.svg")  # the fixture catalogue has no image URLs
    assert response.mimetype == "image/svg+xml"
    assert response.headers["X-Image-Source"] == "placeholder"
    assert "immutable" not in response.headers["Cache-Control"]


def resolve(mode, email, verified=True):
    with deckledger.app.app_context():
        row = deckledger.auth.resolve_oauth_identity({"account_matching": mode}, "subject-1", email, "Name", email_verified=verified)
        return dict(row) if row else None


def test_sso_links_a_user_by_verified_email():
    query("UPDATE users SET email='demo@example.com' WHERE username='demo'")
    assert resolve("email", "demo@example.com")["username"] == "demo"
    assert query("SELECT oauth_subject s FROM users WHERE username='demo'")[0]["s"] == "subject-1"


def test_sso_does_not_hand_over_or_duplicate_an_account_for_an_unverified_email():
    query("UPDATE users SET email='demo@example.com' WHERE username='demo'")
    for mode in ("email", "auto_provision"):
        assert resolve(mode, "demo@example.com", verified=False) is None, mode
    assert query("SELECT COUNT(*) n FROM users")[0]["n"] == 2
    assert query("SELECT oauth_subject s FROM users WHERE username='demo'")[0]["s"] == ""


def test_sso_links_an_admin_by_email_like_any_other_account():
    query("UPDATE users SET email='admin@example.com' WHERE username='admin'")
    assert resolve("auto_provision", "admin@example.com")["username"] == "admin"
    assert query("SELECT COUNT(*) n FROM users")[0]["n"] == 2, "no second account next to the admin"


def test_sso_auto_provision_creates_a_ready_to_use_account_for_an_unknown_identity():
    created = resolve("auto_provision", "neu@example.com")
    assert (created["username"], created["role"], created["email"]) == ("neu", "user", "neu@example.com")
    lists = query("SELECT COUNT(*) n FROM named_watchlists WHERE user_id=?", (created["id"],))[0]["n"]
    assert lists == query("SELECT COUNT(*) n FROM games")[0]["n"], "a default watchlist for every game"


def test_sso_email_mode_links_but_never_creates():
    assert resolve("email", "ganz-neu@example.com") is None
    assert query("SELECT COUNT(*) n FROM users")[0]["n"] == 2


def test_sso_manual_mode_only_accepts_linked_identities():
    query("UPDATE users SET email='demo@example.com' WHERE username='demo'")
    assert resolve("manual", "demo@example.com") is None
    query("UPDATE users SET oauth_provider='generic', oauth_subject='subject-1' WHERE username='demo'")
    assert resolve("manual", "")["username"] == "demo"


def test_a_failed_sso_login_says_why_in_the_log(anonymous, monkeypatch, caplog):
    """Regression: every failure after the return from the provider ended in the same message on
    the login page and nothing in the log, so a wrong secret could not be told from a network problem."""
    config = {**deckledger.config.OAUTH_CONFIG_DEFAULTS, "enabled": True, "client_id": "id", "client_secret": "secret"}
    monkeypatch.setattr(deckledger.auth, "resolve_oauth_config", lambda: config)

    def refuse(*args):
        raise deckledger.auth.OAuthConfigError("invalid_client: Client authentication failed")

    monkeypatch.setattr(deckledger.auth, "exchange_code", refuse)
    with anonymous.session_transaction() as session:
        session["oauth_state"], session["oauth_code_verifier"] = "state-1", "verifier"
    with caplog.at_level("WARNING"):
        response = anonymous.get("/oauth/callback?code=abc&state=state-1")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login?error=provider_error")
    assert "token exchange" in caplog.text and "invalid_client" in caplog.text and "/oauth/callback" in caplog.text
    assert "secret" not in caplog.text.replace("invalid_client", "") and "abc" not in caplog.text
