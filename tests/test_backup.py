"""Export, restore and import-undo: the paths a collection backup depends on."""
from conftest import BOOST, EMBER8, EMBER8_HOLO, TIDE8, query, stored_collection


def fill_collection(client):
    for payload in (
        {"variant_id": EMBER8, "delta": 3},
        {"variant_id": EMBER8, "delta": 2, "condition": "Played", "notes": "Ecke geknickt"},
        {"variant_id": EMBER8, "delta": 1, "is_graded": True, "grade_label": "PSA 10", "price_override": 250},
        {"variant_id": EMBER8, "delta": 1, "is_graded": True, "grade_label": "BGS 9.5", "price_override": 180.5},
        {"variant_id": EMBER8_HOLO, "delta": 4},
        {"variant_id": TIDE8, "delta": 1, "condition": "Mint", "price_override": 12},
    ):
        assert client.post("/api/collection", json=payload).status_code == 200
    query("UPDATE collection_entries SET created_at='2025-03-01T10:00:00+00:00', last_added_at='2025-04-02T11:00:00+00:00'")


def test_export_carries_everything_a_row_stores(client):
    fill_collection(client)
    backup = client.get("/api/export.json").get_json()
    assert backup["format_version"] == 2
    assert len(backup["collection"]) == 6
    graded = next(row for row in backup["collection"] if row["grade_label"] == "PSA 10")
    assert graded["variant_id"] == EMBER8 and graded["is_graded"] == 1 and graded["price_override"] == 250
    assert {"created_at", "last_added_at", "condition", "notes"} <= set(graded)


def test_export_includes_decks_and_watchlists(client):
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "Backup-Deck"}).get_json()["id"]
    client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": EMBER8, "zone": "auto", "delta": 3})
    client.post("/api/watchlist", json={"variant_id": TIDE8})
    backup = client.get("/api/export.json").get_json()
    assert [(deck["name"], [(card["variant_id"], card["quantity"]) for card in deck["cards"]]) for deck in backup["decks"]] == [("Backup-Deck", [(EMBER8, 3)])]
    watched = [entry["variant_id"] for watchlist in backup["watchlists"] for entry in watchlist["entries"]]
    assert watched == [TIDE8]


def test_csv_has_the_same_columns_as_json(client):
    fill_collection(client)
    header = client.get("/api/export.csv").get_data(as_text=True).splitlines()[0].split(",")
    assert header == list(client.get("/api/export.json").get_json()["collection"][0])


def test_restore_reproduces_the_collection_exactly(client):
    fill_collection(client)
    before = stored_collection()
    backup = client.get("/api/export.json").get_json()
    query("DELETE FROM collection_entries")

    result = client.post("/api/import/json/apply", json={"collection": backup["collection"], "strategy": "add"}).get_json()

    assert result["applied"] == result["total"] == 6
    assert stored_collection() == before
    assert client.get("/api/export.json").get_json()["collection"] == backup["collection"]


def test_restoring_twice_with_replace_does_not_double(client):
    fill_collection(client)
    before = stored_collection()
    backup = client.get("/api/export.json").get_json()
    for _ in range(2):
        client.post("/api/import/json/apply", json={"collection": backup["collection"], "strategy": "replace"})
    assert stored_collection() == before


def test_backup_without_ids_or_grading_still_restores(client):
    fill_collection(client)
    backup = client.get("/api/export.json").get_json()
    old_fields = ("game", "set_code", "set_name", "collector_number", "canonical_name", "language", "finish", "condition", "quantity", "notes")
    legacy = [{key: row[key] for key in old_fields} for row in backup["collection"] if not row["is_graded"]]
    query("DELETE FROM collection_entries")

    result = client.post("/api/import/json/apply", json={"collection": legacy, "strategy": "add"}).get_json()

    assert result["applied"] == 4
    assert {(row["variant_id"], row["condition"], row["quantity"]) for row in stored_collection()} == {
        (EMBER8, "Near Mint", 3), (EMBER8, "Played", 2), (EMBER8_HOLO, "Near Mint", 4), (TIDE8, "Mint", 1),
    }


def test_unknown_variant_id_falls_back_to_set_number_and_name(client):
    fill_collection(client)
    exported = next(row for row in client.get("/api/export.json").get_json()["collection"] if row["variant_id"] == TIDE8)
    row = dict(exported, variant_id="vcard-print-renamed-upstream")
    preview = client.post("/api/import/json/preview", json={"collection": [row]}).get_json()
    assert [(item["status"], item["match"]["variant_id"]) for item in preview] == [("matched", TIDE8)]


def test_undoing_a_backup_import_only_reverts_its_own_rows(client):
    fill_collection(client)
    before = [{k: v for k, v in row.items() if k != "last_added_at"} for row in stored_collection()]
    ungraded = [row for row in client.get("/api/export.json").get_json()["collection"] if not row["is_graded"] and row["variant_id"] == EMBER8]

    operation = client.post("/api/import/json/apply", json={"collection": ungraded, "strategy": "add"}).get_json()
    assert query("SELECT quantity FROM collection_entries WHERE variant_id=? AND condition='Near Mint' AND is_graded=0", (EMBER8,))[0]["quantity"] == 6
    client.post(f"/api/import/{operation['operation_id']}/undo")

    assert [{k: v for k, v in row.items() if k != "last_added_at"} for row in stored_collection()] == before


def test_undoing_a_text_import_keeps_graded_copies(client):
    """Regression: the undo used to match only variant + condition and deleted graded copies too."""
    client.post("/api/collection", json={"variant_id": BOOST, "delta": 1, "is_graded": True, "grade_label": "PSA 10"})
    operation = client.post("/api/import/apply", json={"text": "2 Tide (PL8)\n2 Boost", "game_id": "vcard", "language": "EN"}).get_json()
    assert operation["applied"] == 2

    client.post(f"/api/import/{operation['operation_id']}/undo")

    assert [(row["variant_id"], row["grade_label"], row["quantity"]) for row in stored_collection()] == [(BOOST, "PSA 10", 1)]
