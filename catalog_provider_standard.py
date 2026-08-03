"""Tier-1 declarative catalog provider.

Turns a small JSON config (field-mapping only, no code) into a catalog dict
matching the shared contract in catalog_provider_contract.py. Deliberately
narrow in scope: clean JSON/CSV sources with 1:1 or 1:N field mapping. Sources
needing HTML scraping, PDFs, or conditional/lookup logic beyond simple value
substitution belong in a Tier-2 custom-code provider instead.

Config shape (stored as JSON text in catalog_providers.config):

{
  "sources": [{"language": "EN", "type": "static-json"|"http-json"|"csv"|"github-release",
                "url": "...", "paginate": {...}?, "repo": "...", "tag": "latest", "asset_pattern": "..."}],
  "preferred_language": "EN",
  "sets_path": "sets", "cards_path": "cards",           # dot-path into the fetched payload; omit for flat lists (CSV)
  "set_mapping": {"code_field": "$key", "name_field": "name", "set_type_field": "type",
                   "release_date_field": "releaseDate", "printed_card_count_field": "cardCounts.total",
                   "classifications_field": "formats"},
  "identity_mapping": {"id_field": "id", "alias_field": "baseId", "canonical_name_field": "fullName",
                        "rules_text_field": "fullText", "card_type_field": "type",
                        "attributes": {"color": "color", "cost": "cost"}},
  "printing_mapping": {"set_code_field": "setCode", "collector_number_field": "number",
                        "rarity_field": "rarity", "attributes": {"artists": "artists"}},
  "variant_mapping": {"mode": "expand_array_field", "field": "foilTypes", "default": ["Normal"],
                       "image_field": "images.full"}
               # or: {"mode": "group_variants_by", "finish_field": "finish", "image_field": "image"}
  "lookup_tables": {"rarity_label": {"C": "Common"}, "set_type": {...}, "set_name": {...}},
  "default_accent": "#6366f1",
  "overrides": {"sets": {}, "identities": {}, "printings": {}, "variants": {}},
  "minimum_sets": 0, "minimum_cards": 0
}
"""

from __future__ import annotations

import csv
import io
import json
import re

from catalog_provider_contract import empty_catalog, fetch, merge, put_identity, slug

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_path(record, path, key=None):
    if not path:
        return None
    if path == "$key":
        return key
    current = record
    for name, index in _PATH_TOKEN.findall(path):
        if current is None:
            return None
        if name:
            current = current.get(name) if isinstance(current, dict) else None
        else:
            i = int(index)
            current = current[i] if isinstance(current, list) and i < len(current) else None
    return current


def apply_lookup(lookup_tables: dict, table_name: str, value):
    return (lookup_tables.get(table_name) or {}).get(value)


def extract_collection(raw, path):
    return raw if not path else resolve_path(raw, path)


def iter_collection(collection):
    if collection is None:
        return
    if isinstance(collection, dict):
        yield from collection.items()
    elif isinstance(collection, list):
        for record in collection:
            yield None, record


def resolve_github_release_asset(repo: str, tag: str, asset_pattern: str) -> str:
    api_url = f"https://api.github.com/repos/{repo}/releases/{'latest' if tag in (None, 'latest') else 'tags/' + tag}"
    payload = json.loads(fetch(api_url))
    pattern = re.compile(asset_pattern)
    for asset in payload.get("assets", []):
        if pattern.search(asset["name"]):
            return asset["browser_download_url"]
    raise RuntimeError(f"Kein Release-Asset in {repo}@{tag} passt zu Muster {asset_pattern!r}")


def fetch_paginated_json(url: str, paginate: dict) -> list:
    items = []
    current_url = url
    page = 1
    max_pages = paginate.get("max_pages", 200)
    while current_url and page <= max_pages:
        payload = json.loads(fetch(current_url))
        page_items = resolve_path(payload, paginate["items_path"]) if paginate.get("items_path") else payload
        if not page_items:
            break
        items.extend(page_items)
        next_url = None
        if paginate.get("next_field"):
            next_url = resolve_path(payload, paginate["next_field"])
        elif paginate.get("page_param"):
            page += 1
            if page > max_pages:
                break
            separator = "&" if "?" in url else "?"
            next_url = f"{url}{separator}{paginate['page_param']}={page}"
        current_url = next_url
        page += 1 if not paginate.get("page_param") else 0
    return items


def load_source(source: dict):
    fetch_type = source["type"]
    if fetch_type == "github-release":
        url = resolve_github_release_asset(source["repo"], source.get("tag", "latest"), source["asset_pattern"])
        data_format = source.get("format", "json")
    else:
        url = source["url"]
        data_format = "csv" if fetch_type == "csv" else "json"
    if fetch_type == "http-json" and source.get("paginate"):
        return fetch_paginated_json(url, source["paginate"])
    text = fetch(url)
    if data_format == "csv":
        return list(csv.DictReader(io.StringIO(text)))
    return json.loads(text)


