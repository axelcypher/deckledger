"""Import the real card catalogues used by DeckLedger.

The importer downloads and validates every source before opening the database
transaction.  A failed or incomplete upstream response can therefore never
leave the application with a half-written catalogue.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app import DB_PATH, GAME_DATA, SCHEMA


USER_AGENT = "DeckLedger/1.0 (+local collection manager)"
CATALOG_VERSION = "real-catalog-v13"
ONE_PIECE_DON_API = "https://www.optcgapi.com/api/allDonCards/"
ONE_PIECE_DON_DOCS = "https://www.optcgapi.com/documentation"
ONE_PIECE_DON_JP_PDF = "https://asia-en.onepiece-cardgame.com/pdf/don-cardlist.pdf"
ONE_PIECE_EN_PRODUCTS = "https://en.onepiece-cardgame.com/products/"
CARD_IMAGE_CACHE = Path(DB_PATH).parent / "card-images"


# Bandai split EB-04 across two Western English booster products.  Those
# product codes describe packaging, not additional card sets: the canonical
# card sets remain OP-14, OP-15, and EB-04.  Product metadata is retained on
# each printing for source attribution and Cardmarket matching.
ONE_PIECE_COMBINED_RELEASES = {
    "OP14-EB04": {
        "primary_set": "OP-14",
        "release_date": "2026-01-16",
    },
    "OP15-EB04": {
        "primary_set": "OP-15",
        "release_date": "2026-04-03",
    },
}

ONE_PIECE_CANONICAL_SETS = {
    "OP-14": {
        "name": "BOOSTER PACK -THE AZURE SEA'S SEVEN- [OP-14]",
        "set_type": "Booster Set",
    },
    "OP-15": {
        "name": "BOOSTER PACK -ADVENTURE ON KAMI'S ISLAND- [OP-15]",
        "set_type": "Booster Set",
    },
    "EB-04": {
        "name": "EXTRA BOOSTER -EGGHEAD CRISIS- [EB-04]",
        "set_type": "Extra Booster",
    },
}


# LorcanaJSON keeps the original gameplay set in ``setCode`` for promotional
# reprints.  Their physical catalogue set is supplied separately in
# ``promoGrouping``.  These are source-owned group codes, not synthetic sets.
LORCANA_PROMO_GROUPS = {
    "P1": ("Promo Set 1", "Promo Set"),
    "P2": ("Promo Set 2", "Promo Set"),
    "P3": ("Promo Set 3", "Promo Set"),
    "P4": ("Promo Set 4", "Promo Set"),
    "C1": ("Lorcana Challenge 1", "Challenge Promo"),
    "C2": ("Lorcana Challenge 2", "Challenge Promo"),
    "D23": ("D23 Collection", "Special Collection"),
    "PD1": ("Product Series 1", "Product Promo"),
    "CC1": ("Curator's Collection: Heroines Edition", "Special Collection"),
    "DIS": ("Discover Promo", "Promo Set"),
}

LORCANA_TFC_21_MISPRINT = {
    "image_url": "https://patagiumgames.com/cdn/shop/files/IMG_2869.jpg?v=1700057930",
    "source_url": "https://patagiumgames.com/products/lorcana-2023-stitch-carefree-surfer-sorgloser-surfer-german-language-misprint",
    "price_url": "https://www.cardmarket.com/de/Lorcana/Products/Singles/The-First-Chapter/Stitch-Carefree-Surfer-V3?language=3",
}


def fetch(url: str, attempts: int = 3) -> str:
    error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"})
            with urlopen(request, timeout=40) as response:
                return response.read().decode("utf-8", "ignore")
        except Exception as exc:  # Network failures are retried, then fail the sync.
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Quelle nicht erreichbar: {url}: {error}")


def fetch_bytes(url: str, attempts: int = 3) -> bytes:
    error = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,image/*,*/*"})
            with urlopen(request, timeout=90) as response:
                return response.read()
        except Exception as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Binärquelle nicht erreichbar: {url}: {error}")


def clean(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return text or hashlib.sha1(str(value).encode()).hexdigest()[:12]


def digest(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:12]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_catalog() -> dict:
    return {"sets": {}, "identities": {}, "printings": {}, "variants": {}, "sources": {}}


def put_identity(catalog: dict, record: dict, prefer: bool = False) -> None:
    old = catalog["identities"].get(record["id"])
    if old is None or prefer:
        catalog["identities"][record["id"]] = record


def import_lorcana() -> dict:
    catalog = empty_catalog()
    root = "https://lorcanajson.org/files/current"
    payloads = {}
    for language in ("en", "de"):
        url = f"{root}/{language}/allCards.json"
        payloads[language.upper()] = json.loads(fetch(url))
        catalog["sources"][f"lorcana_{language}"] = url

    english_names = {str(c["id"]): c for c in payloads["EN"]["cards"]}
    accents = ["#7c3aed", "#2563eb", "#db2777", "#0891b2", "#d97706", "#059669"]
    for language, payload in payloads.items():
        for code, source_set in payload["sets"].items():
            set_id = f"lorcana-{slug(code)}"
            existing = catalog["sets"].get(set_id)
            if existing is None or language == "EN":
                counts = source_set.get("cardCounts", {})
                catalog["sets"][set_id] = {
                    "id": set_id,
                    "game_id": "lorcana",
                    "code": str(code),
                    "name": source_set.get("name") or str(code),
                    "set_type": source_set.get("type", "set").replace("_", " ").title(),
                    "release_date": source_set.get("releaseDate"),
                    "printed_card_count": counts.get("total") or counts.get("base"),
                    "classifications": list(source_set.get("allowedInFormats", {}).keys()),
                    "accent": accents[(int(source_set.get("number") or 1) - 1) % len(accents)],
                    "_source_language": language,
                }

        for card in payload["cards"]:
            card_key = str(card["id"])
            # baseId links a promotional/reprint card back to the same
            # language-independent gameplay identity. The concrete source card
            # id remains part of the printing/variant id and therefore still
            # distinguishes the physical release.
            identity_key = str(card.get("baseId") or card_key)
            canonical = english_names.get(identity_key, english_names.get(card_key, card))
            identity_id = f"lorcana-card-{slug(identity_key)}"
            identity_attrs = {
                "color": canonical.get("color"),
                "cost": canonical.get("cost"),
                "inkwell": canonical.get("inkwell"),
                "strength": canonical.get("strength"),
                "willpower": canonical.get("willpower"),
                "lore": canonical.get("lore"),
                "subtypes": canonical.get("subtypes", []),
                "allowedInFormats": canonical.get("allowedInFormats", {}),
                "source": "LorcanaJSON / Ravensburger",
                "sourceLanguage": language,
            }
            put_identity(catalog, {
                "id": identity_id,
                "game_id": "lorcana",
                "canonical_name": canonical.get("fullName") or canonical.get("name") or card_key,
                "rules_text": canonical.get("fullText") or "",
                "card_type": canonical.get("type") or "Unknown",
                "attributes": identity_attrs,
            }, prefer=language == "EN")

            promo_group = card.get("promoGrouping")
            physical_set_code = str(promo_group or card["setCode"])
            set_id = f"lorcana-{slug(physical_set_code)}"
            if promo_group:
                promo_name, promo_type = LORCANA_PROMO_GROUPS.get(
                    physical_set_code,
                    (f"Promo Group {physical_set_code}", "Promo Set"),
                )
                existing = catalog["sets"].get(set_id)
                if existing is None or language == "EN":
                    catalog["sets"][set_id] = {
                        "id": set_id,
                        "game_id": "lorcana",
                        "code": physical_set_code,
                        "name": promo_name,
                        "set_type": promo_type,
                        "release_date": None,
                        "printed_card_count": 0,
                        "classifications": ["Promo"],
                        "accent": accents[(len(catalog["sets"]) - 1) % len(accents)],
                        "_source_language": language,
                    }
            printing_id = f"lorcana-print-{slug(card_key)}-{language.lower()}"
            links = card.get("externalLinks", {})
            printing_attrs = {
                "localizedName": card.get("fullName") or card.get("name"),
                "localizedRulesText": card.get("fullText"),
                "fullIdentifier": card.get("fullIdentifier"),
                "artists": card.get("artists", []),
                "baseId": card.get("baseId"),
                "promoGrouping": promo_group,
                "promoSource": card.get("promoSource"),
                "promoSourceCategory": card.get("promoSourceCategory"),
                "externalLinks": links,
                "sourceUrl": catalog["sources"][f"lorcana_{language.lower()}"],
            }
            catalog["printings"][printing_id] = {
                "id": printing_id,
                "identity_id": identity_id,
                "game_id": "lorcana",
                "set_id": set_id,
                "collector_number": str(card.get("number", card_key)),
                "language": language,
                "rarity": card.get("rarity") or "Unknown",
                "attributes": printing_attrs,
            }
            foil_types = card.get("foilTypes") or ["None"]
            for foil in foil_types:
                code = "normal" if foil == "None" else slug(foil)
                image_url = (card.get("images") or {}).get("full")
                variant_id = f"{printing_id}-{code}"
                catalog["variants"][variant_id] = {
                    "id": variant_id,
                    "printing_id": printing_id,
                    "game_id": "lorcana",
                    "variant_code": code,
                    "finish": "Normal" if foil == "None" else foil,
                    "artwork_id": card_key,
                    "is_parallel": 0 if foil == "None" else 1,
                    "source_type": "lorcanajson",
                    "attributes": {
                        "imageUrl": image_url,
                        "imageSourceUrl": "https://lorcanajson.org/",
                        "catalogSourceUrl": catalog["sources"][f"lorcana_{language.lower()}"],
                        "priceUrl": links.get("cardmarketUrl") or links.get("tcgPlayerUrl") or links.get("cardTraderUrl"),
                        "priceSource": "Cardmarket" if links.get("cardmarketUrl") else "TCGplayer" if links.get("tcgPlayerUrl") else None,
                    },
                }
            # The first German printing of TFC 21 physically exists with one
            # lore instead of two.  Ravensburger corrected later print runs,
            # while Cardmarket keeps the error card as its own V3 product.
            # Both non-foil and foil error cards are collectible variants.
            if language == "DE" and card_key == "21" and physical_set_code == "1" and not promo_group:
                for code, finish, parallel in (
                    ("normal-misprint-1-lore", "Normal", 0),
                    ("silver-misprint-1-lore", "Silver", 1),
                ):
                    variant_id = f"{printing_id}-{code}"
                    catalog["variants"][variant_id] = {
                        "id": variant_id,
                        "printing_id": printing_id,
                        "game_id": "lorcana",
                        "variant_code": code,
                        "finish": finish,
                        "artwork_id": f"{card_key}-misprint-1-lore",
                        "is_parallel": parallel,
                        "source_type": "physical-errata-printing",
                        "attributes": {
                            "imageUrl": LORCANA_TFC_21_MISPRINT["image_url"],
                            "imageSource": "Patagium Games – Foto der physischen Fehldruckkarte",
                            "imageSourceUrl": LORCANA_TFC_21_MISPRINT["source_url"],
                            "catalogSourceUrl": catalog["sources"]["lorcana_de"],
                            "priceUrl": LORCANA_TFC_21_MISPRINT["price_url"],
                            "priceSource": "Cardmarket",
                            "editionLabel": "Fehldruck · 1 Legendenpunkt",
                            "isMisprint": True,
                            "printedLore": 1,
                            "correctedLore": 2,
                            "errata": "Der erste deutsche Druck zeigt 1 statt 2 Legendenpunkte.",
                        },
                    }
    # Count physical EN printings per set. This prevents the two imported
    # languages from inflating the number shown in the set browser.
    for record in catalog["sets"].values():
        record["printed_card_count"] = sum(
            1 for printing in catalog["printings"].values()
            if printing["set_id"] == record["id"] and printing["language"] == "EN"
        )
    return catalog


def parse_one_piece_options(page: str) -> list[tuple[str, str, str, str]]:
    result = []
    for series, label in re.findall(r'<option[^>]+value="(\d+)"[^>]*>(.*?)</option>', page, re.I | re.S):
        name = clean(label)
        code_match = re.search(r"[\[【]([^\]】]+)[\]】]\s*$", name)
        code = code_match.group(1).upper().replace(" ", "") if code_match else "PROMO" if "promot" in name.lower() or "\u30d7\u30ed\u30e2" in name else f"SERIES-{series[-3:]}"
        kind = "Starter Deck" if re.search(r"starter|\u30b9\u30bf\u30fc\u30bf\u30fc", name, re.I) else "Booster Set" if re.search(r"booster|\u30d6\u30fc\u30b9\u30bf\u30fc", name, re.I) else "Product Cards"
        result.append((series, code, name, kind))
    return result


def one_piece_field(block: str, class_name: str) -> str:
    match = re.search(rf'<div class="[^"]*\b{re.escape(class_name)}\b[^"]*">(.*?)</div>', block, re.I | re.S)
    if not match:
        return ""
    body = re.sub(r"<h3>.*?</h3>", "", match.group(1), flags=re.I | re.S)
    return clean(body)


def parse_one_piece_page(page: str, language: str, base: str, set_record: dict) -> dict:
    catalog = empty_catalog()
    catalog["sets"][set_record["id"]] = set_record
    blocks = re.findall(r'<dl class="modalCol" id="([^"]+)">(.*?)</dl>', page, re.I | re.S)
    for modal_id, block in blocks:
        info = re.search(r'<div class="infoCol">(.*?)</div>', block, re.I | re.S)
        values = [clean(v) for v in re.findall(r"<span>(.*?)</span>", info.group(1), re.I | re.S)] if info else []
        if len(values) < 3:
            continue
        number, rarity, card_type = values[:3]
        name_match = re.search(r'<div class="cardName">(.*?)</div>', block, re.I | re.S)
        image_match = re.search(r'data-src="([^"?]+)', block, re.I)
        if not name_match or not image_match:
            continue
        name = clean(name_match.group(1))
        image_url = urljoin(base + "/cardlist/", image_match.group(1))
        identity_id = f"one-piece-card-{slug(number)}"
        attrs = {
            "color": one_piece_field(block, "color"),
            "cost": one_piece_field(block, "cost"),
            "power": one_piece_field(block, "power"),
            "counter": one_piece_field(block, "counter"),
            "attribute": one_piece_field(block, "attribute"),
            "traits": one_piece_field(block, "feature"),
            "source": "Official One Piece Card Game catalogue",
            "sourceLanguage": language,
        }
        rules = one_piece_field(block, "text")
        put_identity(catalog, {
            "id": identity_id,
            "game_id": "one-piece",
            "canonical_name": name,
            "rules_text": rules,
            "card_type": card_type.title(),
            "attributes": attrs,
        }, prefer=language == "EN")
        printing_id = f"one-piece-print-{slug(set_record['code'])}-{slug(number)}-{language.lower()}"
        catalog["printings"].setdefault(printing_id, {
            "id": printing_id,
            "identity_id": identity_id,
            "game_id": "one-piece",
            "set_id": set_record["id"],
            "collector_number": number,
            "language": language,
            "rarity": rarity,
            "attributes": {"localizedName": name, "localizedRulesText": rules, "sourceUrl": set_record["source_url"]},
        })
        suffix = modal_id[len(number):].strip("_") or "standard"
        finish = "Normal" if suffix == "standard" else f"Parallel {suffix}"
        variant_id = f"{printing_id}-{slug(suffix)}"
        catalog["variants"][variant_id] = {
            "id": variant_id,
            "printing_id": printing_id,
            "game_id": "one-piece",
            "variant_code": suffix,
            "finish": finish,
            "artwork_id": modal_id,
            "is_parallel": int(suffix != "standard"),
            "source_type": "official-one-piece",
            "attributes": {
                "imageUrl": image_url,
                "imageSourceUrl": set_record["source_url"],
                "catalogSourceUrl": set_record["source_url"],
            },
        }
    return catalog


def normalize_one_piece_product_code(code: str) -> str:
    """Normalize source product labels without turning DON!! into numbered cards."""
    code = re.sub(r"\s+", "", code.upper())
    if code == "OP-PR":
        return "PROMO"
    match = re.fullmatch(r"(OP|ST|EB|PRB)-?(\d{1,2})", code)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    return code


def canonicalize_one_piece_combined_releases(catalog: dict) -> None:
    """Assign Western combined-product cards to their canonical card sets.

    EB04-numbered cards stay in EB-04.  Every other inclusion, including
    alternate-art reprints and unnumbered DON!! cards, belongs to the primary
    OP set represented by that physical release.  Printing and variant IDs are
    deliberately unchanged so all user-owned references remain stable.
    """
    combined_ids = {
        f"one-piece-{slug(product_code)}": (product_code, release)
        for product_code, release in ONE_PIECE_COMBINED_RELEASES.items()
    }
    for printing in catalog["printings"].values():
        if printing["game_id"] != "one-piece" or printing["language"] != "EN":
            continue
        combined = combined_ids.get(printing["set_id"])
        if not combined:
            continue
        product_code, release = combined
        product_set = catalog["sets"][printing["set_id"]]
        canonical_code = "EB-04" if re.match(r"^EB04-", printing["collector_number"], re.I) else release["primary_set"]
        canonical_id = f"one-piece-{slug(canonical_code)}"
        if canonical_id not in catalog["sets"]:
            raise RuntimeError(f"Kanonisches One-Piece-Set fehlt: {canonical_code}")
        printing["set_id"] = canonical_id
        printing["attributes"].update({
            "canonicalSetCode": canonical_code,
            "releaseProductCode": product_code,
            "releaseProductName": product_set["name"],
            "releaseProductReleaseDate": release["release_date"],
            "releaseProductSourceUrl": printing["attributes"].get("sourceUrl"),
        })

    for combined_id in combined_ids:
        catalog["sets"].pop(combined_id, None)

    for code, override in ONE_PIECE_CANONICAL_SETS.items():
        set_id = f"one-piece-{slug(code)}"
        record = catalog["sets"].get(set_id)
        if not record:
            raise RuntimeError(f"Kanonisches One-Piece-Set fehlt: {code}")
        record.update(override)
        record["classifications"] = [override["set_type"]]


def one_piece_title_case(value: str) -> str:
    """Convert provider all-caps English titles to readable display casing."""
    if not re.search(r"[A-Za-z]", value):
        return value
    small_words = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "vs"}
    words = value.split()
    result = []
    for index, word in enumerate(words):
        prefix = re.match(r"^[^A-Za-z0-9]*", word).group(0)
        suffix = re.search(r"[^A-Za-z0-9.]*$", word).group(0)
        core = word[len(prefix):len(word) - len(suffix) if suffix else None]
        lowered = core.lower()
        if lowered in small_words and index not in {0, len(words) - 1}:
            formatted = lowered
        else:
            parts = lowered.split("'")
            formatted = "'".join(
                part if part == "s" and part_index else part[:1].upper() + part[1:]
                for part_index, part in enumerate(parts)
            )
        result.append(f"{prefix}{formatted}{suffix}")
    title = " ".join(result)
    title = re.sub(r"\bVol\.\s*(\d+)\b", r"Vol. \1", title, flags=re.I)
    # This is part of the official PRB product title, not an article joining
    # ordinary title words.
    return title.replace("One Piece Card the Best", "One Piece Card The Best")


def normalize_one_piece_set_names(catalog: dict) -> None:
    """Keep product categories and codes out of One Piece display titles."""
    for record in catalog["sets"].values():
        if record["game_id"] != "one-piece":
            continue
        name = re.sub(r"\s*[\[【][^\]】]+[\]】]\s*$", "", record["name"]).strip()
        booster = bool(re.match(r"^(?:OP|EB|PRB)-\d+$", record["code"]))
        prefix = (
            r"^(?:BOOSTER\s+PACK|EXTRA\s+BOOSTER|PREMIUM\s+BOOSTER|"
            r"ブースターパック|エクストラブースター|プレミアムブースター)\s*"
            if booster else
            r"^(?:STARTER\s+DECK(?:\s+EX)?|ULTRA\s+DECK|START\s+DECK|"
            r"スタートデッキ|ライブスタートデッキ)\s*"
        )
        name = re.sub(prefix, "", name, flags=re.I).strip(" -–—")
        if name:
            record["name"] = one_piece_title_case(name) if booster else name
    display_overrides = {
        "PROMO": "Promotion Cards",
        "SERIES-801": "Other Product Cards",
    }
    for code, name in display_overrides.items():
        record = catalog["sets"].get(f"one-piece-{slug(code)}")
        if record:
            record["name"] = name


def one_piece_english_release_dates() -> dict[str, list[str]]:
    """Read Western EN product dates from Bandai instead of inferring them."""
    first_page = fetch(ONE_PIECE_EN_PRODUCTS)
    maximum = re.search(r'<span class="pageMax">\s*(\d+)\s*</span>', first_page, re.I)
    page_count = int(maximum.group(1)) if maximum else 1
    pages = [first_page]
    if page_count > 1:
        with ThreadPoolExecutor(max_workers=6) as pool:
            pages.extend(pool.map(fetch, (f"{ONE_PIECE_EN_PRODUCTS}?page={page}" for page in range(2, page_count + 1))))

    releases: dict[str, set[str]] = {}
    for page in pages:
        for block in re.findall(r'<li class="linkListColBox"[^>]*>(.*?)</li>', page, re.I | re.S):
            title_match = re.search(r'<h4 class="linkListColTitle">(.*?)</h4>', block, re.I | re.S)
            date_match = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})"', block, re.I)
            if not title_match or not date_match:
                continue
            title = clean(title_match.group(1))
            code_match = re.search(r"[\[【]([^\]】]+)[\]】]\s*$", title)
            if not code_match:
                continue
            source_code = code_match.group(1).upper().replace(" ", "")
            target_codes = [normalize_one_piece_product_code(source_code)]
            combined = ONE_PIECE_COMBINED_RELEASES.get(source_code)
            if combined:
                target_codes = [combined["primary_set"], "EB-04"]
            for code in target_codes:
                releases.setdefault(code, set()).add(date_match.group(1))
    return {code: sorted(dates) for code, dates in releases.items()}


def apply_one_piece_release_dates(catalog: dict, releases: dict[str, list[str]]) -> None:
    """Use latest Western availability for sorting and retain split dates."""
    for code, dates in releases.items():
        record = catalog["sets"].get(f"one-piece-{slug(code)}")
        if record and dates:
            record["release_date"] = dates[-1]
            record["_release_dates"] = dates


def one_piece_don_set_id(code: str | None, language: str, available_sets: dict) -> str:
    """Resolve a source-owned DON!! product code to its physical catalogue set."""
    fallback = "one-piece-don"
    if not code:
        return fallback
    normalized = normalize_one_piece_product_code(code)
    candidate = f"one-piece-{slug(normalized)}"
    return candidate if candidate in available_sets else fallback


def one_piece_don_source_code(name: str) -> str | None:
    match = re.search(r"\(((?:OP|ST|EB|PRB)-?\d{1,2}|OP-PR|OPDD)\)\s*$", name, re.I)
    if match:
        return match.group(1).upper()
    # The provider currently omits the code from this otherwise explicit title.
    if re.search(r"-\s*The Time of Battle\s*$", name, re.I):
        return "OP-16"
    return None


def decode_one_piece_don_pdf_code(encoded: str) -> str | None:
    """Decode product codes from Bandai's embedded subset font."""
    prefixes = (("13#", "PRB"), ("01", "OP"), ("45", "ST"), ("&#", "EB"))
    prefix = next(((raw, decoded) for raw, decoded in prefixes if encoded.startswith(raw)), None)
    if not prefix:
        return None
    raw_prefix, decoded_prefix = prefix
    suffix = []
    for character in encoded[len(raw_prefix):]:
        value = ord(character)
        if value == 14:
            suffix.append("-")
        elif 17 <= value <= 26:
            suffix.append(str(value - 17))
        elif character.isdigit():
            suffix.append(character)
    code = decoded_prefix + "".join(suffix)
    return code if re.fullmatch(r"(?:OP|ST|EB|PRB)-?\d{1,2}", code) else None


