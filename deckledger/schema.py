"""Database schema, start-up migrations and the data a fresh database is seeded with."""

import json
import os
import sqlite3

from werkzeug.security import generate_password_hash

from catalog_provider_contract import digest

from .config import DB_PATH, ROOT, now_iso
from .games import GAMES, playset_size


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '', oauth_provider TEXT NOT NULL DEFAULT '', oauth_subject TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY, module_id TEXT NOT NULL, name TEXT NOT NULL, short_name TEXT NOT NULL,
  module_version TEXT NOT NULL, languages TEXT NOT NULL, accent TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  rarity_order TEXT NOT NULL DEFAULT '{}', price_method TEXT, deck_ruleset TEXT, cardmarket_game_id INTEGER
);
CREATE TABLE IF NOT EXISTS sets (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), code TEXT NOT NULL, name TEXT NOT NULL,
  set_type TEXT NOT NULL, release_date TEXT, printed_card_count INTEGER, classifications TEXT NOT NULL,
  accent TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'imported'
);
CREATE TABLE IF NOT EXISTS card_identities (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), canonical_name TEXT NOT NULL,
  rules_text TEXT, card_type TEXT, attributes TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'imported'
);
CREATE TABLE IF NOT EXISTS printings (
  id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES card_identities(id), game_id TEXT NOT NULL REFERENCES games(id),
  set_id TEXT NOT NULL REFERENCES sets(id), collector_number TEXT NOT NULL, language TEXT NOT NULL,
  rarity TEXT NOT NULL, attributes TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'imported'
);
CREATE TABLE IF NOT EXISTS variants (
  id TEXT PRIMARY KEY, printing_id TEXT NOT NULL REFERENCES printings(id), game_id TEXT NOT NULL REFERENCES games(id),
  variant_code TEXT NOT NULL, finish TEXT NOT NULL, artwork_id TEXT, is_parallel INTEGER NOT NULL DEFAULT 0,
  source_type TEXT NOT NULL DEFAULT 'imported', attributes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, variant_id TEXT NOT NULL REFERENCES variants(id), provider_id TEXT NOT NULL,
  metric TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marketplace_products (
  provider_id TEXT NOT NULL, external_product_id TEXT NOT NULL,
  variant_id TEXT NOT NULL REFERENCES variants(id), game_id TEXT NOT NULL REFERENCES games(id),
  source_url TEXT NOT NULL, match_method TEXT NOT NULL, matched_at TEXT NOT NULL, attributes TEXT NOT NULL,
  PRIMARY KEY(provider_id, variant_id)
);
CREATE TABLE IF NOT EXISTS collection_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  variant_id TEXT NOT NULL REFERENCES variants(id), condition TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
  notes TEXT, is_graded INTEGER NOT NULL DEFAULT 0, grade_label TEXT NOT NULL DEFAULT '', price_override REAL,
  created_at TEXT NOT NULL DEFAULT '', last_added_at TEXT NOT NULL DEFAULT '',
  UNIQUE(user_id, variant_id, condition, is_graded, grade_label)
);
CREATE TABLE IF NOT EXISTS watchlist_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  variant_id TEXT NOT NULL REFERENCES variants(id), created_at TEXT NOT NULL, UNIQUE(user_id, variant_id)
);
CREATE TABLE IF NOT EXISTS named_watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  game_id TEXT NOT NULL REFERENCES games(id), name TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, UNIQUE(user_id, game_id, name)
);
CREATE TABLE IF NOT EXISTS named_watchlist_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, list_id INTEGER NOT NULL REFERENCES named_watchlists(id) ON DELETE CASCADE,
  variant_id TEXT NOT NULL REFERENCES variants(id), created_at TEXT NOT NULL, UNIQUE(list_id, variant_id)
);
CREATE TABLE IF NOT EXISTS decks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  game_id TEXT NOT NULL REFERENCES games(id), name TEXT NOT NULL, format_id TEXT NOT NULL,
  notes TEXT, cover_variant_id TEXT REFERENCES variants(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deck_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  variant_id TEXT NOT NULL REFERENCES variants(id), zone TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
  UNIQUE(deck_id, variant_id, zone)
);
CREATE TABLE IF NOT EXISTS import_operations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  game_id TEXT NOT NULL, source_text TEXT NOT NULL, changes TEXT NOT NULL, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id INTEGER NOT NULL REFERENCES users(id), key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(user_id, key)
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_metadata (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_providers (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), label TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('custom_code')),
  code TEXT,
  minimum_sets INTEGER NOT NULL DEFAULT 0, minimum_cards INTEGER NOT NULL DEFAULT 0,
  timeout_seconds INTEGER NOT NULL DEFAULT 300,
  provider_version TEXT NOT NULL, last_synced_version TEXT,
  last_run_at TEXT, last_status TEXT, last_summary TEXT, last_error TEXT,
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  customized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS applied_requests (
  user_id INTEGER NOT NULL REFERENCES users(id), request_id TEXT NOT NULL, response TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(user_id, request_id)
);
CREATE TABLE IF NOT EXISTS trade_sheets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  game_id TEXT NOT NULL REFERENCES games(id), name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'WTS',
  subtitle TEXT NOT NULL DEFAULT '', background TEXT NOT NULL DEFAULT 'midnight',
  sort TEXT NOT NULL DEFAULT 'number', layout TEXT NOT NULL DEFAULT 'auto',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_sheet_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sheet_id INTEGER NOT NULL REFERENCES trade_sheets(id) ON DELETE CASCADE,
  variant_id TEXT NOT NULL REFERENCES variants(id), quantity INTEGER NOT NULL DEFAULT 1,
  label TEXT NOT NULL DEFAULT '', UNIQUE(sheet_id, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_trade_sheets_user_game ON trade_sheets(user_id,game_id);
CREATE TABLE IF NOT EXISTS game_price_overrides (
  game_id TEXT NOT NULL REFERENCES games(id), language TEXT NOT NULL, price_method TEXT NOT NULL,
  PRIMARY KEY (game_id, language)
);
CREATE INDEX IF NOT EXISTS idx_catalog_providers_game ON catalog_providers(game_id);
CREATE INDEX IF NOT EXISTS idx_printings_set ON printings(set_id);
CREATE INDEX IF NOT EXISTS idx_printings_identity_language ON printings(identity_id,language,set_id);
-- Matches parse_import()/parse_json_backup()'s "UPPER(p.collector_number)=UPPER(?) AND
-- p.language=?" import-matching lookup exactly (a plain index on collector_number can't be used
-- for a function-wrapped comparison like UPPER(column)=?, only an expression index on the same
-- expression can). Without this, those two queries fall back to scanning the entire printings
-- (or, pre-ANALYZE, variants) table per row -- 6-14ms per line even indexed on game_id alone,
-- which is what actually made a several-hundred-line import take upwards of ten seconds.
CREATE INDEX IF NOT EXISTS idx_printings_number_language ON printings(UPPER(collector_number),language);
CREATE INDEX IF NOT EXISTS idx_variants_printing ON variants(printing_id);
CREATE INDEX IF NOT EXISTS idx_marketplace_variant ON marketplace_products(variant_id);
CREATE INDEX IF NOT EXISTS idx_prices_variant_metric ON price_observations(variant_id, provider_id, metric, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_variants_game ON variants(game_id);
CREATE INDEX IF NOT EXISTS idx_collection_user ON collection_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_named_watchlists_user_game ON named_watchlists(user_id,game_id);
CREATE INDEX IF NOT EXISTS idx_decks_user_game ON decks(user_id,game_id);
"""


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
        [
            ("demo", "Alex Morgan", generate_password_hash("deckledger"), "user", now_iso()),
            ("admin", "DeckLedger Admin", generate_password_hash("admin"), "admin", now_iso()),
        ],
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


SALE_LIST_NAME = "Verkaufsliste"


def migrate_sale_lists(connection):
    """The fixed per-game "Verkaufsliste" among the watchlists was the forerunner of the trade
    sheets (is_sale_list=1; its entries were tagged 'auto' when the app had put them there for
    stock beyond a playset, 'manual' otherwise). Every such list that holds cards becomes a WTS
    sheet of the same name with the same cards and quantities, then the list is removed. An 'auto'
    entry is only taken over while its card is still surplus -- the list dropped those the next
    time it was opened.

    List names are unique per account and game, so where an account already had a list of its own
    called "Verkaufsliste" the fixed one was never created next to it and that list served as the
    sale list. It is taken over the same way -- once, marked in app_settings, so a watchlist given
    that name later stays a watchlist.

    Runs inside init_database's start-up transaction, so concurrently starting workers do it once."""
    stamp = now_iso()
    by_name = not connection.execute("SELECT 1 FROM app_settings WHERE key='sale_lists_migrated'").fetchone()
    connection.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('sale_lists_migrated',?)", (stamp,))
    for list_id, uid, game_id, name, created_at in connection.execute(
        "SELECT id,user_id,game_id,name,created_at FROM named_watchlists WHERE is_sale_list=1 OR (? AND is_default=0 AND name=?) ORDER BY id",
        (by_name, SALE_LIST_NAME),
    ).fetchall():
        entries = connection.execute(
            """SELECT e.variant_id,e.quantity FROM named_watchlist_entries e WHERE e.list_id=? AND (e.source!='auto' OR
                 (SELECT COALESCE(SUM(c.quantity),0) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=e.variant_id)>?)
               ORDER BY e.id""", (list_id, uid, playset_size(game_id)),
        ).fetchall()
        if entries:
            sheet_id = connection.execute(
                "INSERT INTO trade_sheets(user_id,game_id,name,kind,created_at,updated_at) VALUES(?,?,?,'WTS',?,?)",
                (uid, game_id, name, created_at or stamp, stamp),
            ).lastrowid
            connection.executemany(
                "INSERT OR IGNORE INTO trade_sheet_cards(sheet_id,variant_id,quantity) VALUES(?,?,?)",
                [(sheet_id, variant_id, max(1, min(99, quantity))) for variant_id, quantity in entries],
            )
        connection.execute("DELETE FROM named_watchlist_entries WHERE list_id=?", (list_id,))
        connection.execute("DELETE FROM named_watchlists WHERE id=?", (list_id,))


def init_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(SCHEMA)
    # One-time catch-up for any database that predates this: without ANALYZE, SQLite has no table
    # size statistics at all (no sqlite_stat1) and its query planner badly misjudges several
    # catalog-matching queries -- e.g. import matching was measured doing a full ~27k-row scan of
    # `variants` per line (6ms+ each) instead of the sub-0.1ms indexed lookup it should be, which
    # is what actually made importing a several-hundred-line collection take upwards of ten
    # seconds. catalog_sync.py re-runs ANALYZE after every write, so this only ever needs to catch
    # up a database that's never had it; a fresh install runs its first catalog_sync immediately
    # after this anyway (see docker-entrypoint.sh).
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'").fetchone():
        connection.execute("ANALYZE")
    deck_columns = {row[1] for row in connection.execute("PRAGMA table_info(decks)")}
    if "cover_variant_id" not in deck_columns:
        connection.execute("ALTER TABLE decks ADD COLUMN cover_variant_id TEXT REFERENCES variants(id)")
    game_columns = {row[1] for row in connection.execute("PRAGMA table_info(games)")}
    if "rarity_order" not in game_columns:
        connection.execute("ALTER TABLE games ADD COLUMN rarity_order TEXT NOT NULL DEFAULT '{}'")
    if "price_method" not in game_columns:
        connection.execute("ALTER TABLE games ADD COLUMN price_method TEXT")
    if "deck_ruleset" not in game_columns:
        connection.execute("ALTER TABLE games ADD COLUMN deck_ruleset TEXT")
    if "cardmarket_game_id" not in game_columns:
        connection.execute("ALTER TABLE games ADD COLUMN cardmarket_game_id INTEGER")
    provider_columns = {row[1] for row in connection.execute("PRAGMA table_info(catalog_providers)")}
    if "customized" not in provider_columns:
        connection.execute("ALTER TABLE catalog_providers ADD COLUMN customized INTEGER NOT NULL DEFAULT 0")
    user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    if "email" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    if "oauth_provider" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN oauth_provider TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE users ADD COLUMN oauth_subject TEXT NOT NULL DEFAULT ''")
    # Partial index (only rows actually linked to an IdP identity) -- created here rather than in
    # SCHEMA because on an upgrade from an older DB the ALTER TABLEs above (which add the columns
    # this index is built on) only just ran; SCHEMA's own executescript() runs before this point.
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth_identity
      ON users(oauth_provider,oauth_subject) WHERE oauth_subject != ''""")
    watchlist_entry_columns = {row[1] for row in connection.execute("PRAGMA table_info(named_watchlist_entries)")}
    if "quantity" not in watchlist_entry_columns:
        connection.execute("ALTER TABLE named_watchlist_entries ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")
    if "source" not in watchlist_entry_columns:
        # Both this column and is_sale_list below belong to the former fixed sale list. Nothing
        # writes them any more; they stay so migrate_sale_lists() can read a database of any age.
        connection.execute("ALTER TABLE named_watchlist_entries ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
    watchlist_columns = {row[1] for row in connection.execute("PRAGMA table_info(named_watchlists)")}
    if "is_sale_list" not in watchlist_columns:
        connection.execute("ALTER TABLE named_watchlists ADD COLUMN is_sale_list INTEGER NOT NULL DEFAULT 0")
    for table in ("sets", "card_identities", "printings"):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "source_type" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT NOT NULL DEFAULT 'imported'")
    collection_columns = {row[1] for row in connection.execute("PRAGMA table_info(collection_entries)")}
    if "is_graded" not in collection_columns:
        connection.execute("ALTER TABLE collection_entries ADD COLUMN is_graded INTEGER NOT NULL DEFAULT 0")
        connection.execute("ALTER TABLE collection_entries ADD COLUMN grade_label TEXT NOT NULL DEFAULT ''")
        connection.execute("ALTER TABLE collection_entries ADD COLUMN price_override REAL")
        # Widen the uniqueness key so a graded copy (its own condition+grade) can coexist with
        # an ungraded row of the same condition, and multiple different grades of the same
        # variant can each get their own row. SQLite can't ALTER a UNIQUE constraint, so this
        # rebuilds the table -- wrapped in one transaction with a row-count check, since real
        # collection data lives here.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE TABLE collection_entries_new (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
          variant_id TEXT NOT NULL REFERENCES variants(id), condition TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
          notes TEXT, is_graded INTEGER NOT NULL DEFAULT 0, grade_label TEXT NOT NULL DEFAULT '', price_override REAL,
          UNIQUE(user_id, variant_id, condition, is_graded, grade_label)
        )""")
        connection.execute("""INSERT INTO collection_entries_new
          (id,user_id,variant_id,condition,quantity,notes,is_graded,grade_label,price_override)
          SELECT id,user_id,variant_id,condition,quantity,notes,is_graded,grade_label,price_override FROM collection_entries""")
        before_count = connection.execute("SELECT COUNT(*) FROM collection_entries").fetchone()[0]
        after_count = connection.execute("SELECT COUNT(*) FROM collection_entries_new").fetchone()[0]
        if before_count != after_count:
            connection.execute("ROLLBACK")
            raise RuntimeError(f"collection_entries migration row count mismatch: {before_count} -> {after_count}, aborted")
        connection.execute("DROP TABLE collection_entries")
        connection.execute("ALTER TABLE collection_entries_new RENAME TO collection_entries")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_collection_user ON collection_entries(user_id)")
        connection.commit()
    collection_columns = {row[1] for row in connection.execute("PRAGMA table_info(collection_entries)")}
    if "created_at" not in collection_columns:
        connection.execute("ALTER TABLE collection_entries ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    if "last_added_at" not in collection_columns:
        connection.execute("ALTER TABLE collection_entries ADD COLUMN last_added_at TEXT NOT NULL DEFAULT ''")
    collection_stamp = now_iso()
    connection.execute("UPDATE collection_entries SET created_at=? WHERE created_at=''", (collection_stamp,))
    connection.execute("UPDATE collection_entries SET last_added_at=created_at WHERE last_added_at=''", ())
    connection.commit()
    # Gunicorn workers can import the app concurrently on a fresh volume.
    # Serialize the one-time seed so both workers never insert the demo user.
    connection.execute("BEGIN IMMEDIATE")
    seed_database(connection)
    # Preserve the original single watchlist while upgrading to named,
    # game-scoped lists. The INSERTs are idempotent for every worker restart.
    ensure_user_lists(connection)
    connection.execute("""INSERT OR IGNORE INTO named_watchlist_entries(list_id,variant_id,created_at)
      SELECT nw.id,w.variant_id,w.created_at FROM watchlist_entries w
      JOIN variants v ON v.id=w.variant_id
      JOIN named_watchlists nw ON nw.user_id=w.user_id AND nw.game_id=v.game_id AND nw.is_default=1""")
    migrate_sale_lists(connection)
    connection.commit()
    connection.close()
