"""The fixed "Verkaufsliste" among the watchlists is gone: existing ones become trade sheets at
start-up, and a backup that still carries one restores it as a sheet."""
import sqlite3

from conftest import BOOST, ELSA, EMBER8, EMBER8_HOLO, TIDE8, deckledger, query


def legacy_sale_list(game_id="vcard", user="demo", entries=()):
    """A sale list as an older version left it in the database. entries: (variant, quantity, source)."""
    user_id = query("SELECT id FROM users WHERE username=?", (user,))[0]["id"]
    query(
        "INSERT INTO named_watchlists(user_id,game_id,name,is_default,is_sale_list,created_at) VALUES(?,?,'Verkaufsliste',0,1,'2026-05-01T10:00:00+00:00')",
        (user_id, game_id),
    )
    list_id = query("SELECT MAX(id) id FROM named_watchlists")[0]["id"]
    for variant_id, quantity, source in entries:
        query(
            "INSERT INTO named_watchlist_entries(list_id,variant_id,quantity,source,created_at) VALUES(?,?,?,?,'2026-05-02T10:00:00+00:00')",
            (list_id, variant_id, quantity, source),
        )
    return list_id


def own(variant_id, quantity, user="demo"):
    user_id = query("SELECT id FROM users WHERE username=?", (user,))[0]["id"]
    query(
        "INSERT INTO collection_entries(user_id,variant_id,condition,quantity,created_at,last_added_at) VALUES(?,?,'Near Mint',?,'','')",
        (user_id, variant_id, quantity),
    )


def sheets():
    return [(row["username"], row["game_id"], row["name"], row["kind"], row["variant_id"], row["quantity"]) for row in query(
        """SELECT u.username,t.game_id,t.name,t.kind,c.variant_id,c.quantity FROM trade_sheets t JOIN users u ON u.id=t.user_id
           LEFT JOIN trade_sheet_cards c ON c.sheet_id=t.id ORDER BY t.id,c.id""")]


def sale_lists_left():
    return query("SELECT COUNT(*) n FROM named_watchlists WHERE is_sale_list=1")[0]["n"]


def test_a_sale_list_becomes_a_sheet_with_the_same_cards():
    legacy_sale_list(entries=[(EMBER8, 2, "manual"), (TIDE8, 1, "manual")])
    entries_before = query("SELECT COUNT(*) n FROM named_watchlist_entries")[0]["n"]

    deckledger.init_database()

    assert sheets() == [("demo", "vcard", "Verkaufsliste", "WTS", EMBER8, 2), ("demo", "vcard", "Verkaufsliste", "WTS", TIDE8, 1)]
    assert sale_lists_left() == 0
    assert query("SELECT COUNT(*) n FROM named_watchlist_entries")[0]["n"] == entries_before - 2
    assert query("SELECT created_at FROM trade_sheets")[0]["created_at"] == "2026-05-01T10:00:00+00:00"


def test_migration_runs_once():
    legacy_sale_list(entries=[(EMBER8, 2, "manual")])
    deckledger.init_database()
    deckledger.init_database()
    assert sheets() == [("demo", "vcard", "Verkaufsliste", "WTS", EMBER8, 2)]


def test_every_account_and_game_keeps_its_own_list():
    legacy_sale_list("vcard", "demo", [(EMBER8, 1, "manual")])
    legacy_sale_list("lorcana", "demo", [(ELSA, 3, "manual")])
    legacy_sale_list("vcard", "admin", [(BOOST, 4, "manual")])

    deckledger.init_database()

    assert sheets() == [
        ("demo", "vcard", "Verkaufsliste", "WTS", EMBER8, 1),
        ("demo", "lorcana", "Verkaufsliste", "WTS", ELSA, 3),
        ("admin", "vcard", "Verkaufsliste", "WTS", BOOST, 4),
    ]


def test_automatic_entries_only_move_while_the_card_is_still_surplus():
    """The list removed an automatic entry the next time it was opened once the card was no longer
    owned beyond a playset (3 for VCard). The migration must not resurrect those."""
    own(EMBER8, 5)       # still two over the playset
    own(TIDE8, 3)        # exactly a playset: no longer surplus
    legacy_sale_list(entries=[(EMBER8, 2, "auto"), (TIDE8, 1, "auto"), (BOOST, 1, "auto"), (EMBER8_HOLO, 1, "manual")])

    deckledger.init_database()

    assert [(row[4], row[5]) for row in sheets()] == [(EMBER8, 2), (EMBER8_HOLO, 1)]


