"""The app served under a path, as Traefik serves it as ophir.<domain>/cards (StripPrefix plus
X-Forwarded-Prefix, which ProxyFix turns into SCRIPT_NAME), and its session cookie on a host it
shares with other Ophir apps."""

from flask.sessions import SecureCookieSessionInterface

import ophir_cards
from conftest import DEMO

UNDER_PATH = "http://localhost/cards"


def login_under_path():
    client = ophir_cards.app.test_client()
    response = client.post("/login", data={"username": DEMO[0], "password": DEMO[1]}, base_url=UNDER_PATH)
    return client, response


def test_signing_in_under_a_path_stays_there():
    client, response = login_under_path()
    assert response.status_code == 302 and response.headers["Location"] == "/cards/"
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("ophir_cards_session=") and "Path=/cards;" in cookie, "only sent to the app's own path"
    page = client.get("/", base_url=UNDER_PATH).get_data(as_text=True)
    assert '<meta name="app-base" content="/cards">' in page
    assert 'href="/cards/static/app.css?v=' in page and 'action="/cards/logout"' in page
    assert 'href="/cards/api/export.json"' in page


def test_at_the_root_of_its_host_nothing_changes(client):
    page = client.get("/").get_data(as_text=True)
    assert '<meta name="app-base" content="">' in page and 'href="/static/app.css?v=' in page
    response = ophir_cards.app.test_client().post("/login", data={"username": DEMO[0], "password": DEMO[1]})
    assert "Path=/;" in response.headers["Set-Cookie"]


def test_the_service_worker_may_only_cover_the_app():
    response = ophir_cards.app.test_client().get("/service-worker.js", base_url=UNDER_PATH)
    assert response.headers["Service-Worker-Allowed"] == "/cards/"


def test_a_shared_collection_link_carries_the_path(monkeypatch):
    monkeypatch.setattr(ophir_cards.public_profile, "PUBLIC_BASE_URL", "")
    client, _ = login_under_path()
    saved = client.put("/api/account/public-profile", json={"enabled": True, "slug": "alex", "games": ["vcard"]}, base_url=UNDER_PATH)
    assert saved.status_code == 200, saved.get_data(as_text=True)
    assert saved.get_json()["url"] == "http://localhost/cards/u/alex"


def test_a_session_another_app_signed_with_the_same_key_is_not_accepted(anonymous):
    # Another Ophir app on the same host may get the same SECRET_KEY; Flask's default session
    # serializer would sign a {"user_id": 1} of its own exactly the way this app used to.
    with ophir_cards.app.test_request_context():
        foreign = SecureCookieSessionInterface().get_signing_serializer(ophir_cards.app).dumps({"user_id": 1})
    anonymous.set_cookie("ophir_cards_session", foreign)
    anonymous.set_cookie("session", foreign)
    assert anonymous.get("/api/bootstrap").status_code == 401


def test_the_ebay_return_stays_under_the_path():
    client, _ = login_under_path()
    assert client.get("/ebay/callback?code=x&state=wrong", base_url=UNDER_PATH).headers["Location"] == "/cards/?ebay=state"