def one_piece_don_pdf_set_codes(page) -> dict[int, list[str]]:
    """Map PDF image-object numbers to the product code printed below them."""
    images: list[tuple[int, float, float]] = []
    labels: list[tuple[str, float, float]] = []

    def visit_operand(operator, operands, current_matrix, _text_matrix):
        if operator != b"Do" or not operands:
            return
        match = re.fullmatch(r"/Im(\d+)", str(operands[0]))
        if match and match.group(1) != "1":
            images.append((int(match.group(1)), float(current_matrix[4]), float(current_matrix[5])))

    def visit_text(text, _current_matrix, text_matrix, _font, _size):
        for encoded in re.findall(r"ʲ([^ʳ]+)ʳ", text):
            code = decode_one_piece_don_pdf_code(encoded)
            if code:
                labels.append((code, float(text_matrix[4]), float(text_matrix[5])))

    page.extract_text(visitor_operand_before=visit_operand, visitor_text=visit_text)
    result: dict[int, list[str]] = {}
    for code, label_x, label_y in labels:
        candidates = [image for image in images if abs(image[2] - label_y) <= 30]
        if not candidates:
            continue
        sequence, _, _ = min(candidates, key=lambda image: abs(image[1] - label_x))
        if code not in result.setdefault(sequence, []):
            result[sequence].append(code)
    return result


