"""The heart's list menu and the collection's multi-select: watchlist membership, adding to lists
and sheets in bulk, manual prices for many cards, removing cards and putting them back."""
from conftest import ADMIN, ELSA, EMBER8, EMBER9, TIDE8, login, query, stored_collection
from test_prices_and_assets import price_of


def lists(client, variant_id=EMBER8):
    return {row["name"]: row["contains"] for row in client.get(f"/api/watchlists/membership?variant_id={variant_id}").get_json()["lists"]}


def test_the_heart_menu_shows_and_sets_each_list(client):
    other = client.post("/api/watchlists", json={"game_id": "vcard", "name": "Deck"}).get_json()["id"]
    assert lists(client) == {"Merkliste": False, "Deck": False}
    response = client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": other, "active": True}).get_json()
    assert (response["active"], response["watchlisted"]) == (True, True)
    # Setting the same state again changes nothing (a toggle would have taken it off).
    assert client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": other, "active": True}).get_json()["active"] is True
    assert lists(client) == {"Merkliste": False, "Deck": True}
    assert client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": other, "active": False}).get_json()["watchlisted"] is False
    assert client.get("/api/watchlists/membership?variant_id=nope").status_code == 404


def test_a_new_list_from_the_menu_starts_with_the_card(client):
    created = client.post("/api/watchlists", json={"name": "Wunsch", "variant_id": EMBER8})
    assert created.status_code == 201 and created.get_json()["game_id"] == "vcard"
    assert lists(client)["Wunsch"] is True
    assert client.post("/api/watchlists", json={"game_id": "lorcana", "name": "Falsch", "variant_id": EMBER8}).status_code == 400


def test_adding_many_cards_to_a_list(client):
    list_id = client.get("/api/watchlists?game_id=vcard").get_json()[0]["id"]
    client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": list_id})
    assert client.post(f"/api/watchlists/{list_id}/entries/add", json={"variant_ids": [EMBER8, EMBER9, ELSA]}).get_json() == {"added": 1}
    assert login(ADMIN).post(f"/api/watchlists/{list_id}/entries/add", json={"variant_ids": [TIDE8]}).status_code == 404


def test_one_manual_price_for_many_cards(client):
    response = client.post("/api/variants/manual-prices", json={"variant_ids": [EMBER8, EMBER9, "missing"], "amount": "2,5"}).get_json()
    assert response == {"changed": 2, "amount": 2.5}
    assert price_of(client, EMBER9, "vcard-card-ember9")["price"] == 2.5
    assert client.post("/api/variants/manual-prices", json={"variant_ids": [EMBER8], "amount": "0"}).status_code == 400
    assert client.post("/api/variants/manual-prices", json={"variant_ids": [EMBER8, EMBER9], "remove": True}).get_json()["changed"] == 2
    assert query("SELECT COUNT(*) n FROM price_observations WHERE provider_id='manual'")[0]["n"] == 0


def test_removing_cards_and_undoing_it(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 2})
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1, "condition": "Played"})
    client.post("/api/collection", json={"variant_id": TIDE8, "delta": 1})
    before = stored_collection()
    removed = client.post("/api/collection/remove", json={"variant_ids": [EMBER8, TIDE8]}).get_json()
    assert (removed["removed"], removed["copies"]) == (2, 4)
    assert stored_collection() == []
    assert login(ADMIN).post("/api/collection/restore", json={"entries": removed["entries"]}).get_json()["restored"] == 3
    assert query("SELECT COUNT(*) n FROM collection_entries WHERE user_id=1")[0]["n"] == 0  # restored into the admin's own collection, not demo's
    query("DELETE FROM collection_entries")
    assert client.post("/api/collection/restore", json={"entries": removed["entries"]}).get_json()["restored"] == 3
    assert stored_collection() == before


def test_taking_cards_onto_a_sheet_keeps_counts_already_there(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "S"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": EMBER8, "quantity": 3})
    payload = client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"entries": [{"variant_id": EMBER8, "at_least": 1}, {"variant_id": EMBER9, "at_least": 1}]}).get_json()
    assert {card["variant_id"]: card["quantity"] for card in payload["cards"]} == {EMBER8: 3, EMBER9: 1}
