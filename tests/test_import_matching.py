"""Text import by collector number: a number names a printing, never "whichever row came first"."""
from conftest import BOOST, EMBER8, EMBER8_HOLO, NAMI, SPARK_SET_TWO, TIDE8


def preview(client, text, game_id="vcard"):
    return client.post("/api/import/preview", json={"text": text, "game_id": game_id, "language": "EN"}).get_json()


def outcome(rows):
    return [(row["status"], row["match"]["variant_id"] if row["status"] == "matched" else None) for row in rows]


def test_number_with_several_finishes_matches_the_base_variant(client):
    """Regression: a card with a Normal and a Holo print was reported as ambiguous."""
    assert outcome(preview(client, "2 003\n006")) == [("matched", TIDE8), ("matched", BOOST)]


def test_finish_can_be_named(client):
    assert outcome(preview(client, "TEST:001;1;EN;Holo")) == [("not_found", None)]  # no set with that code
    assert outcome(preview(client, "1:001;1;EN;Holo")) == [("matched", EMBER8_HOLO)]
    rows = preview(client, "1:001;1;EN;Gold")
    assert rows[0]["status"] == "ambiguous" and "gold" in rows[0]["message"].lower()


def test_same_number_in_two_sets_is_ambiguous_and_says_how_to_resolve_it(client):
    rows = preview(client, "3 001")
    assert rows[0]["status"] == "ambiguous"
    assert "1:001" in rows[0]["message"]


def test_set_prefix_picks_the_printing(client):
    assert outcome(preview(client, "3 1:001\n2x 2:001 EN")) == [("matched", EMBER8), ("matched", SPARK_SET_TWO)]


def test_reprint_under_the_original_number_resolves_to_the_original_set(client):
    """Regression: One Piece reprints keep the number; the import took an arbitrary printing."""
    assert outcome(preview(client, "4 OP01-016", "one-piece")) == [("matched", NAMI)]
    assert outcome(preview(client, "1 PRB-01:OP01-016", "one-piece")) == [("matched", "one-piece-print-prb-op01-016-en-standard")]
    assert outcome(preview(client, "OP01-016;1;EN;parallel", "one-piece")) == [("matched", "one-piece-print-op01-016-en-parallel")]


def test_ambiguous_lines_are_not_applied(client):
    result = client.post("/api/import/apply", json={"text": "3 001\n1 003", "game_id": "vcard", "language": "EN"}).get_json()
    assert result["applied"] == 1


def test_names_still_work_and_unknown_lines_are_reported(client):
    rows = preview(client, "2 Tide (PL8)\n1 999\nNo Such Card")
    assert outcome(rows) == [("matched", TIDE8), ("not_found", None), ("not_found", None)]


def deck_preview(client, text, game_id="vcard"):
    deck_id = client.post("/api/decks", json={"game_id": game_id, "name": "Import"}).get_json()["id"]
    return client.post(f"/api/decks/{deck_id}/import/preview", json={"text": text}).get_json()


def test_deck_import_uses_the_same_rules(client):
    rows = deck_preview(client, "3 003\n2 001\n2 1:001\n3 Boost")
    assert [(row["status"], (row["match"] or {}).get("variant_id")) for row in rows] == [
        ("matched", TIDE8), ("ambiguous", None), ("matched", EMBER8), ("matched", BOOST),
    ]
    assert [(row["status"], row["match"]["variant_id"]) for row in deck_preview(client, "4x OP01-016", "one-piece")] == [("matched", NAMI)]
