"""Official Ravensburger foil layer/mask lookup, download and disk cache.

Lorcana's own publisher exposes the exact foil masks/layers used on their own
app via their public catalog API (no auth, no key) --
https://api.lorcana.ravensburger.com/v3/catalog/<en|de>. Each card there
carries a `culture_invariant_id` and a `variants` array (Ravensburger's OWN
finish concept: "Regular" / "Foiled" / "StarterFoil"), and each of THOSE
sub-variants can carry foil_mask_url / foil_type / foil_top_layer /
foil_top_layer_mask_url / second_foil_top_layer_mask_url / hot_foil_color /
second_hot_foil_color.

Matching key: NOT culture_invariant_id (nothing in our own schema stores
that) -- instead `detail_image_url` on their side is BYTE-IDENTICAL to the
`imageUrl` we already store in variants.attributes (both ultimately trace
back to the same Ravensburger CDN asset; lorcanajson.org, our own catalog
source, sources its image URLs from the same place). Confirmed empirically
against a live card before writing this: variant lorcana-print-3201-en-magma
(The Madrigal Family, Enchanted) has attributes.imageUrl equal, character for
character, to the detail_image_url on culture_invariant_id 3201 in
Ravensburger's own feed.
Within one matched card, our own `finish` column (e.g. "Magma", "Silver",
"Normal") is ALSO already identical to Ravensburger's `foil_type` values --
lorcanajson's foilTypes field is itself sourced from the same enum -- so
picking the right sub-variant is a direct string comparison, no fuzzy
matching needed anywhere in this module...

This module has zero Flask/DB dependency by design (matches the
catalog_provider_contract.py convention elsewhere in this codebase) -- app.py
wires it to real variant rows via two routes; see the routes' own docstrings.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

CATALOG_URLS = {
    "en": "https://api.lorcana.ravensburger.com/v3/catalog/en",
    "de": "https://api.lorcana.ravensburger.com/v3/catalog/de",
}
CATALOG_TTL_SECONDS = 24 * 3600  # the catalog only changes on new-set-release cadence
USER_AGENT = "DeckLedger/1.0 (+local collection manager)"
# Cards live at catalog["cards"][<supertype>] -- catalog["cards"] is an OBJECT keyed by
# supertype, not a flat array (confirmed against the live payload). Four supertypes observed;
# no "songs" key -- Songs are a subtype of Action in Lorcana's own rules, filed under
# "actions" here too.
CARD_SUPERTYPE_KEYS = ("characters", "actions", "items", "locations")

# Every foil_type actually observed in the live feed (verified 2026-08-07), used purely for the
# "does this look like a real value" comment trail -- NOT for validation. An unrecognised value
# must never error (a future set could add a new type any day); see MASK_KIND_FIELDS below and
# static/app.js's FOIL_EFFECTS registry for how the "fall back to generic" requirement is met on
# each side.
KNOWN_FOIL_TYPES = {
    "Silver", "Lava", "Satin", "Glitter", "VerticalWave", "Tempest", "FreeForm1", "FreeForm2",
    "SeaWave", "Lore", "Magma", "RainbowPillars", "CalendarWave",
}
KNOWN_TOP_LAYER_TYPES = {
    "HighGloss", "RainbowHotFoil", "MetallicHotFoil", "SnowHotFoil", "ChromeRainbowHotFoil", "MatteHotFoil",
}

_catalog_cache: dict[str, tuple[float, dict]] = {}
_index_cache: dict[str, dict] = {}


def _cache_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "deckledger.db"))
    path = Path(os.path.dirname(db_path)) / "ravensburger-catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _asset_cache_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "deckledger.db"))
    path = Path(os.path.dirname(db_path)) / "ravensburger-foil-assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def load_catalog(language: str) -> dict | None:
    """Return the parsed Ravensburger catalog for `language` ("en"/"de"), fetching + disk-
    caching it if missing or stale (24h TTL -- a set-release-cadence source has no business
    being re-fetched every request; ~4MB of JSON per language). In-memory cache on top of the
    disk one so a single running process only re-parses that JSON once per TTL window, not once
    per request. Returns None (never raises) on total failure -- callers treat "no official
    data available" as a normal, expected outcome, not an error."""
    language = language.lower()
    if language not in CATALOG_URLS:
        return None
    now = time.time()
    cached = _catalog_cache.get(language)
    if cached and now - cached[0] < CATALOG_TTL_SECONDS:
        return cached[1]

    disk_path = _cache_dir() / f"{language}.json"
    if disk_path.exists() and now - disk_path.stat().st_mtime < CATALOG_TTL_SECONDS:
        try:
            data = json.loads(disk_path.read_text("utf-8"))
            _catalog_cache[language] = (now, data)
            return data
        except Exception:
            pass  # fall through to a real re-fetch, disk copy is corrupt/unreadable

    try:
        data = _fetch_json(CATALOG_URLS[language])
    except Exception:
        # Fetch failed -- serve a stale disk copy if one exists rather than going fully dark.
        if disk_path.exists():
            try:
                data = json.loads(disk_path.read_text("utf-8"))
                _catalog_cache[language] = (now, data)
                return data
            except Exception:
                return None
        return None

    try:
        disk_path.write_text(json.dumps(data), "utf-8")
    except Exception:
        pass  # cache write failing shouldn't fail the request -- in-memory copy still works
    _index_cache.pop(language, None)  # invalidate the derived lookup index, rebuilt lazily below
    _catalog_cache[language] = (now, data)
    return data


