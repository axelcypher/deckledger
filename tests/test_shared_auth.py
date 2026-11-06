"""Shared sign-in of the Ophir apps on one host (OPHIR_AUTH_MODE, ophir_cards/shared_auth.py): the
master signs people in under /auth and hands the sign-in to the slaves in the cookie ophir_auth."""

import pytest

import ophir_auth
import ophir_cards
from conftest import DEMO, query

AUTH = "http://localhost/auth"
CARDS = "http://localhost/cards"
FIGURES = "http://localhost/figures"


@pytest.fixture
def mode(monkeypatch):
    def switch(value):
        monkeypatch.setattr(ophir_cards.config, "AUTH_MODE", value)
        monkeypatch.setattr(ophir_cards.config, "AUTH_PATH", "/auth")
        monkeypatch.setattr(ophir_cards.config, "AUTH_HOME", "/cards/")
    return switch


def shared_cookie(response):
    """The value the response sets for ophir_auth ('' when it clears it), None when untouched."""
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith(f"{ophir_auth.COOKIE}="):
            assert "Path=/;" in header and "HttpOnly" in header
            return header.split(";", 1)[0].split("=", 1)[1]
    return None


def token(**identity):
    return ophir_auth.sign(ophir_cards.app.config["SECRET_KEY"], {"sub": "7", "username": "kim", "name": "Kim", "email": "kim@example.org", "role": "user", **identity})


def demo_id():
    return query("SELECT id FROM users WHERE username='demo'")[0]["id"]


# ---- master ------------------------------------------------------------------------------------

def test_the_master_signs_in_under_the_auth_path_for_every_app(mode):
    mode("master")
    client = ophir_cards.app.test_client()
    signed_in = client.post("/login?next=/figures/", data={"username": DEMO[0], "password": DEMO[1]}, base_url=AUTH)
    assert signed_in.status_code == 302 and signed_in.headers["Location"] == "/figures/"
    identity, _ = ophir_auth.read(ophir_cards.app.config["SECRET_KEY"], shared_cookie(signed_in), 60)
    assert identity["sub"] == str(demo_id()) and identity["username"] == "demo" and identity["role"] == "user"

    # The app itself (under /cards) takes the same sign-in: a browser with only the shared cookie.
    other = ophir_cards.app.test_client()
    other.set_cookie(ophir_auth.COOKIE, shared_cookie(signed_in))
    assert other.get("/api/bootstrap", base_url=CARDS).get_json()["auth"] == {"mode": "master", "base": "/auth"}


def test_the_auth_path_serves_sign_in_only(mode):
    mode("master")
    client = ophir_cards.app.test_client()
    assert client.get("/login", base_url=AUTH).status_code == 200
    assert client.get("/static/login.css", base_url=AUTH).status_code == 200
    assert client.get("/", base_url=AUTH).status_code == 404
    assert client.get("/api/bootstrap", base_url=AUTH).status_code == 404
    assert client.get("/service-worker.js", base_url=AUTH).status_code == 404


def test_on_the_shared_host_the_master_sends_sign_in_and_sso_to_the_auth_path(mode):
    mode("master")
    client = ophir_cards.app.test_client()
    assert client.get("/login", base_url=CARDS).headers["Location"] == "/auth/login?next=/cards/"
    assert client.get("/oauth/login", base_url=CARDS).headers["Location"] == "/auth/oauth/login?next=/cards/"
    # On a host of its own (no path) it signs people in itself, as before.
    assert client.get("/login").status_code == 200


def test_signing_out_at_the_master_signs_out_everywhere(mode, client):
    mode("master")
    signed_in = client.post("/login", data={"username": DEMO[0], "password": DEMO[1]}, base_url=AUTH)
    assert shared_cookie(signed_in)
    signed_out = client.post("/logout", base_url=AUTH)
    assert shared_cookie(signed_out) == "" and signed_out.headers["Location"] == "/auth/login"
    assert client.get("/api/bootstrap", base_url=CARDS).status_code == 401


def test_a_deleted_account_loses_the_shared_sign_in(mode):
    mode("master")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, token(sub="999999"))
    answer = client.get("/api/bootstrap", base_url=CARDS)
    assert answer.status_code == 401 and shared_cookie(answer) == ""


def test_the_master_renews_an_older_sign_in(mode, monkeypatch):
    mode("master")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, token(sub=str(demo_id()), username="demo"))
    assert shared_cookie(client.get("/api/bootstrap", base_url=CARDS)) is None, "a recent token stays as it is"
    monkeypatch.setattr(ophir_auth, "RENEW_AFTER_SECONDS", -1)
    renewed = shared_cookie(client.get("/api/bootstrap", base_url=CARDS))
    identity, _ = ophir_auth.read(ophir_cards.app.config["SECRET_KEY"], renewed, 60)
    assert identity["username"] == "demo" and identity["name"] == query("SELECT display_name FROM users WHERE username='demo'")[0]["display_name"]


# ---- slave -------------------------------------------------------------------------------------