def test_an_own_list_of_that_name_stood_in_for_the_fixed_one():
    """Names are unique per game: an account that already had a "Verkaufsliste" never got the
    fixed list, its own one was the sale list. It moves too, but only on the first start."""
    legacy_sale_list(entries=[(EMBER8, 2, "manual")])
    query("UPDATE named_watchlists SET is_sale_list=0 WHERE name='Verkaufsliste'")
    query("DELETE FROM app_settings WHERE key='sale_lists_migrated'")

    deckledger.init_database()

    assert sheets() == [("demo", "vcard", "Verkaufsliste", "WTS", EMBER8, 2)]
    assert query("SELECT COUNT(*) n FROM named_watchlists WHERE name='Verkaufsliste'")[0]["n"] == 0


def test_a_watchlist_named_like_that_later_stays_a_watchlist(client):
    created = client.post("/api/watchlists", json={"game_id": "vcard", "name": "Verkaufsliste"}).get_json()["id"]
    client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": created})

    deckledger.init_database()

    assert sheets() == []
    assert [item["name"] for item in client.get("/api/watchlists?game_id=vcard").get_json()] == ["Merkliste", "Verkaufsliste"]


def test_an_empty_sale_list_just_disappears():
    legacy_sale_list()
    legacy_sale_list("lorcana", entries=[(ELSA, 1, "auto")])     # nothing owned: the entry is stale
    deckledger.init_database()
    assert sheets() == [] and sale_lists_left() == 0


def test_quantities_are_brought_into_the_range_a_sheet_allows():
    legacy_sale_list(entries=[(EMBER8, 250, "manual"), (TIDE8, 0, "manual")])
    deckledger.init_database()
    assert [(row[4], row[5]) for row in sheets()] == [(EMBER8, 99), (TIDE8, 1)]


def test_other_watchlists_are_untouched(client):
    client.post("/api/watchlist", json={"variant_id": TIDE8})
    custom = client.post("/api/watchlists", json={"game_id": "vcard", "name": "Kaufen"}).get_json()["id"]
    client.post("/api/watchlist", json={"variant_id": EMBER8, "list_id": custom})
    legacy_sale_list(entries=[(BOOST, 1, "manual")])

    deckledger.init_database()

    lists = client.get("/api/watchlists?game_id=vcard").get_json()
    assert [(item["name"], item["count"]) for item in lists] == [("Merkliste", 1), ("Kaufen", 1)]
    sheet = client.get("/api/trade-sheets?game_id=vcard").get_json()
    assert [(item["name"], item["kind"], item["card_count"]) for item in sheet] == [("Verkaufsliste", "WTS", 1)]


