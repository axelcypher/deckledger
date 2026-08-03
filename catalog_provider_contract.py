"""Shared, dependency-light building blocks for producing a catalog dict.

Deliberately has zero dependency on app.py or catalog_sync.py (which pulls in
the whole Flask app + triggers init_database() at import time) so that any
provider-side code — the Tier-1 declarative interpreter, and eventually a
Tier-2 custom-code subprocess — can use these helpers without that overhead.
catalog_sync.py imports these back so its own builtin importers keep working
unchanged.

The contract every provider must produce: a dict with exactly five keys
(sets, identities, printings, variants, sources), each itself a dict keyed by
record id. See the project plan for the per-record-type field shapes.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from urllib.request import Request, urlopen

USER_AGENT = "DeckLedger/1.0 (+local collection manager)"


def fetch(url: str, attempts: int = 3, headers: dict | None = None) -> str:
    error = None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json", **(headers or {})}
    for attempt in range(attempts):
        try:
            request = Request(url, headers=request_headers)
            with urlopen(request, timeout=40) as response:
                return response.read().decode("utf-8", "ignore")
        except Exception as exc:  # Network failures are retried, then fail the sync.
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Quelle nicht erreichbar: {url}: {error}")


def fetch_bytes(url: str, attempts: int = 3, headers: dict | None = None) -> bytes:
    error = None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,image/*,*/*", **(headers or {})}
    for attempt in range(attempts):
        try:
            request = Request(url, headers=request_headers)
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


def empty_catalog() -> dict:
    return {"sets": {}, "identities": {}, "printings": {}, "variants": {}, "sources": {}}


def put_identity(catalog: dict, record: dict, prefer: bool = False) -> None:
    old = catalog["identities"].get(record["id"])
    if old is None or prefer:
        catalog["identities"][record["id"]] = record


def merge(target: dict, source: dict, *, preferred_language: str = "EN") -> None:
    for key, incoming in source["sets"].items():
        existing = target["sets"].get(key)
        if existing is None or incoming.get("_source_language") == preferred_language or existing.get("_source_language") != preferred_language:
            target["sets"][key] = incoming
    for key, incoming in source["identities"].items():
        existing = target["identities"].get(key)
        incoming_language = incoming.get("attributes", {}).get("sourceLanguage")
        existing_language = (existing or {}).get("attributes", {}).get("sourceLanguage")
        if existing is None or incoming_language == preferred_language or existing_language != preferred_language:
            target["identities"][key] = incoming
    for key, incoming in source["printings"].items():
        existing = target["printings"].get(key)
        incoming_virtual = incoming.get("attributes", {}).get("virtualPoolSource", False)
        existing_virtual = (existing or {}).get("attributes", {}).get("virtualPoolSource", False)
        if existing is None or (existing_virtual and not incoming_virtual):
            target["printings"][key] = incoming
    for section in ("variants", "sources"):
        target[section].update(source[section])


def fill_missing_printed_card_counts(catalog: dict) -> None:
    """A provider's own source may not carry an explicit per-set card count.
    Derive it generically (EN printings per set) so every provider gets a
    sensible value without needing to compute it itself, mirroring what the
    Lorcana builtin importer already does by hand."""
    counts: dict[str, int] = {}
    for printing in catalog["printings"].values():
        if printing["language"] == "EN":
            counts[printing["set_id"]] = counts.get(printing["set_id"], 0) + 1
    for set_id, record in catalog["sets"].items():
        if not record.get("printed_card_count"):
            record["printed_card_count"] = counts.get(set_id, 0)
