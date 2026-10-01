"""Catalogue imports must never cost a user their data, and shipped provider code must reach
existing installations."""
import sqlite3
from datetime import datetime, timedelta, timezone

import catalog_sync
from conftest import BOOST, ELSA, EMBER8, LEAF8, TIDE8, deckledger, query, sample_catalog


def catalog_without(*keys):
    catalog = sample_catalog()
    for key in keys:
        for variant_id in [v for v, row in catalog["variants"].items() if row["printing_id"] == f"vcard-print-{key}-en"]:
            del catalog["variants"][variant_id]
        del catalog["printings"][f"vcard-print-{key}-en"]
        del catalog["identities"][f"vcard-card-{key}"]
    return catalog


def test_cards_missing_upstream_stay_while_users_reference_them(client):
    """Regression: a short or changed source used to delete collection, deck and watchlist rows."""
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3})
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "Sync"}).get_json()["id"]
    client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": TIDE8, "zone": "auto", "delta": 2})
    client.patch(f"/api/decks/{deck_id}", json={"cover_variant_id": TIDE8})
    client.post("/api/watchlist", json={"variant_id": BOOST})

    catalog_sync.write_database(catalog_without("ember8", "tide8", "boost", "leaf8"), {"vcard"})

    assert query("SELECT SUM(quantity) q FROM collection_entries WHERE variant_id=?", (EMBER8,))[0]["q"] == 3
    assert query("SELECT quantity q FROM deck_cards WHERE variant_id=?", (TIDE8,))[0]["q"] == 2
    assert query("SELECT cover_variant_id c FROM decks WHERE id=?", (deck_id,))[0]["c"] == TIDE8
    assert query("SELECT COUNT(*) n FROM named_watchlist_entries WHERE variant_id=?", (BOOST,))[0]["n"] == 1
    # The retained card is still a complete catalogue entry, not a dangling row.
    assert client.get("/api/cards/vcard-card-ember8").get_json()["canonical_name"] == "Ember (PL8)"


def test_unreferenced_leftovers_are_pruned(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1})
    catalog_sync.write_database(catalog_without("ember8", "leaf8"), {"vcard"})
    assert query("SELECT COUNT(*) n FROM variants WHERE id=?", (LEAF8,))[0]["n"] == 0
    assert query("SELECT COUNT(*) n FROM card_identities WHERE id='vcard-card-leaf8'")[0]["n"] == 0
    # Only the owned finish is kept, not the whole printing's other variants.
    assert [row["id"] for row in query("SELECT id FROM variants WHERE printing_id='vcard-print-ember8-en'")] == [EMBER8]


def test_a_single_game_import_leaves_other_games_alone():
    catalog = {key: {k: v for k, v in rows.items() if not rows or key == "sources" or v["game_id"] == "vcard"} for key, rows in sample_catalog().items()}
    catalog_sync.write_database(catalog, {"vcard"})
    assert query("SELECT COUNT(*) n FROM variants WHERE id=?", (ELSA,))[0]["n"] == 1


def provider_row(**overrides):
    row = {"provider_version": "v1", "last_synced_version": "v1", "last_status": "ok", "last_run_at": catalog_sync.iso_now()}
    return {**row, **overrides}


def test_due_for_refresh():
    day_old = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(microsecond=0).isoformat()
    assert not catalog_sync.due_for_refresh(provider_row(), 24)
    assert catalog_sync.due_for_refresh(provider_row(last_run_at=day_old), 24)
    assert not catalog_sync.due_for_refresh(provider_row(last_run_at=day_old), None), "without a max age only a code change counts"
    assert catalog_sync.due_for_refresh(provider_row(provider_version="v2"), None)
    assert catalog_sync.due_for_refresh(provider_row(last_status="error"), 24)
    assert catalog_sync.due_for_refresh(provider_row(last_run_at=None), 24)


def test_one_failing_provider_does_not_block_the_others(monkeypatch, client):
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 2})

    def dispatch(provider):
        if provider["id"] == "vcard":
            raise RuntimeError("Quelle nicht erreichbar")
        return {key: {k: v for k, v in rows.items() if key == "sources" or v["game_id"] == provider["game_id"]} for key, rows in sample_catalog().items()}

    monkeypatch.setattr(catalog_sync, "dispatch_provider", dispatch)
    monkeypatch.setattr("sys.argv", ["catalog_sync.py"])
    query("UPDATE catalog_providers SET minimum_sets=0, minimum_cards=0")
    query("UPDATE catalog_providers SET enabled=0 WHERE id NOT IN ('vcard','lorcana')")

    assert catalog_sync.main() == 1

    status = {row["id"]: (row["last_status"], row["last_error"]) for row in query("SELECT id,last_status,last_error FROM catalog_providers WHERE enabled=1")}
    assert status["lorcana"] == ("ok", None)
    assert status["vcard"][0] == "error" and "Quelle nicht erreichbar" in status["vcard"][1]
    assert query("SELECT SUM(quantity) q FROM collection_entries WHERE variant_id=?", (EMBER8,))[0]["q"] == 2


def reseed():
    connection = sqlite3.connect(deckledger.DB_PATH)
    deckledger.seed_default_providers(connection)
    connection.commit()
    connection.close()
    return query("SELECT code,provider_version,last_synced_version,customized FROM catalog_providers WHERE id='vcard'")[0]


def test_shipped_provider_code_replaces_a_stale_database_copy():
    """Regression: provider fixes in the repository never reached an existing database."""
    query("UPDATE catalog_providers SET code='stale', provider_version='old', last_synced_version='old' WHERE id='vcard'")
    row = reseed()
    assert row["code"] == deckledger.default_provider_code("vcard")
    assert row["provider_version"] != row["last_synced_version"], "the new code has to trigger a re-import"


def test_admin_edited_provider_code_is_kept(admin):
    custom = "def fetch_catalog():\n    return {}\n"
    assert admin.patch("/api/admin/providers/vcard", json={"code": custom}).status_code == 200
    assert reseed()["code"] == custom
    admin.patch("/api/admin/providers/vcard", json={"code": deckledger.default_provider_code("vcard")})
    assert reseed()["customized"] == 0, "saving the shipped code again hands the provider back to the repository"


def test_import_never_empties_user_tables_even_without_the_catalogue_marker(client):
    """Regression: a database without the "real catalogue imported" metadata row had every table
    emptied before the import, collection, decks and watchlists included."""
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 4})
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "Bleibt"}).get_json()["id"]
    client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": TIDE8, "zone": "auto", "delta": 1})
    client.post("/api/watchlist", json={"variant_id": BOOST})
    query("DELETE FROM catalog_metadata")

    catalog_sync.write_database(sample_catalog(), {"vcard", "lorcana", "one-piece"})

    assert query("SELECT SUM(quantity) q FROM collection_entries")[0]["q"] == 4
    assert query("SELECT COUNT(*) n FROM decks")[0]["n"] == 1 and query("SELECT COUNT(*) n FROM deck_cards")[0]["n"] == 1
    assert query("SELECT COUNT(*) n FROM named_watchlist_entries")[0]["n"] == 1
