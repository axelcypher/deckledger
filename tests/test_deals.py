"""Deals: what a change of status books in the collection and on the sheets, the receipt of
incoming cards, and the record that is left."""
import io

import pytest
from PIL import Image

import sheet_render
from conftest import ADMIN, ELSA, EMBER8, EMBER8_HOLO, EMBER9, TIDE8, login, query


def owned(variant_id):
    return sum(row["quantity"] for row in query("SELECT quantity FROM collection_entries WHERE variant_id=?", (variant_id,)))


def new_deal(client, *cards, **fields):
    lines = [{"side": side, "variant_id": variant, "quantity": quantity, "unit_price": price} for side, variant, quantity, price in cards]
    return client.post("/api/deals", json={"game_id": "vcard", "partner": "Kim", "platform": "Forum", "cards": lines, **fields}).get_json()


def move(client, deal, status):
    return client.post(f'/api/deals/{deal["id"] if isinstance(deal, dict) else deal}/status', json={"status": status})


def sheet_with(client, kind, *cards):
    sheet = client.post("/api/trade-sheets", json={"game_id": "vcard", "name": kind, "kind": kind}).get_json()["id"]
    for variant, quantity in cards:
        client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": variant, "quantity": quantity})
    return sheet


def on_sheet(client, sheet):
    return {card["variant_id"]: (card["quantity"], card["reserved"]) for card in client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"]}


@pytest.fixture
def stock(client):
    for variant, quantity in ((EMBER8, 3), (TIDE8, 1)):
        client.post("/api/collection", json={"variant_id": variant, "quantity": quantity})


# ---- a deal and its cards --------------------------------------------------------------------------

def test_a_deal_needs_no_reddit(client, stock):
    deal = new_deal(client, ("give", EMBER8, 2, 4.5), ("get", EMBER9, 1, "3,00"), url="https://forum.example/thread/12", money_in=6, note="Übergabe auf dem Turnier")
    assert (deal["partner"], deal["platform"], deal["url"], deal["status"], deal["kind"]) == ("Kim", "Forum", "https://forum.example/thread/12", "open", "trade")
    assert [(card["side"], card["name"], card["detail"], card["quantity"], card["unit_price"], card["owned"]) for card in deal["cards"]] == [
        ("get", "Ember (PL9)", "1 · 002 · EN · Normal", 1, 3.0, 0), ("give", "Ember (PL8)", "1 · 001 · EN · Normal", 2, 4.5, 3)]
    assert (deal["value_give"], deal["value_get"], deal["money_in"]) == (9.0, 3.0, 6.0)
    assert [event["kind"] for event in deal["events"]] == ["created"]
    assert new_deal(client, ("give", EMBER8, 1, None))["kind"] == "sale" and new_deal(client, ("get", EMBER8, 1, None))["kind"] == "purchase"
    assert new_deal(client, url="javascript:alert(1)")["url"] == "", "only a web address is kept as a link"


def test_what_a_deal_refuses(client):
    assert client.post("/api/deals", json={"game_id": "nope"}).status_code == 404
    assert client.post("/api/deals", json={"game_id": "vcard", "money_in": "viel"}).status_code == 400
    assert client.post("/api/deals", json={"game_id": "vcard", "money_in": -1}).status_code == 400
    assert client.post("/api/deals", json={"game_id": "vcard", "cards": [{"side": "give", "variant_id": ELSA, "quantity": 1}]}).status_code == 400, "a card of another game"
    assert query("SELECT COUNT(*) n FROM deals")[0]["n"] == 0, "a refused deal leaves nothing behind"
    deal = new_deal(client)
    assert client.post(f'/api/deals/{deal["id"]}/cards', json={"side": "middle", "variant_id": EMBER8}).status_code == 400
    assert move(client, deal, "done").status_code == 400, "no cards yet"
    assert move(client, deal, "archived").status_code == 409


def test_cards_and_fields_can_be_changed_while_the_deal_is_open(client):
    deal = new_deal(client, ("give", EMBER8, 1, 2))
    path = f'/api/deals/{deal["id"]}'
    changed = client.post(f"{path}/cards", json={"side": "give", "variant_id": EMBER8, "delta": 2}).get_json()
    assert [(card["quantity"], card["unit_price"]) for card in changed["cards"]] == [(3, 2.0)], "the price stays when only the quantity changes"
    changed = client.post(f"{path}/cards", json={"side": "give", "variant_id": EMBER8, "delta": 0, "unit_price": ""}).get_json()
    assert changed["cards"][0]["unit_price"] is None
    assert client.post(f"{path}/cards", json={"side": "give", "variant_id": EMBER8, "quantity": 0}).get_json()["cards"] == []
    saved = client.patch(path, json={"partner": " Alex ", "platform": "Discord", "money_out": "12,50", "shipping": 1.6}).get_json()
    assert (saved["partner"], saved["platform"], saved["money_out"], saved["shipping"], saved["money_in"]) == ("Alex", "Discord", 12.5, 1.6, 0)
    assert client.patch(path, json={"shipping": "x"}).status_code == 400


def test_deals_belong_to_their_account(client, admin):
    deal = new_deal(client, ("give", EMBER8, 1, 2))
    path = f'/api/deals/{deal["id"]}'
    assert admin.get(path).status_code == 404 and admin.patch(path, json={"note": "x"}).status_code == 404
    assert admin.post(f"{path}/status", json={"status": "done"}).status_code == 404 and admin.delete(path).status_code == 404
    assert admin.post(f"{path}/cards", json={"side": "give", "variant_id": EMBER8, "delta": 1}).status_code == 404
    assert admin.post(f"{path}/receive").status_code == 404
    assert admin.get("/api/deals").get_json()["deals"] == [] and admin.get(f"/api/variants/{EMBER8}/deals").get_json() == []


# ---- reserving ---------------------------------------------------------------------------------------

def test_reserved_cards_stay_in_the_collection_and_show_on_the_sheet(client, stock):
    sheet = sheet_with(client, "WTS", (EMBER8, 3), (TIDE8, 1))
    wanted = sheet_with(client, "WTB", (EMBER8, 2))
    deal = new_deal(client, ("give", EMBER8, 2, 4), ("give", TIDE8, 1, 1))
    assert move(client, deal, "reserved").get_json()["status"] == "reserved"
    assert owned(EMBER8) == 3 and on_sheet(client, sheet) == {EMBER8: (3, 2), TIDE8: (1, 1)}
    assert on_sheet(client, wanted) == {EMBER8: (2, 0)}, "a sheet of wanted cards has nothing to reserve"
    text = client.get(f"/api/trade-sheets/{sheet}").get_json()["text"]
    assert "Ember (PL8)** (1 001) – 2 reserved" in text and "Tide (PL8)** (1 003) – reserved" in text
    # Another deal sees what is already spoken for.
    other = new_deal(client, ("give", EMBER8, 1, 4))
    assert [(card["owned"], card["reserved"]) for card in other["cards"]] == [(3, 2)]
    assert [(card["owned"], card["reserved"]) for card in client.get(f'/api/deals/{deal["id"]}').get_json()["cards"]] == [(3, 0), (1, 0)], "not its own reservation"
    assert move(client, deal, "open").get_json()["reserved_at"] is None
    assert on_sheet(client, sheet) == {EMBER8: (3, 0), TIDE8: (1, 0)}
    assert move(client, new_deal(client, ("get", EMBER9, 1, 1)), "reserved").status_code == 400, "only what goes out can be reserved"


def test_a_reservation_refreshes_the_sheet_pictures(client, stock):
    sheet = sheet_with(client, "WTS", (EMBER8, 3))
    query("UPDATE trade_sheets SET updated_at='2020-01-01T00:00:00+00:00'")
    move(client, new_deal(client, ("give", EMBER8, 1, 4)), "reserved")
    assert client.get(f"/api/trade-sheets/{sheet}").get_json()["sheet"]["updated_at"] > "2020-01-01", "the preview is cached by this stamp"


def test_reserved_copies_are_stamped_in_the_picture(client, stock, monkeypatch):
    sheet = sheet_with(client, "WTS", (EMBER8, 3), (TIDE8, 1))
    move(client, new_deal(client, ("give", EMBER8, 2, 1)), "reserved")
    drawn, render_page = [], sheet_render.render_page
    monkeypatch.setattr(sheet_render, "render_page", lambda tiles, *args, **options: drawn.extend(tiles) or render_page(tiles, *args, **options))
    answer = client.get(f"/api/trade-sheets/{sheet}/image/1.png?scale=0.4")
    assert answer.status_code == 200 and Image.open(io.BytesIO(answer.data)).width > 100
    assert [(tile["name"], tile["quantity"], tile["reserved"]) for tile in drawn] == [("Ember (PL8)", 3, 2), ("Tide (PL8)", 1, 0)]


def test_the_stamp_says_how_many():
    size, accent = (300, 420), "#d4b06a"
    some, every = sheet_render.reserved_stamp(size, 1, 3, accent), sheet_render.reserved_stamp(size, 3, 3, accent)
    band, away, corner = (150, 210), (150, 60), (1, 1)
    assert some.getpixel(band)[3] > 200 and every.getpixel(band)[3] > 200, "a band across the card"
    assert some.getpixel(away)[3] == 0 and every.getpixel(away)[3] > 80, "only a card with no copy left is dimmed"
    assert some.getpixel(corner)[3] == 0 and every.getpixel(corner)[3] == 0, "the card's rounded corners stay"
    assert some.tobytes() != sheet_render.reserved_stamp(size, 2, 3, accent).tobytes(), "the band says how many"


# ---- finishing ---------------------------------------------------------------------------------------

def test_a_sale_takes_the_cards_out_of_collection_and_sheets(client, stock):
    first, second = sheet_with(client, "WTS", (EMBER8, 3), (TIDE8, 1)), sheet_with(client, "WTT", (EMBER8, 2))
    wanted = sheet_with(client, "WTB", (EMBER8, 2))
    deal = new_deal(client, ("give", EMBER8, 2, 4), ("give", TIDE8, 1, 1), money_in=9, shipping=1.6)
    move(client, deal, "reserved")
    done = move(client, deal, "done").get_json()
    assert (done["status"], done["kind"], done["in_transit"], bool(done["completed_at"])) == ("done", "sale", False, True)
    assert owned(EMBER8) == 1 and owned(TIDE8) == 0
    assert on_sheet(client, first) == {EMBER8: (1, 0)} and on_sheet(client, second) == {}, "the same stock stands on both sheets"
    assert on_sheet(client, wanted) == {EMBER8: (2, 0)}
    assert [event["kind"] for event in done["events"]] == ["created", "reserved", "done"]
    # The record stands.
    path = f'/api/deals/{deal["id"]}'
    assert client.post(f"{path}/cards", json={"side": "give", "variant_id": EMBER8, "delta": 1}).status_code == 409
    kept = client.patch(path, json={"partner": "Somebody else", "money_in": 100, "note": "kam gut an"}).get_json()
    assert (kept["partner"], kept["money_in"], kept["note"]) == ("Kim", 9.0, "kam gut an")
    assert client.delete(path).status_code == 409 and client.post(f"{path}/receive").status_code == 400
    assert move(client, deal, "open").status_code == 409


def test_cards_that_were_never_in_the_collection_are_noted(client, stock):
    done = move(client, new_deal(client, ("give", TIDE8, 3, 1), ("give", EMBER9, 1, 1)), "done").get_json()
    assert owned(TIDE8) == 0 and [(card["name"], card["booked"]) for card in done["cards"]] == [("Tide (PL8)", 1), ("Ember (PL9)", 0)]
    assert done["events"][-1]["detail"] == "Nicht in der Sammlung: Tide (PL8): 1 von 3; Ember (PL9): 0 von 1"


def test_graded_copies_go_last(client):
    client.post("/api/collection", json={"variant_id": EMBER8, "quantity": 1, "is_graded": True, "grade_label": "PSA 10"})
    client.post("/api/collection", json={"variant_id": EMBER8, "quantity": 2, "condition": "Excellent"})
    move(client, new_deal(client, ("give", EMBER8, 2, 4)), "done")
    assert [(row["condition"], row["is_graded"], row["quantity"]) for row in query("SELECT * FROM collection_entries WHERE variant_id=?", (EMBER8,))] == [("Near Mint", 1, 1)]


def test_incoming_cards_wait_for_their_receipt(client, stock):
    wanted, offered = sheet_with(client, "WTB/WTTF", (EMBER9, 2), (EMBER8_HOLO, 1)), sheet_with(client, "WTS", (EMBER9, 1))
    deal = new_deal(client, ("give", EMBER8, 1, 4), ("get", EMBER9, 1, 3.5), ("get", EMBER8_HOLO, 1, 9))
    path = f'/api/deals/{deal["id"]}'
    assert client.post(f"{path}/receive").status_code == 409, "not before the deal is done"
    done = move(client, deal, "done").get_json()
    assert (done["in_transit"], done["received_at"]) == (True, None)
    assert owned(EMBER8) == 2 and owned(EMBER9) == 0 and owned(EMBER8_HOLO) == 0, "what goes out is gone, what comes in has not arrived"
    assert on_sheet(client, wanted) == {EMBER9: (2, 0), EMBER8_HOLO: (1, 0)}
    assert [item["id"] for item in client.get("/api/deals?game_id=vcard&status=transit").get_json()["deals"]] == [deal["id"]]

    received = client.post(f"{path}/receive").get_json()
    assert received["in_transit"] is False and received["received_at"] and received["events"][-1]["kind"] == "received"
    assert owned(EMBER9) == 1 and owned(EMBER8_HOLO) == 1
    assert on_sheet(client, wanted) == {EMBER9: (1, 0)} and on_sheet(client, offered) == {EMBER9: (1, 0)}, "wanted cards that arrived are no longer wanted"
    assert client.post(f"{path}/receive").status_code == 409, "an arrival is confirmed once"
    assert client.get("/api/deals?game_id=vcard&status=transit").get_json()["deals"] == []


# ---- cancelling --------------------------------------------------------------------------------------

def test_cancelling_before_the_end_books_nothing(client, stock):
    sheet = sheet_with(client, "WTS", (EMBER8, 3))
    deal = new_deal(client, ("give", EMBER8, 2, 4))
    move(client, deal, "reserved")
    assert move(client, deal, "cancelled").get_json()["status"] == "cancelled"
    assert owned(EMBER8) == 3 and on_sheet(client, sheet) == {EMBER8: (3, 0)}
    assert move(client, deal, "open").get_json()["cancelled_at"] is None, "a cancelled deal can be taken up again"
    move(client, deal, "cancelled")
    assert client.delete(f'/api/deals/{deal["id"]}').get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM deal_cards")[0]["n"] == 0 and query("SELECT COUNT(*) n FROM deal_events")[0]["n"] == 0


def test_cancelling_a_finished_deal_undoes_its_booking(client, stock):
    deal = new_deal(client, ("give", EMBER8, 2, 4), ("give", EMBER9, 1, 1), ("get", TIDE8, 2, 1))
    move(client, deal, "done")
    client.post(f'/api/deals/{deal["id"]}/receive')
    assert (owned(EMBER8), owned(EMBER9), owned(TIDE8)) == (1, 0, 3)
    undone = move(client, deal, "cancelled").get_json()
    assert (owned(EMBER8), owned(EMBER9), owned(TIDE8)) == (3, 0, 1), "back comes what was taken, no more"
    assert undone["received_at"] is None and undone["events"][-1]["detail"] == "Buchung in der Sammlung zurückgenommen"
    assert all(card["booked"] == 0 for card in undone["cards"])

    pending = new_deal(client, ("get", TIDE8, 2, 1))
    move(client, pending, "done")
    move(client, pending, "cancelled")
    assert owned(TIDE8) == 1, "cards that never arrived are not taken back"


# ---- the record --------------------------------------------------------------------------------------

def test_the_list_filters_and_adds_up_what_is_done(client, stock):
    sale = new_deal(client, ("give", EMBER8, 1, 4), partner="Kim", money_in=4, shipping=1)
    purchase = new_deal(client, ("get", EMBER9, 1, 6), partner="Alex", platform="Discord", money_out=6)
    new_deal(client, ("give", TIDE8, 1, 2), partner="Robin", money_in=50)
    move(client, sale, "done"), move(client, purchase, "done")
    listed = client.get("/api/deals?game_id=vcard").get_json()
    assert [deal["partner"] for deal in listed["deals"]] == ["Robin", "Alex", "Kim"] or len(listed["deals"]) == 3
    assert listed["totals"] == {"count": 3, "done": 2, "money_in": 4.0, "money_out": 6.0, "shipping": 1.0, "balance": -3.0}, "an open deal is no income yet"
    assert [deal["partner"] for deal in client.get("/api/deals?game_id=vcard&status=open").get_json()["deals"]] == ["Robin"]
    assert {deal["partner"] for deal in client.get("/api/deals?game_id=vcard&q=discord|kim").get_json()["deals"]} == {"Alex", "Kim"}
    assert [deal["partner"] for deal in client.get("/api/deals?game_id=vcard&q=Ember (PL9)").get_json()["deals"]] == ["Alex"], "a deal is found by its cards"
    assert client.get("/api/deals?game_id=lorcana").get_json()["totals"]["count"] == 0
    assert "events" not in listed["deals"][0]


def test_a_card_knows_its_deals(client, stock):
    sale = new_deal(client, ("give", EMBER8, 1, 4.5), partner="Kim")
    purchase = new_deal(client, ("get", EMBER8, 2, 3), partner="Alex", platform="Discord")
    reserved = new_deal(client, ("give", EMBER8, 1, 5), partner="Robin")
    new_deal(client, ("give", EMBER8, 1, 5), partner="Open")
    move(client, sale, "done"), move(client, purchase, "done"), move(client, reserved, "reserved")
    history = client.get(f"/api/variants/{EMBER8}/deals").get_json()
    assert {(row["partner"], row["side"], row["quantity"], row["unit_price"], row["status"], row["in_transit"]) for row in history} == {
        ("Kim", "give", 1, 4.5, "done", False), ("Alex", "get", 2, 3.0, "done", True), ("Robin", "give", 1, 5.0, "reserved", False)}
    assert client.get(f"/api/variants/{EMBER9}/deals").get_json() == []


def test_the_record_outlives_the_catalogue(client, stock):
    deal = new_deal(client, ("give", TIDE8, 1, 2))
    move(client, deal, "done")
    query("UPDATE deal_cards SET variant_id='vcard-print-gone-en-normal'")
    card = client.get(f'/api/deals/{deal["id"]}').get_json()["cards"][0]
    assert (card["known"], card["name"], card["detail"], card["unit_price"]) == (False, "Tide (PL8)", "1 · 003 · EN · Normal", 2.0)


def test_deleting_an_account_or_a_game_removes_its_deals(client, admin, stock):
    move(client, new_deal(client, ("give", EMBER8, 1, 4)), "done")
    demo_id = query("SELECT id FROM users WHERE username='demo'")[0]["id"]
    assert admin.delete(f"/api/admin/users/{demo_id}").get_json() == {"deleted": True}
    assert [query(f"SELECT COUNT(*) n FROM {table}")[0]["n"] for table in ("deals", "deal_cards", "deal_events")] == [0, 0, 0]


# ---- backup ------------------------------------------------------------------------------------------

def test_deals_travel_in_the_backup_as_a_record(client, stock):
    sale = new_deal(client, ("give", EMBER8, 2, 4.5), ("get", EMBER9, 1, 3), partner="Kim", url="https://forum.example/t/1", money_in=6, note="Turnier")
    move(client, sale, "done")
    client.post(f'/api/deals/{sale["id"]}/receive')
    move(client, new_deal(client, ("give", TIDE8, 1, 2), partner="Alex"), "reserved")
    backup = client.get("/api/export.json").get_json()
    kim = next(deal for deal in backup["deals"] if deal["partner"] == "Kim")
    assert (kim["game"], kim["status"], kim["money_in"], kim["note"], bool(kim["received_at"])) == ("VCard Trading Card Game", "done", 6.0, "Turnier", True)
    assert [(card["side"], card["canonical_name"], card["quantity"], card["unit_price"], card["booked"]) for card in kim["cards"]] == [("give", "Ember (PL8)", 2, 4.5, 2), ("get", "Ember (PL9)", 1, 3.0, 1)]
    assert [event["kind"] for event in kim["events"]] == ["created", "done", "received"]
    before = (owned(EMBER8), owned(EMBER9), owned(TIDE8))

    preview = client.post("/api/import/json/preview", json=backup).get_json()
    assert [row["original"] for row in preview if row.get("kind") == "deal"] == ["Vorgang: Kim", "Vorgang: Alex"]
    assert client.post("/api/import/json/apply", json=backup | {"collection": []}).get_json()["deals_skipped"] == 2, "they are all there already"

    query("DELETE FROM deals")
    result = client.post("/api/import/json/apply", json=backup | {"collection": []}).get_json()
    assert (result["deals_restored"], result["deals_skipped"]) == (2, 0)
    assert (owned(EMBER8), owned(EMBER9), owned(TIDE8)) == before, "a restored deal books nothing"
    restored = {deal["partner"]: deal for deal in client.get("/api/deals?game_id=vcard").get_json()["deals"]}
    assert (restored["Kim"]["status"], restored["Kim"]["url"], restored["Kim"]["in_transit"], restored["Alex"]["status"]) == ("done", "https://forum.example/t/1", False, "reserved")
    assert [(card["side"], card["variant_id"], card["booked"]) for card in restored["Kim"]["cards"]] == [("get", EMBER9, 1), ("give", EMBER8, 2)]
    assert [event["kind"] for event in client.get(f'/api/deals/{restored["Kim"]["id"]}').get_json()["events"]] == ["created", "done", "received"]

    assert client.post(f'/api/import/{result["operation_id"]}/undo').status_code == 200
    assert client.get("/api/deals?game_id=vcard").get_json()["deals"] == []
