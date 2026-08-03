"""Tier-2 custom-code provider: hololive OCG, sourced from the official EN/JP
card list sites.

Self-contained on purpose (Tier-2 code runs in an isolated subprocess with no
access to the rest of the app): only stdlib + catalog_provider_contract.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urljoin

from catalog_provider_contract import clean, digest, empty_catalog, fetch, merge, put_identity, slug


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


def fetch_catalog() -> dict:
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