def import_one_piece_don(available_sets: dict) -> dict:
    """Import collectible DON!! artwork omitted by Bandai's card-list feed.

    Bandai documents DON!! cards and product contents, but its public card-list
    endpoint does not enumerate the collectible artwork. OPTCG API provides a
    dedicated, stable DON endpoint with names and actual card images.
    """
    rows = json.loads(fetch(ONE_PIECE_DON_API))
    if not isinstance(rows, list):
        raise RuntimeError("OPTCG API: DON-Antwort ist keine Liste")
    catalog = empty_catalog()
    fallback_set_id = "one-piece-don"
    catalog["sets"][fallback_set_id] = {
        "id": fallback_set_id,
        "game_id": "one-piece",
        "code": "DON!!",
        "name": "DON!! Cards",
        "set_type": "DON!! Cards",
        "release_date": None,
        "printed_card_count": None,
        "classifications": ["DON!!"],
        "accent": "#dc2626",
        "_source_language": "EN",
    }
    for row in rows:
        image_url = row.get("card_image")
        image_id = str(row.get("card_image_id") or "").strip().lower()
        if not image_url or not re.fullmatch(r"don_\d+", image_id):
            # Do not fabricate artwork when the provider has not published it.
            continue
        sequence = int(image_id.split("_", 1)[1])
        display_name = clean(row.get("optcg_don_name") or row.get("card_name") or "DON!! Card")
        product_code = one_piece_don_source_code(display_name)
        set_id = one_piece_don_set_id(product_code, "EN", available_sets)
        identity_id = f"one-piece-card-{image_id.replace('_', '-')}"
        source_attrs = {
            "color": None,
            "source": "OPTCG API DON catalogue",
            "sourceLanguage": "EN",
            "providerId": image_id,
            "providerName": display_name,
            "providerUrl": ONE_PIECE_DON_API,
        }
        put_identity(catalog, {
            "id": identity_id,
            "game_id": "one-piece",
            "canonical_name": display_name,
            "rules_text": clean(row.get("card_text") or "Your Turn +1000"),
            "card_type": "DON!!",
            "attributes": source_attrs,
        }, prefer=True)
        printing_id = f"one-piece-print-don-{sequence:03d}-en"
        catalog["printings"][printing_id] = {
            "id": printing_id,
            "identity_id": identity_id,
            "game_id": "one-piece",
            "set_id": set_id,
            "collector_number": "",
            "language": "EN",
            "rarity": "DON!!",
            "attributes": {
                "localizedName": display_name,
                "localizedRulesText": clean(row.get("card_text") or "Your Turn +1000"),
                "sourceUrl": ONE_PIECE_DON_API,
                "productCode": product_code,
                "releaseProductCode": (
                    f"{normalize_one_piece_product_code(product_code).replace('-', '')}-EB04"
                    if product_code and normalize_one_piece_product_code(product_code) in {"OP-14", "OP-15"}
                    else None
                ),
                "catalogueKey": f"DON-{sequence:03d}",
                "unprintedCollectorNumber": True,
            },
        }
        finish = "Gold" if re.search(r"\bgold\b", display_name, re.I) else "Normal"
        variant_id = f"{printing_id}-standard"
        catalog["variants"][variant_id] = {
            "id": variant_id,
            "printing_id": printing_id,
            "game_id": "one-piece",
            "variant_code": "standard",
            "finish": finish,
            "artwork_id": image_id,
            "is_parallel": int(finish != "Normal"),
            "source_type": "optcgapi-don",
            "attributes": {
                "imageUrl": image_url,
                "imageSource": "OPTCG API DON catalogue",
                "imageSourceUrl": ONE_PIECE_DON_DOCS,
                "catalogSourceUrl": ONE_PIECE_DON_API,
                "providerId": image_id,
            },
        }
    import_one_piece_don_jp(catalog, available_sets)
    catalog["sets"][fallback_set_id]["printed_card_count"] = sum(
        1 for printing in catalog["printings"].values() if printing["set_id"] == fallback_set_id
    )
    catalog["sources"]["one_piece_don"] = ONE_PIECE_DON_API
    catalog["sources"]["one_piece_don_jp"] = ONE_PIECE_DON_JP_PDF
    if len(catalog["variants"]) < 400:
        raise RuntimeError(f"OPTCG API: DON-Katalog unvollständig ({len(catalog['variants'])} Varianten)")
    return catalog


