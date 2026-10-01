"""The collection write endpoint and the small input checks around it."""
import pytest

from conftest import ADMIN, ELSA, EMBER8, TIDE8, login, query


def quantity(variant_id, **filters):
    where = "".join(f" AND {column}=?" for column in filters)
    rows = query(f"SELECT COALESCE(SUM(quantity),0) total FROM collection_entries WHERE variant_id=?{where}", (variant_id, *filters.values()))
    return rows[0]["total"]


def test_delta_and_absolute_quantity(client):
    assert client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3}).get_json()["quantity"] == 3
    assert client.post("/api/collection", json={"variant_id": EMBER8, "delta": -1}).get_json() == {
        "variant_id": EMBER8, "condition": "Near Mint", "before": 3, "quantity": 2,
    }
    client.post("/api/collection", json={"variant_id": EMBER8, "quantity": 0})
    assert quantity(EMBER8) == 0


def test_tile_minus_takes_from_the_condition_that_has_a_copy(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 2, "condition": "Played"})
    result = client.post("/api/collection", json={"variant_id": EMBER8, "delta": -1, "condition": "Near Mint", "any_condition": True}).get_json()
    assert (result["condition"], result["before"], result["quantity"]) == ("Played", 2, 1)


def test_tile_minus_without_the_flag_stays_on_its_condition(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 2, "condition": "Played"})
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": -1, "condition": "Near Mint"})
    assert quantity(EMBER8, condition="Played") == 2


def test_tile_minus_never_takes_a_graded_copy(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1, "is_graded": True, "grade_label": "PSA 9"})
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": -1, "condition": "Near Mint", "any_condition": True})
    assert quantity(EMBER8, is_graded=1) == 1


def test_replayed_request_is_applied_once(client):
    payload = {"variant_id": EMBER8, "delta": 1, "request_id": "offline-1"}
    first = client.post("/api/collection", json=payload).get_json()
    assert client.post("/api/collection", json=payload).get_json() == first
    assert quantity(EMBER8) == 1


def test_request_ids_are_scoped_to_the_user(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1, "request_id": "same-id"})
    other = login(ADMIN).post("/api/collection", json={"variant_id": EMBER8, "delta": 1, "request_id": "same-id"}).get_json()
    assert (other["before"], other["quantity"]) == (0, 1)


@pytest.mark.parametrize("label, method, url, body", [
    ("unknown variant", "post", "/api/collection", {"variant_id": "nope", "delta": 1}),
    ("quantity is not a number", "post", "/api/collection", {"variant_id": EMBER8, "quantity": "abc"}),
    ("body is not an object", "post", "/api/collection", [1, 2]),
    ("deck for an unknown game", "post", "/api/decks", {"game_id": "nope"}),
    ("settings body is not an object", "post", "/api/settings", [1, 2]),
])
def test_invalid_input_is_a_client_error(client, label, method, url, body):
    status = getattr(client, method)(url, json=body).status_code
    assert 400 <= status < 500, label
    # A rejected write must not leave the connection in a failed transaction.
    assert client.post("/api/collection", json={"variant_id": TIDE8, "delta": 1}).status_code == 200


def test_watchlist_rejects_unknown_and_foreign_game_cards(client):
    vcard_list = client.get("/api/watchlists?game_id=vcard").get_json()[0]["id"]
    assert client.post("/api/watchlist", json={"variant_id": "nope", "list_id": vcard_list}).status_code == 404
    assert client.post("/api/watchlist", json={"variant_id": ELSA, "list_id": vcard_list}).status_code == 400
    assert client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": vcard_list}).get_json()["active"] is True


def test_sale_list_uses_the_games_playset_size(client):
    """VCard allows 3 copies of a card, the default playset is 4."""
    for variant in (EMBER8, ELSA):
        client.post("/api/collection", json={"variant_id": variant, "delta": 5})
    surplus = {}
    for game in ("vcard", "lorcana"):
        sale = next(item for item in client.get(f"/api/watchlists?game_id={game}").get_json() if item["is_sale_list"])
        surplus[game] = [card["desired_quantity"] for card in client.get(f"/api/watchlists/{sale['id']}/cards").get_json()["cards"]]
    assert surplus == {"vcard": [2], "lorcana": [1]}


def click_concurrently(url, payload, clicks=8, per_click=5):
    """Fires the same request from several threads at once, each with its own session."""
    import threading

    clients = [login() for _ in range(clicks)]
    barrier = threading.Barrier(clicks)
    statuses = []

    def run(c):
        barrier.wait()
        for _ in range(per_click):
            statuses.append(c.post(url, json=payload).status_code)

    threads = [threading.Thread(target=run, args=(c,)) for c in clients]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    return statuses


def test_simultaneous_adds_all_count():
    """Regression: two quick clicks read the same old quantity and overwrote each other, or both
    tried to insert the row and one failed -- a double click added one copy or none."""
    statuses = click_concurrently("/api/collection", {"variant_id": EMBER8, "delta": 1})
    assert set(statuses) == {200}
    assert quantity(EMBER8) == 40


def test_simultaneous_deck_adds_all_count(client):
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "Schnell"}).get_json()["id"]
    statuses = click_concurrently(f"/api/decks/{deck_id}/cards", {"variant_id": EMBER8, "zone": "auto", "delta": 1})
    assert set(statuses) == {200}
    assert query("SELECT quantity q FROM deck_cards WHERE deck_id=?", (deck_id,))[0]["q"] == 40
