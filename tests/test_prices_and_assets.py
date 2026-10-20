"""Latest-price lookup, the totals built on it, and how pages are delivered."""
import gzip
import json
import re
from pathlib import Path

import pytest

from conftest import ELSA, EMBER8, TIDE8, deckledger, query


def observe(variant_id, provider, amount, observed_at, metric="trend", mapped=True):
    game_id = variant_id.split("-")[0]
    if mapped:
        query(
            "INSERT OR IGNORE INTO marketplace_products VALUES(?,?,?,?,?,?,?,?)",
            (provider, f"{provider}-{variant_id}", variant_id, game_id, "https://example.com/p", "test", observed_at, "{}"),
        )
    query(
        "INSERT INTO price_observations(variant_id,provider_id,metric,amount,currency,observed_at) VALUES(?,?,?,?,'EUR',?)",
        (variant_id, provider, metric, amount, observed_at),
    )


def price_of(client, variant_id, identity_id):
    return next(v for v in client.get(f"/api/cards/{identity_id}").get_json()["variants"] if v["id"] == variant_id)


def test_latest_price_is_the_newest_observation(client):
    for day, amount in (("2026-01-01", 1.0), ("2026-01-03", 3.0), ("2026-01-02", 2.0)):
        observe(EMBER8, "cardmarket", amount, f"{day}T06:00:00+00:00")
    variant = price_of(client, EMBER8, "vcard-card-ember8")
    assert (variant["price"], variant["price_provider"]) == (3.0, "cardmarket")
    assert variant["price_observed_at"].startswith("2026-01-03")


def test_a_tie_on_the_timestamp_is_settled_the_same_way_every_time(client):
    """Two rows of one provider with the same timestamp: the older row. Two other providers with
    the same timestamp: the first by name, whatever order their mappings were stored in."""
    observe(EMBER8, "cardmarket", 4.0, "2026-01-05T06:00:00+00:00")
    observe(EMBER8, "cardmarket", 6.0, "2026-01-05T06:00:00+00:00")
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 4.0
    observe(TIDE8, "yuyutei", 2.0, "2026-01-05T06:00:00+00:00")
    observe(TIDE8, "tcgplayer", 3.0, "2026-01-05T06:00:00+00:00")
    variant = price_of(client, TIDE8, "vcard-card-tide8")
    assert (variant["price"], variant["price_provider"]) == (3.0, "tcgplayer")


def test_cardmarket_wins_over_a_newer_price_from_another_provider(client):
    observe(EMBER8, "cardmarket", 5.0, "2026-01-01T06:00:00+00:00")
    observe(EMBER8, "tcgplayer", 9.0, "2026-02-01T06:00:00+00:00")
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 5.0


def test_other_providers_are_used_when_cardmarket_has_nothing(client):
    observe(EMBER8, "tcgplayer", 9.0, "2026-02-01T06:00:00+00:00")
    observe(EMBER8, "yuyutei", 7.0, "2026-03-01T06:00:00+00:00")
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 7.0


def test_observations_without_a_product_mapping_are_ignored(client):
    observe(EMBER8, "cardmarket", 4.0, "2026-01-01T06:00:00+00:00", mapped=False)
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] is None


def test_low_metric_follows_the_same_rule(client):
    observe(EMBER8, "cardmarket", 5.0, "2026-01-01T06:00:00+00:00")
    observe(EMBER8, "cardmarket", 1.5, "2026-01-01T06:00:00+00:00", metric="low")
    observe(EMBER8, "cardmarket", 2.5, "2026-01-02T06:00:00+00:00", metric="low")
    assert price_of(client, EMBER8, "vcard-card-ember8")["price_low"] == 2.5


def test_collection_value_in_bootstrap_and_set_overview(client):
    observe(EMBER8, "cardmarket", 2.0, "2026-01-01T06:00:00+00:00")
    observe(TIDE8, "cardmarket", 10.0, "2026-01-01T06:00:00+00:00")
    observe(ELSA, "cardmarket", 100.0, "2026-01-01T06:00:00+00:00")
    client.post("/api/collection", json={"variant_id": EMBER8, "delta": 3})
    client.post("/api/collection", json={"variant_id": TIDE8, "delta": 1, "condition": "Played"})

    vcard = next(game for game in client.get("/api/bootstrap").get_json()["games"] if game["id"] == "vcard")
    assert (vcard["copies"], vcard["unique_cards"], vcard["value"]) == (4, 2, 16.0)
    test_set = next(item for item in client.get("/api/games/vcard/sets").get_json() if item["id"] == "vcard-test")
    assert (test_set["owned"], test_set["value"]) == (2, 16.0)
    assert test_set["total"] == query("SELECT COUNT(DISTINCT identity_id) n FROM printings WHERE set_id='vcard-test'")[0]["n"]


