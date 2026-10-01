"""Tier-2 custom-code provider: VCard TCG (Gamer Supps), sourced from the
official card database at vcardtcg.com.

Self-contained on purpose (Tier-2 code runs in an isolated subprocess with no
access to the rest of the app): only stdlib + catalog_provider_contract.

The official site has no public API (api.vcardtcg.com is login-only and is
deliberately not touched). Every public set page under /cards/<slug> ships its
complete card list server-rendered inside the Next.js flight payload, so one
plain GET per set is the whole import.
"""

import json
import re
from datetime import datetime
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from catalog_provider_contract import empty_catalog, fetch, slug

BASE = "https://www.vcardtcg.com"
INDEX_URL = f"{BASE}/cards"
# Sets that must import successfully. Newer sets are discovered from the site's
# own set registry on top of this list, so a new release needs no code change;
# this list only guarantees that a broken discovery never silently drops a set
# that already holds collection data.
KNOWN_SET_SLUGS = ("rising-stars", "awakened-worlds", "divine-chaos", "fractured-paradox")
# God Rares are 1-of-1 signature cards; the official collection tracker flags
# them `collectible: false`. Importing them would make 100% set completion
# impossible for everyone, so they are skipped unless this is switched on.
INCLUDE_NON_COLLECTIBLE = False

# Official `cardType` mixes rarity and gameplay role. Left: the label kept as
# the printing's rarity (it is what the official checklists group by). Right:
# the gameplay card type the deck rules care about, None = resolved per card.
CARD_KINDS = {
    "UNCOMMON": ("Uncommon", "VT"),
    "RARE": ("Rare", "VT"),
    "ULTRA_RARE": ("Ultra Rare", "VT"),
    "MASCOT": ("Mascot", "Mascot"),
    "SUPPORT": ("Support", "Support"),
    "WORLD": ("World", "World"),
    "SECRET_RARE": ("Secret Rare", None),
    "PARADOX": ("Paradox", None),
    "GOD_RARE": ("God Rare", "God Rare"),
    "BOX_TOPPER": ("Box Topper", "Box Topper"),
    "PROMO": ("Promo", "Promo"),
}
# Alternate printings of a regular card; their gameplay type is the one of the
# same-named base card in the set, with this as the fallback.
ALTERNATE_FALLBACK = {"SECRET_RARE": "Support", "PARADOX": "Mascot"}
MISSING_NUMBER_PREFIX = {"GOD_RARE": "GR", "PROMO": "P", "BOX_TOPPER": "BT"}
EDITION_ORDER = {"unlimited": 0, "firstEdition": 1}
EDITION_LABELS = {"unlimited": "", "firstEdition": "1st Edition"}
FINISH_ORDER = {"regular": 0, "holographic": 1}
FINISH_LABELS = {"regular": "", "holographic": "Holo"}


def flight_payload(page: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page, re.S)
    return "".join(json.loads(f'"{chunk}"') for chunk in chunks)


def payload_value(payload: str, key: str):
    marker = f'"{key}":'
    start = payload.find(marker)
    if start < 0:
        return None
    return json.JSONDecoder().raw_decode(payload[start + len(marker):])[0]


def discover_set_slugs() -> list[str]:
    """The set registry only exists inside the /cards page bundle, not in its HTML."""
    slugs = list(KNOWN_SET_SLUGS)
    try:
        index = fetch(INDEX_URL)
        bundle = re.search(r'src="([^"]*/app/cards/page-[^"]+\.js[^"]*)"', index)
        if bundle:
            script = fetch(urljoin(INDEX_URL, bundle.group(1)), headers={"Accept": "*/*"})
            for found in re.findall(r'path:"/cards/([a-z0-9-]+)"', script):
                if found not in slugs:
                    slugs.append(found)
    except RuntimeError as error:
        print(f"VCard: Set-Erkennung übersprungen ({error}); bekannte Sets werden geladen.", flush=True)
    return slugs


def release_date(value: str | None) -> str | None:
    text = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", value or "")
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def power_level(card: dict) -> int | None:
    level = str(card.get("level") or "").strip()
    return int(level) if level in ("8", "9", "10") else None


