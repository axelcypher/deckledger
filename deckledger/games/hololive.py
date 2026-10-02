"""hololive Official Card Game."""

import re
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from .base import Game, fetch_html

ZONE_FOR_CARD_TYPE = {"Oshi": "oshi", "Oshi holomem": "oshi", "推しホロメン": "oshi", "Cheer": "cheer", "エール": "cheer"}


def zone_of(card_type):
    return ZONE_FOR_CARD_TYPE.get(str(card_type or "").strip(), "main")


def validate_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("oshi", 0) != 1:
        errors.append("Genau 1 Oshi holomem ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Main Deck: {counts.get("main",0)}/50 Karten.')
    if counts.get("cheer", 0) != 20:
        errors.append(f'Cheer Deck: {counts.get("cheer",0)}/20 Karten.')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare im Main Deck erlaubt.')
    if any(zone_of(c["card_type"]) != "oshi" for c in cards if c["zone"] == "oshi"):
        errors.append("In der Oshi-Zone ist nur ein Oshi holomem erlaubt.")
    if any(zone_of(c["card_type"]) != "main" for c in cards if c["zone"] == "main"):
        errors.append("Oshi- und Cheer-Karten dürfen nicht ins Main Deck.")
    if any(zone_of(c["card_type"]) != "cheer" for c in cards if c["zone"] == "cheer"):
        errors.append("Das Cheer Deck darf nur Cheer-Karten enthalten.")
    return errors, warnings


def remote_image_url(row):
    expansion = row["set_code"]
    official_number = row["collector_number"]
    if expansion.startswith("BP"):
        expansion = f"h{expansion}"
        official_number = f"h{official_number}"
    host = "https://en.hololive-official-cardgame.com" if row["language"] == "EN" else "https://hololive-official-cardgame.com"
    listing = f"{host}/cardlist/cardsearch/?expansion={quote_plus(expansion)}"
    page = urlopen(Request(listing, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read().decode("utf-8", "ignore")
    prefix = "EN_" if row["language"] == "EN" else ""
    pattern = rf'(?:src|data-src)="([^"]*{re.escape(prefix + official_number)}[^"]*\.png[^"]*)"'
    match = re.search(pattern, page, re.I)
    if not match and row["language"] == "EN":
        # Some early cards were published only in the Japanese catalogue.
        fallback = f"https://hololive-official-cardgame.com/cardlist/cardsearch/?expansion={quote_plus(expansion)}"
        page = urlopen(Request(fallback, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read().decode("utf-8", "ignore")
        match = re.search(rf'(?:src|data-src)="([^"]*{re.escape(official_number)}[^"]*\.png[^"]*)"', page, re.I)
        return urljoin(fallback, match.group(1)) if match else None
    return urljoin(listing, match.group(1)) if match else None


def remote_set_visual(set_row):
    for base in ("https://en.hololive-official-cardgame.com", "https://hololive-official-cardgame.com"):
        page_url = f"{base}/cardlist/"
        page = fetch_html(page_url)
        pattern = rf'<a class="anchor" href="(/cardlist/cardsearch/\?expansion={re.escape(set_row["code"])})">(.*?)</a>'
        product = re.search(pattern, page, re.I | re.S)
        if product:
            image = re.search(r'<img[^>]+src="([^"]+)"', product.group(2), re.I)
            if image:
                return urljoin(base, image.group(1))
    return None


def market_links(variant, attributes, mapping, search_term):
    provider_labels = {"tcgplayer": "TCGplayer", "yuyutei": "Yuyutei"}
    fallback_url = (
        f"https://yuyu-tei.jp/sell/hocg/s/{variant['set_code'].lower()}"
        if variant["language"] == "JP"
        else f"https://www.tcgplayer.com/search/all/product?q={quote_plus(search_term)}&view=grid"
    )
    return {
        "price_source": provider_labels.get(variant.get("price_provider")) or ("Yuyutei" if variant["language"] == "JP" else "TCGplayer"),
        "price_url": (mapping or {}).get("source_url") or fallback_url,
        "image_source": "Offizieller hololive Card-Katalog",
        "image_source_url": attributes.get("imageSourceUrl") or "https://en.hololive-official-cardgame.com/cardlist/",
    }


GAME = Game(
    id="hololive", module_id="hololive-ocg", name="hololive Official Card Game", short_name="hololive", module_version="0.9.0",
    languages=("EN", "JP"), accent="#06b6d4", symbol="◈", icon="hololive",
    logo_url="https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/global/logo_w.svg",
    rarity_order={"C": 0, "U": 1, "R": 2, "RR": 3, "SR": 4, "OSR": 5, "SEC": 6, "HR": 6},
    main_set_types=("booster", "boosters"),
    formats=(
        {"id": "standard", "name": "Official Standard", "description": "Offizielles Constructed-Regelset",
         "zones": [{"id": "oshi", "name": "Oshi", "target": 1}, {"id": "main", "name": "Main Deck", "target": 50}, {"id": "cheer", "name": "Cheer Deck", "target": 20}],
         "rules_url": "https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/rule/official_rule_book_ver1_02.pdf"},
    ),
    ruleset="hololive-standard", zone_for_card_type=ZONE_FOR_CARD_TYPE, validate_deck=validate_deck,
    provider=(10, 500, 600), price_method="tcgcsv", price_overrides=(("JP", "yuyutei"),),
    remote_image_url=remote_image_url, remote_set_visual=remote_set_visual, market_links=market_links,
)
