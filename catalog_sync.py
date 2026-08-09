"""Orchestrates catalog providers and writes their combined output atomically.

Every provider (including the three DeckLedger ships with, under providers/)
is a Tier-2 custom-code provider dispatched through catalog_provider_registry.
This module only validates, merges, and persists what a run produced -- it
never fetches or scrapes anything itself. The importer downloads and
validates every source before opening the database transaction, so a failed
or incomplete upstream response can never leave the application with a
half-written catalogue.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

from app import DB_PATH, SCHEMA
from catalog_provider_contract import empty_catalog, fill_missing_printed_card_counts, merge
from catalog_provider_registry import dispatch_provider, get_provider, load_enabled_providers, mark_provider_result, provider_already_current


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate(catalog: dict, provider_minimums: dict[str, tuple[int, int]]) -> None:
    game_ids = {record["game_id"] for section in ("sets", "identities", "printings", "variants") for record in catalog[section].values()}
    counts = {}
    for game_id in game_ids:
        counts[game_id] = {
            "sets": sum(1 for x in catalog["sets"].values() if x["game_id"] == game_id),
            "cards": sum(1 for x in catalog["identities"].values() if x["game_id"] == game_id),
            "printings": sum(1 for x in catalog["printings"].values() if x["game_id"] == game_id),
            "variants": sum(1 for x in catalog["variants"].values() if x["game_id"] == game_id),
        }
    print(json.dumps(counts, indent=2), flush=True)
    for game_id, (minimum_sets, minimum_cards) in provider_minimums.items():
        found = counts.get(game_id, {"sets": 0, "cards": 0})
        if found["sets"] < minimum_sets or found["cards"] < minimum_cards:
            raise RuntimeError(f"Validierung fehlgeschlagen: {game_id} ist unvollständig ({found})")
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
            """INSERT INTO collection_entries
                 (user_id,variant_id,condition,quantity,notes,is_graded,grade_label,price_override,created_at,last_added_at)
               SELECT user_id,?,condition,quantity,notes,is_graded,grade_label,price_override,created_at,last_added_at
               FROM collection_entries WHERE variant_id=?
               ON CONFLICT(user_id,variant_id,condition,is_graded,grade_label) DO UPDATE SET
                 quantity=collection_entries.quantity+excluded.quantity,
                 notes=COALESCE(collection_entries.notes,excluded.notes),
                 price_override=COALESCE(collection_entries.price_override,excluded.price_override),
                 created_at=MIN(collection_entries.created_at,excluded.created_at),
                 last_added_at=MAX(collection_entries.last_added_at,excluded.last_added_at)""",
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


def write_database(catalog: dict, fetched_game_ids: set[str]) -> None:
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys=ON")
    # A web request writing at the same instant (e.g. an admin editing a deck)
    # would otherwise fail immediately with "database is locked" rather than
    # one side briefly waiting -- this run holds a write lock for its whole
    # multi-table BEGIN IMMEDIATE transaction below.
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(SCHEMA)
    connection.execute("BEGIN IMMEDIATE")
    try:
        previous_format = connection.execute("SELECT value FROM catalog_metadata WHERE key='catalog_format'").fetchone()
        previous_version = connection.execute("SELECT value FROM catalog_metadata WHERE key='catalog_version'").fetchone()
        upgrading_real_catalog = bool(
            (previous_format and json.loads(previous_format[0]) == "real-catalog")
            # Back-compat for databases from before per-provider versioning existed.
            or (previous_version and isinstance(json.loads(previous_version[0]), str) and json.loads(previous_version[0]).startswith("real-catalog-"))
        )
        if not upgrading_real_catalog:
            # This is the one-time transition away from the former fabricated IDs.
            for table in (
                "deck_cards", "decks", "named_watchlist_entries", "watchlist_entries",
                "collection_entries", "import_operations", "marketplace_products", "price_observations", "variants",
                "printings", "card_identities", "sets",
            ):
                connection.execute(f"DELETE FROM {table}")
        connection.executemany(
            """INSERT INTO sets VALUES(?,?,?,?,?,?,?,?,?,'imported') ON CONFLICT(id) DO UPDATE SET
               game_id=excluded.game_id,code=excluded.code,name=excluded.name,set_type=excluded.set_type,
               release_date=excluded.release_date,printed_card_count=excluded.printed_card_count,
               classifications=excluded.classifications,accent=excluded.accent""",
            [(x["id"], x["game_id"], x["code"], x["name"], x["set_type"], x["release_date"], x["printed_card_count"], json.dumps(x["classifications"], ensure_ascii=False), x["accent"]) for x in catalog["sets"].values()],
        )
        connection.executemany(
            """INSERT INTO card_identities VALUES(?,?,?,?,?,?,'imported') ON CONFLICT(id) DO UPDATE SET
               game_id=excluded.game_id,canonical_name=excluded.canonical_name,rules_text=excluded.rules_text,
               card_type=excluded.card_type,attributes=excluded.attributes""",
            [(x["id"], x["game_id"], x["canonical_name"], x["rules_text"], x["card_type"], json.dumps(x["attributes"], ensure_ascii=False)) for x in catalog["identities"].values()],
        )
        connection.executemany(
            """INSERT INTO printings VALUES(?,?,?,?,?,?,?,?,'imported') ON CONFLICT(id) DO UPDATE SET
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
            if fetched_game_ids:
                # Providers that did not run this pass (untouched games) and manually
                # entered records must never be pruned just because their game's catalog
                # dict is absent from this run's `catalog` argument.
                placeholders = ",".join("?" for _ in fetched_game_ids)
                for temp_table, source_table in (
                    ("incoming_variants", "variants"), ("incoming_printings", "printings"),
                    ("incoming_identities", "card_identities"), ("incoming_sets", "sets"),
                ):
                    connection.execute(
                        f"""INSERT OR IGNORE INTO {temp_table}(id)
                            SELECT id FROM {source_table}
                            WHERE game_id NOT IN ({placeholders}) OR source_type='manual-override'""",
                        tuple(fetched_game_ids),
                    )
                for table in ("deck_cards", "named_watchlist_entries", "watchlist_entries", "collection_entries", "marketplace_products", "price_observations"):
                    connection.execute(f"DELETE FROM {table} WHERE variant_id NOT IN (SELECT id FROM incoming_variants)")
                connection.execute("DELETE FROM variants WHERE id NOT IN (SELECT id FROM incoming_variants)")
                connection.execute("DELETE FROM printings WHERE id NOT IN (SELECT id FROM incoming_printings)")
                connection.execute("DELETE FROM card_identities WHERE id NOT IN (SELECT id FROM incoming_identities)")
                connection.execute("DELETE FROM sets WHERE id NOT IN (SELECT id FROM incoming_sets)")
        metadata = {
            "catalog_format": "real-catalog",
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


def run_single_provider(connection: sqlite3.Connection, provider_id: str) -> int:
    provider = get_provider(connection, provider_id)
    if not provider:
        print(f"Unbekannter Provider: {provider_id}", file=sys.stderr, flush=True)
        return 1
    now = iso_now()
    try:
        catalog = dispatch_provider(provider)
        fill_missing_printed_card_counts(catalog)
        validate(catalog, {provider["game_id"]: (provider["minimum_sets"] or 0, provider["minimum_cards"] or 0)})
        write_database(catalog, {provider["game_id"]})
        counts = {"sets": len(catalog["sets"]), "cards": len(catalog["identities"])}
        mark_provider_result(connection, provider_id, ok=True, now=now, summary=counts)
        connection.commit()
        print(f"{provider['label']}: Import erfolgreich.", flush=True)
        return 0
    except Exception as exc:
        mark_provider_result(connection, provider_id, ok=False, now=now, error=str(exc))
        connection.commit()
        print(f"{provider['label']}: Import fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Real card catalogue sync")
    parser.add_argument("--if-needed", action="store_true", help="skip providers already imported at their current version")
    parser.add_argument("--provider", help="run exactly one provider id, ignoring staleness")
    args = parser.parse_args()

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        if args.provider:
            return run_single_provider(connection, args.provider)

        providers = load_enabled_providers(connection)
        stale = [p for p in providers if not (args.if_needed and provider_already_current(p))]
        if args.if_needed and not stale:
            print("Alle Kataloge sind aktuell.", flush=True)
            return 0

        combined = empty_catalog()
        fetched_game_ids = set()
        provider_minimums = {}
        for provider in stale:
            print(f"{provider['label']}: Import startet …", flush=True)
            catalog = dispatch_provider(provider)
            merge(combined, catalog)
            fetched_game_ids.add(provider["game_id"])
            provider_minimums[provider["game_id"]] = (provider["minimum_sets"] or 0, provider["minimum_cards"] or 0)

        fill_missing_printed_card_counts(combined)
        validate(combined, provider_minimums)
        write_database(combined, fetched_game_ids)
        now = iso_now()
        for provider in stale:
            counts = {
                "sets": sum(1 for x in combined["sets"].values() if x["game_id"] == provider["game_id"]),
                "cards": sum(1 for x in combined["identities"].values() if x["game_id"] == provider["game_id"]),
            }
            mark_provider_result(connection, provider["id"], ok=True, now=now, summary=counts)
        connection.commit()
        print("Der reale Kartenkatalog wurde atomar gespeichert.", flush=True)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Katalogimport fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
        raise
