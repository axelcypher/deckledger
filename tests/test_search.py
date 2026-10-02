"""What the search boxes match: the text as typed, or the text read as a regular expression."""
import pytest

from conftest import EMBER8, EMBER9, TIDE8
from deckledger.web import search_pattern


def names(rows):
    return {row["canonical_name"] for row in rows}


@pytest.mark.parametrize("query, expected", [
    ("ember", {"Ember (PL8)", "Ember (PL9)", "Ember"}),
    ("PL9|Tide", {"Ember (PL9)", "Tide (PL8)"}),
    ("pl(8|9)", {"Ember (PL8)", "Ember (PL9)", "Tide (PL8)", "Leaf (PL8)"}),
    ("^(ember|tide) \\(", {"Ember (PL8)", "Ember (PL9)", "Tide (PL8)"}),
    ("Ember (PL8)", {"Ember (PL8)"}),       # the name as it is printed, brackets and all
    ("(PL9", {"Ember (PL9)"}),              # no valid expression: taken literally
])
def test_every_search_box_reads_the_text_the_same_way(client, query, expected):
    asked = {"q": query}
    everywhere = client.get("/api/search", query_string={**asked, "game_id": "vcard", "limit": 80}).get_json()
    one_set = client.get("/api/sets/vcard-test/cards", query_string={**asked, "language": "EN", "mode": "all", "finish": "all"}).get_json()["cards"]
    builder = client.get("/api/deckbuilder/catalog", query_string={**asked, "game_id": "vcard", "limit": 200}).get_json()["cards"]
    assert names(row for row in everywhere if row["set_code"] == "1") == expected
    assert names(one_set) == expected
    assert names(builder) >= expected - {"Ember"}       # the box topper is no card to play
    assert not names(builder) & ({"Ember (PL8)", "Ember (PL9)", "Tide (PL8)", "Leaf (PL8)"} - expected)


def test_collection_and_watchlist_take_alternatives(client):
    for variant in (EMBER8, EMBER9, TIDE8):
        client.post("/api/collection", json={"variant_id": variant, "delta": 1})
    owned = client.get("/api/collection", query_string={"game_id": "vcard", "q": "PL9|tide"}).get_json()["cards"]
    assert names(owned) == {"Ember (PL9)", "Tide (PL8)"}
    watchlist = client.get("/api/watchlists?game_id=vcard").get_json()[0]["id"]
    for variant in (EMBER8, EMBER9, TIDE8):
        client.post("/api/watchlist", json={"variant_id": variant, "list_id": watchlist})
    watched = client.get(f"/api/watchlists/{watchlist}/cards", query_string={"q": "PL9|tide"}).get_json()["cards"]
    assert names(watched) == {"Ember (PL9)", "Tide (PL8)"}


def test_pattern():
    assert search_pattern("PL9|10").search("Monarch (PL10)"), "an alternative stands on its own"
    assert not search_pattern("PL9|PL10").search("Monarch (PL8) 010")
    assert search_pattern("mr. mime").search("MR. MIME") and search_pattern("C++").search("c++")
    assert search_pattern("\\d{3}").search("No. 204") and not search_pattern("\\D").search("204")
    assert not search_pattern("a" * 300 + "|b").search("b"), "a very long text is not compiled as an expression"