def test_static_urls_carry_a_content_hash(client, tmp_path, monkeypatch):
    """Regression: hand-maintained ?v=N numbers were forgotten, and the service worker then kept
    serving the old file for good."""
    page = client.get("/").get_data(as_text=True)
    hashed = dict(re.findall(r'/static/([\w./-]+\.(?:js|css))\?v=([0-9a-f]{12})"', page))
    assert {"app.css", "js/core.js", "js/main.js"} <= set(hashed)
    # Every local script and stylesheet the page loads goes through asset_url().
    assert set(re.findall(r'(?:src|href)="/static/([\w./-]+\.(?:js|css))[?"]', page)) == set(hashed)

    monkeypatch.setattr(deckledger.app, "static_folder", str(tmp_path))
    (tmp_path / "probe.js").write_text("one")
    with deckledger.app.test_request_context():
        first = deckledger.web.asset_url("probe.js")
        (tmp_path / "probe.js").write_text("two, and longer")
        second = deckledger.web.asset_url("probe.js")
    assert first != second and first.startswith("/static/probe.js?v=")


def test_hashed_static_urls_are_cached_for_good(client):
    """A URL naming the file's current content can be kept without revalidating; one with no or
    an outdated hash must still be checked every time."""
    with deckledger.app.test_request_context():
        current = deckledger.web.asset_url("js/core.js")
    kept = client.get(current)
    assert kept.status_code == 200
    assert kept.cache_control.immutable and kept.cache_control.max_age == 31_536_000 and kept.cache_control.public
    assert not kept.cache_control.no_cache
    for url in ("/static/js/core.js", "/static/js/core.js?v=000000000000"):
        checked = client.get(url)
        assert checked.cache_control.no_cache and not checked.cache_control.immutable


def test_static_text_is_sent_compressed(client):
    path = Path(deckledger.app.static_folder) / "app.css"
    plain = client.get("/static/app.css")
    packed = client.get("/static/app.css", headers={"Accept-Encoding": "gzip, deflate"})
    assert "Content-Encoding" not in plain.headers and plain.get_data() == path.read_bytes()
    assert packed.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(packed.get_data()) == path.read_bytes()
    assert int(packed.headers["Content-Length"]) == len(packed.get_data()) < path.stat().st_size / 3
    assert "Accept-Encoding" in plain.headers["Vary"] and "Accept-Encoding" in packed.headers["Vary"]
    assert "Accept-Ranges" not in packed.headers
    # Each representation has its own tag, and either revalidates to "not modified".
    assert packed.headers["ETag"] != plain.headers["ETag"]
    for response, encoding in ((packed, "gzip"), (plain, "identity")):
        again = client.get("/static/app.css", headers={"Accept-Encoding": encoding, "If-None-Match": response.headers["ETag"]})
        assert again.status_code == 304 and not again.get_data()


def test_static_images_and_small_files_are_not_compressed(client, tmp_path, monkeypatch):
    image = client.get("/static/icons/icon-192.png", headers={"Accept-Encoding": "gzip"})
    assert image.status_code == 200 and "Content-Encoding" not in image.headers
    monkeypatch.setattr(deckledger.app, "static_folder", str(tmp_path))
    (tmp_path / "tiny.css").write_text("body{}")
    tiny = client.get("/static/tiny.css", headers={"Accept-Encoding": "gzip"})
    assert tiny.status_code == 200 and "Content-Encoding" not in tiny.headers and tiny.get_data() == b"body{}"


def test_large_json_answers_are_compressed(client):
    plain = client.get("/api/sets/vcard-test/cards")
    packed = client.get("/api/sets/vcard-test/cards", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(packed.get_data())) == plain.get_json()
    assert len(packed.get_data()) < len(plain.get_data()) / 3


def test_small_answers_and_images_are_left_alone(client):
    assert "Content-Encoding" not in client.get("/api/watchlists?game_id=vcard", headers={"Accept-Encoding": "gzip"}).headers
    assert "Content-Encoding" not in client.get(f"/art/{EMBER8}.svg", headers={"Accept-Encoding": "gzip"}).headers


def test_card_back_that_ships_with_the_app_is_served(anonymous):
    """Deployments mount their own public folder, so a bundled back must not depend on it."""
    response = anonymous.get("/card-back/vcard")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"
    assert len(response.get_data()) == (Path(deckledger.app.static_folder) / "assets" / "vcard" / "vcard-back.jpg").stat().st_size
    assert anonymous.get("/card-back/unknown-game").status_code == 404


