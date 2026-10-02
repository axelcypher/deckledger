"""A price entered by hand counts like a provider's price and wins over the feeds."""
from conftest import ADMIN, EMBER8, TIDE8, login, query
from test_prices_and_assets import observe, price_of

URL = f"/api/variants/{EMBER8}/manual-price"


def ember(client):
    return price_of(client, EMBER8, "vcard-card-ember8")


def test_a_card_without_a_feed_gets_a_price(client):
    assert ember(client)["price"] is None
    assert client.put(URL, json={"amount": "4,50"}).get_json() == {"saved": True, "amount": 4.5}
    variant = ember(client)
    assert (variant["price"], variant["price_provider"], variant["price_manual"], variant["price_source"]) == (4.5, "manual", True, "eBay")


def test_it_counts_everywhere_prices_are_summed(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3})
    client.post("/api/watchlist", json={"variant_id": EMBER8})
    client.put(URL, json={"amount": 2})
    assert client.get("/api/collection?game_id=vcard").get_json()["stats"]["value"] == 6.0
    assert client.get("/api/watchlists?game_id=vcard").get_json()[0]["value"] == 2.0
    vcard = next(game for game in client.get("/api/bootstrap").get_json()["games"] if game["id"] == "vcard")
    assert vcard["value"] == 6.0


def test_it_wins_over_a_feed_price_until_it_is_removed(client):
    observe(EMBER8, "cardmarket", 9.0, "2026-09-01T06:00:00+00:00")
    client.put(URL, json={"amount": 5})
    assert ember(client)["price"] == 5.0
    assert client.delete(URL).get_json() == {"removed": True}
    variant = ember(client)
    assert (variant["price"], variant["price_provider"], variant["price_manual"]) == (9.0, "cardmarket", False)
    assert query("SELECT COUNT(*) n FROM price_observations WHERE provider_id='manual'")[0]["n"] == 0
    assert query("SELECT COUNT(*) n FROM marketplace_products WHERE provider_id='manual'")[0]["n"] == 0


def test_only_changes_are_stored_and_they_form_the_history(client):
    for amount in (4, 4, 4.0):
        client.put(URL, json={"amount": amount})
    query("UPDATE price_observations SET observed_at='2026-09-01T10:00:00+00:00' WHERE provider_id='manual'")
    client.put(URL, json={"amount": 6})
    assert [row["amount"] for row in query("SELECT amount FROM price_observations WHERE provider_id='manual' ORDER BY id")] == [4.0, 6.0]
    assert query("SELECT COUNT(*) n FROM marketplace_products WHERE provider_id='manual'")[0]["n"] == 1
    points = client.get(f"/api/variants/{EMBER8}/price-history").get_json()["points"]
    assert points and points[-1]["amount"] == 6.0


def test_a_correction_in_the_same_second_replaces_the_entry(client, monkeypatch):
    from conftest import deckledger
    monkeypatch.setattr(deckledger.prices, "now_iso", lambda: "2026-10-01T12:00:00+00:00")
    client.put(URL, json={"amount": 4})
    client.put(URL, json={"amount": 40})
    assert [row["amount"] for row in query("SELECT amount FROM price_observations WHERE provider_id='manual'")] == [40.0]
    assert ember(client)["price"] == 40.0


def test_input_is_validated(client, anonymous):
    for amount in ("", "abc", 0, -3, 2_000_000, None, [1]):
        response = client.put(URL, json={"amount": amount})
        assert response.status_code == 400, amount
    assert client.put("/api/variants/does-not-exist/manual-price", json={"amount": 1}).status_code == 404
    assert anonymous.put(URL, json={"amount": 1}).status_code == 401
    assert ember(client)["price"] is None


def test_one_price_per_card_for_all_accounts(client):
    client.put(URL, json={"amount": 7})
    assert price_of(login(ADMIN), EMBER8, "vcard-card-ember8")["price"] == 7.0
    assert price_of(client, TIDE8, "vcard-card-tide8")["price"] is None


def test_price_sync_leaves_manual_prices_alone(client):
    """The sync replaces the mappings of the providers it ran, by name."""
    import price_sync
    import sqlite3
    from conftest import deckledger

    client.put(URL, json={"amount": 3})
    connection = sqlite3.connect(deckledger.config.DB_PATH)
    try:
        price_sync.compact_price_history(connection, __import__("datetime").date(2027, 6, 1))
        price_sync.drop_unchanged_observations(connection)
        connection.commit()
    finally:
        connection.close()
    assert ember(client)["price"] == 3.0
