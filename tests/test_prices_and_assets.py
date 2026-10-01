"""Latest-price lookup, the totals built on it, and how pages are delivered."""
import gzip
import json
import re

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
    scripts = re.findall(r'/static/(app\.js|app\.css|service-worker\.js)\?v=([0-9a-f]{12})"', page)
    assert {name for name, _ in scripts} == {"app.js", "app.css"}

    monkeypatch.setattr(deckledger.app, "static_folder", str(tmp_path))
    (tmp_path / "probe.js").write_text("one")
    with deckledger.app.test_request_context():
        first = deckledger.asset_url("probe.js")
        (tmp_path / "probe.js").write_text("two, and longer")
        second = deckledger.asset_url("probe.js")
    assert first != second and first.startswith("/static/probe.js?v=")


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
    assert len(response.get_data()) == (deckledger.Path(deckledger.app.static_folder) / "assets" / "vcard" / "vcard-back.jpg").stat().st_size
    assert anonymous.get("/card-back/unknown-game").status_code == 404
