"""VCard Trading Card Game (Gamer Supps)."""

import re
from urllib.parse import quote, quote_plus, urljoin

import sheet_render

from ..config import jload
from .base import Game, fetch_html

# Box Toppers, Promos and God Rares are collectibles without a gameplay role.
PLAYABLE_TYPES = ("VT", "Mascot", "Support", "World")
COPY_LIMITS = {"Mascot": 2}


def validate_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("main", 0) != 50:
        errors.append(f'Deck: {counts.get("main",0)}/50 Karten.')
    copies, elements, has_pl8 = {}, set(), False
    for c in cards:
        attrs = jload(c["attributes"], {})
        if c["card_type"] not in PLAYABLE_TYPES:
            errors.append(f'{c["canonical_name"]} ({c["rarity"]}) ist eine Sammelkarte und nicht spielbar.')
            continue
        # A Secret Rare or Paradox is the same card as its regular printing, so copies are counted
        # per name (which carries the Power Level for VTs) and type, not per printing.
        key = (c["canonical_name"].lower(), c["card_type"], attrs.get("cost"))
        copies.setdefault(key, [c["canonical_name"], 0])[1] += c["quantity"]
        has_pl8 = has_pl8 or (c["card_type"] == "VT" and attrs.get("cost") == 8)
        # Supports carry no element and Neutral Worlds fit every deck.
        if attrs.get("color") and attrs["color"] != "Neutral":
            elements.add(attrs["color"])
    for (_, card_type, _), (name, qty) in copies.items():
        limit = GAME.copy_limit(card_type)
        if qty > limit:
            errors.append(f'{name}: maximal {limit} Exemplare erlaubt.')
    if len(elements) > 2:
        errors.append(f'Das Deck enthält {len(elements)} Elemente ({", ".join(sorted(elements))}); maximal 2 sind erlaubt.')
    if cards and not has_pl8:
        errors.append("Das Deck braucht mindestens 1 PL8 VT.")
    return errors, warnings


def is_foil(variant, game):
    # Every card also exists as a non-holo First Edition print, which is not "Normal" either.
    return "Holo" in variant["finish"]


def remote_set_visual(set_row):
    # The set id is "vcard-<official slug>"; each official set page embeds its own logo path.
    page_url = f"https://www.vcardtcg.com/cards/{set_row['id'].removeprefix('vcard-')}"
    logo = re.search(r'\\"logo\\":\{\\"main\\":\\"(.*?)\\"', fetch_html(page_url))
    return urljoin(page_url, quote(logo.group(1), safe=":/%")) if logo else None


def market_links(variant, attributes, mapping, search_term):
    # No marketplace price feed carries VCard yet, so the market tab links to a plain eBay search
    # for the exact print instead of a mapped product page.
    return {
        "price_source": "eBay",
        "price_url": (mapping or {}).get("source_url") or f"https://www.ebay.com/sch/i.html?_nkw={quote_plus('VCard ' + search_term)}",
        "image_source": "Offizielle VCard-Kartendatenbank",
        "image_source_url": attributes.get("imageSourceUrl") or "https://www.vcardtcg.com/cards",
    }


GAME = Game(
    id="vcard", module_id="vcard-tcg", name="VCard Trading Card Game", short_name="VCard", module_version="0.9.0",
    languages=("EN",), accent="#f5c451", symbol="✪", icon="vcard",
    logo_url="https://cdn.gamersupps.gg/images/Nav-Logo.png",
    playset_size=3,
    # The official checklist groups by a label that is part card type, part rarity. Mascot,
    # Support and World are the common-slot cards, so they sort ahead of the VT rarity ladder.
    rarity_order={
        "Mascot": 0, "Support": 1, "World": 2, "Uncommon": 3, "Rare": 4, "Ultra Rare": 5,
        "Secret Rare": 6, "Paradox": 7, "Box Topper": 8, "Promo": 9, "God Rare": 10,
    },
    is_foil=is_foil,
    # Every card comes from the regular print run (called Limited or Unlimited, depending on the
    # set) and as 1st Edition, each plain and as a Holo.
    tile_editions=(
        ("base", "Limited / Unlimited", "Normal", "Holo", "Holo"),
        ("first", "1st Edition", "1st Edition", "1st Edition Holo", "Holo"),
    ),
    main_set_types=("booster set",),
    formats=(
        {"id": "standard", "name": "Official Standard", "description": "Offizielles Constructed-Regelset",
         "zones": [{"id": "main", "name": "Deck", "target": 50}], "rules_url": "https://cdn.gamersupps.gg/VCARD/files/VCard+Game+Rules.pdf"},
    ),
    ruleset="vcard-standard", validate_deck=validate_deck,
    copy_limits=COPY_LIMITS, playable_types=PLAYABLE_TYPES,
    unplayable_message="{rarity}-Karten sind Sammelkarten und nicht spielbar.",
    # No price method on purpose: neither Cardmarket nor TCGplayer lists the game, so prices are
    # entered by hand rather than estimated (an admin can assign a method once a marketplace does).
    provider=(4, 1200, 300),
    # The publisher puts its print files online: a 63 x 88 mm card with 3 mm bleed on every side.
    image_bleed=sheet_render.PRINT_BLEED,
    # The "?" card the CDN answers with (200 OK) where a print's scan is not uploaded yet, as for
    # Fractured Paradox's Unlimited Paradoxes; the 1st Edition scan stands in for it.
    image_placeholders=((64863, "2b1a429d14a88756e7bc7608c5e4627461e92b81089ff9064675f598e7408214"),),
    remote_set_visual=remote_set_visual, market_links=market_links,
)
