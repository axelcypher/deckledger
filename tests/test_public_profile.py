"""The public profile: a shared collection at /u/<slug>, readable without signing in, limited to
the games the owner chose and without prices (deckledger/public_profile.py)."""
import json

import pytest

from conftest import ADMIN, ELSA, EMBER8, EMBER8_HOLO, TIDE8, deckledger, login, query


@pytest.fixture
def visitor():
    return deckledger.app.test_client()


@pytest.fixture(autouse=True)
def no_image_downloads(monkeypatch):
    monkeypatch.setattr(deckledger.images, "card_image", lambda row, variant_id: None)


def own(variant_id, quantity, user_id=1):
    query("INSERT INTO collection_entries(user_id,variant_id,condition,quantity) VALUES(?,?,'Near Mint',?)", (user_id, variant_id, quantity))


def share(client, **changes):
    return client.put("/api/account/public-profile", json={"enabled": True, "slug": "alex", "games": ["vcard"], **changes})


def set_of(variant_id):
    return query("SELECT p.set_id FROM variants v JOIN printings p ON p.id=v.printing_id WHERE v.id=?", (variant_id,))[0]["set_id"]


def test_the_owner_chooses_address_name_and_games(client):
    assert client.get("/api/account/public-profile").get_json()["enabled"] is False, "off until switched on"
    assert "Kleinbuchstaben" in share(client, slug="Alex Morgan!").get_json()["error"]
    assert "mindestens ein TCG" in share(client, games=[]).get_json()["error"]
    saved = share(client, name="Alex", games=["vcard", "no-such-game"]).get_json()
    assert (saved["enabled"], saved["slug"], saved["games"]) == (True, "alex", ["vcard"])
    assert saved["url"].endswith("/u/alex")
    assert "vergeben" in share(login(ADMIN)).get_json()["error"], "one address, one profile"
    client.post("/api/settings", json={"publicProfile": {"enabled": True, "slug": "x y", "games": ["lorcana"]}})
    assert client.get("/api/account/public-profile").get_json()["slug"] == "alex", "not around the address check"


def test_nothing_is_public_unless_switched_on(client, visitor):
    own(EMBER8, 2)
    for response in (visitor.get("/u/alex"), visitor.get("/api/public/alex")):
        assert response.status_code == 404
    share(client)
    client.put("/api/account/public-profile", json={"enabled": False})
    assert visitor.get("/api/public/alex").status_code == 404
    assert visitor.get("/").status_code == 302, "the app itself stays behind the sign-in"


def test_visitors_see_every_card_of_a_shared_game_with_counts_but_no_prices(client, visitor):
    own(EMBER8, 2), own(EMBER8_HOLO, 1), own(ELSA, 3)
    share(client, name="Alex")
    page = visitor.get("/u/alex")
    assert page.status_code == 200 and b"Alex" in page.data and b"noindex" in page.data
    profile = visitor.get("/api/public/alex").get_json()
    assert profile["name"] == "Alex" and [game["id"] for game in profile["games"]] == ["vcard"]
    assert profile["games"][0]["owned"] == 1 and profile["games"][0]["copies"] == 3
    sets = visitor.get("/api/public/alex/games/vcard/sets").get_json()
    assert sets and all("value" not in item for item in sets)
    cards = visitor.get(f"/api/public/alex/sets/{set_of(EMBER8)}/cards")
    text = cards.get_data(as_text=True)
    assert "price" not in text and "value" not in text and "condition" not in text and "watchlisted" not in text
    shown = cards.get_json()["cards"]
    ember = next(card for card in shown if any(variant["variant_id"] == EMBER8 for variant in card["variants"]))
    assert ember["quantity"] == 3, "every copy of the card counts, normal and holo"
    assert any(card["quantity"] == 0 for card in shown), "the cards not owned are there too"
    owned_only = visitor.get(f"/api/public/alex/sets/{set_of(EMBER8)}/cards?mode=owned").get_json()["cards"]
    assert owned_only and all(card["quantity"] for card in owned_only)


def test_a_game_not_shared_stays_private(client, visitor):
    own(ELSA, 1)
    share(client)
    assert visitor.get("/api/public/alex/games/lorcana/sets").status_code == 404
    assert visitor.get(f"/api/public/alex/sets/{set_of(ELSA)}/cards").status_code == 404
    assert visitor.get(f"/u/alex/art/{ELSA}").status_code == 404
    assert visitor.get(f"/u/alex/art/{EMBER8}").status_code == 200
    assert visitor.get("/u/alex/game-logo/lorcana").status_code == 404
    assert visitor.get(f"/art/{EMBER8}.svg").status_code == 302, "the app's own image route still asks for a sign-in"


def test_the_shown_counts_follow_the_collection(client, visitor):
    share(client)
    set_id = set_of(TIDE8)
    tide = lambda: next(card for card in visitor.get(f"/api/public/alex/sets/{set_id}/cards").get_json()["cards"] if card["variant_id"] == TIDE8)
    assert tide()["quantity"] == 0
    own(TIDE8, 4)
    assert tide()["quantity"] == 4
    assert json.loads(query("SELECT value FROM user_settings WHERE key='publicProfile'")[0]["value"])["slug"] == "alex"