def build_partial_catalog(game_id: str, source: dict, config: dict) -> dict:
    raw = load_source(source)
    catalog = empty_catalog()
    language = source.get("language", "EN")
    lookup_tables = config.get("lookup_tables") or {}
    accent = config.get("default_accent", "#6366f1")

    set_mapping = config.get("set_mapping") or {}
    for key, record in iter_collection(extract_collection(raw, config.get("sets_path"))):
        code = resolve_path(record, set_mapping.get("code_field", "$key"), key)
        if code is None:
            continue
        set_id = f"{game_id}-{slug(code)}"
        catalog["sets"][set_id] = {
            "id": set_id, "game_id": game_id, "code": str(code),
            "name": resolve_path(record, set_mapping.get("name_field")) or str(code),
            "set_type": apply_lookup(lookup_tables, "set_type", resolve_path(record, set_mapping.get("set_type_field"))) or resolve_path(record, set_mapping.get("set_type_field")) or "Set",
            "release_date": resolve_path(record, set_mapping.get("release_date_field")),
            "printed_card_count": resolve_path(record, set_mapping.get("printed_card_count_field")),
            "classifications": resolve_path(record, set_mapping.get("classifications_field")) or [],
            "accent": accent,
            "_source_language": language,
        }

    identity_mapping = config.get("identity_mapping") or {}
    printing_mapping = config.get("printing_mapping") or {}
    variant_mapping = config.get("variant_mapping") or {"mode": "expand_array_field", "field": None, "default": ["Normal"]}
    preferred_language = config.get("preferred_language", "EN")

    for key, record in iter_collection(extract_collection(raw, config.get("cards_path"))):
        own_id = resolve_path(record, identity_mapping.get("id_field", "id"), key)
        if own_id is None:
            continue
        alias = resolve_path(record, identity_mapping.get("alias_field")) if identity_mapping.get("alias_field") else None
        identity_key_source = alias if alias not in (None, "") else own_id
        identity_id = f"{game_id}-card-{slug(identity_key_source)}"
        attrs = {attr_key: resolve_path(record, attr_path) for attr_key, attr_path in (identity_mapping.get("attributes") or {}).items()}
        attrs["sourceLanguage"] = language
        put_identity(catalog, {
            "id": identity_id, "game_id": game_id,
            "canonical_name": resolve_path(record, identity_mapping.get("canonical_name_field")) or str(own_id),
            "rules_text": resolve_path(record, identity_mapping.get("rules_text_field")) or "",
            "card_type": resolve_path(record, identity_mapping.get("card_type_field")) or "Unknown",
            "attributes": attrs,
        }, prefer=language == preferred_language)

        set_code = resolve_path(record, printing_mapping.get("set_code_field"))
        if set_code is None:
            continue
        set_id = f"{game_id}-{slug(set_code)}"
        if set_id not in catalog["sets"]:
            # No matching entry from sets_path (or no sets_path at all, e.g. flat CSV
            # sources) -- synthesize a minimal set record from the card's own set code.
            catalog["sets"][set_id] = {
                "id": set_id, "game_id": game_id, "code": str(set_code),
                "name": apply_lookup(lookup_tables, "set_name", set_code) or str(set_code),
                "set_type": "Set", "release_date": None, "printed_card_count": None,
                "classifications": [], "accent": accent, "_source_language": language,
            }

        collector_number = resolve_path(record, printing_mapping.get("collector_number_field")) or str(own_id)
        printing_id = f"{game_id}-print-{slug(set_code)}-{slug(collector_number)}-{language.lower()}"
        raw_rarity = resolve_path(record, printing_mapping.get("rarity_field"))
        rarity = apply_lookup(lookup_tables, "rarity_label", raw_rarity) or raw_rarity or "Unknown"
        printing_attrs = {attr_key: resolve_path(record, attr_path) for attr_key, attr_path in (printing_mapping.get("attributes") or {}).items()}
        catalog["printings"].setdefault(printing_id, {
            "id": printing_id, "identity_id": identity_id, "game_id": game_id, "set_id": set_id,
            "collector_number": str(collector_number), "language": language, "rarity": rarity,
            "attributes": printing_attrs,
        })

        image_url = resolve_path(record, variant_mapping.get("image_field"))
        mode = variant_mapping.get("mode", "expand_array_field")
        if mode == "group_variants_by":
            finish = resolve_path(record, variant_mapping.get("finish_field")) or "Normal"
            finishes = [finish]
        else:
            field = variant_mapping.get("field")
            finishes = (resolve_path(record, field) if field else None) or variant_mapping.get("default") or ["Normal"]
        for finish in finishes:
            is_base = finish in ("None", "Normal", None)
            code = "normal" if is_base else slug(finish)
            variant_id = f"{printing_id}-{code}"
            catalog["variants"][variant_id] = {
                "id": variant_id, "printing_id": printing_id, "game_id": game_id,
                "variant_code": code, "finish": "Normal" if is_base else finish,
                "artwork_id": str(own_id), "is_parallel": 0 if is_base else 1,
                "source_type": "declarative-provider",
                "attributes": {"imageUrl": image_url},
            }
    return catalog


def apply_overrides(catalog: dict, overrides: dict) -> None:
    for section in ("sets", "identities", "printings", "variants"):
        catalog[section].update(overrides.get(section, {}))


def run(provider_row: dict, languages: list[str]) -> dict:
    config = json.loads(provider_row["config"])
    catalog = empty_catalog()
    preferred_language = config.get("preferred_language", "EN")
    for source in config["sources"]:
        partial = build_partial_catalog(provider_row["game_id"], source, config)
        merge(catalog, partial, preferred_language=preferred_language)
    if config.get("overrides"):
        apply_overrides(catalog, config["overrides"])
    return catalog
