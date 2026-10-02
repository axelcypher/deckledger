"""Trade / sale sheets: storage, sorting, paging and the rendered image."""
import io

import pytest
from PIL import Image

import catalog_sync
import sheet_render
from conftest import ADMIN, BOOST, BOOST_SECRET, ELSA, EMBER8, EMBER8_HOLO, EMBER9, SPARK_SET_TWO, SPARKY, TIDE8, login, query, sample_catalog


@pytest.fixture
def sheet(client):
    return client.post("/api/trade-sheets", json={"game_id": "vcard", "name": "Holos", "kind": "WTT"}).get_json()["id"]


def add(client, sheet_id, variant_id, **fields):
    return client.post(f"/api/trade-sheets/{sheet_id}/cards", json={"variant_id": variant_id, "delta": 1, **fields})


@pytest.mark.parametrize("count, pages", [
    (1, [(1, 1, 1)]), (5, [(3, 2, 5)]), (6, [(3, 2, 6)]), (7, [(4, 2, 7)]), (15, [(5, 3, 15)]), (16, [(6, 3, 16)]),
    (32, [(8, 4, 32)]), (33, [(8, 5, 33)]), (41, [(8, 5, 40), (1, 1, 1)]), (95, [(8, 5, 40), (8, 5, 40), (5, 3, 15)]), (0, []),
])
def test_layout_follows_the_number_of_cards(count, pages):
    assert sheet_render.paginate(count) == pages


def test_fixed_layout_and_invalid_input():
    assert sheet_render.paginate(20, "5x3") == [(5, 3, 15), (5, 3, 5)]
    assert sheet_render.parse_layout("8x4") == (8, 4)
    assert sheet_render.parse_layout("99x1") is None and sheet_render.parse_layout("quer") is None


def test_create_edit_and_delete(client, sheet):
    listing = client.get("/api/trade-sheets?game_id=vcard").get_json()
    assert [(item["name"], item["kind"], item["card_count"]) for item in listing] == [("Holos", "WTT", 0)]
    changed = client.patch(f"/api/trade-sheets/{sheet}", json={
        "name": "Verkauf Oktober", "kind": "WTS", "subtitle": "u/test", "background": "fire", "sort": "rarity", "layout": "5x3",
    }).get_json()["sheet"]
    assert (changed["name"], changed["kind"], changed["subtitle"], changed["background"], changed["sort"], changed["layout"]) == (
        "Verkauf Oktober", "WTS", "u/test", "fire", "rarity", "5x3")
    # Unknown values leave the stored ones alone.
    kept = client.patch(f"/api/trade-sheets/{sheet}", json={"kind": "XYZ", "background": "nope", "sort": "random", "layout": "100x100"}).get_json()["sheet"]
    assert (kept["kind"], kept["background"], kept["sort"], kept["layout"]) == ("WTS", "fire", "rarity", "5x3")
    assert client.delete(f"/api/trade-sheets/{sheet}").get_json() == {"deleted": True}
    assert query("SELECT COUNT(*) n FROM trade_sheets")[0]["n"] == 0


def test_cards_quantities_labels_and_removal(client, sheet):
    add(client, sheet, EMBER8)
    add(client, sheet, EMBER8)
    payload = add(client, sheet, TIDE8, label="4,50 €").get_json()
    assert {card["variant_id"]: (card["quantity"], card["label"]) for card in payload["cards"]} == {EMBER8: (2, ""), TIDE8: (1, "4,50 €")}
    payload = client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": EMBER8, "quantity": 0}).get_json()
    assert [card["variant_id"] for card in payload["cards"]] == [TIDE8]
    assert client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": TIDE8, "quantity": 500}).get_json()["cards"][0]["quantity"] == 99


def test_cards_of_another_game_or_unknown_cards_are_rejected(client, sheet):
    assert add(client, sheet, ELSA).status_code == 400
    assert add(client, sheet, "nope").status_code == 400
    bulk = client.post(f"/api/trade-sheets/{sheet}/cards", json={"entries": [{"variant_id": EMBER8, "quantity": 2}, {"variant_id": ELSA, "quantity": 1}]}).get_json()
    assert bulk["skipped"] == 1 and [card["variant_id"] for card in bulk["cards"]] == [EMBER8]