def import_one_piece_don_jp(catalog: dict, available_sets: dict) -> None:
    """Extract Bandai's official Japanese/Asian DON!! artwork catalogue.

    DON!! cards do not carry normal collector numbers. The PDF image-object
    number remains an internal catalogue key and is never shown as one.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(fetch_bytes(ONE_PIECE_DON_JP_PDF)))
    CARD_IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    imported = 0
    for page in reader.pages:
        product_codes = one_piece_don_pdf_set_codes(page)
        for embedded in page.images:
            name_match = re.fullmatch(r"Im(\d+)\.[A-Za-z0-9]+", embedded.name)
            if not name_match or embedded.name == "Im1.jpg":
                continue
            sequence = int(name_match.group(1))
            product_code = next(iter(product_codes.get(sequence, [])), None)
            set_id = one_piece_don_set_id(product_code, "JP", available_sets)
            identity_id = f"one-piece-card-don-jp-{sequence:03d}"
            printing_id = f"one-piece-print-don-jp-{sequence:03d}"
            variant_id = f"{printing_id}-standard"
            display_name = "DON!! Card (Japanese)"
            put_identity(catalog, {
                "id": identity_id,
                "game_id": "one-piece",
                "canonical_name": display_name,
                "rules_text": "自分のターン +1000",
                "card_type": "DON!!",
                "attributes": {
                    "color": None,
                    "source": "Official Bandai DON!! Card List",
                    "sourceLanguage": "JP",
                    "providerId": f"pdf-image-{sequence}",
                    "providerUrl": ONE_PIECE_DON_JP_PDF,
                    "catalogueKeyOnly": True,
                },
            })
            catalog["printings"][printing_id] = {
                "id": printing_id,
                "identity_id": identity_id,
                "game_id": "one-piece",
                "set_id": set_id,
                "collector_number": "",
                "language": "JP",
                "rarity": "DON!!",
                "attributes": {
                    "localizedName": display_name,
                    "localizedRulesText": "自分のターン +1000",
                    "sourceUrl": ONE_PIECE_DON_JP_PDF,
                    "catalogueKeyOnly": True,
                    "productCode": product_code,
                    "catalogueKey": f"DON-JP-{sequence:03d}",
                    "unprintedCollectorNumber": True,
                },
            }
            catalog["variants"][variant_id] = {
                "id": variant_id,
                "printing_id": printing_id,
                "game_id": "one-piece",
                "variant_code": "standard",
                "finish": "Normal",
                "artwork_id": f"jp-don-{sequence:03d}",
                "is_parallel": 0,
                "source_type": "official-one-piece-don-jp",
                "attributes": {
                    "imageSource": "Official Bandai DON!! Card List",
                    "imageSourceUrl": ONE_PIECE_DON_JP_PDF,
                    "catalogSourceUrl": ONE_PIECE_DON_JP_PDF,
                    "providerId": f"pdf-image-{sequence}",
                },
            }
            image = embedded.image.convert("RGB")
            payload = io.BytesIO()
            image.save(payload, format="JPEG", quality=92, optimize=True)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", variant_id)
            (CARD_IMAGE_CACHE / f"{safe_id}.img").write_bytes(payload.getvalue())
            (CARD_IMAGE_CACHE / f"{safe_id}.mime").write_text("image/jpeg")
            imported += 1
    if imported < 200:
        raise RuntimeError(f"Offizielle JP-DON-Liste unvollständig ({imported} Bilder)")


def import_one_piece() -> dict:
    catalog = empty_catalog()
    jobs = []
    english_releases = one_piece_english_release_dates()
    catalog["sources"]["one_piece_en_products"] = ONE_PIECE_EN_PRODUCTS
    for language, base in (("EN", "https://en.onepiece-cardgame.com"), ("JP", "https://www.onepiece-cardgame.com")):
        index_url = f"{base}/cardlist/"
        index = fetch(index_url)
        catalog["sources"][f"one_piece_{language.lower()}"] = index_url
        for series, code, name, kind in parse_one_piece_options(index):
            set_id = f"one-piece-{slug(code)}"
            source_url = f"{index_url}?series={series}"
            record = {
                "id": set_id,
                "game_id": "one-piece",
                "code": code,
                "name": name,
                "set_type": kind,
                "release_date": None,
                "printed_card_count": None,
                "classifications": [kind],
                "accent": "#dc2626",
                "source_url": source_url,
                "_source_language": language,
            }
            jobs.append((language, base, source_url, record))

    print(f"One Piece: {len(jobs)} offizielle Produktlisten werden geladen …", flush=True)
    parts = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, url): (language, base, record) for language, base, url, record in jobs}
        for future in as_completed(futures):
            language, base, record = futures[future]
            parts.append(parse_one_piece_page(future.result(), language, base, record))
    for part in parts:
        merge(catalog, part)
    canonicalize_one_piece_combined_releases(catalog)
    merge(catalog, import_one_piece_don(catalog["sets"]))
    normalize_one_piece_set_names(catalog)
    apply_one_piece_release_dates(catalog, english_releases)
    for record in catalog["sets"].values():
        record.pop("source_url", None)
        record["printed_card_count"] = len({p["identity_id"] for p in catalog["printings"].values() if p["set_id"] == record["id"]})
    return catalog


def parse_holo_products(page: str, language: str, base: str) -> list[dict]:
    products = []
    pattern = r'<a class="anchor" href="(/cardlist/cardsearch/\?expansion=([^"&]+))">(.*?)</a>'
    for href, expansion, body in re.findall(pattern, page, re.I | re.S):
        name_match = re.search(r'<div class="name[^>]*>(.*?)</div>', body, re.I | re.S)
        type_match = re.search(r'<div class="cat[^>]*>(.*?)</div>', body, re.I | re.S)
        date_match = re.search(r'<dd class="detail bold">(.*?)</dd>', body, re.I | re.S)
        name = clean(name_match.group(1)) if name_match else expansion
        release = clean(date_match.group(1)) if date_match else ""
        if language == "JP":
            jp_date = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", release)
            release = f"{jp_date.group(1)}-{int(jp_date.group(2)):02d}-{int(jp_date.group(3)):02d}" if jp_date else None
        else:
            try:
                release = datetime.strptime(release, "%B %d, %Y").date().isoformat()
            except ValueError:
                release = None
        products.append({
            "id": f"hololive-{slug(expansion)}",
            "game_id": "hololive",
            "code": expansion,
            "name": name,
            "set_type": clean(type_match.group(1)).title() if type_match else "Product",
            "release_date": release,
            "printed_card_count": None,
            "classifications": [clean(type_match.group(1))] if type_match else [],
            "accent": "#0891b2",
            "source_url": urljoin(base, href),
            "language": language,
            "base": base,
            "_source_language": language,
            # ``sele…`` is an official-site filter for a tournament card pool,
            # not a physical product. Its cards still belong to the product
            # encoded in their printed collector number (hBP…, hSD…, …).
            "_virtual_pool": expansion.lower().startswith("sele"),
        })
    return products


def holo_info(block: str) -> dict[str, str]:
    result = {}
    for key, value in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", block, re.I | re.S):
        result[clean(key)] = clean(value)
    return result


def parse_holo_page(page: str, product: dict) -> dict:
    catalog = empty_catalog()
    if not product.get("_virtual_pool"):
        set_record = {k: v for k, v in product.items() if k not in ("source_url", "language", "base", "_virtual_pool")}
        catalog["sets"][product["id"]] = set_record
    for block in re.findall(r"<li[^>]*>\s*<a[^>]*>(.*?)</a>\s*</li>", page, re.I | re.S):
        image = re.search(r'<img[^>]+src="([^"]+)"[^>]+alt="([^"]*)"', block, re.I | re.S)
        number_match = re.search(r'<p class="number">(.*?)</p>', block, re.I | re.S)
        name_match = re.search(r'<p class="name">(.*?)</p>', block, re.I | re.S)
        if not image or not number_match or not name_match:
            continue
        image_url = urljoin(product["base"], image.group(1))
        number = clean(number_match.group(1))
        name = clean(name_match.group(1))
        if not number or number.lower() == "null":
            # The Selection Cup page contains a non-card rules explainer.
            continue
        fields = holo_info(block)
        card_type = fields.get("Card Type") or fields.get("カードタイプ") or fields.get("カード種類") or "Unknown"
        rarity = fields.get("Rarity") or fields.get("レアリティ") or PurePosixPath(image_url).stem.rsplit("_", 1)[-1]
        identity_id = f"hololive-card-{slug(number)}"
        rules_parts = []
        for rule in re.findall(r'<div class="(?:keyword|oshi skill|sp skill|sp arts|extra)[^"]*">(.*?)</div>', block, re.I | re.S):
            value = clean(rule)
            if value:
                rules_parts.append(value)
        attrs = {
            "color": fields.get("Color") or fields.get("色"),
            "tags": fields.get("Tag") or fields.get("タグ"),
            "hp": fields.get("HP"),
            "bloomLevel": fields.get("Bloom Level") or fields.get("Bloomレベル") or fields.get("ブルームレベル"),
            "source": "Official hololive OCG catalogue",
            "sourceLanguage": product["language"],
        }
        put_identity(catalog, {
            "id": identity_id,
            "game_id": "hololive",
            "canonical_name": name,
            "rules_text": "\n".join(rules_parts),
            "card_type": card_type,
            "attributes": attrs,
        }, prefer=product["language"] == "EN")
        physical_code = number.split("-", 1)[0] if product.get("_virtual_pool") and "-" in number else product["code"]
        physical_set_id = f"hololive-{slug(physical_code)}"
        printing_id = f"hololive-print-{slug(physical_code)}-{slug(number)}-{product['language'].lower()}"
        catalog["printings"].setdefault(printing_id, {
            "id": printing_id,
            "identity_id": identity_id,
            "game_id": "hololive",
            "set_id": physical_set_id,
            "collector_number": number,
            "language": product["language"],
            "rarity": rarity,
            "attributes": {
                "localizedName": name,
                "localizedRulesText": "\n".join(rules_parts),
                "sourceUrl": product["source_url"],
                "virtualPoolSource": bool(product.get("_virtual_pool")),
            },
        })
        variant_code = f"{slug(rarity)}-{digest(image_url)}"
        variant_id = f"{printing_id}-{variant_code}"
        catalog["variants"][variant_id] = {
            "id": variant_id,
            "printing_id": printing_id,
            "game_id": "hololive",
            "variant_code": variant_code,
            "finish": rarity,
            "artwork_id": PurePosixPath(image_url).stem,
            "is_parallel": int(rarity.upper() not in {"C", "U", "R", "RR", "S", "OSR"}),
            "source_type": "official-hololive",
            "attributes": {"imageUrl": image_url, "imageSourceUrl": product["source_url"], "catalogSourceUrl": product["source_url"]},
        }
    return catalog


def fetch_holo_product(product: dict) -> dict:
    combined = empty_catalog()
    first = fetch(product["source_url"] + "&view=text")
    merge(combined, parse_holo_page(first, product))
    for page_number in range(2, 80):
        url = f"{product['base']}/cardlist/cardsearch_ex?expansion={product['code']}&view=text&page={page_number}"
        try:
            fragment = fetch(url)
        except RuntimeError as error:
            # Single-page products legitimately have no AJAX page 2.
            if "404" in str(error):
                break
            raise
        part = parse_holo_page(fragment, product)
        if not part["variants"]:
            break
        previous = len(combined["variants"])
        merge(combined, part)
        if len(combined["variants"]) == previous:
            break
    return combined


def import_hololive() -> dict:
    catalog = empty_catalog()
    products = []
    for language, base in (("EN", "https://en.hololive-official-cardgame.com"), ("JP", "https://hololive-official-cardgame.com")):
        index_url = f"{base}/cardlist/"
        index = fetch(index_url)
        products.extend(parse_holo_products(index, language, base))
        catalog["sources"][f"hololive_{language.lower()}"] = index_url
    # The product list can contain duplicate tiles. One expansion/language is enough.
    products = list({(p["language"], p["code"]): p for p in products}.values())
    print(f"hololive: {len(products)} offizielle Produktlisten werden geladen …", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_holo_product, product) for product in products]
        for future in as_completed(futures):
            merge(catalog, future.result())
    for record in catalog["sets"].values():
        record["printed_card_count"] = len({p["identity_id"] for p in catalog["printings"].values() if p["set_id"] == record["id"]})
    return catalog


def merge(target: dict, source: dict) -> None:
    for key, incoming in source["sets"].items():
        existing = target["sets"].get(key)
        if existing is None or incoming.get("_source_language") == "EN" or existing.get("_source_language") != "EN":
            target["sets"][key] = incoming
    for key, incoming in source["identities"].items():
        existing = target["identities"].get(key)
        incoming_language = incoming.get("attributes", {}).get("sourceLanguage")
        existing_language = (existing or {}).get("attributes", {}).get("sourceLanguage")
        if existing is None or incoming_language == "EN" or existing_language != "EN":
            target["identities"][key] = incoming
    for key, incoming in source["printings"].items():
        existing = target["printings"].get(key)
        incoming_virtual = incoming.get("attributes", {}).get("virtualPoolSource", False)
        existing_virtual = (existing or {}).get("attributes", {}).get("virtualPoolSource", False)
        if existing is None or (existing_virtual and not incoming_virtual):
            target["printings"][key] = incoming
    for section in ("variants", "sources"):
        target[section].update(source[section])


def validate(catalog: dict) -> None:
    counts = {}
    for game_id in ("lorcana", "one-piece", "hololive"):
        counts[game_id] = {
            "sets": sum(1 for x in catalog["sets"].values() if x["game_id"] == game_id),
            "cards": sum(1 for x in catalog["identities"].values() if x["game_id"] == game_id),
            "printings": sum(1 for x in catalog["printings"].values() if x["game_id"] == game_id),
            "variants": sum(1 for x in catalog["variants"].values() if x["game_id"] == game_id),
        }
    print(json.dumps(counts, indent=2), flush=True)
    minimums = {"lorcana": (15, 2500), "one-piece": (20, 1000), "hololive": (10, 500)}
    for game_id, (minimum_sets, minimum_cards) in minimums.items():
        if counts[game_id]["sets"] < minimum_sets or counts[game_id]["cards"] < minimum_cards:
            raise RuntimeError(f"Validierung fehlgeschlagen: {game_id} ist unvollständig ({counts[game_id]})")
    dangling = [v["id"] for v in catalog["variants"].values() if v["printing_id"] not in catalog["printings"]]
    if dangling:
        raise RuntimeError(f"Validierung fehlgeschlagen: {len(dangling)} Varianten ohne Printing")
    dangling_sets = [p["id"] for p in catalog["printings"].values() if p["set_id"] not in catalog["sets"]]
    if dangling_sets:
        raise RuntimeError(f"Validierung fehlgeschlagen: {len(dangling_sets)} Printings ohne physisches Set")


def replace_variant_ids(value, mapping: dict[str, str]):
    """Replace variant ids in stored import-operation JSON recursively."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_variant_ids(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: replace_variant_ids(item, mapping) for key, item in value.items()}
    return value


