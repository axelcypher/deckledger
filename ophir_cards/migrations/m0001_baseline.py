"""The schema as it stood when numbered migrations were introduced.

A new database gets exactly these tables. A database from before -- of any age, back to the first
release -- is brought to the same shape first: until then the schema was created with CREATE TABLE
IF NOT EXISTS and patched at every start by checking which columns were missing. Those checks are
kept here, once, so that every database starts the numbered migrations from one known state.

Frozen: a later change to the schema is a new migration, never an edit here.
"""

NAME = "baseline"

TABLES = """
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
  created_at TEXT NOT NULL, is_sale_list INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id, game_id, name)
);
CREATE TABLE IF NOT EXISTS named_watchlist_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, list_id INTEGER NOT NULL REFERENCES named_watchlists(id) ON DELETE CASCADE,
  variant_id TEXT NOT NULL REFERENCES variants(id), created_at TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL DEFAULT 'manual', UNIQUE(list_id, variant_id)
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
CREATE INDEX IF NOT EXISTS idx_marketplace_external ON marketplace_products(provider_id, external_product_id);
CREATE INDEX IF NOT EXISTS idx_prices_variant_metric ON price_observations(variant_id, provider_id, metric, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_variants_game ON variants(game_id);
CREATE INDEX IF NOT EXISTS idx_collection_user ON collection_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_named_watchlists_user_game ON named_watchlists(user_id,game_id);
CREATE INDEX IF NOT EXISTS idx_decks_user_game ON decks(user_id,game_id);
-- Only rows linked to an identity of the SSO provider.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth_identity ON users(oauth_provider,oauth_subject) WHERE oauth_subject != '';
"""


def columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def add_missing_columns(connection, table, definitions):
    present = columns(connection, table)
    for name, definition in definitions:
        if name not in present:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def upgrade_older_tables(connection, stamp):
    """Everything an existing table may lack. Must run before TABLES, whose indexes name columns
    that only this adds to an older table."""
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    additions = {
        "decks": [("cover_variant_id", "TEXT REFERENCES variants(id)")],
        "games": [("rarity_order", "TEXT NOT NULL DEFAULT '{}'"), ("price_method", "TEXT"), ("deck_ruleset", "TEXT"), ("cardmarket_game_id", "INTEGER")],
        "catalog_providers": [("customized", "INTEGER NOT NULL DEFAULT 0")],
        "users": [("email", "TEXT NOT NULL DEFAULT ''"), ("oauth_provider", "TEXT NOT NULL DEFAULT ''"), ("oauth_subject", "TEXT NOT NULL DEFAULT ''")],
        "named_watchlist_entries": [("quantity", "INTEGER NOT NULL DEFAULT 1"), ("source", "TEXT NOT NULL DEFAULT 'manual'")],
        "named_watchlists": [("is_sale_list", "INTEGER NOT NULL DEFAULT 0")],
        "sets": [("source_type", "TEXT NOT NULL DEFAULT 'imported'")],
        "card_identities": [("source_type", "TEXT NOT NULL DEFAULT 'imported'")],
        "printings": [("source_type", "TEXT NOT NULL DEFAULT 'imported'")],
    }
    for table, definitions in additions.items():
        if table in existing:
            add_missing_columns(connection, table, definitions)
    if "collection_entries" not in existing:
        return
    if "is_graded" not in columns(connection, "collection_entries"):
        # A graded copy has to coexist with an ungraded one of the same condition, and different
        # grades of one card with each other. SQLite cannot widen a UNIQUE constraint in place,
        # so the table is rebuilt -- with a row count check, since this is the collection itself.
        connection.execute("""CREATE TABLE collection_entries_new (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
          variant_id TEXT NOT NULL REFERENCES variants(id), condition TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
          notes TEXT, is_graded INTEGER NOT NULL DEFAULT 0, grade_label TEXT NOT NULL DEFAULT '', price_override REAL,
          created_at TEXT NOT NULL DEFAULT '', last_added_at TEXT NOT NULL DEFAULT '',
          UNIQUE(user_id, variant_id, condition, is_graded, grade_label)
        )""")
        connection.execute("""INSERT INTO collection_entries_new(id,user_id,variant_id,condition,quantity,notes)
          SELECT id,user_id,variant_id,condition,quantity,notes FROM collection_entries""")
        before = connection.execute("SELECT COUNT(*) FROM collection_entries").fetchone()[0]
        after = connection.execute("SELECT COUNT(*) FROM collection_entries_new").fetchone()[0]
        if before != after:
            raise RuntimeError(f"collection_entries: {before} rows before the rebuild, {after} after")
        connection.execute("DROP TABLE collection_entries")
        connection.execute("ALTER TABLE collection_entries_new RENAME TO collection_entries")
    add_missing_columns(connection, "collection_entries", [("created_at", "TEXT NOT NULL DEFAULT ''"), ("last_added_at", "TEXT NOT NULL DEFAULT ''")])
    connection.execute("UPDATE collection_entries SET created_at=? WHERE created_at=''", (stamp,))
    connection.execute("UPDATE collection_entries SET last_added_at=created_at WHERE last_added_at=''")


def carry_over_single_watchlist(connection, stamp):
    """The first release had one watchlist per account (watchlist_entries). Its entries go onto
    the default list of their card's game, which is created here where it does not exist yet."""
    connection.execute("""INSERT OR IGNORE INTO named_watchlists(user_id,game_id,name,is_default,created_at)
      SELECT DISTINCT w.user_id,v.game_id,'Merkliste',1,? FROM watchlist_entries w JOIN variants v ON v.id=w.variant_id""", (stamp,))
    connection.execute("""INSERT OR IGNORE INTO named_watchlist_entries(list_id,variant_id,created_at)
      SELECT nw.id,w.variant_id,w.created_at FROM watchlist_entries w
      JOIN variants v ON v.id=w.variant_id
      JOIN named_watchlists nw ON nw.user_id=w.user_id AND nw.game_id=v.game_id AND nw.is_default=1""")


def apply(connection, context):
    upgrade_older_tables(connection, context.now)
    context.run_script(TABLES)
    carry_over_single_watchlist(connection, context.now)
    # Without statistics SQLite's planner misjudges the catalogue lookups badly (import matching
    # scanned all variants per line). catalog_sync.py refreshes them after every import; this
    # covers a database that has never had any.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'").fetchone():
        connection.execute("ANALYZE")