def test_a_slave_sends_whoever_is_not_signed_in_to_the_master(mode):
    mode("slave")
    client = ophir_cards.app.test_client()
    assert client.get("/", base_url=FIGURES).headers["Location"] == "/figures/login"
    assert client.get("/login", base_url=FIGURES).headers["Location"] == "/auth/login?next=/figures/"
    assert client.post("/login", data={"username": DEMO[0], "password": DEMO[1]}, base_url=FIGURES).headers["Location"].startswith("/auth/login")
    assert client.get("/api/bootstrap", base_url=FIGURES).status_code == 401
    signed_out = client.post("/logout", base_url=FIGURES)
    assert signed_out.status_code == 307 and signed_out.headers["Location"] == "/auth/logout?next=/figures/"


def test_a_slave_takes_the_masters_sign_in_and_account(mode):
    mode("slave")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, token(role="admin"))
    boot = client.get("/api/bootstrap", base_url=FIGURES).get_json()
    assert boot["auth"] == {"mode": "slave", "base": "/auth"}
    assert (boot["user"]["username"], boot["user"]["display_name"], boot["user"]["role"]) == ("kim", "Kim", "admin")
    assert query("SELECT auth_subject, password_hash FROM users WHERE username='kim'") == [{"auth_subject": "7", "password_hash": ""}]

    # The role follows the master.
    client.set_cookie(ophir_auth.COOKIE, token(role="user", name="Kim K."))
    boot = client.get("/api/bootstrap", base_url=FIGURES).get_json()
    assert (boot["user"]["display_name"], boot["user"]["role"]) == ("Kim K.", "user")
    assert query("SELECT COUNT(*) n FROM users WHERE auth_subject='7'")[0]["n"] == 1


def test_a_slave_links_an_account_it_already_has(mode):
    mode("slave")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, token(sub="42", username="DEMO", email=""))
    assert client.get("/api/bootstrap", base_url=FIGURES).get_json()["user"]["id"] == demo_id()
    assert query("SELECT auth_subject FROM users WHERE username='demo'")[0]["auth_subject"] == "42"


def test_a_slave_refuses_outdated_and_foreign_sign_ins(mode, monkeypatch):
    mode("slave")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, ophir_auth.sign("another-key", {"sub": "7", "username": "kim", "role": "admin"}))
    assert client.get("/api/bootstrap", base_url=FIGURES).status_code == 401
    monkeypatch.setattr(ophir_auth, "FRESH_SECONDS", -1)
    client.set_cookie(ophir_auth.COOKIE, token())
    assert client.get("/api/bootstrap", base_url=FIGURES).status_code == 401, "back to the master to have it renewed"


def test_a_slave_leaves_account_changes_to_the_master(mode):
    mode("slave")
    client = ophir_cards.app.test_client()
    client.set_cookie(ophir_auth.COOKIE, token(role="admin"))
    assert client.patch("/api/account", json={"display_name": "Other"}, base_url=FIGURES).status_code == 409
    assert client.post("/api/account/password", json={"new_password": "a-new-password"}, base_url=FIGURES).status_code == 409
    assert client.post("/api/admin/users", json={"username": "new-one", "password": "a-new-password", "role": "user"}, base_url=FIGURES).status_code == 409
    assert client.delete(f"/api/admin/users/{demo_id()}", base_url=FIGURES).status_code == 409


def test_where_to_go_after_signing_in_stays_on_this_host():
    for value in ("https://evil.example/", "//evil.example/x", "/\\evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=y", ""):
        assert ophir_auth.safe_next(value, "/cards/") == "/cards/"
    assert ophir_auth.safe_next("/figures/?linked=1", "/cards/") == "/figures/?linked=1"


def test_a_sign_out_is_not_undone_by_a_request_stopped_early(mode):
    """The session cookies of /auth and /cards still name the account after a sign-out elsewhere;
    a request that ends before the token is checked (a 404 under /auth, a refused cross-site write)
    must not hand that out as a new sign-in."""
    mode("master")
    client = ophir_cards.app.test_client()
    client.post("/login", data={"username": DEMO[0], "password": DEMO[1]}, base_url=AUTH)
    assert client.get("/api/bootstrap", base_url=CARDS).status_code == 200
    client.delete_cookie(ophir_auth.COOKIE)  # signed out at another app's request

    assert client.get("/no-such-page", base_url=AUTH).status_code == 404
    refused = client.post("/api/settings", json={}, headers={"Origin": "https://evil.example"}, base_url=CARDS)
    assert refused.status_code == 403
    assert shared_cookie(refused) is None
    assert client.get("/api/bootstrap", base_url=CARDS).status_code == 401


def test_the_sso_button_keeps_where_the_sign_in_ends(mode, monkeypatch):
    mode("master")
    config = {**ophir_cards.config.OAUTH_CONFIG_DEFAULTS, "enabled": True, "provider_name": "Authentik"}
    monkeypatch.setattr(ophir_cards.auth, "resolve_oauth_config", lambda: config)
    page = ophir_cards.app.test_client().get("/login?next=/figures/", base_url=AUTH).get_data(as_text=True)
    assert 'href="/auth/oauth/login?next=/figures/"' in page or 'href="/auth/oauth/login?next=%2Ffigures%2F"' in page
