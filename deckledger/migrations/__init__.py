"""Numbered schema migrations.

The schema is whatever the migrations in MIGRATIONS produce, applied in order; nothing else
creates or alters tables. Which ones a database has had is recorded in schema_migrations, so each
runs exactly once -- a migration can therefore rename, drop and rewrite without first checking
what state it finds.

To change the schema, add a module mNNNN_<what>.py with NAME and apply(connection, context) and
list it below. Never edit one that has been released: databases that already ran it would not get
the change.
"""

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..config import now_iso
from . import m0001_baseline, m0002_sale_lists_to_sheets, m0003_sheet_backgrounds, m0004_drop_classic_theme_setting, m0005_sheet_posts, m0006_communities, m0007_deals

MIGRATIONS = [m0001_baseline, m0002_sale_lists_to_sheets, m0003_sheet_backgrounds, m0004_drop_classic_theme_setting, m0005_sheet_posts, m0006_communities, m0007_deals]

BACKUPS_KEPT = 2


@dataclass
class Context:
    """What a migration gets besides the connection."""
    connection: sqlite3.Connection
    now: str

    def run_script(self, script):
        """Runs several statements inside the migration's transaction. (sqlite3's own
        executescript() would commit it first.)"""
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                self.connection.execute(statement)
                statement = ""
        if statement.strip():
            raise ValueError(f"incomplete statement at the end of the script: {statement.strip()[:80]}")


def applied_versions(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    return {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}


def status(connection):
    """(version the database is at, version the code expects)."""
    return max(applied_versions(connection), default=0), len(MIGRATIONS)


def back_up(connection, database_path, version):
    """A copy of the database as it was before migrating, next to it. Only the newest few stay."""
    database_path = Path(database_path)
    target = database_path.with_name(f"{database_path.name}.before-migration-{version + 1:04d}")
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    copy = sqlite3.connect(temporary)
    try:
        connection.backup(copy)
    finally:
        copy.close()
    os.replace(temporary, target)
    older = sorted(database_path.parent.glob(f"{database_path.name}.before-migration-[0-9][0-9][0-9][0-9]"))
    for path in older[:-BACKUPS_KEPT]:
        path.unlink(missing_ok=True)
    return target


def migrate(connection, database_path=None, migrations=None, log=print):
    """Applies every migration the database has not had, each in its own transaction: it either
    happens completely and is recorded, or leaves no trace. Returns the versions applied.

    Safe to call from several processes at once (gunicorn workers start together): each step
    takes the write lock before it looks at what is left to do.
    """
    migrations = MIGRATIONS if migrations is None else migrations
    isolation_level, connection.isolation_level = connection.isolation_level, None   # explicit BEGIN/COMMIT below
    done = []
    try:
        pending = set(range(1, len(migrations) + 1)) - applied_versions(connection)
        holds_data = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
        if pending and holds_data and database_path:
            log(f"Datenbank-Migration: Sicherung unter {back_up(connection, database_path, min(pending) - 1)}")
        while True:
            connection.execute("BEGIN IMMEDIATE")
            try:
                applied = applied_versions(connection)
                if applied - set(range(1, len(migrations) + 1)):
                    raise RuntimeError(
                        f"Die Datenbank ist auf Schema-Version {max(applied)}, diese Programmversion kennt nur {len(migrations)}. "
                        "Sie wurde von einer neueren Version geschrieben; ein älteres Image darf sie nicht öffnen."
                    )
                version = next((number for number in range(1, len(migrations) + 1) if number not in applied), None)
                if version is None:
                    connection.execute("COMMIT")
                    return done
                migration = migrations[version - 1]
                stamp = now_iso()
                migration.apply(connection, Context(connection, stamp))
                connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)", (version, migration.NAME, stamp))
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            done.append(version)
            log(f"Datenbank-Migration {version:04d} angewendet: {migration.NAME}")
    finally:
        connection.isolation_level = isolation_level
