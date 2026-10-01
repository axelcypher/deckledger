"""VCard: the provider's parsing of the official set pages and the deck rules."""
import importlib.util
import json

import pytest

from conftest import BOOST, BOOST_SECRET, EMBER8, EMBER9, LEAF8, ROOT, SPARKY, TIDE8, TOPPER

spec = importlib.util.spec_from_file_location("vcard_provider", ROOT / "providers" / "vcard.py")
vcard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vcard)


def source_card(card_id, name, card_type, number, level="N/A", element=None, editions=("unlimited", "firstEdition"), finishes=("regular", "holographic"), **extra):
    folder = card_type + "S"
    return {
        "cardId": f"CARD#{card_id}", "name": name, "cardType": card_type, "cardNumber": number, "level": level,
        "element": element, "cardDescription": f"Text of {name}", "finishes": list(finishes), "revealed": True,
        "artistName": "Artist", "personalityName": "Creator", "strength": "FIRE", "weakness": None,
        "variants": {
            edition: {"edition": edition, "playable": card_type not in ("BOX_TOPPER", "GOD_RARE", "PROMO"), "collectible": card_type != "GOD_RARE",
                      "url": f"https://cdn.example/VCARD/Set/{edition}/{folder}/{name}.png"}
            for edition in editions
        },
        **extra,
    }