def migrate_hololive_virtual_pools(connection: sqlite3.Connection) -> dict[str, int]:
    """Move user references from former ``sele…`` pseudo sets to physical cards.

    Target selection prefers the same artwork and finish. The collector-number
    prefix determines the physical product, e.g. hBP01-024 -> hBP01. The whole
    catalogue write is rolled back if a referenced variant cannot be mapped
    unambiguously, so collection data is never silently discarded.
    """
    sources = connection.execute(
        """SELECT v.id,p.collector_number,p.language,v.finish,v.artwork_id
           FROM variants v JOIN printings p ON p.id=v.printing_id
           JOIN sets s ON s.id=p.set_id
           WHERE v.game_id='hololive' AND lower(s.code) LIKE 'sele%'"""
    ).fetchall()
    mapping: dict[str, str] = {}
    unresolved: set[str] = set()
    for source_id, number, language, finish, artwork_id in sources:
        physical_code = number.split("-", 1)[0] if number and "-" in number else ""
        targets = connection.execute(
            """SELECT v.id,v.finish,v.artwork_id
               FROM variants v JOIN printings p ON p.id=v.printing_id
               JOIN sets s ON s.id=p.set_id
               WHERE v.game_id='hololive' AND lower(s.code)=lower(?)
                 AND p.collector_number=? AND p.language=?
               ORDER BY v.id""",
            (physical_code, number, language),
        ).fetchall()
        ranked = [row for row in targets if row[1] == finish and row[2] == artwork_id]
        if not ranked:
            ranked = [row for row in targets if row[2] == artwork_id]
        if not ranked:
            ranked = [row for row in targets if row[1] == finish]
        if len(ranked) == 1:
            mapping[source_id] = ranked[0][0]
        else:
            unresolved.add(source_id)

    reference_tables = (
        "collection_entries", "watchlist_entries", "named_watchlist_entries",
        "deck_cards", "price_observations",
    )
    referenced = set()
    stats = {table: 0 for table in reference_tables}
    for table in reference_tables:
        rows = connection.execute(
            f"""SELECT e.variant_id,COUNT(*) FROM {table} e
                JOIN variants v ON v.id=e.variant_id
                JOIN printings p ON p.id=v.printing_id
                JOIN sets s ON s.id=p.set_id
                WHERE v.game_id='hololive' AND lower(s.code) LIKE 'sele%'
                GROUP BY e.variant_id"""
        ).fetchall()
        stats[table] = sum(row[1] for row in rows)
        referenced.update(row[0] for row in rows)
    blocked = sorted(referenced & unresolved)
    if blocked:
        raise RuntimeError(f"Migration abgebrochen: {len(blocked)} verwendete sele-Varianten sind nicht eindeutig zuordenbar")

    for source_id, target_id in mapping.items():
        connection.execute(
            """INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes)
               SELECT user_id,?,condition,quantity,notes FROM collection_entries WHERE variant_id=?
               ON CONFLICT(user_id,variant_id,condition) DO UPDATE SET
                 quantity=collection_entries.quantity+excluded.quantity,
                 notes=COALESCE(collection_entries.notes,excluded.notes)""",
            (target_id, source_id),
        )
        connection.execute("DELETE FROM collection_entries WHERE variant_id=?", (source_id,))
        for table, owner_column in (("watchlist_entries", "user_id"), ("named_watchlist_entries", "list_id")):
            connection.execute(
                f"INSERT OR IGNORE INTO {table}({owner_column},variant_id,created_at) SELECT {owner_column},?,created_at FROM {table} WHERE variant_id=?",
                (target_id, source_id),
            )
            connection.execute(f"DELETE FROM {table} WHERE variant_id=?", (source_id,))
        connection.execute(
            """INSERT INTO deck_cards(deck_id,variant_id,zone,quantity)
               SELECT deck_id,?,zone,quantity FROM deck_cards WHERE variant_id=?
               ON CONFLICT(deck_id,variant_id,zone) DO UPDATE SET quantity=deck_cards.quantity+excluded.quantity""",
            (target_id, source_id),
        )
        connection.execute("DELETE FROM deck_cards WHERE variant_id=?", (source_id,))
        connection.execute("UPDATE price_observations SET variant_id=? WHERE variant_id=?", (target_id, source_id))

    if mapping:
        for operation_id, changes in connection.execute("SELECT id,changes FROM import_operations").fetchall():
            parsed = json.loads(changes)
            migrated = replace_variant_ids(parsed, mapping)
            if migrated != parsed:
                connection.execute(
                    "UPDATE import_operations SET changes=? WHERE id=?",
                    (json.dumps(migrated, ensure_ascii=False), operation_id),
                )
    stats["mapped_variants"] = len(mapping)
    stats["unreferenced_unresolved"] = len(unresolved - referenced)
    return stats


