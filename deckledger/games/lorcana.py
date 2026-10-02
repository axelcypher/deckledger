"""Disney Lorcana."""

import json
import re
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from ..config import jload
from .base import Game, fetch_html

RULES_URL = "https://www.disneylorcana.com/en-US/play/ways-to-play"
DECK = ({"id": "main", "name": "Deck", "target": 60},)

# Language-independent keys for the icon-based rarity filter (one key covers both the English and
# the German printed label, since a card can appear under either depending on the language viewed).
RARITY_KEYS = {
    "common": 0, "uncommon": 1, "rare": 2, "super-rare": 3, "legendary": 4,
    "epic": 5, "enchanted": 6, "iconic": 7, "special": 8,
}

PRODUCT_PATHS = {
    "1": "the-first-chapter", "2": "rise-of-the-floodborn", "3": "into-the-inklands",
    "4": "ursulas-return", "5": "shimmering-skies", "6": "azurite-sea",
    "7": "archazias-island", "8": "reign-of-jafar", "9": "fabled", "10": "whispers",
    "11": "winterspell", "12": "wilds-unknown", "13": "attack-of-the-vine",
    "14": "hyperia-city", "15": "into-the-inkdark", "Q1": "deep-trouble",
    "Q2": "palace-heist", "Q3": "great-hunny-rescue",
}


def validate_deck(deck, cards, counts):
    errors, warnings = [], []
    is_coconut = deck["format_id"] == "coconut"
    if counts.get("main", 0) < 60:
        errors.append(f'Noch {60-counts.get("main",0)} Karten bis zum Minimum von 60.')
    names = {}
    colors = set()
    for c in cards:
        names[c["canonical_name"]] = names.get(c["canonical_name"], 0) + c["quantity"]
        attrs = jload(c["attributes"], {})
        colors.add(attrs.get("color"))
        if str(c.get("set_type") or "").lower() == "quest":
            errors.append(f'{c["canonical_name"]} ist eine Quest-Karte und nicht für Constructed-Decks zulässig.')
        if deck["format_id"] == "core" and attrs.get("legality") in ("future", "rotated", "banned"):
            errors.append(f'{c["canonical_name"]} ist im Core-Format nicht legal.')
    color_limit = 3 if is_coconut else 2
    if len(colors - {None}) > color_limit:
        errors.append(f"Das Deck enthält mehr als {color_limit} Tintenfarben.")
    if is_coconut:
        repeated = [name for name, qty in names.items() if qty > 1]
        if len(repeated) > 1:
            errors.append("Im Coconut-Format darf nur die zur Coconut-Karte gehörende Charakterkarte mehrfach enthalten sein.")
        for name in repeated:
            qty = names[name]
            matching_cards = [card for card in cards if card["canonical_name"] == name]
            if qty > 4:
                errors.append(f'{name}: auch die Coconut-Charakterkarte ist auf 4 Exemplare begrenzt.')
            if any(card.get("card_type") != "Character" for card in matching_cards):
                errors.append(f'{name}: nur die zugehörige Charakterkarte darf mehrfach enthalten sein.')
        warnings.append("Coconut-Karte und passende Tintenfarbe bitte mit der offiziellen Companion App abgleichen.")
    else:
        for name, qty in names.items():
            if qty > 4:
                errors.append(f'{name}: maximal 4 Exemplare erlaubt.')
    return errors, warnings


def is_foil(variant, game):
    # Foil% tracks the standard Silver alternate. The Epic/Enchanted/Iconic prints use their own
    # one-off finish names (Lava, Magma, ...) that are not "Normal" either, but belong to the
    # separate premium bucket, not the base+foil ladder.
    return variant["finish"] != "Normal" and game.rarity_rank(variant["rarity"]) not in game.premium_ranks


