"""What a database is seeded with, and bringing it up to date at start-up. The schema itself
is defined by the migrations in deckledger/migrations."""

import json
import os
import sqlite3

from werkzeug.security import generate_password_hash

from catalog_provider_contract import digest

from .config import DB_PATH, ROOT, now_iso
from .games import GAMES
from .migrations import migrate


GAME_COLUMNS = "id, module_id, name, short_name, module_version, languages, accent, enabled"


def default_provider_code(game_id: str) -> str:
    path = ROOT / "providers" / f"{game_id.replace('-', '_')}.py"
    return path.read_text(encoding="utf-8")


def seed_default_providers(connection):
    # The provider's code lives in providers/<id>.py -- read at seed time, not embedded, so it
    # stays normal, lintable Python in the repository.
    for game_id, (minimum_sets, minimum_cards, timeout_seconds) in ((game.id, game.provider) for game in GAMES.values() if game.provider):
        code = default_provider_code(game_id)
        version = digest(code)
        existing = connection.execute("SELECT kind,code,customized FROM catalog_providers WHERE id=?", (game_id,)).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO catalog_providers
                   (id, game_id, label, kind, code, minimum_sets, minimum_cards, timeout_seconds, provider_version, enabled, created_at, updated_at)
                   VALUES (?,?,?,'custom_code',?,?,?,?,?,1,?,?)""",
                (game_id, game_id, game_id, code, minimum_sets, minimum_cards, timeout_seconds, version, now_iso(), now_iso()),
            )
        elif existing[0] != "custom_code":
            # One-time migration from the former hardcoded/builtin dispatch --
            # never touches a row an admin has already converted or edited.
            connection.execute(
                "UPDATE catalog_providers SET kind='custom_code', code=?, provider_version=?, updated_at=? WHERE id=?",
                (code, version, now_iso(), game_id),
            )
        elif existing[1] != code and not existing[2]:
            # The database copy is what actually runs. Unless an admin replaced it with their own
            # code (customized=1, see admin_update_provider), it follows providers/<id>.py, so a
            # provider fix shipped with a new image takes effect; the changed provider_version
            # then triggers a re-import on the next catalog_sync --if-needed.
            connection.execute(
                "UPDATE catalog_providers SET code=?, provider_version=?, updated_at=? WHERE id=?",
                (code, version, now_iso(), game_id),
            )


def seed_default_deck_rulesets(connection):
    for game in GAMES.values():
        if game.ruleset:
            connection.execute("UPDATE games SET deck_ruleset=? WHERE id=? AND deck_ruleset IS NULL", (game.ruleset, game.id))


def seed_default_price_methods(connection):
    for game in GAMES.values():
        if game.price_method:
            connection.execute("UPDATE games SET price_method=? WHERE id=? AND price_method IS NULL", (game.price_method, game.id))
        for language, method in game.price_overrides:
            connection.execute(
                "INSERT OR IGNORE INTO game_price_overrides(game_id, language, price_method) VALUES(?,?,?)",
                (game.id, language, method),
            )


def seed_default_cardmarket_game_ids(connection):
    # Cardmarket's numeric idGame is stable but has no discovery endpoint: for a game without a
    # module it is a one-time lookup on cardmarket.com, entered in the admin UI.
    for game in GAMES.values():
        if game.cardmarket_game_id:
            connection.execute(
                "UPDATE games SET cardmarket_game_id=? WHERE id=? AND cardmarket_game_id IS NULL", (game.cardmarket_game_id, game.id),
            )


def seed_database(connection):
    game_rows = [
        (game.id, game.module_id, game.name, game.short_name, game.module_version, json.dumps(list(game.languages)), game.accent)
        for game in GAMES.values()
    ]
    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        connection.executemany(f"INSERT OR IGNORE INTO games({GAME_COLUMNS}) VALUES(?,?,?,?,?,?,?,1)", game_rows)
        seed_default_providers(connection)
        seed_default_deck_rulesets(connection)
        seed_default_price_methods(connection)
        seed_default_cardmarket_game_ids(connection)
        return
    connection.executemany(
        "INSERT INTO users(username, display_name, password_hash, role, created_at) VALUES(?,?,?,?,?)",
        [("admin", "DeckLedger Admin", generate_password_hash("admin"), "admin", now_iso())],
    )
    connection.executemany(f"INSERT INTO games({GAME_COLUMNS}) VALUES(?,?,?,?,?,?,?,1)", game_rows)
    seed_default_providers(connection)
    seed_default_deck_rulesets(connection)
    seed_default_price_methods(connection)
    seed_default_cardmarket_game_ids(connection)


def ensure_user_lists(connection, user_id=None):
    """Every account has a default watchlist for every game. Idempotent; runs for all accounts at
    start-up and for one account right after it is created -- an account made while the app is
    running (admin, SSO auto-provisioning) used to have none until the next restart, so its first
    "add to watchlist" failed."""
    stamp = now_iso()
    scope, args = ("WHERE u.id=?", (user_id,)) if user_id else ("", ())
    connection.execute(f"""INSERT OR IGNORE INTO named_watchlists(user_id,game_id,name,is_default,created_at)
      SELECT u.id,g.id,'Merkliste',1,? FROM users u CROSS JOIN games g {scope}""", (stamp, *args))


def init_database():
    """Brings the database to the current schema (deckledger/migrations), then makes sure the
    data that follows the code -- games, their providers and defaults, every account's default
    lists -- is there. Runs at import, in every process that loads the app."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        migrate(connection, DB_PATH)
        # Gunicorn workers can import the app concurrently on a fresh volume.
        # Serialize the one-time seed so both workers never insert the first admin.
        connection.execute("BEGIN IMMEDIATE")
        seed_database(connection)
        ensure_user_lists(connection)
        connection.commit()
    finally:
        connection.close()
