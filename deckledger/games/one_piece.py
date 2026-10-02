"""One Piece Card Game."""

import re
from urllib.parse import quote_plus, urljoin

from ..config import jload
from .base import Game, fetch_html


def validate_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("leader", 0) != 1:
        errors.append("Genau 1 Leader ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Hauptdeck: {counts.get("main",0)}/50 Karten.')
    # Explicit DON!! artwork is optional. Any open slots are represented by
    # the standard DON!! card in the builder and do not have to be stored.
    if counts.get("don", 0) > 10:
        errors.append(f'DON!!-Deck: maximal 10 Karten ({counts.get("don",0)}/10).')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare erlaubt.')
    if any(c["card_type"] != "Leader" for c in cards if c["zone"] == "leader"):
        errors.append("In der Leader-Zone sind nur Leader-Karten erlaubt.")
    if any(c["card_type"] == "Leader" or c["card_type"] == "DON!!" for c in cards if c["zone"] == "main"):
        errors.append("Leader- und DON!!-Karten dürfen nicht ins Hauptdeck.")
    if any(c["card_type"] != "DON!!" for c in cards if c["zone"] == "don"):
        errors.append("Das DON!!-Deck darf nur DON!!-Karten enthalten.")
    leader = next((c for c in cards if c["zone"] == "leader"), None)
    if leader:
        leader_color = jload(leader["attributes"], {}).get("color")
        invalid = [c["canonical_name"] for c in cards if c["zone"] == "main" and jload(c["attributes"], {}).get("color") != leader_color]
        if invalid:
            warnings.append(f'{len(invalid)} Karten entsprechen nicht der Leader-Farbe {leader_color}.')
    return errors, warnings


def remote_image_url(row):
    host = "https://en.onepiece-cardgame.com" if row["language"] == "EN" else "https://www.onepiece-cardgame.com"
    suffix = "_p1" if row["variant_code"] in ("parallel", "manga") else ""
    return f'{host}/images/cardlist/card/{row["collector_number"]}{suffix}.png'


def remote_set_visual(set_row):
    code = re.sub(r"[^a-z0-9]", "", set_row["code"].lower())
    category = "decks" if set_row["code"].startswith("ST-") else "boosters"
    filenames = [code]
    starter_number = re.fullmatch(r"st(\d{2})", code)
    if starter_number:
        number = int(starter_number.group(1))
        for first, last in ((1, 4), (8, 9), (15, 20), (23, 28), (31, 36)):
            if first <= number <= last:
                filenames.append(f"st{first:02d}-{last:02d}")
    for base in ("https://en.onepiece-cardgame.com", "https://www.onepiece-cardgame.com"):
        for filename in filenames:
            page_url = f"{base}/products/{category}/{filename}.php"
            try:
                page = fetch_html(page_url)
            except Exception:
                continue
            images = re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', page, re.I)
            hero = next((src for src in images if "/images/products/" in src and re.search(r"/mv(?:_|\.)", src, re.I)), None)
            if hero:
                return urljoin(page_url, hero)
    return None


def market_links(variant, attributes, mapping, search_term):
    return {
        "price_source": "Cardmarket",
        "price_url": (mapping or {}).get("source_url") or f"https://www.cardmarket.com/en/OnePiece/Products/Search?searchString={quote_plus(search_term)}",
        "image_source": attributes.get("imageSource") or "Offizieller One Piece Card-Katalog",
        "image_source_url": attributes.get("imageSourceUrl") or "https://en.onepiece-cardgame.com/cardlist/",
    }


GAME = Game(
    id="one-piece", module_id="one-piece-card-game", name="One Piece Card Game", short_name="One Piece", module_version="1.0.0",
    languages=("EN", "JP"), accent="#ef4444", symbol="☠", icon="one-piece",
    logo_url="https://en.onepiece-cardgame.com/renewal/images/common/logo_op_white.png",
    rarity_order={
        "C": 0, "UC": 1, "R": 2, "SR": 3, "SEC": 4, "L": 5,
        "SP": 6, "SP CARD": 6, "SPカード": 6, "SP P": 6, "TR": 7,
    },
    main_set_types=("booster set",),
    formats=(
        {"id": "standard", "name": "Official Standard", "description": "Aktuelle offizielle Regulation",
         "zones": [{"id": "leader", "name": "Leader", "target": 1}, {"id": "main", "name": "Deck", "target": 50}, {"id": "don", "name": "DON!!", "target": 10}],
         "rules_url": "https://en.onepiece-cardgame.com/rules/"},
    ),
    ruleset="one-piece-standard", zone_for_card_type={"Leader": "leader", "DON!!": "don"}, validate_deck=validate_deck,
    deck_language="EN",
    # A card reprinted in a later product keeps its number: the builder lists it once.
    deck_catalog_across_printings=True,
    auto_fill_zone=("don", 10, "one-piece-print-don-008-en-standard"),
    provider=(20, 1000, 600), price_method="cardmarket", cardmarket_game_id=18,
    remote_image_url=remote_image_url, remote_set_visual=remote_set_visual, market_links=market_links,
)
