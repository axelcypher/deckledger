"""Loads registered catalog providers and dispatches each to the function that
produces its catalog dict, regardless of whether it's a first-party builtin
importer, a declarative field-mapping config, or admin-supplied custom code.

Kept separate from catalog_sync.py so a future custom-code subprocess runner
can depend on this module without pulling in the whole Flask/app.py stack.
"""

from __future__ import annotations

import importlib
import json
import sqlite3


def load_enabled_providers(connection: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in connection.execute("SELECT * FROM catalog_providers WHERE enabled=1")]


def get_provider(connection: sqlite3.Connection, provider_id: str) -> dict | None:
    row = connection.execute("SELECT * FROM catalog_providers WHERE id=?", (provider_id,)).fetchone()
    return dict(row) if row else None


def provider_already_current(row: dict) -> bool:
    return bool(row["last_synced_version"]) and row["last_synced_version"] == row["provider_version"]


def dispatch_provider(row: dict, languages: list[str]) -> dict:
    if row["kind"] == "builtin":
        # Lazy import: catalog_sync.py itself calls into this module, so a
        # module-level `import catalog_sync` here would be circular. By the
        # time a provider is actually dispatched, catalog_sync is already
        # fully loaded and sits in sys.modules, so this resolves cleanly.
        module_name, func_name = row["entrypoint"].split(":")
        module = importlib.import_module(module_name)
        return getattr(module, func_name)()
    if row["kind"] == "declarative":
        from catalog_provider_standard import run as run_standard
        return run_standard(row, languages)
    if row["kind"] == "custom_code":
        raise NotImplementedError("Custom-Code-Provider (Tier 2) sind noch nicht implementiert.")
    raise RuntimeError(f"Unbekannter Provider-Typ: {row['kind']}")


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
