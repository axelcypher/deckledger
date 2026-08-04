"""Loads registered catalog providers and dispatches each to its custom-code
subprocess. Every provider -- including the ones DeckLedger ships with -- is
`kind='custom_code'`: one uniform system instead of a hardcoded/builtin path
plus a separate declarative interpreter.

Kept separate from catalog_sync.py so the subprocess runner can depend on
this module without pulling in the whole Flask/app.py stack.
"""

from __future__ import annotations

import json
import sqlite3

from catalog_provider_runner import run_custom_code


def load_enabled_providers(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute("SELECT * FROM catalog_providers WHERE enabled=1")]


def get_provider(connection: sqlite3.Connection, provider_id: str) -> dict | None:
    row = connection.execute("SELECT * FROM catalog_providers WHERE id=?", (provider_id,)).fetchone()
    return dict(row) if row else None


def provider_already_current(row: dict) -> bool:
    return bool(row["last_synced_version"]) and row["last_synced_version"] == row["provider_version"]


def dispatch_provider(row: dict) -> dict:
    if row["kind"] != "custom_code":
        raise RuntimeError(f"Unbekannter Provider-Typ: {row['kind']}")
    return run_custom_code(row["code"], row["timeout_seconds"])


def mark_provider_result(connection: sqlite3.Connection, provider_id: str, *, ok: bool, now: str, summary: dict | None = None, error: str | None = None) -> None:
    if ok:
        connection.execute(
            """UPDATE catalog_providers SET last_synced_version=provider_version, last_run_at=?, last_status='ok',
               last_summary=?, last_error=NULL, updated_at=? WHERE id=?""",
            (now, json.dumps(summary or {}, ensure_ascii=False), now, provider_id),
        )
    else:
        connection.execute(
            "UPDATE catalog_providers SET last_run_at=?, last_status='error', last_error=?, updated_at=? WHERE id=?",
            (now, error, now, provider_id),
        )
