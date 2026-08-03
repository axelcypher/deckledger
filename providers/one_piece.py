"""Tier-2 custom-code provider: One Piece, sourced from Bandai's official card
list site (EN + JP) plus OPTCG API and Bandai's official DON!! PDF for DON!!
artwork the official card-list feed omits.

Self-contained on purpose (Tier-2 code runs in an isolated subprocess with no
access to the rest of the app): only stdlib, pypdf, and catalog_provider_contract.
"""

import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

from catalog_provider_contract import clean, empty_catalog, fetch, fetch_bytes, merge, put_identity, slug


ONE_PIECE_DON_API = "https://www.optcgapi.com/api/allDonCards/"
ONE_PIECE_DON_DOCS = "https://www.optcgapi.com/documentation"
ONE_PIECE_DON_JP_PDF = "https://asia-en.onepiece-cardgame.com/pdf/don-cardlist.pdf"
ONE_PIECE_EN_PRODUCTS = "https://en.onepiece-cardgame.com/products/"
# DATABASE_PATH is the same env var app.py/price_sync.py resolve independently
# -- this file must stay importable with zero dependency on the rest of the app.
DB_PATH = os.environ.get("DATABASE_PATH", "/data/deckledger.db")
CARD_IMAGE_CACHE = Path(DB_PATH).parent / "card-images"


# Bandai split EB-04 across two Western English booster products. Those
# product codes describe packaging, not additional card sets: the canonical
# card sets remain OP-14, OP-15, and EB-04. Product metadata is retained on
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


def parse_one_piece_options(page: str) -> list[tuple[str, str, str, str]]:
    result = []
    for series, label in re.findall(r'<option[^>]+value="(\d+)"[^>]*>(.*?)</option>', page, re.I | re.S):
        name = clean(label)
        code_match = re.search(r"[\[【]([^\]】]+)[\]】]\s*$", name)
        code = code_match.group(1).upper().replace(" ", "") if code_match else "PROMO" if "promot" in name.lower() or "プロモ" in name else f"SERIES-{series[-3:]}"
        kind = "Starter Deck" if re.search(r"starter|スターター", name, re.I) else "Booster Set" if re.search(r"booster|ブースター", name, re.I) else "Product Cards"
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

    EB04-numbered cards stay in EB-04. Every other inclusion, including
    alternate-art reprints and unnumbered DON!! cards, belongs to the primary
    OP set represented by that physical release. Printing and variant IDs are
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


def fetch_catalog() -> dict:
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