def title(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text.title() if text else None


def image_url(variant: dict) -> str | None:
    url = variant.get("imageUrl") or variant.get("url")
    if not url:
        return None
    # File names are derived from card names and contain apostrophes, commas,
    # ampersands and non-ASCII letters; "%" stays safe so nothing is escaped twice.
    parts = urlsplit(urljoin(BASE, url))
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"), parts.query, parts.fragment))


def edition_label(edition: str) -> str:
    if edition in EDITION_LABELS:
        return EDITION_LABELS[edition]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", edition).title()


def box_topper_numbers(cards: list[dict], set_number) -> dict[str, int]:
    """Newer sets list box toppers as '<set><n><total>' run together: 3780 is
    set 3, number 7 of 80. Only decoded when every topper of the set fits."""
    prefix, suffix = str(set_number), str(len(cards))
    decoded = {}
    for card in cards:
        raw = str(card.get("cardNumber") or "")
        middle = raw[len(prefix):-len(suffix)] if raw.startswith(prefix) and raw.endswith(suffix) else ""
        if not middle.isdigit() or not 1 <= int(middle) <= len(cards):
            return {}
        decoded[card["cardId"]] = int(middle)
    return decoded if len(set(decoded.values())) == len(cards) else {}


def collector_number(card: dict, key: str, toppers: dict[str, int], topper_width: int) -> str:
    if card["cardId"] in toppers:
        return f"BT-{toppers[card['cardId']]:0{topper_width}d}"
    raw = str(card.get("cardNumber") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return f"{int(raw):03d}"
    if raw and raw != "0":
        return raw
    # God Rares and most promos carry no printed number in the source.
    return f"{MISSING_NUMBER_PREFIX.get(card.get('cardType'), 'X')}-{key.upper()}"


def parse_set_page(page: str, set_slug: str, source_url: str) -> dict:
    catalog = empty_catalog()
    payload = flight_payload(page)
    config = payload_value(payload, "setConfig")
    cards_by_type = payload_value(payload, "initialCardsData")
    if not isinstance(config, dict) or not isinstance(cards_by_type, dict):
        return catalog
    set_number = config.get("setId")
    set_id = f"vcard-{slug(set_slug)}"
    cards = [
        card for group in cards_by_type.values() for card in group
        if card.get("cardId") and card.get("name") and card.get("revealed") is not False and not card.get("isPlaceholder")
    ]
    if not cards:
        return catalog
    catalog["sets"][set_id] = {
        "id": set_id,
        "game_id": "vcard",
        "code": str(set_number or set_slug),
        "name": config.get("name") or set_slug,
        "set_type": "Booster Set",
        "release_date": release_date(config.get("releaseDate")),
        "printed_card_count": None,
        "classifications": ["Booster Set"],
        "accent": (config.get("theme") or {}).get("primary") or "#f5c451",
        "_source_language": "EN",
    }
    base_types = {}
    for card in cards:
        card_type = CARD_KINDS.get(card.get("cardType"), (None, None))[1]
        if card_type in ("VT", "Mascot", "Support", "World"):
            base_types[(card["name"].strip().lower(), power_level(card))] = card_type
    toppers_in_set = [card for card in cards if card.get("cardType") == "BOX_TOPPER"]
    toppers = box_topper_numbers(toppers_in_set, set_number)
    topper_width = len(str(len(toppers_in_set)))

    for card in cards:
        official_type = card.get("cardType") or "UNKNOWN"
        rarity, card_type = CARD_KINDS.get(official_type, (official_type.replace("_", " ").title(), None))
        level = power_level(card)
        name = card["name"].strip()
        if card_type is None:
            card_type = base_types.get((name.lower(), level)) or ("VT" if level else ALTERNATE_FALLBACK.get(official_type, rarity))
        key = slug(card["cardId"].split("#", 1)[-1])
        number = collector_number(card, key, toppers, topper_width)
        editions = {
            edition: variant for edition, variant in (card.get("variants") or {}).items()
            if image_url(variant) and (INCLUDE_NON_COLLECTIBLE or variant.get("collectible") is not False)
        }
        if not editions:
            continue
        playable = any(variant.get("playable") for variant in editions.values())
        identity_id = f"vcard-card-{slug(set_number or set_slug)}-{key}"
        printing_id = f"vcard-print-{slug(set_number or set_slug)}-{key}-en"
        rules_text = (card.get("cardDescription") or card.get("description") or "").strip()
        catalog["identities"][identity_id] = {
            "id": identity_id,
            "game_id": "vcard",
            # A creator's PL8, PL9 and PL10 cards are three different cards printed
            # under one name; name-based decklists and exports need them apart.
            "canonical_name": f"{name} (PL{level})" if card_type == "VT" and level else name,
            "rules_text": rules_text,
            "card_type": card_type,
            "attributes": {
                # `color`/`cost` are the names the shared catalogue filters and
                # sorters read; for VCard they hold the element and Power Level.
                "color": title(card.get("element")),
                "cost": level,
                "strength": title(card.get("strength")),
                "weakness": title(card.get("weakness")),
                "creator": card.get("personalityName"),
                "legality": "Playable" if playable else "Collectible only",
                "source": "Official VCard TCG card database",
                "sourceLanguage": "EN",
            },
        }
        catalog["printings"][printing_id] = {
            "id": printing_id,
            "identity_id": identity_id,
            "game_id": "vcard",
            "set_id": set_id,
            "collector_number": number,
            "language": "EN",
            "rarity": rarity,
            "attributes": {
                "cardId": card["cardId"],
                "officialCardType": official_type,
                "artists": [card["artistName"]] if card.get("artistName") else [],
                "creator": card.get("personalityName"),
                "sourceUrl": source_url,
            },
        }
        finishes = [finish for finish in (card.get("finishes") or ["regular"]) if finish] or ["regular"]
        ordered = sorted(
            ((edition, finish) for edition in editions for finish in finishes),
            key=lambda pair: (EDITION_ORDER.get(pair[0], 9), pair[0], FINISH_ORDER.get(pair[1], 9), pair[1]),
        )
        for position, (edition, finish) in enumerate(ordered):
            variant = editions[edition]
            label = edition_label(edition)
            finish_label = FINISH_LABELS.get(finish, finish.title())
            code = f"{slug(label or 'unlimited')}-{slug(finish_label or 'regular')}"
            variant_id = f"{printing_id}-{code}"
            fallbacks = [image_url(other) for name_, other in editions.items() if name_ != edition]
            catalog["variants"][variant_id] = {
                "id": variant_id,
                "printing_id": printing_id,
                "game_id": "vcard",
                # "normal" marks the printing's default variant for the shared
                # catalogue/deckbuilder queries -- the Unlimited regular print
                # where one exists, otherwise the closest thing to it.
                "variant_code": "normal" if position == 0 else code,
                "finish": " ".join(part for part in (label, finish_label) if part) or "Normal",
                "artwork_id": f"{key}-{slug(edition)}",
                "is_parallel": 0,
                "source_type": "official-vcard",
                "attributes": {
                    "imageUrl": image_url(variant),
                    # Freshly released sets publish their First Edition scans
                    # before the Unlimited ones exist on the CDN.
                    "imageFallbackUrls": [url for url in fallbacks if url and url != image_url(variant)],
                    "imageSourceUrl": source_url,
                    "catalogSourceUrl": source_url,
                    "edition": edition,
                    "playable": bool(variant.get("playable")),
                },
            }
    return catalog


def fetch_catalog() -> dict:
    catalog = empty_catalog()
    catalog["sources"]["vcard_en"] = INDEX_URL
    for set_slug in discover_set_slugs():
        source_url = f"{INDEX_URL}/{set_slug}"
        try:
            page = fetch(source_url)
        except RuntimeError:
            if set_slug in KNOWN_SET_SLUGS:
                raise
            continue
        part = parse_set_page(page, set_slug, source_url)
        if not part["printings"]:
            if set_slug in KNOWN_SET_SLUGS:
                raise RuntimeError(f"VCard: {source_url} enthält keine Kartendaten mehr")
            # Announced sets get a registry entry before any card is revealed.
            continue
        print(f"VCard: {set_slug} – {len(part['printings'])} Karten, {len(part['variants'])} Varianten", flush=True)
        for section in ("sets", "identities", "printings", "variants"):
            catalog[section].update(part[section])
    return catalog