def test_sorting_by_number_and_by_rarity(client, sheet):
    for variant in (SPARKY, EMBER9, SPARK_SET_TWO, BOOST_SECRET, EMBER8):
        add(client, sheet, variant)
    by_number = [card["variant_id"] for card in client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"]]
    # Older set first, then the collector number within a set.
    assert by_number == [SPARK_SET_TWO, EMBER8, EMBER9, SPARKY, BOOST_SECRET]
    by_rarity = [card["variant_id"] for card in client.patch(f"/api/trade-sheets/{sheet}", json={"sort": "rarity"}).get_json()["cards"]]
    assert by_rarity == [BOOST_SECRET, EMBER9, SPARK_SET_TWO, EMBER8, SPARKY]


def test_pages_and_text(client, sheet):
    add(client, sheet, EMBER8_HOLO, label="3 €")
    add(client, sheet, TIDE8)
    payload = client.patch(f"/api/trade-sheets/{sheet}", json={"kind": "WTS", "name": "Abgabe"}).get_json()
    assert payload["pages"] == [{"columns": 2, "rows": 1, "count": 2}]
    assert payload["text"].splitlines() == [
        "**[WTS] Abgabe**", "",
        "* 1x **Ember (PL8)** (1 001, EN, Holo) – 3 €",
        "* 1x **Tide (PL8)** (1 003, EN)",
    ]


def test_image_is_rendered_for_each_page(client, sheet):
    for variant in (EMBER8, EMBER9, TIDE8, SPARKY, BOOST, BOOST_SECRET, EMBER8_HOLO):
        add(client, sheet, variant)
    response = client.get(f"/api/trade-sheets/{sheet}/image/1.jpg")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    image = Image.open(io.BytesIO(response.get_data()))
    # 7 cards -> 4 x 2: wider than tall, at the full card width of 300 px.
    assert image.width > 4 * 300 and image.height > 2 * 420 and image.width > image.height
    small = Image.open(io.BytesIO(client.get(f"/api/trade-sheets/{sheet}/image/1.png?scale=0.5").get_data()))
    assert small.format == "PNG" and abs(small.width - image.width / 2) <= 2
    download = client.get(f"/api/trade-sheets/{sheet}/image/1.jpg?download=1")
    assert download.headers["Content-Disposition"] == "attachment; filename=wtt-holos.jpg"
    assert client.get(f"/api/trade-sheets/{sheet}/image/2.jpg").status_code == 404
    assert client.get(f"/api/trade-sheets/{sheet}/image/1.gif").status_code == 404


def test_every_background_renders():
    cards = [{"image_path": None, "name": f"Karte {n}", "set_code": "1", "number": f"{n:03d}", "quantity": n, "label": "5 €"} for n in range(1, 6)]
    for name in sheet_render.BACKGROUNDS:
        image = sheet_render.render_page(cards, 3, 2, background=name, kind="WTS", title="Titel – mit Umlauten äöü", subtitle="u/name", scale=0.4)
        # 3 columns of 120 px cards plus gaps and margins; two rows with a label line each.
        assert 400 < image.size[0] < 520 and 450 < image.size[1] < 600
    assert sheet_render.swatch("water").size == (240, 150)


@pytest.mark.parametrize("finish, variant_code, rarity, game, parallel, holo", [
    ("Normal", "normal", "Uncommon", "vcard", 0, False),
    ("1st Edition", "1st-edition-regular", "Secret Rare", "vcard", 0, False),
    ("Holo", "unlimited-holo", "Uncommon", "vcard", 0, True),
    ("1st Edition Holo", "1st-edition-holo", "Common", "vcard", 0, True),
    ("Silver", "silver", "Rare", "lorcana", 1, True),
    ("Magma", "magma", "Rare", "lorcana", 1, True),             # named after its pattern: known by the flag
    ("Normal", "normal", "Enchanted", "lorcana", 0, True),
    ("standard", "normal", "R", "one-piece", 0, False),
    ("parallel", "parallel", "R", "one-piece", 1, True),
    ("standard", "normal", "SEC", "one-piece", 0, True),
    ("SR", "normal", "SR", "hololive", 0, True),
    ("C", "normal", "C", "hololive", 0, False),
    (None, None, None, None, None, False),
])
def test_which_cards_count_as_holo(finish, variant_code, rarity, game, parallel, holo):
    assert sheet_render.is_holo(finish, variant_code, rarity, game, parallel) is holo


def test_holo_cards_are_marked_in_the_image(tmp_path):
    """A holo gets a rainbow rim outside the card and a sheen on it; a regular card neither."""
    scan = tmp_path / "card.png"
    Image.new("RGB", (300, 420), "#3a6ea5").save(scan)
    card = {"image_path": str(scan), "name": "Karte", "set_code": "1", "number": "001", "quantity": 1, "label": ""}
    regular = sheet_render.render_page([card], 1, 1).convert("RGB")
    holo = sheet_render.render_page([{**card, "holo": True}], 1, 1).convert("RGB")
    assert regular.size == holo.size
    left, top = (regular.width - 300) // 2, round(300 * .34) + round(300 * .44)
    saturation = lambda image, point: (lambda pixel: max(pixel) - min(pixel))(image.getpixel(point))
    rim = [(left - 4, top + 210), (left + 303, top + 210), (left + 150, top - 4), (left + 150, top + 423)]
    assert all(saturation(holo, point) > 90 for point in rim), "rainbow rim on all four sides"
    assert all(saturation(regular, point) < 40 for point in rim), "the mat itself is nearly grey there"
    inside = [(left + x, top + y) for x in (40, 150, 260) for y in (60, 210, 360)]
    assert {regular.getpixel(point) for point in inside} == {(58, 110, 165)}
    assert len({holo.getpixel(point) for point in inside}) > 3, "the sheen varies across the card"
    assert all(sum(holo.getpixel(point)) >= sum(regular.getpixel(point)) for point in inside), "it only adds light"


def test_holo_sheen_leaves_black_areas_alone():
    image = Image.new("RGBA", (300, 420), "#000000")
    assert sheet_render.holo_sheen(image).convert("RGB").getcolors() == [(300 * 420, (0, 0, 0))]


def test_sheet_image_marks_the_holo_variant(client, sheet):
    add(client, sheet, EMBER8)
    plain = client.get(f"/api/trade-sheets/{sheet}/image/1.png").get_data()
    client.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": EMBER8, "quantity": 0})
    add(client, sheet, EMBER8_HOLO)
    holo = Image.open(io.BytesIO(client.get(f"/api/trade-sheets/{sheet}/image/1.png").get_data())).convert("RGB")
    plain = Image.open(io.BytesIO(plain)).convert("RGB")
    left, top = (holo.width - 300) // 2, round(300 * .34) + round(300 * .44)
    spread = lambda image: (lambda pixel: max(pixel) - min(pixel))(image.getpixel((left - 4, top + 210)))
    assert spread(holo) > 90 and spread(plain) < 40


def test_bundled_font_has_the_glyphs_labels_need():
    face = sheet_render.font(40)
    assert all(face.getmask(glyph).getbbox() for glyph in "×–€äöüß")


def test_sheets_belong_to_their_user(client, sheet):
    other = login(ADMIN)
    assert other.get(f"/api/trade-sheets/{sheet}").status_code == 404
    assert other.post(f"/api/trade-sheets/{sheet}/cards", json={"variant_id": EMBER8, "delta": 1}).status_code == 404
    assert other.get(f"/api/trade-sheets/{sheet}/image/1.jpg").status_code == 404
    assert other.delete(f"/api/trade-sheets/{sheet}").status_code == 404
    assert other.get("/api/trade-sheets?game_id=vcard").get_json() == []


def test_catalog_sync_keeps_cards_that_are_on_a_sheet(client, sheet):
    add(client, sheet, EMBER8)
    catalog = sample_catalog()
    for key in [k for k, row in catalog["variants"].items() if row["printing_id"] == "vcard-print-ember8-en"]:
        del catalog["variants"][key]
    del catalog["printings"]["vcard-print-ember8-en"], catalog["identities"]["vcard-card-ember8"]
    catalog_sync.write_database(catalog, {"vcard"})
    assert [card["variant_id"] for card in client.get(f"/api/trade-sheets/{sheet}").get_json()["cards"]] == [EMBER8]


def test_export_contains_sheets(client, sheet):
    add(client, sheet, EMBER8, label="2 €")
    exported = client.get("/api/export.json").get_json()["trade_sheets"]
    assert [(item["name"], item["kind"], [(card["variant_id"], card["quantity"], card["label"]) for card in item["cards"]]) for item in exported] == [
        ("Holos", "WTT", [(EMBER8, 1, "2 €")]),
    ]
