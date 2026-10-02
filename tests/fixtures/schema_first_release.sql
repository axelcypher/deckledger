CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY, module_id TEXT NOT NULL, name TEXT NOT NULL, short_name TEXT NOT NULL,
  module_version TEXT NOT NULL, languages TEXT NOT NULL, accent TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sets (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), code TEXT NOT NULL, name TEXT NOT NULL,
  set_type TEXT NOT NULL, release_date TEXT, printed_card_count INTEGER, classifications TEXT NOT NULL,
  accent TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_identities (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), canonical_name TEXT NOT NULL,
  rules_text TEXT, card_type TEXT, attributes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS printings (
  id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES card_identities(id), game_id TEXT NOT NULL REFERENCES games(id),
  set_id TEXT NOT NULL REFERENCES sets(id), collector_number TEXT NOT NULL, language TEXT NOT NULL,
  rarity TEXT NOT NULL, attributes TEXT NOT NULL
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
  notes TEXT, UNIQUE(user_id, variant_id, condition)
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
CREATE TABLE IF NOT EXISTS catalog_metadata (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_printings_set ON printings(set_id);
CREATE INDEX IF NOT EXISTS idx_printings_identity_language ON printings(identity_id,language,set_id);
CREATE INDEX IF NOT EXISTS idx_variants_printing ON variants(printing_id);
CREATE INDEX IF NOT EXISTS idx_variants_game ON variants(game_id);
CREATE INDEX IF NOT EXISTS idx_collection_user ON collection_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_named_watchlists_user_game ON named_watchlists(user_id,game_id);
CREATE INDEX IF NOT EXISTS idx_decks_user_game ON decks(user_id,game_id);