def set_page(cards, set_number=3):
    """A set page as the official site serves it: the data sits JSON-encoded inside script pushes."""
    config = {"id": "test-set", "name": "Test Set", "slug": "test-set", "setId": set_number, "releaseDate": "February 27th, 2026", "theme": {"primary": "#ff6b6b"}}
    by_type = {}
    for card in cards:
        by_type.setdefault(card["cardType"], []).append(card)
    payload = 'f:["$","$L1a",null,' + json.dumps({"setConfig": config, "initialCardsData": by_type}, separators=(",", ":")) + "]\n"
    half = len(payload) // 2  # the real pages split the payload across several pushes
    return "<html>" + "".join(f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>" for part in (payload[:half], payload[half:])) + "</html>"


CARDS = [
    source_card("1001", "Ember", "UNCOMMON", 1, level="8", element="FIRE"),
    source_card("1002", "Ember", "RARE", 2, level="9", element="FIRE"),
    source_card("1003", "Ember", "ULTRA_RARE", 3, level="10", element="FIRE", finishes=("holographic",)),
    source_card("1004", "Sparky", "MASCOT", 4, element="fire"),
    source_card("1005", "Hunter's Moon", "SUPPORT", 5),
    source_card("1006", "Hunter's Moon", "SECRET_RARE", 6, finishes=("holographic",)),
    source_card("1007", "Ember", "PARADOX", 3, level="10", element="FIRE", finishes=("holographic",)),
    source_card("1008", "Sparky", "PARADOX", 4, element="FIRE", finishes=("holographic",)),
    source_card("1009", "Ember", "GOD_RARE", 9, level="10", element="FIRE", editions=("firstEdition",), finishes=("holographic",)),
    source_card("1010", "Ember", "PROMO", None, level="9", element="FIRE", editions=("unlimited",)),
    source_card("1011", "Ember", "BOX_TOPPER", 312, finishes=("regular",)),
    source_card("1012", "Sparky", "BOX_TOPPER", 322, finishes=("regular",)),
    source_card("1013", "Hidden", "UNCOMMON", 13, level="8", revealed=False),
]


@pytest.fixture(scope="module")
def parsed():
    return vcard.parse_set_page(set_page(CARDS), "test-set", "https://www.vcardtcg.com/cards/test-set")


def printing(parsed, key):
    return parsed["printings"][f"vcard-print-3-{key}-en"]


def test_set_metadata(parsed):
    assert parsed["sets"]["vcard-test-set"] == {
        "id": "vcard-test-set", "game_id": "vcard", "code": "3", "name": "Test Set", "set_type": "Booster Set",
        "release_date": "2026-02-27", "printed_card_count": None, "classifications": ["Booster Set"],
        "accent": "#ff6b6b", "_source_language": "EN",
    }


def test_rarity_and_gameplay_type(parsed):
    kinds = {p["collector_number"] + " " + p["rarity"]: parsed["identities"][p["identity_id"]]["card_type"] for p in parsed["printings"].values()}
    assert kinds == {
        "001 Uncommon": "VT", "002 Rare": "VT", "003 Ultra Rare": "VT", "004 Mascot": "Mascot",
        "005 Support": "Support", "006 Secret Rare": "Support",
        "003 Paradox": "VT", "004 Paradox": "Mascot",  # alternates inherit the type of the card they reprint
        "P-1010 Promo": "Promo", "BT-1 Box Topper": "Box Topper", "BT-2 Box Topper": "Box Topper",
    }


def test_power_level_is_part_of_a_vt_name(parsed):
    names = sorted(i["canonical_name"] for i in parsed["identities"].values() if i["card_type"] == "VT")
    assert names == ["Ember (PL10)", "Ember (PL10)", "Ember (PL8)", "Ember (PL9)"]
    ember8 = parsed["identities"]["vcard-card-3-1001"]["attributes"]
    assert (ember8["color"], ember8["cost"], ember8["legality"]) == ("Fire", 8, "Playable")


def test_god_rares_and_unrevealed_cards_are_left_out(parsed):
    assert "vcard-print-3-1009-en" not in parsed["printings"]
    assert "vcard-print-3-1013-en" not in parsed["printings"]


def test_every_edition_and_finish_is_a_variant(parsed):
    variants = {v["id"].removeprefix("vcard-print-3-1001-en-"): (v["variant_code"], v["finish"]) for v in parsed["variants"].values() if v["printing_id"] == "vcard-print-3-1001-en"}
    assert variants == {
        "unlimited-regular": ("normal", "Normal"), "unlimited-holo": ("unlimited-holo", "Holo"),
        "1st-edition-regular": ("1st-edition-regular", "1st Edition"), "1st-edition-holo": ("1st-edition-holo", "1st Edition Holo"),
    }


def test_holo_only_cards_still_have_a_base_variant(parsed):
    codes = sorted(v["variant_code"] for v in parsed["variants"].values() if v["printing_id"] == "vcard-print-3-1003-en")
    assert codes == ["1st-edition-holo", "normal"]


def test_image_urls_are_encoded_and_fall_back_to_the_other_edition(parsed):
    variant = parsed["variants"]["vcard-print-3-1005-en-unlimited-regular"]["attributes"]
    assert variant["imageUrl"] == "https://cdn.example/VCARD/Set/unlimited/SUPPORTS/Hunter%27s%20Moon.png"
    assert variant["imageFallbackUrls"] == ["https://cdn.example/VCARD/Set/firstEdition/SUPPORTS/Hunter%27s%20Moon.png"]


def test_box_topper_numbers_are_only_decoded_when_the_whole_set_fits():
    """'312' = set 3, number 1 of 2 toppers. Plain running numbers stay as they are."""
    toppers = [card for card in CARDS if card["cardType"] == "BOX_TOPPER"]
    assert vcard.box_topper_numbers(toppers, 3) == {"CARD#1011": 1, "CARD#1012": 2}
    plain = [dict(toppers[0], cardNumber=287), dict(toppers[1], cardNumber=288)]
    assert vcard.box_topper_numbers(plain, 1) == {}


def test_release_date_parsing():
    assert vcard.release_date("January 31st, 2025") == "2025-01-31"
    assert vcard.release_date("August 15th, 2025") == "2025-08-15"
    assert vcard.release_date("soon") is None


def test_page_without_card_data_yields_nothing():
    assert vcard.parse_set_page("<html></html>", "x", "https://example.com")["printings"] == {}


# ---- deck rules ----------------------------------------------------------------------------

@pytest.fixture
def deck(client):
    deck_id = client.post("/api/decks", json={"game_id": "vcard", "name": "Regeltest"}).get_json()["id"]

    def add(variant_id, quantity):
        response = client.post(f"/api/decks/{deck_id}/cards", json={"variant_id": variant_id, "zone": "auto", "delta": quantity})
        return response.status_code, response.get_json()

    return add


def errors(result):
    return result[1]["validation"]["errors"]


def test_copy_limits(deck):
    assert "Ember (PL8): maximal 3 Exemplare erlaubt." in errors(deck(EMBER8, 4))
    assert "Sparky: maximal 2 Exemplare erlaubt." in errors(deck(SPARKY, 3))
    # The PL9 card shares the creator's name but is a different card with its own limit.
    assert not any("Ember (PL9)" in message for message in errors(deck(EMBER9, 3)))


def test_alternate_printings_count_as_the_same_card(deck):
    deck(BOOST, 2)
    assert "Boost: maximal 3 Exemplare erlaubt." in errors(deck(BOOST_SECRET, 2))


def test_at_most_two_elements(deck):
    deck(EMBER8, 1)
    assert not any("Elemente" in message for message in errors(deck(TIDE8, 1)))
    assert any("3 Elemente (Fire, Grass, Water)" in message for message in errors(deck(LEAF8, 1)))


def test_needs_a_pl8_vt(deck):
    assert "Das Deck braucht mindestens 1 PL8 VT." in errors(deck(EMBER9, 1))
    assert "Das Deck braucht mindestens 1 PL8 VT." not in errors(deck(EMBER8, 1))


def test_collectibles_cannot_be_added(deck, client):
    status, body = deck(TOPPER, 1)
    assert status == 400 and "nicht spielbar" in body["error"]
    catalog = client.get("/api/deckbuilder/catalog?game_id=vcard&limit=200").get_json()["cards"]
    assert "Box Topper" not in {card["card_type"] for card in catalog}


def test_a_legal_50_card_deck(deck, client):
    result = None
    for variant_id, quantity in [(EMBER8, 3), (EMBER9, 3), (TIDE8, 3), (SPARKY, 2), (BOOST, 3)] + [(f"vcard-print-filler{n}-en-normal", 3) for n in range(1, 13)]:
        result = deck(variant_id, quantity)
    assert result[1]["validation"]["counts"] == {"main": 50}
    assert result[1]["validation"]["valid"] is True, errors(result)
