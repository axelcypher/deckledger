"""The per-game rules live in deckledger/games; the rest of the app only asks the registry."""
import re
from pathlib import Path

import pytest

from conftest import ELSA, EMBER8, SPARKY, deckledger, query
from deckledger import games
from deckledger.games import GAMES, GENERIC, game

PACKAGE = Path(deckledger.__file__).parent


def test_every_shipped_game_is_complete():
    assert set(GAMES) == {"lorcana", "one-piece", "hololive", "vcard"}
    for rules in GAMES.values():
        assert rules.name and rules.short_name and rules.languages and rules.accent.startswith("#"), rules.id
        assert rules.formats and all(item["zones"] and item["rules_url"] for item in rules.formats), rules.id
        assert rules.ruleset and rules.validate_deck and games.DECK_RULESETS[rules.ruleset] is rules, rules.id
        assert rules.rarity_order and rules.provider and rules.logo_url and rules.main_set_types, rules.id
        assert (PACKAGE.parent / "providers" / f"{rules.id.replace('-', '_')}.py").is_file(), rules.id
        assert (PACKAGE.parent / "static" / "tcg-icons" / f"{rules.icon}.svg").is_file(), rules.id


def test_only_the_games_package_knows_a_game_by_its_id():
    """The point of the registry: no module outside it branches on a game's id."""
    pattern = re.compile(r"""game_id"?\]?\s*(?:==|!=|in\s*\()\s*["'(]""")
    offenders = [
        f"{path.name}:{number}: {line.strip()[:90]}"
        for path in sorted(PACKAGE.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_a_game_without_a_module_gets_plain_defaults():
    rules = game("my-own-game")
    assert rules is GENERIC
    assert (rules.playset_size, rules.copy_limit("Anything"), rules.formats, rules.format("standard")) == (4, 4, (), {})
    assert rules.rarity_rank("Rare") == games.RARITY_FALLBACK_RANK
    assert rules.deck_card_error({"set_type": "Quest", "card_type": "X", "rarity": "R"}, adding=True) is None
    assert games.rarity_case_sql("my-own-game", "p.rarity") == "p.rarity COLLATE NOCASE"
    assert rules.is_foil({"finish": "Foil"}, rules) and not rules.is_foil({"finish": "Normal"}, rules)


@pytest.mark.parametrize("game_id, card_type, limit", [
    ("vcard", "VT", 3), ("vcard", "Mascot", 2), ("lorcana", "Character", 4), ("one-piece", "Character", 4), ("hololive", "holomem", 4),
])
def test_copy_limits(game_id, card_type, limit):
    assert game(game_id).copy_limit(card_type) == limit


def test_playset_sizes():
    assert {game_id: games.playset_size(game_id) for game_id in GAMES} == {"lorcana": 4, "one-piece": 4, "hololive": 4, "vcard": 3}


def test_what_may_go_into_a_deck():
    lorcana, vcard = game("lorcana"), game("vcard")
    quest = {"set_type": "Quest", "card_type": "Character", "rarity": "Rare"}
    topper = {"set_type": "Booster Set", "card_type": "Box Topper", "rarity": "Box Topper"}
    assert "Quest" in lorcana.deck_card_error(quest, adding=True) and "Quest" in lorcana.deck_card_error(quest, adding=False)
    assert vcard.deck_card_error(topper, adding=True) == "Box Topper-Karten sind Sammelkarten und nicht spielbar."
    assert vcard.deck_card_error(topper, adding=False) is None, "an unplayable card can always be taken out again"
    assert vcard.deck_card_error({"set_type": None, "card_type": "VT", "rarity": "Rare"}, adding=True) is None


def test_foil_counts_follow_the_game():
    lorcana, vcard, hololive = game("lorcana"), game("vcard"), game("hololive")
    assert lorcana.is_foil({"finish": "Silver", "rarity": "Rare"}, lorcana)
    assert not lorcana.is_foil({"finish": "Magma", "rarity": "Enchanted"}, lorcana), "premium prints are their own bucket"
    assert vcard.is_foil({"finish": "1st Edition Holo"}, vcard) and not vcard.is_foil({"finish": "1st Edition"}, vcard)
    assert hololive.is_foil({"finish": "SR"}, hololive)


def test_cost_filter_cap_means_this_or_more():
    assert games.cost_matches("lorcana", 9, ["7"]) and not games.cost_matches("lorcana", 6, ["7"])
    assert games.cost_matches("vcard", 9, ["9"]) and not games.cost_matches("vcard", 9, ["8"])
    assert games.matches_catalog_filters("lorcana", "Rare", {"cost": 8}, [], ["7"], [], "")
    assert not games.matches_catalog_filters("vcard", "Rare", {"cost": 8}, [], ["7"], [], "")


def test_rarity_filter_keys_are_lorcanas():
    assert games.rarity_filter_ranks("lorcana", ["rare", "enchanted", "nonsense"]) == {2, 6}
    assert games.rarity_filter_ranks("vcard", ["rare"]) == set()
    assert games.matches_catalog_filters("lorcana", "Selten", {}, ["rare"], [], [], "")
    assert not games.matches_catalog_filters("lorcana", "Common", {}, ["rare"], [], [], "")


def test_zone_follows_the_ruleset():
    assert games.zone_for_card_type("one-piece-standard", "Leader") == "leader"
    assert games.zone_for_card_type("hololive-standard", "エール") == "cheer"
    assert games.zone_for_card_type("vcard-standard", "VT") == "main"
    assert games.zone_for_card_type("no-such-ruleset", "Leader") == "main"


def test_market_links_name_each_games_marketplace():
    variant = {"price_provider": None, "language": "EN", "set_code": "1"}
    sources = {game_id: rules.market_links(variant, {}, None, "Name 001 Normal")["price_source"] for game_id, rules in GAMES.items()}
    assert sources == {"lorcana": "Cardmarket", "one-piece": "Cardmarket", "hololive": "TCGplayer", "vcard": "eBay"}
    mapped = game("vcard").market_links(variant, {}, {"source_url": "https://example.com/p"}, "x")
    assert mapped["price_url"] == "https://example.com/p"


def test_the_database_is_seeded_from_the_registry():
    rows = {row["id"]: row for row in query("SELECT * FROM games")}
    assert set(rows) == set(GAMES)
    for game_id, rules in GAMES.items():
        row = rows[game_id]
        assert (row["name"], row["short_name"], row["accent"], row["deck_ruleset"]) == (rules.name, rules.short_name, rules.accent, rules.ruleset)
        assert (row["price_method"], row["cardmarket_game_id"]) == (rules.price_method, rules.cardmarket_game_id)
    assert {row["id"] for row in query("SELECT id FROM catalog_providers")} == set(GAMES)
    assert query("SELECT language,price_method FROM game_price_overrides WHERE game_id='hololive'") == [{"language": "JP", "price_method": "yuyutei"}]


def test_the_frontend_gets_its_rules_with_the_game(client):
    sent = {item["id"]: item for item in client.get("/api/bootstrap").get_json()["games"]}
    assert (sent["vcard"]["playset_size"], sent["vcard"]["copy_limits"], sent["vcard"]["icon"]) == (3, {"Mascot": 2}, "vcard")
    assert (sent["lorcana"]["playset_size"], sent["lorcana"]["copy_limits"], sent["lorcana"]["symbol"]) == (4, {}, "✦")
    assert [item["id"] for item in client.get("/api/games/one-piece/formats").get_json()] == ["standard"]


def test_deck_builder_refuses_what_the_game_refuses(client):
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "D"}).get_json()["id"]
    topper = "vcard-print-topper-en-normal"
    refused = client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": topper, "zone": "auto", "delta": 1})
    assert refused.status_code == 400 and "Sammelkarten" in refused.get_json()["error"]
    assert client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": SPARKY, "zone": "auto", "delta": 1}).status_code == 200
    assert client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": ELSA, "zone": "auto", "delta": 1}).status_code == 400
    names = {item["canonical_name"] for item in client.get("/api/deckbuilder/catalog?game_id=vcard&limit=200").get_json()["cards"]}
    assert "Ember (PL8)" in names and "Sparky" in names
    assert not any(item["card_type"] == "Box Topper" for item in client.get("/api/deckbuilder/catalog?game_id=vcard&limit=200").get_json()["cards"])
    assert client.post("/api/collection", json={"variant_id": EMBER8, "delta": 1}).status_code == 200