def remote_image_url(row):
    # Lorcast exposes stable image URIs from its API; do not construct CDN URLs.
    base_name = re.sub(r"\s·\s(?:Awakened|New Journey|Altitude)$", "", row["canonical_name"])
    base_name = base_name.replace(" · ", " ")
    endpoint = f"https://api.lorcast.com/v0/cards/search?q={quote_plus(base_name)}&unique=prints"
    payload = json.loads(urlopen(Request(endpoint, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read())
    results = payload.get("results", [])
    if results:
        return results[0].get("image_uris", {}).get("digital", {}).get("normal")
    return None


def remote_set_visual(set_row):
    path = PRODUCT_PATHS.get(set_row["code"])
    if not path:
        return None
    page_url = f"https://www.disneylorcana.com/en-US/product/{path}"
    page = fetch_html(page_url)
    images = re.findall(r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"', page, re.I | re.S)
    header = next((src for src, alt in images if "header" in alt.lower() or "logo" in alt.lower()), None)
    return urljoin(page_url, header) if header else None


def bundled_set_logo(set_row, public_dir):
    # The numbered sets' logos live alongside the game's other icon assets (set{code}-logo.png),
    # not in the shared public set folder.
    if not set_row["code"].isdigit():
        return None
    candidate = public_dir / "icons" / "lorcana" / f"set{int(set_row['code']):02d}-logo.png"
    return candidate if candidate.is_file() else None


def market_links(variant, attributes, mapping, search_term):
    return {
        "price_source": "Cardmarket" if variant.get("price_provider") == "cardmarket" else attributes.get("priceSource") or "Cardmarket",
        "price_url": (mapping or {}).get("source_url") or attributes.get("priceUrl") or f"https://www.cardmarket.com/en/Lorcana/Products/Search?searchString={quote_plus(search_term)}",
        "image_source": attributes.get("imageSource") or "Ravensburger-Kartenbild via LorcanaJSON",
        "image_source_url": attributes.get("imageSourceUrl") or "https://lorcanajson.org/",
    }


GAME = Game(
    id="lorcana", module_id="disney-lorcana", name="Disney Lorcana", short_name="Lorcana", module_version="1.0.0",
    languages=("DE", "EN"), accent="#8b5cf6", symbol="✦", icon="lorcana",
    logo_url="https://www.disneylorcana.com/_nuxt/logo-br-2x.Sweb4xgr.png",
    # Printings carry the rarity label in their own language, so the English and the official
    # German terms map to the same rank (Ravensburger's ladder incl. the Epic/Iconic tiers added
    # with Fabled in Sept. 2025).
    rarity_order={
        "Common": 0, "Gewöhnlich": 0,
        "Uncommon": 1, "Ungewöhnlich": 1,
        "Rare": 2, "Selten": 2,
        "Super Rare": 3, "Episch": 3,
        "Legendary": 4, "Legendär": 4,
        "Epic": 5, "Mythisch": 5,
        "Enchanted": 6, "Verzaubert": 6,
        "Iconic": 7, "Ikonisch": 7,
        "Special": 8, "Speziell": 8,
    },
    rarity_filter_keys=RARITY_KEYS,
    premium_ranks=frozenset({RARITY_KEYS["epic"], RARITY_KEYS["enchanted"], RARITY_KEYS["iconic"]}),
    cost_filter_cap=7,
    tile_editions=(("base", "", "Normal", "Silver", "Foil"),),
    is_foil=is_foil,
    main_set_types=("expansion",),
    formats=(
        {"id": "core", "name": "Core Constructed", "description": "Rotierender offizieller Kartenpool", "zones": list(DECK), "rules_url": RULES_URL},
        {"id": "infinity", "name": "Infinity Constructed", "description": "Nicht-rotierender offizieller Kartenpool", "zones": list(DECK), "rules_url": RULES_URL},
        {"id": "coconut", "name": "Coconut (Open Beta)", "description": "Offizieller thematischer Multiplayer-Betatest", "zones": list(DECK), "rules_url": "https://files.disneylorcana.com/FormatCoconut_Rules.pdf"},
    ),
    ruleset="lorcana-standard", validate_deck=validate_deck,
    excluded_deck_set_types={"quest": "Quest-Karten sind nicht für Lorcana-Constructed-Decks zulässig."},
    provider=(15, 2500, 300), price_method="cardmarket", cardmarket_game_id=19,
    official_foil_layers=True,
    remote_image_url=remote_image_url, remote_set_visual=remote_set_visual, bundled_set_logo=bundled_set_logo,
    market_links=market_links,
)