def write_database(catalog: dict) -> None:
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA)
    connection.execute("BEGIN IMMEDIATE")
    try:
        previous = connection.execute("SELECT value FROM catalog_metadata WHERE key='catalog_version'").fetchone()
        previous_version = json.loads(previous[0]) if previous else ""
        upgrading_real_catalog = isinstance(previous_version, str) and previous_version.startswith("real-catalog-")
        if not upgrading_real_catalog:
            # This is the one-time transition away from the former fabricated IDs.
            for table in (
                "deck_cards", "decks", "named_watchlist_entries", "watchlist_entries",
                "collection_entries", "import_operations", "marketplace_products", "price_observations", "variants",
                "printings", "card_identities", "sets",
            ):
                connection.execute(f"DELETE FROM {table}")
        connection.executemany(
            """INSERT INTO games VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
               module_id=excluded.module_id,name=excluded.name,short_name=excluded.short_name,
               module_version=excluded.module_version,languages=excluded.languages,accent=excluded.accent,enabled=1""",
            [(a, b, c, d, e, json.dumps(f, ensure_ascii=False), g) for a, b, c, d, e, f, g in GAME_DATA],
        )
        connection.executemany(
            """INSERT INTO sets VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               game_id=excluded.game_id,code=excluded.code,name=excluded.name,set_type=excluded.set_type,
               release_date=excluded.release_date,printed_card_count=excluded.printed_card_count,
               classifications=excluded.classifications,accent=excluded.accent""",
            [(x["id"], x["game_id"], x["code"], x["name"], x["set_type"], x["release_date"], x["printed_card_count"], json.dumps(x["classifications"], ensure_ascii=False), x["accent"]) for x in catalog["sets"].values()],
        )
        connection.executemany(
            """INSERT INTO card_identities VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               game_id=excluded.game_id,canonical_name=excluded.canonical_name,rules_text=excluded.rules_text,
               card_type=excluded.card_type,attributes=excluded.attributes""",
            [(x["id"], x["game_id"], x["canonical_name"], x["rules_text"], x["card_type"], json.dumps(x["attributes"], ensure_ascii=False)) for x in catalog["identities"].values()],
        )
        connection.executemany(
            """INSERT INTO printings VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               identity_id=excluded.identity_id,game_id=excluded.game_id,set_id=excluded.set_id,
               collector_number=excluded.collector_number,language=excluded.language,
               rarity=excluded.rarity,attributes=excluded.attributes""",
            [(x["id"], x["identity_id"], x["game_id"], x["set_id"], x["collector_number"], x["language"], x["rarity"], json.dumps(x["attributes"], ensure_ascii=False)) for x in catalog["printings"].values()],
        )
        connection.executemany(
            """INSERT INTO variants VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
               printing_id=excluded.printing_id,game_id=excluded.game_id,variant_code=excluded.variant_code,
               finish=excluded.finish,artwork_id=excluded.artwork_id,is_parallel=excluded.is_parallel,
               source_type=excluded.source_type,attributes=excluded.attributes""",
            [(x["id"], x["printing_id"], x["game_id"], x["variant_code"], x["finish"], x["artwork_id"], x["is_parallel"], x["source_type"], json.dumps(x["attributes"], ensure_ascii=False)) for x in catalog["variants"].values()],
        )
        if upgrading_real_catalog:
            migration_stats = migrate_hololive_virtual_pools(connection)
            if migration_stats["mapped_variants"] or any(migration_stats[table] for table in (
                "collection_entries", "watchlist_entries", "named_watchlist_entries", "deck_cards"
            )):
                print(f"hololive sele-Migration: {json.dumps(migration_stats, ensure_ascii=False)}", flush=True)
            # Stable IDs preserve all user data. Only references to cards genuinely
            # removed upstream are pruned before their catalogue rows disappear.
            for table, records in (
                ("incoming_variants", catalog["variants"]),
                ("incoming_printings", catalog["printings"]),
                ("incoming_identities", catalog["identities"]),
                ("incoming_sets", catalog["sets"]),
            ):
                connection.execute(f"CREATE TEMP TABLE {table}(id TEXT PRIMARY KEY)")
                connection.executemany(f"INSERT INTO {table}(id) VALUES(?)", ((key,) for key in records))
            for table in ("deck_cards", "named_watchlist_entries", "watchlist_entries", "collection_entries", "marketplace_products", "price_observations"):
                connection.execute(f"DELETE FROM {table} WHERE variant_id NOT IN (SELECT id FROM incoming_variants)")
            connection.execute("DELETE FROM variants WHERE id NOT IN (SELECT id FROM incoming_variants)")
            connection.execute("DELETE FROM printings WHERE id NOT IN (SELECT id FROM incoming_printings)")
            connection.execute("DELETE FROM card_identities WHERE id NOT IN (SELECT id FROM incoming_identities)")
            connection.execute("DELETE FROM sets WHERE id NOT IN (SELECT id FROM incoming_sets)")
        metadata = {
            "catalog_version": CATALOG_VERSION,
            "catalog_synced_at": iso_now(),
            "catalog_sources": catalog["sources"],
            "catalog_counts": {key: len(catalog[key]) for key in ("sets", "identities", "printings", "variants")},
        }
        connection.executemany(
            "INSERT OR REPLACE INTO catalog_metadata(key,value) VALUES(?,?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def already_current() -> bool:
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.executescript(SCHEMA)
        row = connection.execute("SELECT value FROM catalog_metadata WHERE key='catalog_version'").fetchone()
        return bool(row and json.loads(row[0]) == CATALOG_VERSION)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Real card catalogue sync")
    parser.add_argument("--if-needed", action="store_true", help="skip a database already imported with this importer version")
    args = parser.parse_args()
    if args.if_needed and already_current():
        print("Realer Kartenkatalog ist bereits vorhanden.", flush=True)
        return 0
    combined = empty_catalog()
    for label, importer in (("Lorcana", import_lorcana), ("One Piece", import_one_piece), ("hololive", import_hololive)):
        print(f"{label}: Import startet …", flush=True)
        merge(combined, importer())
    validate(combined)
    write_database(combined)
    print("Der reale Kartenkatalog wurde atomar gespeichert.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Katalogimport fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
        raise
