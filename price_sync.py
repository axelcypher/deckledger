"""Import real market prices and map them to physical card variants.

Cardmarket publishes a stable product catalogue and daily price guide for each
game.  Matching happens once at import time and is persisted with the external
product ID; runtime price reads never guess by display name. Hololive uses the
daily TCGplayer export from TCGCSV for EN and Yuyutei retail prices for JP.
"""

import argparse
import fcntl
import html
import json
import os
import re
import sqlite3
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from urllib.parse import quote_plus, unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "deckledger.db"))
LOCK_PATH = os.path.join(os.path.dirname(DB_PATH), "price-sync.lock")
CARDMARKET_BASE = "https://downloads.s3.cardmarket.com/productCatalog"
CARDMARKET_GAMES = {"one-piece": 18, "lorcana": 19}
PROVIDER_ID = "cardmarket"
TCGCSV_BASE = "https://tcgcsv.com/tcgplayer/87"
TCGCSV_UPDATED = "https://tcgcsv.com/last-updated.txt"
YUYUTEI_BASE = "https://yuyu-tei.jp/sell/hocg/s"
YUYUTEI_CACHE = os.path.join(os.path.dirname(DB_PATH), "price-cache", "yuyutei")
ECB_RATES = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
HOLOLIVE_FINISH_CODES = {"C", "S", "U", "SR", "R", "P", "RR", "UR", "OSR", "OUR", "SEC", "SY", "HR", "OC"}
HOLOLIVE_RARITY_CODES = {
    "Common": "C", "Uncommon": "U", "Rare": "R", "Super Rare": "SR",
    "Double Rare": "RR", "Oshi Common": "OC", "Oshi Super Rare": "OSR",
    "Oshi Ultra Rare": "OUR", "Ultra Rare": "UR", "Secret Rare": "SEC",
    "Special": "S", "Promo": "P",
}


def local_today() -> date:
    return datetime.now(ZoneInfo("Europe/Berlin")).date()

PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_products (
  provider_id TEXT NOT NULL,
  external_product_id TEXT NOT NULL,
  variant_id TEXT NOT NULL REFERENCES variants(id),
  game_id TEXT NOT NULL REFERENCES games(id),
  source_url TEXT NOT NULL,
  match_method TEXT NOT NULL,
  matched_at TEXT NOT NULL,
  attributes TEXT NOT NULL,
  PRIMARY KEY(provider_id, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_marketplace_external
  ON marketplace_products(provider_id, external_product_id);
CREATE INDEX IF NOT EXISTS idx_prices_variant_metric
  ON price_observations(variant_id, provider_id, metric, observed_at DESC);
"""


def fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch_bytes(url: str, accept="*/*") -> bytes:
    request = Request(url, headers={"User-Agent": "DeckLedger/1.0", "Accept": accept})
    with urlopen(request, timeout=60) as response:
        return response.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url, "text/plain,text/html").decode("utf-8", "replace")


def normalized(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def natural_key(value: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value or "")]


def collector_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def one_piece_product_key(name: str):
    matches = list(re.finditer(r"\(([A-Z][A-Z0-9-]*-\d{3})\)", name, re.I))
    if not matches:
        return None
    match = matches[-1]
    return collector_key(match.group(1)), normalized(name[:match.start()].strip())


def lorcana_url_parts(url: str):
    if not url or "cardmarket.com" not in url:
        return None
    parts = [unquote(part) for part in urlsplit(url).path.split("/") if part]
    try:
        index = parts.index("Singles")
        expansion, product = parts[index + 1:index + 3]
    except (ValueError, IndexError):
        return None
    version = 1
    match = re.search(r"-V(\d+)$", product, re.I)
    if match:
        version = int(match.group(1))
        product = product[:match.start()]
    return normalized(expansion), normalized(product), version


def parse_catalog_date(value):
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def expansion_scores(
    internal_groups: dict,
    products: list,
    product_key,
    internal_dates: dict | None = None,
    excluded_expansions: dict | None = None,
) -> tuple[dict, dict]:
    provider_groups = defaultdict(set)
    provider_dates = defaultdict(list)
    for product in products:
        key = product_key(product)
        if key is not None:
            provider_groups[product["idExpansion"]].add(key)
            added = parse_catalog_date(product.get("dateAdded"))
            if added:
                provider_dates[product["idExpansion"]].append(added)
    # dateAdded can contain a handful of later corrections.  The dominant date
    # is the reliable catalogue creation date for an expansion.
    dominant_dates = {
        expansion_id: sorted(Counter(dates).items(), key=lambda item: (-item[1], item[0]))[0][0]
        for expansion_id, dates in provider_dates.items() if dates
    }
    matches, audit = {}, {}
    for group_id, internal_keys in internal_groups.items():
        excluded = set((excluded_expansions or {}).get(group_id, ()))
        candidates = []
        for expansion_id, provider_keys in provider_groups.items():
            if expansion_id in excluded:
                continue
            common = len(internal_keys & provider_keys)
            if not common:
                continue
            coverage = common / max(1, len(internal_keys))
            precision = common / max(1, len(provider_keys))
            f1 = 2 * coverage * precision / max(0.0001, coverage + precision)
            candidates.append((f1, coverage, precision, common, expansion_id))
        candidates.sort(reverse=True)
        if not candidates:
            continue
        best = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0
        selected = best
        release_date = parse_catalog_date((internal_dates or {}).get(group_id))
        selected_by_date = False
        if release_date:
            minimum_common = min(3, len(internal_keys))
            # Western and Japanese One Piece expansions commonly have nearly
            # identical fingerprints.  Compare dates only between genuinely
            # set-shaped candidates, never against promo/reprint grab bags.
            comparable = [
                candidate for candidate in candidates
                if candidate[3] >= minimum_common
                and candidate[1] >= 0.45
                and candidate[0] >= max(0.65, best[0] - 0.08)
                and candidate[4] in dominant_dates
            ]
            if comparable:
                selected = min(
                    comparable,
                    key=lambda candidate: (
                        abs((dominant_dates[candidate[4]] - release_date).days),
                        -candidate[0],
                    ),
                )
                selected_by_date = True
        selected_date = dominant_dates.get(selected[4])
        date_delta = abs((selected_date - release_date).days) if selected_date and release_date else None
        audit[group_id] = {
            "expansion": selected[4], "score": round(selected[0], 4), "coverage": round(selected[1], 4),
            "precision": round(selected[2], 4), "common": selected[3], "margin": round(best[0] - second_score, 4),
            "release_date": release_date.isoformat() if release_date else None,
            "catalogue_date": selected_date.isoformat() if selected_date else None,
            "date_delta_days": date_delta,
            "selected_by_date": selected_by_date,
            "excluded_expansions": sorted(excluded),
        }
        minimum_common = min(3, len(internal_keys))
        # Equal-looking expansions are common in One Piece because Cardmarket
        # lists Western and Japanese releases separately.  A clear fingerprint
        # margin or a release-date match is therefore required.
        unambiguous = best[0] - second_score >= 0.04
        date_match = selected_by_date and date_delta is not None and date_delta <= 120
        if selected[3] >= minimum_common and selected[1] >= 0.45 and (unambiguous or date_match):
            matches[group_id] = selected[4]
    return matches, audit


def variant_rows(connection, game_id: str) -> list[dict]:
    rows = connection.execute(
        """SELECT v.id variant_id,v.variant_code,v.finish,v.artwork_id,v.is_parallel,v.attributes variant_attributes,
           p.id printing_id,p.collector_number,p.language,p.set_id,p.attributes printing_attributes,
           i.canonical_name,s.name set_name,s.code set_code,s.release_date
           FROM variants v JOIN printings p ON p.id=v.printing_id
           JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
           WHERE v.game_id=?""", (game_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def map_lorcana(connection, products: list) -> tuple[list[dict], dict]:
    rows = variant_rows(connection, "lorcana")
    parsed = []
    internal_groups = defaultdict(set)
    for row in rows:
        attributes = json.loads(row["variant_attributes"] or "{}")
        parts = lorcana_url_parts(attributes.get("priceUrl"))
        if not parts:
            continue
        expansion_slug, product_key, version = parts
        internal_groups[expansion_slug].add(product_key)
        parsed.append((row, attributes, expansion_slug, product_key, version))
    expansion_map, audit = expansion_scores(internal_groups, products, lambda p: normalized(p["name"]))
    product_index = defaultdict(list)
    for product in products:
        product_index[(product["idExpansion"], normalized(product["name"]))].append(product)
    for candidates in product_index.values():
        candidates.sort(key=lambda item: item["idProduct"])
    mappings = []
    for row, attributes, expansion_slug, product_key, version in parsed:
        expansion_id = expansion_map.get(expansion_slug)
        candidates = product_index.get((expansion_id, product_key), [])
        if not candidates or version > len(candidates):
            continue
        # Cardmarket appends V1/V2 to otherwise identical product slugs.  The
        # catalogue's stable product IDs are ordered in that same product order.
        if len(candidates) > 1 and not re.search(r"-V\d+$", urlsplit(attributes["priceUrl"]).path, re.I):
            continue
        product = candidates[version - 1]
        mappings.append({
            "variant_id": row["variant_id"], "game_id": "lorcana", "product": product,
            "price_mode": "normal" if row["finish"].lower() == "normal" else "foil",
            "source_url": attributes["priceUrl"], "method": "provider-url+expansion-fingerprint+product-version",
        })
    return mappings, {"expansions": len(expansion_map), "expansion_audit": audit, "variants_seen": len(rows)}


def map_one_piece(connection, products: list) -> tuple[list[dict], dict]:
    # Cardmarket models Western and Japanese One Piece releases as distinct
    # expansions and therefore gives them distinct product IDs and price
    # histories.  The public download contains only the numeric expansion ID,
    # not its language/name.  First resolve the Western expansion with Bandai's
    # official Western release date.  Then resolve the Japanese catalogue from
    # the remaining set-shaped fingerprint candidate.  This keeps prices
    # language-separated without borrowing an EN product or guessing by name.
    rows = [
        row for row in variant_rows(connection, "one-piece")
        if row["set_id"] != "one-piece-don" and collector_key(row["collector_number"])
    ]
    rows_by_language = {
        language: [row for row in rows if row["language"] == language]
        for language in ("EN", "JP")
    }

    en_groups = defaultdict(set)
    en_price_groups = {}
    en_release_dates = {}
    for row in rows_by_language["EN"]:
        attributes = json.loads(row["printing_attributes"] or "{}")
        price_group = attributes.get("releaseProductCode") or row["set_id"]
        en_price_groups[row["printing_id"]] = price_group
        en_release_dates[price_group] = attributes.get("releaseProductReleaseDate") or row["release_date"]
        en_groups[price_group].add((collector_key(row["collector_number"]), normalized(row["canonical_name"])))
    en_expansions, en_audit = expansion_scores(
        en_groups, products, lambda p: one_piece_product_key(p["name"]), internal_dates=en_release_dates,
    )

    # Combined Western products (for example OP14-EB04) are split back into
    # their canonical sets in DeckLedger.  Remember the resolved Western ID per
    # canonical set so it can be excluded from the corresponding JP match.
    western_by_set = defaultdict(set)
    for row in rows_by_language["EN"]:
        expansion_id = en_expansions.get(en_price_groups.get(row["printing_id"]))
        if expansion_id is not None:
            western_by_set[row["set_id"]].add(expansion_id)

    jp_groups = defaultdict(set)
    jp_price_groups = {}
    for row in rows_by_language["JP"]:
        price_group = row["set_id"]
        jp_price_groups[row["printing_id"]] = price_group
        jp_groups[price_group].add((collector_key(row["collector_number"]), normalized(row["canonical_name"])))
    jp_expansions, jp_audit = expansion_scores(
        jp_groups,
        products,
        lambda p: one_piece_product_key(p["name"]),
        excluded_expansions=western_by_set,
    )

    expansion_maps = {"EN": en_expansions, "JP": jp_expansions}
    price_groups = {"EN": en_price_groups, "JP": jp_price_groups}
    product_index = defaultdict(list)
    for product in products:
        key = one_piece_product_key(product["name"])
        if key:
            product_index[(product["idExpansion"], *key)].append(product)
    for candidates in product_index.values():
        candidates.sort(key=lambda item: item["idProduct"])

    by_printing = defaultdict(list)
    for row in rows:
        by_printing[row["printing_id"]].append(row)
    mappings = []
    skipped_ambiguous = 0
    for printing_rows in by_printing.values():
        representative = printing_rows[0]
        language = representative["language"]
        expansion_id = expansion_maps.get(language, {}).get(price_groups.get(language, {}).get(representative["printing_id"]))
        key = (expansion_id, collector_key(representative["collector_number"]), normalized(representative["canonical_name"]))
        candidates = product_index.get(key, [])
        ordered_variants = sorted(printing_rows, key=lambda row: (row["variant_code"] != "standard", natural_key(row["variant_code"])))
        if not candidates or len(candidates) != len(ordered_variants):
            skipped_ambiguous += len(ordered_variants)
            continue
        for row, product in zip(ordered_variants, candidates):
            search = quote_plus(product["name"])
            language = {"EN": 1, "DE": 3, "JP": 7}.get(row["language"])
            language_query = f"&language={language}" if language else ""
            mappings.append({
                "variant_id": row["variant_id"], "game_id": "one-piece", "product": product,
                "language": row["language"],
                "price_mode": "normal",
                "source_url": f"https://www.cardmarket.com/en/OnePiece/Products/Search?searchString={search}{language_query}",
                "method": "language-expansion+set-fingerprint+collector-number+card-name+artwork-order",
            })
    return mappings, {
        "expansions": {"EN": len(en_expansions), "JP": len(jp_expansions)},
        "expansion_audit": {"EN": en_audit, "JP": jp_audit},
        "variants_seen": {language: len(language_rows) for language, language_rows in rows_by_language.items()},
        "skipped_ambiguous": skipped_ambiguous,
    }


def hololive_set_key(value: str) -> str:
    """Normalize only explicitly verified EN set abbreviations.

    The leading h belongs to the card number, while TCGplayer inconsistently
    includes it. Its trailing E denotes the English release. SD12/13 currently
    arrive as SD012E/SD013E in TCGplayer's group metadata.
    """
    key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    aliases = {"hocgp": "pr", "s01e": "ys01", "sd012e": "sd12", "sd013e": "sd13"}
    if key in aliases:
        return aliases[key]
    if key.endswith("e"):
        key = key[:-1]
    if key.startswith("h"):
        key = key[1:]
    return key


def tcgplayer_finish(product: dict) -> str | None:
    codes = [
        part.strip().upper() for part in re.findall(r"\(([^()]*)\)", product.get("name") or "")
        if part.strip().upper() in HOLOLIVE_FINISH_CODES
    ]
    if len(codes) == 1:
        return codes[0]
    extended = {item.get("name"): item.get("value") for item in product.get("extendedData") or []}
    return HOLOLIVE_RARITY_CODES.get(extended.get("Rarity"))


def tcgplayer_number(product: dict) -> str | None:
    extended = {item.get("name"): item.get("value") for item in product.get("extendedData") or []}
    return extended.get("Number")


def load_tcgplayer_hololive() -> tuple[list[dict], dict, str]:
    groups = fetch_json(f"{TCGCSV_BASE}/groups").get("results") or []
    released = []
    today = local_today()
    for group in groups:
        published = parse_catalog_date(group.get("publishedOn"))
        if published and published <= today:
            released.append(group)

    def load_group(group):
        group_id = group["groupId"]
        products = fetch_json(f"{TCGCSV_BASE}/{group_id}/products").get("results") or []
        # TCGCSV asks consumers to avoid bursty polling. Each worker sleeps once
        # between the two endpoints and the complete sync is still daily data.
        time.sleep(0.12)
        prices = fetch_json(f"{TCGCSV_BASE}/{group_id}/prices").get("results") or []
        return group, products, prices

    payloads = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(load_group, group) for group in released]
        for future in as_completed(futures):
            payloads.append(future.result())
    updated = fetch_text(TCGCSV_UPDATED).strip()
    return payloads, {"updated": updated, "groups": len(released)}, updated


def map_hololive_tcgplayer(connection, payloads: list, rates: dict) -> tuple[list[dict], dict]:
    rows = [row for row in variant_rows(connection, "hololive") if row["language"] == "EN"]
    internal = defaultdict(list)
    for row in rows:
        internal[(hololive_set_key(row["set_code"]), collector_key(row["collector_number"]), row["finish"].upper())].append(row)

    provider = defaultdict(list)
    prices = defaultdict(list)
    group_names = {}
    for group, products, price_rows in payloads:
        set_key = hololive_set_key(group.get("abbreviation"))
        group_names[group["groupId"]] = group.get("name")
        for product in products:
            number = tcgplayer_number(product)
            finish = tcgplayer_finish(product)
            if number and finish:
                provider[(set_key, collector_key(number), finish)].append(product)
        for row in price_rows:
            prices[str(row.get("productId"))].append(row)

    mappings = []
    skipped_ambiguous = 0
    for key, internal_rows in internal.items():
        products = provider.get(key, [])
        if len(internal_rows) != 1 or len(products) != 1:
            if products:
                skipped_ambiguous += len(internal_rows)
            continue
        row, product = internal_rows[0], products[0]
        available = [entry for entry in prices.get(str(product["productId"]), []) if entry.get("marketPrice")]
        if len(available) != 1:
            continue
        quote = available[0]
        native = {"trend": quote.get("marketPrice"), "low": quote.get("lowPrice")}
        values = {
            metric: round(float(value) / rates["USD"], 4)
            for metric, value in native.items() if isinstance(value, (int, float)) and value > 0
        }
        if "trend" not in values:
            continue
        mappings.append({
            "provider_id": "tcgplayer", "external_product_id": str(product["productId"]),
            "variant_id": row["variant_id"], "game_id": "hololive", "source_url": product["url"],
            "method": "language+tcgplayer-group+collector-number+rarity",
            "values": values,
            "attributes": {
                "productName": product["name"], "groupId": product.get("groupId"),
                "groupName": group_names.get(product.get("groupId")), "subType": quote.get("subTypeName"),
                "sourceCurrency": "USD", "sourceTrend": native["trend"], "sourceLow": native.get("low"),
                "eurRate": rates["USD"], "exchangeDate": rates["date"],
            },
        })
    return mappings, {
        "variants_seen": len(rows), "provider_keys": len(provider),
        "skipped_ambiguous": skipped_ambiguous,
    }


YUYUTEI_CARD_PATTERN = re.compile(
    r'<a\s+href="(?P<url>https://yuyu-tei\.jp/sell/hocg/card/[^"]+)">\s*'
    r'<div[^>]*product-img[^>]*>\s*<img[^>]*alt="(?P<alt>[^"]+)"[^>]*>.*?</a>.*?'
    r'<strong[^>]*>\s*(?P<price>[\d,]+)\s*円', re.S,
)


def parse_yuyutei_page(set_code: str, page: str) -> list[dict]:
    rows = []
    for match in YUYUTEI_CARD_PATTERN.finditer(page):
        alt = html.unescape(match.group("alt")).strip()
        parts = alt.split(maxsplit=2)
        if len(parts) < 2 or parts[1].upper() not in HOLOLIVE_FINISH_CODES:
            continue
        rows.append({
            "set_key": hololive_set_key(set_code), "number": collector_key(parts[0]),
            "finish": parts[1].upper(), "name": parts[2] if len(parts) > 2 else "",
            "url": html.unescape(match.group("url")), "price": float(match.group("price").replace(",", "")),
            "external_product_id": match.group("url").rstrip("/").rsplit("/", 1)[-1],
        })
    return rows


def load_yuyutei_hololive(connection) -> tuple[list[dict], dict]:
    today = local_today().isoformat()
    set_codes = [
        row[0] for row in connection.execute(
            """SELECT DISTINCT s.code FROM sets s JOIN printings p ON p.set_id=s.id
               WHERE s.game_id='hololive' AND p.language='JP'
                 AND (s.release_date IS NULL OR s.release_date<=?)""", (today,)
        )
    ]

    os.makedirs(YUYUTEI_CACHE, exist_ok=True)
    pages, failures, missing, cached = {}, {}, {}, 0
    today_utc = datetime.now(timezone.utc).date()
    throttled = False
    for index, code in enumerate(sorted(set_codes, key=natural_key)):
        cache_path = os.path.join(YUYUTEI_CACHE, f"{code.lower()}.html")
        if os.path.exists(cache_path) and datetime.fromtimestamp(os.path.getmtime(cache_path), timezone.utc).date() == today_utc:
            with open(cache_path, "r", encoding="utf-8") as handle:
                pages[code] = handle.read()
            cached += 1
            continue
        if index:
            time.sleep(0.55)
        url = f"{YUYUTEI_BASE}/{code.lower()}"
        if throttled:
            failures[code] = "HTTP 429 (Abruf nach globaler Drosselung ausgesetzt)"
            continue
        try:
            page = fetch_text(url)
            pages[code] = page
            temporary = f"{cache_path}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(page)
            os.replace(temporary, cache_path)
        except HTTPError as exc:
            if exc.code == 429:
                # One respectful cooldown; if it is still rejected, stop all
                # remaining requests and resume from the daily cache next run.
                time.sleep(15)
                try:
                    page = fetch_text(url)
                    pages[code] = page
                    temporary = f"{cache_path}.tmp"
                    with open(temporary, "w", encoding="utf-8") as handle:
                        handle.write(page)
                    os.replace(temporary, cache_path)
                    continue
                except HTTPError as retry_exc:
                    if retry_exc.code == 429:
                        throttled = True
                    failures[code] = f"HTTP {retry_exc.code} nach Cooldown"
                except Exception as retry_exc:
                    failures[code] = f"{type(retry_exc).__name__}: {retry_exc}"
            else:
                target = missing if exc.code == 404 else failures
                target[code] = f"HTTP {exc.code}"
        except Exception as exc:
            failures[code] = f"{type(exc).__name__}: {exc}"
    products = []
    for code, page in pages.items():
        products.extend(parse_yuyutei_page(code, page))
    return products, {
        "sets_requested": len(set_codes), "sets_loaded": len(pages), "sets_cached": cached,
        "transient_failures": failures, "not_listed": missing,
    }


def map_hololive_yuyutei(connection, products: list, rates: dict) -> tuple[list[dict], dict]:
    rows = [row for row in variant_rows(connection, "hololive") if row["language"] == "JP"]
    internal = defaultdict(list)
    for row in rows:
        internal[(hololive_set_key(row["set_code"]), collector_key(row["collector_number"]), row["finish"].upper())].append(row)
    provider = defaultdict(list)
    for product in products:
        provider[(product["set_key"], product["number"], product["finish"])].append(product)

    mappings = []
    skipped_ambiguous = 0
    for key, internal_rows in internal.items():
        provider_rows = provider.get(key, [])
        if len(internal_rows) != 1 or len(provider_rows) != 1:
            if provider_rows:
                skipped_ambiguous += len(internal_rows)
            continue
        row, product = internal_rows[0], provider_rows[0]
        eur = round(product["price"] / rates["JPY"], 4)
        mappings.append({
            "provider_id": "yuyutei", "external_product_id": product["external_product_id"],
            "variant_id": row["variant_id"], "game_id": "hololive", "source_url": product["url"],
            "method": "language+yuyutei-set+collector-number+rarity",
            "values": {"trend": eur},
            "attributes": {
                "productName": product["name"], "sourceCurrency": "JPY", "sourceTrend": product["price"],
                "eurRate": rates["JPY"], "exchangeDate": rates["date"], "priceType": "retail",
            },
        })
    return mappings, {
        "variants_seen": len(rows), "provider_keys": len(provider),
        "skipped_ambiguous": skipped_ambiguous,
    }


def ecb_rates() -> dict:
    root = ET.fromstring(fetch_bytes(ECB_RATES, "application/xml,text/xml"))
    result = {}
    for element in root.iter():
        if element.attrib.get("time"):
            result["date"] = element.attrib["time"]
        currency = element.attrib.get("currency")
        if currency in {"USD", "JPY"}:
            result[currency] = float(element.attrib["rate"])
    if not {"date", "USD", "JPY"}.issubset(result):
        raise RuntimeError("EZB-Wechselkurse für USD/JPY sind unvollständig")
    return result


def price_values(mapping: dict, guide: dict) -> dict:
    suffix = "-foil" if mapping["price_mode"] == "foil" else ""
    fields = {"trend": f"trend{suffix}", "low": f"low{suffix}", "avg30": f"avg30{suffix}"}
    result = {}
    for metric, field in fields.items():
        value = guide.get(field)
        if isinstance(value, (int, float)) and value > 0:
            result[metric] = float(value)
    return result


def metadata_value(connection, key: str, default=None):
    row = connection.execute("SELECT value FROM catalog_metadata WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return default


CARDMARKET_MATCHERS = {"lorcana": map_lorcana, "one-piece": map_one_piece}


def resolve_price_assignments(connection) -> dict[str, set[str]]:
    """{price_method: {game_id, ...}} -- which games use which price pipeline this run.

    Each of a game's own languages resolves its per-language override first,
    falling back to the game's primary `price_method`; a game ends up in a
    method's set if *any* of its languages resolve to it. Data-driven so the
    dispatch below never hardcodes a game_id -- only the pipelines themselves
    (which external site, how matching works) stay bespoke code, exactly like
    catalog_providers' `kind='builtin'` importers.
    """
    overrides = {(r["game_id"], r["language"]): r["price_method"] for r in connection.execute("SELECT game_id, language, price_method FROM game_price_overrides")}
    assignments = defaultdict(set)
    for row in connection.execute("SELECT id, languages, price_method FROM games"):
        for language in json.loads(row["languages"]):
            method = overrides.get((row["id"], language), row["price_method"])
            if method:
                assignments[method].add(row["id"])
    return assignments


def synchronize(if_needed=False, dry_run=False) -> dict:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(PRICE_SCHEMA)
        assignments = resolve_price_assignments(connection)
        cardmarket_game_ids = assignments.get("cardmarket", set())
        hololive_tcgcsv = "hololive" in assignments.get("tcgcsv", set())
        hololive_yuyutei = "hololive" in assignments.get("yuyutei", set())

        payloads = {}
        for game_id in cardmarket_game_ids:
            number = CARDMARKET_GAMES.get(game_id)
            if number is None:
                continue  # assigned to Cardmarket, but no Cardmarket-side numeric id configured for it yet
            product_url = f"{CARDMARKET_BASE}/productList/products_singles_{number}.json"
            guide_url = f"{CARDMARKET_BASE}/priceGuide/price_guide_{number}.json"
            payloads[game_id] = (product_url, guide_url, fetch_json(product_url), fetch_json(guide_url))
        tcgplayer_payloads = tcgplayer_version = tcgplayer_observed = None
        if hololive_tcgcsv:
            tcgplayer_payloads, tcgplayer_version, tcgplayer_observed = load_tcgplayer_hololive()
        rates = ecb_rates()
        versions = {game: {"products": data[2].get("createdAt"), "prices": data[3].get("createdAt")} for game, data in payloads.items()}
        if hololive_tcgcsv:
            versions["hololive_en"] = tcgplayer_version
        if hololive_yuyutei:
            versions["hololive_jp"] = {"date": local_today().isoformat()}
        versions["exchange_rates"] = rates

        if if_needed and metadata_value(connection, "price_sync_versions") == versions:
            result = metadata_value(connection, "price_sync_counts", {})
            result["skipped"] = "Die täglichen Marktdateien sind bereits importiert."
            return result
        all_mappings, audits, guides = [], {}, {}
        for game_id, (_, _, products_data, guide_data) in payloads.items():
            products = products_data.get("products") or []
            guide_rows = guide_data.get("priceGuides") or []
            guides.update({str(row["idProduct"]): row for row in guide_rows})
            matcher = CARDMARKET_MATCHERS.get(game_id)
            if not matcher:
                audits[game_id] = {"skipped": "kein Cardmarket-Matcher für dieses Spiel hinterlegt"}
                continue
            mappings, audit = matcher(connection, products)
            for mapping in mappings:
                mapping["provider_id"] = PROVIDER_ID
                mapping["external_product_id"] = str(mapping["product"]["idProduct"])
                mapping["observed_at"] = guide_data.get("createdAt") or datetime.now(timezone.utc).isoformat()
                mapping["attributes"] = {
                    "productName": mapping["product"]["name"],
                    "expansionId": mapping["product"]["idExpansion"],
                    "priceMode": mapping["price_mode"],
                    **({"marketLanguage": mapping["language"]} if mapping.get("language") else {}),
                }
            all_mappings.extend(mappings)
            audits[game_id] = audit

        if hololive_tcgcsv:
            tcgplayer_mappings, tcgplayer_audit = map_hololive_tcgplayer(connection, tcgplayer_payloads, rates)
            for mapping in tcgplayer_mappings:
                mapping["observed_at"] = tcgplayer_observed or datetime.now(timezone.utc).isoformat()
            all_mappings.extend(tcgplayer_mappings)
            audits["hololive_en"] = tcgplayer_audit

        yuyutei_success = False
        if hololive_yuyutei:
            yuyutei_products, yuyutei_load_audit = load_yuyutei_hololive(connection)
            yuyutei_mappings, yuyutei_audit = map_hololive_yuyutei(connection, yuyutei_products, rates)
            yuyutei_observed = datetime.now(timezone.utc).isoformat()
            for mapping in yuyutei_mappings:
                mapping["observed_at"] = yuyutei_observed
            audits["hololive_jp"] = {**yuyutei_load_audit, **yuyutei_audit}
            yuyutei_success = not audits["hololive_jp"].get("transient_failures") and len(yuyutei_mappings) >= 500
            if yuyutei_success:
                all_mappings.extend(yuyutei_mappings)
            else:
                audits["hololive_jp"]["preserved_previous_data"] = True
                versions["hololive_jp"] = {"incomplete": yuyutei_observed}

        counts = defaultdict(int)
        observation_rows = []
        for mapping in all_mappings:
            values = mapping.get("values")
            if values is None:
                guide = guides.get(mapping["external_product_id"])
                if not guide:
                    continue
                values = price_values(mapping, guide)
            if "trend" not in values:
                continue
            mapping["values"] = values
            counts[f"{mapping['game_id']}_{mapping['provider_id']}_mappings"] += 1
            if mapping.get("language"):
                counts[f"{mapping['game_id']}_{mapping['provider_id']}_{mapping['language'].lower()}_mappings"] += 1
            for metric, amount in values.items():
                observation_rows.append((mapping["variant_id"], mapping["provider_id"], metric, amount, "EUR", mapping["observed_at"]))
                counts[f"{mapping['game_id']}_{mapping['provider_id']}_{metric}"] += 1
                if mapping.get("language"):
                    counts[f"{mapping['game_id']}_{mapping['provider_id']}_{mapping['language'].lower()}_{metric}"] += 1

        if hololive_yuyutei and not yuyutei_success:
            counts["hololive_yuyutei_mappings"] = connection.execute(
                "SELECT COUNT(*) FROM marketplace_products WHERE provider_id='yuyutei' AND game_id='hololive'"
            ).fetchone()[0]
            counts["hololive_yuyutei_trend"] = connection.execute(
                """SELECT COUNT(*) FROM marketplace_products mp WHERE mp.provider_id='yuyutei' AND mp.game_id='hololive'
                   AND EXISTS(SELECT 1 FROM price_observations po WHERE po.variant_id=mp.variant_id
                              AND po.provider_id='yuyutei' AND po.metric='trend')"""
            ).fetchone()[0]
            counts["hololive_yuyutei_preserved"] = 1

        # A provider format change must never silently erase working mappings.
        # Each floor only applies to a game/method actually dispatched this run --
        # a game an admin unassigns from a method must not trip its old floor.
        if "lorcana" in cardmarket_game_ids and counts["lorcana_cardmarket_mappings"] < 5000:
            raise RuntimeError(f"Cardmarket-Matching (Lorcana) unter Sicherheitsgrenze: {dict(counts)}")
        if "one-piece" in cardmarket_game_ids:
            if counts["one-piece_cardmarket_mappings"] < 400:
                raise RuntimeError(f"Cardmarket-Matching (One Piece) unter Sicherheitsgrenze: {dict(counts)}")
            if counts["one-piece_cardmarket_en_mappings"] < 400 or counts["one-piece_cardmarket_jp_mappings"] < 400:
                raise RuntimeError(f"Sprachgetrenntes One-Piece-Matching unter Sicherheitsgrenze: {dict(counts)}")
        if hololive_tcgcsv and counts["hololive_tcgplayer_mappings"] < 500:
            raise RuntimeError(f"Hololive-Matching unter Sicherheitsgrenze: {dict(counts)}")
        result = {"counts": dict(counts), "versions": versions, "audits": audits}
        if dry_run:
            return result

        matched_at = datetime.now(timezone.utc).isoformat()
        connection.execute("BEGIN")
        providers_to_replace = ["optcgapi"]
        if cardmarket_game_ids:
            providers_to_replace.append(PROVIDER_ID)
        if hololive_tcgcsv:
            providers_to_replace.append("tcgplayer")
        if yuyutei_success:
            providers_to_replace.append("yuyutei")
        placeholders = ",".join("?" for _ in providers_to_replace)
        connection.execute(f"DELETE FROM marketplace_products WHERE provider_id IN ({placeholders})", providers_to_replace)
        connection.execute("DELETE FROM price_observations WHERE provider_id='optcgapi'")
        connection.executemany(
            """INSERT INTO marketplace_products
               (provider_id,external_product_id,variant_id,game_id,source_url,match_method,matched_at,attributes)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(
                mapping["provider_id"], mapping["external_product_id"], mapping["variant_id"], mapping["game_id"],
                mapping["source_url"], mapping["method"], matched_at,
                json.dumps(mapping["attributes"], ensure_ascii=False),
            ) for mapping in all_mappings if "values" in mapping
            ],
        )
        connection.executemany(
            """INSERT INTO price_observations(variant_id,provider_id,metric,amount,currency,observed_at)
               SELECT ?,?,?,?,?,? WHERE NOT EXISTS(
                 SELECT 1 FROM price_observations WHERE variant_id=? AND provider_id=? AND metric=? AND observed_at=?
               )""",
            [(*row, row[0], row[1], row[2], row[5]) for row in observation_rows],
        )
        metadata = {
            "price_sync_versions": versions,
            "price_sync_counts": dict(counts),
            "price_sync_last_success": matched_at,
            "price_sync_sources": {game: {"products": data[0], "prices": data[1]} for game, data in payloads.items()},
        }
        if hololive_tcgcsv:
            metadata["price_sync_sources"]["hololive_en"] = {"provider": "TCGplayer via TCGCSV", "groups": f"{TCGCSV_BASE}/groups", "updated": TCGCSV_UPDATED}
        if hololive_yuyutei:
            metadata["price_sync_sources"]["hololive_jp"] = {"provider": "Yuyutei retail", "sets": YUYUTEI_BASE}
        metadata["price_sync_sources"]["exchange_rates"] = {"provider": "European Central Bank", "url": ECB_RATES}
        connection.executemany(
            "INSERT OR REPLACE INTO catalog_metadata(key,value) VALUES(?,?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        result.pop("audits", None)
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="Cardmarket product and price sync")
    parser.add_argument("--if-needed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(synchronize(if_needed=args.if_needed, dry_run=args.dry_run), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
