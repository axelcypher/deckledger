"""What the search boxes match: every word somewhere in the card, forgiving about spacing, case and
small typos -- or, with regular-expression syntax, the text as typed or read as an expression."""
import pytest

from conftest import EMBER8, EMBER9, TIDE8
from deckledger import search


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


SMUG_ALANA = (("Smug Alana (PL9)", search.NAME), ("PL9", search.NUMBER), ("Fractured Paradox", search.SET), ("FP", search.SET),
              ("1st Edition Holo", search.DETAIL), ("Rare", search.DETAIL), ("Gains 2 power when attacking.", search.TEXT))


@pytest.mark.parametrize("query", [
    "Smug Alana", "smug alana", "Smugalana", "SmugAlana PL9", "SmugAlana Fractured Paradox 1st Ed", "alana smug",
    "Smug Alana (PL9)", "Smug Allana", "smgu alana", "Smug Alana Holo", "PL9 Paradox", "Smúg Alana",
])
def test_a_card_is_found_however_it_is_typed(query):
    assert search.score(query, *[value for field in SMUG_ALANA for value in field]) > 0


@pytest.mark.parametrize("query", ["Smug Alana PL8", "Smug Bob", "Alana Shattered", "Smug Alana 2nd"])
def test_every_word_has_to_fit(query):
    assert search.score(query, *[value for field in SMUG_ALANA for value in field]) == 0


def test_rules_text_is_searched_but_not_for_typos():
    assert search.matches("attacking", SMUG_ALANA) and not search.matches("atacking", SMUG_ALANA)


def test_the_closest_name_ranks_first():
    exact = search.query("Goddess").score((("Goddess", search.NAME),))
    longer = search.query("Goddess").score((("Goddess Sansindra", search.NAME),))
    in_text = search.query("Goddess").score((("Ember", search.NAME), ("Pray to the goddess.", search.TEXT)))
    assert exact > longer > in_text > 0


def test_expressions_still_work():
    fields = (("Monarch (PL10)", search.NAME),)
    assert search.matches("PL9|10", fields), "an alternative stands on its own"
    assert not search.matches("PL9|PL10", (("Monarch (PL8) 010", search.NAME),))
    assert search.matches("C++", (("c++", search.NAME),)) and search.matches("mr. mime", (("MR. MIME", search.NAME),))
    assert search.matches(r"\d{3}", (("No. 204", search.NAME),)) and not search.matches(r"\D", (("204", search.NAME),))
    assert not search.matches("a" * 300 + "|b", (("b", search.NAME),)), "a very long text is not compiled as an expression"


def test_the_global_search_is_forgiving_and_ranked(client):
    found = client.get("/api/search", query_string={"q": "emberpl8", "game_id": "vcard", "limit": 80}).get_json()
    assert {row["canonical_name"] for row in found} == {"Ember (PL8)"}
    found = client.get("/api/search", query_string={"q": "Embr Test Set", "game_id": "vcard", "limit": 80}).get_json()
    assert found and found[0]["canonical_name"] == "Ember" and {"Ember (PL8)", "Ember (PL9)"} <= {row["canonical_name"] for row in found}


def reference_near_miss(token, text):
    """near_miss as it was first written: a full edit-distance table per word and per prefix
    length. The faster version (one banded table per word) has to agree with it everywhere."""
    def within(word, other, limit):
        if abs(len(word) - len(other)) > limit:
            return False
        previous = list(range(len(other) + 1))
        for i, char in enumerate(word, 1):
            current = [i]
            for j, other_char in enumerate(other, 1):
                current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char != other_char)))
            previous = current
        return previous[-1] <= limit

    if len(token) < 4 or any(char.isdigit() for char in token):
        return False
    words = search.field_words(text)[0]
    limit = 1 if len(token) < 8 else 2
    return any(
        within(token, word, limit) or any(within(token, word[:size], limit) for size in (len(token) - 1, len(token), len(token) + 1) if 0 < size < len(word))
        for word in list(words) + [a + b for a, b in zip(words, words[1:])]
    )


def test_typo_tolerance_agrees_with_the_plain_edit_distance():
    import random
    generator = random.Random(7)
    # A small alphabet makes near misses common; lengths around 4 and 8 cover both limits.
    alphabet = "abcde"
    texts = ["Mickey Mouse - Detective", "Smug Alana PL9", "Goddess of the Sea", "Ember (PL8)", "Maleficent", "a", ""]
    texts += [" ".join("".join(generator.choice(alphabet) for _ in range(generator.randint(1, 11))) for _ in range(generator.randint(1, 3))) for _ in range(400)]
    tokens = ["mickey", "mikey", "mickeymouse", "godess", "goddes", "smugalana", "malefiscent", "embr", "ember", "pl8"]
    tokens += ["".join(generator.choice(alphabet) for _ in range(generator.randint(3, 10))) for _ in range(150)]
    disagreements = [(token, text) for token in tokens for text in texts if search.near_miss(token, text) != reference_near_miss(token, text)]
    assert not disagreements
    matches = sum(reference_near_miss(token, text) for token in tokens for text in texts)
    assert matches > 500, "the sample has to contain plenty of near misses to mean anything"


def test_short_words_and_numbers_stay_out_of_the_typo_cache():
    search.fuzzy_match.cache_clear()
    assert not search.near_miss("els", "Elsa - Snow Queen") and not search.near_miss("pl10", "Smug Alana PL9")
    assert search.fuzzy_match.cache_info().currsize == 0