def _build_index(language: str) -> dict:
    """detail_image_url -> card dict, covering every sub-variant's own URL (in practice all of
    a card's sub-variants share one URL, but indexing each individually costs nothing and
    doesn't rely on that always holding)."""
    catalog = load_catalog(language)
    index: dict = {}
    if not catalog:
        return index
    cards_by_supertype = catalog.get("cards") or {}
    for key in CARD_SUPERTYPE_KEYS:
        for card in cards_by_supertype.get(key, []):
            urls = set()
            thumb = card.get("thumbnail_url")
            if thumb:
                urls.add(thumb)
            for variant in card.get("variants", []):
                image_url = variant.get("detail_image_url")
                if image_url:
                    urls.add(image_url)
            for url in urls:
                index[url] = card
    return index


def _index_for(language: str) -> dict:
    language = language.lower()
    load_catalog(language)  # ensures freshness / populates _catalog_cache first
    if language not in _index_cache:
        _index_cache[language] = _build_index(language)
    return _index_cache[language]


def find_official_variant(image_url: str, language: str, finish: str) -> dict | None:
    """The core lookup: given OUR variant's own stored imageUrl + language + finish (e.g.
    "Magma", "Silver", "Normal"), return Ravensburger's matching sub-variant dict (with
    foil_mask_url / foil_type / foil_top_layer / ... whichever of those it actually has), or
    None if this exact card+finish isn't in their feed (a different game entirely, a printing
    Ravensburger doesn't carry foil data for, or just "Normal" with nothing special to show)."""
    if not image_url:
        return None
    card = _index_for(language).get(image_url)
    if not card:
        return None
    variants = card.get("variants", [])
    if finish == "Normal":
        for v in variants:
            if v.get("variant_id") == "Regular" and not v.get("foil_type"):
                return v
        return None
    for v in variants:
        if v.get("foil_type") == finish:
            return v
    return None


MASK_KIND_FIELDS = {
    "base": "foil_mask_url",
    "top": "foil_top_layer_mask_url",
    "top2": "second_foil_top_layer_mask_url",
}


def cached_asset(url: str) -> Path | None:
    """Generic download-once-then-serve-from-disk cache for a Ravensburger CDN asset URL --
    same sha256-of-url cache-key convention as app.py's own cached_real_image, deliberately not
    sharing that function directly since this module stays Flask/DB-free (see module docstring)."""
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = _asset_cache_dir() / f"{cache_key}.jpg"
    if path.exists():
        return path
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
        with urlopen(request, timeout=30) as response:
            payload = response.read(8_000_000)
    except Exception:
        return None
    if len(payload) < 200:
        return None
    path.write_bytes(payload)
    return path