def test_a_failing_migration_leaves_the_list_in_place(monkeypatch):
    """Everything happens in one transaction: either the sheet exists and the list is gone, or
    nothing changed."""
    legacy_sale_list(entries=[(EMBER8, 2, "manual")])
    connection = sqlite3.connect(deckledger.DB_PATH)
    connection.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(deckledger, "playset_size", lambda game_id: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        deckledger.migrate_sale_lists(connection)
    except RuntimeError:
        connection.rollback()
    connection.close()
    assert sale_lists_left() == 1 and sheets() == []
    assert query("SELECT COUNT(*) n FROM named_watchlist_entries WHERE variant_id=?", (EMBER8,))[0]["n"] == 1


def test_new_accounts_and_games_get_no_sale_list(client):
    assert [item["name"] for item in client.get("/api/watchlists?game_id=vcard").get_json()] == ["Merkliste"]
    assert all("is_sale_list" not in item for item in client.get("/api/export.json").get_json()["watchlists"])


def test_bootstrap_tells_the_picker_the_playset_size(client):
    games = {game["id"]: game["playset_size"] for game in client.get("/api/bootstrap").get_json()["games"]}
    assert games["vcard"] == 3 and games["lorcana"] == 4


# ---- backups ----------------------------------------------------------------------------------

def card(variant_id, **fields):
    row = query(
        """SELECT v.id variant_id,g.name game,s.code set_code,s.name set_name,p.collector_number,i.canonical_name,p.language,v.finish
           FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
           JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id WHERE v.id=?""", (variant_id,))[0]
    return {**row, **fields}


OLD_BACKUP = lambda: {
    "format_version": 2, "collection": [],
    "watchlists": [
        {"name": "Merkliste", "game": "VCard Trading Card Game", "is_default": 1, "is_sale_list": 0, "entries": [card(TIDE8, quantity=1, source="manual")]},
        {"name": "Verkaufsliste", "game": "VCard Trading Card Game", "is_default": 0, "is_sale_list": 1,
         "entries": [card(EMBER8, quantity=2, source="auto"), card(BOOST, quantity=1, source="manual")]},
    ],
}


def test_old_backup_restores_its_sale_list_as_a_sheet(client):
    preview = client.post("/api/import/json/preview", json=OLD_BACKUP()).get_json()
    assert [(row["kind"], row["original"]) for row in preview] == [("watchlist", "Watchlist: Merkliste"), ("sheet", "Sheet: Verkaufsliste")]

    result = client.post("/api/import/json/apply", json={**OLD_BACKUP(), "strategy": "add"}).get_json()

    assert (result["watchlist_entries_restored"], result["sheets_restored"]) == (1, 1)
    assert sheets() == [("demo", "vcard", "Verkaufsliste", "WTS", EMBER8, 2), ("demo", "vcard", "Verkaufsliste", "WTS", BOOST, 1)]
    assert [item["name"] for item in client.get("/api/watchlists?game_id=vcard").get_json()] == ["Merkliste"]

    client.post(f"/api/import/{result['operation_id']}/undo")
    assert sheets() == []


def test_old_backup_with_an_own_list_of_that_name(client):
    backup = OLD_BACKUP()
    backup["watchlists"][1]["is_sale_list"] = 0
    result = client.post("/api/import/json/apply", json={**backup, "strategy": "add"}).get_json()
    assert result["sheets_restored"] == 1 and len(sheets()) == 2

    # A current backup has no such marker: there, a watchlist of that name is just a watchlist.
    query("DELETE FROM trade_sheet_cards")
    query("DELETE FROM trade_sheets")
    current = {"watchlists": [{"name": "Verkaufsliste", "game": "VCard Trading Card Game", "is_default": 0, "entries": [card(EMBER8, quantity=2)]}]}
    result = client.post("/api/import/json/apply", json={**current, "strategy": "add"}).get_json()
    assert (result["sheets_restored"], result["watchlist_entries_restored"]) == (0, 1) and sheets() == []


def test_sheets_survive_export_and_restore(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Holos", "kind": "WTT"}).get_json()["id"]
    client.patch(f"/api/trade-sheets/{sheet_id}", json={"subtitle": "u/test", "background": "ember", "sort": "rarity", "layout": "5x3"})
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": EMBER8_HOLO, "quantity": 2, "label": "5 €"})
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": TIDE8, "quantity": 1})
    stored = lambda: (
        query("SELECT name,kind,subtitle,background,sort,layout FROM trade_sheets ORDER BY id"),
        query("SELECT variant_id,quantity,label FROM trade_sheet_cards ORDER BY variant_id"),
    )
    backup, before = client.get("/api/export.json").get_json(), stored()
    query("DELETE FROM trade_sheet_cards")
    query("DELETE FROM trade_sheets")

    result = client.post("/api/import/json/apply", json={**backup, "strategy": "add"}).get_json()

    assert result["sheets_restored"] == 1 and stored() == before


def test_an_existing_sheet_is_only_overwritten_with_replace(client):
    sheet_id = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Holos"}).get_json()["id"]
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": EMBER8, "quantity": 2, "label": "alt"})
    backup = client.get("/api/export.json").get_json()
    client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": TIDE8, "quantity": 1})
    cards = lambda: [(row["variant_id"], row["quantity"], row["label"]) for row in query("SELECT variant_id,quantity,label FROM trade_sheet_cards ORDER BY id")]
    edited = cards()

    added = client.post("/api/import/json/apply", json={**backup, "strategy": "add"}).get_json()
    assert (added["sheets_restored"], added["sheets_skipped"]) == (0, 1) and cards() == edited

    replaced = client.post("/api/import/json/apply", json={**backup, "strategy": "replace"}).get_json()
    assert replaced["sheets_restored"] == 1 and cards() == [(EMBER8, 2, "alt")]
    assert query("SELECT COUNT(*) n FROM trade_sheets")[0]["n"] == 1

    client.post(f"/api/import/{replaced['operation_id']}/undo")
    assert cards() == edited