def test_json_answers_are_written_as_flask_writes_them():
    """orjson writes the answers (deckledger/web.py OrjsonProvider); what a client reads stays the same."""
    import datetime
    import decimal
    from flask import json as flask_json
    payload = {"b": 1, "a": [1.5, None, True, 1e16], "ä": "Pokémon", "when": datetime.date(2026, 1, 2),
               "price": decimal.Decimal("1.50"), "by_id": {7: "seven", 3: "three"}}
    with deckledger.app.test_request_context():
        fast = deckledger.app.json.response(payload).get_data()
        standard = flask_json.provider.DefaultJSONProvider(deckledger.app).response(payload).get_data()
        assert json.loads(fast) == json.loads(standard)
        text = fast.decode()
        assert text.index('"a"') < text.index('"b"') < text.index('"by_id"') and text.index('"3"') < text.index('"7"'), "keys stay sorted"
        assert deckledger.app.json.response(1, 2).get_json() == [1, 2] and deckledger.app.json.response().get_json() is None
        assert json.loads(deckledger.app.json.response(nan=float("nan")).get_data()) == {"nan": None}


def test_a_set_logo_dropped_into_the_public_folder_is_used_at_once(client, tmp_path, monkeypatch):
    """The folder's listing is kept between requests; adding, replacing and removing a file shows."""
    import os
    import time
    monkeypatch.setattr(deckledger.assets, "PUBLIC_SET_DIR", tmp_path)
    set_row = {"id": "vcard-test", "game_id": "vcard", "code": "1", "name": "Test Set"}

    def version():
        return deckledger.assets.set_visual_version(set_row)

    assert version() == "provider-v3"
    (tmp_path / "vcard-test.png").write_bytes(b"first")
    os.utime(tmp_path, ns=(time.time_ns(), time.time_ns() + 1_000_000))  # some file systems keep coarse folder times
    first = version()
    assert first.startswith("public-")
    assert client.get("/set-logo/vcard-test").get_data() == b"first"
    (tmp_path / "vcard-test.png").write_bytes(b"second, replaced in place")
    assert version() != first
    (tmp_path / "vcard-test.png").unlink()
    os.utime(tmp_path, ns=(time.time_ns(), time.time_ns() + 2_000_000))
    assert version() == "provider-v3"


@pytest.mark.parametrize("url", ["/static/app.css", "/assets/lorcana/foil-presets.json", "/glass-plate.svg", "/service-worker.js"])
def test_compressed_files_answer_like_the_plain_ones(client, url):
    plain = client.get(url)
    packed = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert packed.headers["Content-Encoding"] == "gzip" and gzip.decompress(packed.get_data()) == plain.get_data()
    head = client.head(url, headers={"Accept-Encoding": "gzip"})
    assert head.get_data() == b"" and head.headers["Content-Length"] == packed.headers["Content-Length"]
    not_modified = client.get(url, headers={"Accept-Encoding": "gzip", "If-None-Match": packed.headers["ETag"]})
    assert not_modified.status_code == 304 and "Accept-Encoding" in not_modified.headers["Vary"]
    if url == "/service-worker.js":
        assert not_modified.headers["Service-Worker-Allowed"] == packed.headers["Service-Worker-Allowed"] == "/"
    # Byte ranges come from the file as it is; a client that refuses gzip gets it plain.
    ranged = client.get(url, headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert ranged.status_code == 206 and "Content-Encoding" not in ranged.headers and ranged.get_data() == plain.get_data()[:10]
    assert "Content-Encoding" not in client.get(url, headers={"Accept-Encoding": "gzip;q=0, identity"}).headers


def test_the_page_shell_is_sent_compressed(client):
    plain = client.get("/")
    packed = client.get("/", headers={"Accept-Encoding": "gzip, deflate, br"})
    assert packed.headers["Content-Encoding"] == "gzip" and gzip.decompress(packed.get_data()) == plain.get_data()
    assert "Accept-Encoding" in plain.headers["Vary"]
    assert "Content-Encoding" not in client.get("/", headers={"Accept-Encoding": "gzip;q=0"}).headers


def test_the_inlined_glass_plate_follows_its_file(tmp_path, monkeypatch):
    monkeypatch.setattr(deckledger.pages, "PUBLIC_DIR", tmp_path)
    (tmp_path / "glass-plate.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><g id="glass_side"/></svg>')
    assert deckledger.pages.glass_plate_inline_markup() == '<g id="glass_side"/>'
    (tmp_path / "glass-plate.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg">\r\n<g id="glass_side" fill="#fff"/></svg>')
    assert deckledger.pages.glass_plate_inline_markup() == '\n<g id="glass_side" fill="#fff"/>'
    (tmp_path / "glass-plate.svg").unlink()
    assert deckledger.pages.glass_plate_inline_markup() == ""
