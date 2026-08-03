import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import xml.sax.saxutils as xml_escape
from datetime import datetime, timezone
from functools import cmp_to_key, wraps
from pathlib import Path
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from catalog_provider_contract import digest, slug


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "deckledger-local-development-key")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "deckledger.db"))
IMAGE_CACHE = Path(os.path.dirname(DB_PATH)) / "card-images"
IMAGE_SOURCE_CACHE = Path(os.path.dirname(DB_PATH)) / "card-image-sources"
IMAGE_THUMB_CACHE = Path(os.path.dirname(DB_PATH)) / "card-thumbnails"
IMAGE_LOCK_CACHE = Path(os.path.dirname(DB_PATH)) / "card-image-locks"
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", Path(__file__).with_name("public")))
PUBLIC_SET_DIR = PUBLIC_DIR / "sets"
PUBLIC_OP_ICON_DIR = PUBLIC_DIR / "icons" / "one-piece"
PUBLIC_IMAGE_EXTENSIONS = (".avif", ".webp", ".png", ".jpg", ".jpeg", ".svg")

GAME_LOGOS = {
    "lorcana": "https://www.disneylorcana.com/_nuxt/logo-br-2x.Sweb4xgr.png",
    "one-piece": "https://en.onepiece-cardgame.com/renewal/images/common/logo_op_white.png",
    "hololive": "https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/global/logo_w.svg",
}

LORCANA_PRODUCT_PATHS = {
    "1": "the-first-chapter", "2": "rise-of-the-floodborn", "3": "into-the-inklands",
    "4": "ursulas-return", "5": "shimmering-skies", "6": "azurite-sea",
    "7": "archazias-island", "8": "reign-of-jafar", "9": "fabled", "10": "whispers",
    "11": "winterspell", "12": "wilds-unknown", "13": "attack-of-the-vine",
    "14": "hyperia-city", "15": "into-the-inkdark", "Q1": "deep-trouble",
    "Q2": "palace-heist", "Q3": "great-hunny-rescue",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        # Without this, two connections writing at the same instant (a web
        # request racing a background catalog_sync.py/price_sync.py run, or
        # just two concurrent gunicorn threads) fail immediately with
        # "database is locked" instead of one briefly waiting its turn.
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS catalog_providers (
  id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), label TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('custom_code')),
  code TEXT,
  minimum_sets INTEGER NOT NULL DEFAULT 0, minimum_cards INTEGER NOT NULL DEFAULT 0,
  timeout_seconds INTEGER NOT NULL DEFAULT 300,
  provider_version TEXT NOT NULL, last_synced_version TEXT,
  last_run_at TEXT, last_status TEXT, last_summary TEXT, last_error TEXT,
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_price_overrides (
  game_id TEXT NOT NULL REFERENCES games(id), language TEXT NOT NULL, price_method TEXT NOT NULL,
  PRIMARY KEY (game_id, language)
);
CREATE INDEX IF NOT EXISTS idx_catalog_providers_game ON catalog_providers(game_id);
CREATE INDEX IF NOT EXISTS idx_printings_set ON printings(set_id);
CREATE INDEX IF NOT EXISTS idx_printings_identity_language ON printings(identity_id,language,set_id);
CREATE INDEX IF NOT EXISTS idx_variants_printing ON variants(printing_id);
CREATE INDEX IF NOT EXISTS idx_variants_game ON variants(game_id);
CREATE INDEX IF NOT EXISTS idx_collection_user ON collection_entries(user_id);
CREATE INDEX IF NOT EXISTS idx_named_watchlists_user_game ON named_watchlists(user_id,game_id);
CREATE INDEX IF NOT EXISTS idx_decks_user_game ON decks(user_id,game_id);
"""

FORMAT_PROFILES = {
    "lorcana": [
        {"id": "core", "name": "Core Constructed", "description": "Rotierender offizieller Kartenpool", "zones": [{"id":"main","name":"Deck","target":60}], "rules_url": "https://www.disneylorcana.com/en-US/play/ways-to-play"},
        {"id": "infinity", "name": "Infinity Constructed", "description": "Nicht-rotierender offizieller Kartenpool", "zones": [{"id":"main","name":"Deck","target":60}], "rules_url": "https://www.disneylorcana.com/en-US/play/ways-to-play"},
    ],
    "one-piece": [
        {"id": "standard", "name": "Official Standard", "description": "Aktuelle offizielle Regulation", "zones": [{"id":"leader","name":"Leader","target":1},{"id":"main","name":"Deck","target":50},{"id":"don","name":"DON!!","target":10}], "rules_url": "https://en.onepiece-cardgame.com/rules/"},
    ],
    "hololive": [
        {"id": "standard", "name": "Official Standard", "description": "Offizielles Constructed-Regelset", "zones": [{"id":"oshi","name":"Oshi","target":1},{"id":"main","name":"Main Deck","target":50},{"id":"cheer","name":"Cheer Deck","target":20}], "rules_url": "https://en.hololive-official-cardgame.com/wp-content/themes/tcg_en/assets/img/rule/official_rule_book_ver1_02.pdf"},
    ],
}


def validate_lorcana_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("main", 0) < 60:
        errors.append(f'Noch {60-counts.get("main",0)} Karten bis zum Minimum von 60.')
    names = {}
    colors = set()
    for c in cards:
        names[c["canonical_name"]] = names.get(c["canonical_name"], 0) + c["quantity"]
        attrs = jload(c["attributes"], {})
        colors.add(attrs.get("color"))
        if str(c.get("set_type") or "").lower() == "quest":
            errors.append(f'{c["canonical_name"]} ist eine Quest-Karte und nicht für Constructed-Decks zulässig.')
        if deck["format_id"] == "core" and attrs.get("legality") in ("future", "rotated", "banned"):
            errors.append(f'{c["canonical_name"]} ist im Core-Format nicht legal.')
    if len(colors - {None}) > 2:
        errors.append("Das Deck enthält mehr als zwei Tintenfarben.")
    for name, qty in names.items():
        if qty > 4:
            errors.append(f'{name}: maximal 4 Exemplare erlaubt.')
    return errors, warnings


def validate_one_piece_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("leader", 0) != 1:
        errors.append("Genau 1 Leader ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Hauptdeck: {counts.get("main",0)}/50 Karten.')
    # Explicit DON!! artwork is optional. Any open slots are represented by
    # the standard DON!! card in the builder and do not have to be stored.
    if counts.get("don", 0) > 10:
        errors.append(f'DON!!-Deck: maximal 10 Karten ({counts.get("don",0)}/10).')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare erlaubt.')
    if any(c["card_type"] != "Leader" for c in cards if c["zone"] == "leader"):
        errors.append("In der Leader-Zone sind nur Leader-Karten erlaubt.")
    if any(c["card_type"] == "Leader" or c["card_type"] == "DON!!" for c in cards if c["zone"] == "main"):
        errors.append("Leader- und DON!!-Karten dürfen nicht ins Hauptdeck.")
    if any(c["card_type"] != "DON!!" for c in cards if c["zone"] == "don"):
        errors.append("Das DON!!-Deck darf nur DON!!-Karten enthalten.")
    leader = next((c for c in cards if c["zone"] == "leader"), None)
    if leader:
        leader_color = jload(leader["attributes"], {}).get("color")
        invalid = [c["canonical_name"] for c in cards if c["zone"] == "main" and jload(c["attributes"], {}).get("color") != leader_color]
        if invalid:
            warnings.append(f'{len(invalid)} Karten entsprechen nicht der Leader-Farbe {leader_color}.')
    return errors, warnings


def validate_hololive_deck(deck, cards, counts):
    errors, warnings = [], []
    if counts.get("oshi", 0) != 1:
        errors.append("Genau 1 Oshi holomem ist erforderlich.")
    if counts.get("main", 0) != 50:
        errors.append(f'Main Deck: {counts.get("main",0)}/50 Karten.')
    if counts.get("cheer", 0) != 20:
        errors.append(f'Cheer Deck: {counts.get("cheer",0)}/20 Karten.')
    numbers = {}
    for c in cards:
        if c["zone"] == "main":
            numbers[c["collector_number"]] = numbers.get(c["collector_number"], 0) + c["quantity"]
    for number, qty in numbers.items():
        if qty > 4:
            errors.append(f'{number}: maximal 4 Exemplare im Main Deck erlaubt.')
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "oshi" for c in cards if c["zone"] == "oshi"):
        errors.append("In der Oshi-Zone ist nur ein Oshi holomem erlaubt.")
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "main" for c in cards if c["zone"] == "main"):
        errors.append("Oshi- und Cheer-Karten dürfen nicht ins Main Deck.")
    if any(zone_for_card_type("hololive-standard", c["card_type"]) != "cheer" for c in cards if c["zone"] == "cheer"):
        errors.append("Das Cheer Deck darf nur Cheer-Karten enthalten.")
    return errors, warnings


# Bespoke deck-legality rules per game stay code (they're real business logic,
# not data), but which ruleset a game uses is a `games.deck_ruleset` value,
# not a hardcoded game_id check -- see deck_validation() / zone_for_card_type().
DECK_RULESETS = {
    "lorcana-standard": {"zone_for_card_type": {}, "validate": validate_lorcana_deck},
    "one-piece-standard": {"zone_for_card_type": {"Leader": "leader", "DON!!": "don"}, "validate": validate_one_piece_deck},
    "hololive-standard": {
        "zone_for_card_type": {"Oshi": "oshi", "Oshi holomem": "oshi", "推しホロメン": "oshi", "Cheer": "cheer", "エール": "cheer"},
        "validate": validate_hololive_deck,
    },
}


def zone_for_card_type(deck_ruleset, card_type):
    mapping = (DECK_RULESETS.get(deck_ruleset) or {}).get("zone_for_card_type", {})
    return mapping.get(str(card_type or "").strip(), "main")


def game_deck_ruleset(game_id):
    row = db().execute("SELECT deck_ruleset FROM games WHERE id=?", (game_id,)).fetchone()
    return row["deck_ruleset"] if row else None


GAME_DATA = [
    ("lorcana", "disney-lorcana", "Disney Lorcana", "Lorcana", "1.0.0", ["DE", "EN"], "#8b5cf6"),
    ("one-piece", "one-piece-card-game", "One Piece Card Game", "One Piece", "1.0.0", ["EN", "JP"], "#ef4444"),
    ("hololive", "hololive-ocg", "hololive Official Card Game", "hololive", "0.9.0", ["EN", "JP"], "#06b6d4"),
]

# Ordinal rarity rank per game, least to most rare. Printings carry the rarity label in the
# printing's own language, so Lorcana needs both the English and the official German terms
# mapped to the same rank (verified against Ravensburger's 8-tier ladder incl. the Epic/Iconic
# tiers added with Fabled in Sept. 2025: Common<Uncommon<Rare<Super Rare<Legendary<Epic<Enchanted
# <Iconic). Codes with no confidently-known slot (promos, DON!!, unclear hololive tiers) are left
# unmapped and sort after every ranked rarity rather than guessing a position.
RARITY_ORDER = {
    "lorcana": {
        "Common": 0, "Gewöhnlich": 0,
        "Uncommon": 1, "Ungewöhnlich": 1,
        "Rare": 2, "Selten": 2,
        "Super Rare": 3, "Episch": 3,
        "Legendary": 4, "Legendär": 4,
        "Epic": 5, "Mythisch": 5,
        "Enchanted": 6, "Verzaubert": 6,
        "Iconic": 7, "Ikonisch": 7,
        "Special": 8, "Speziell": 8,
    },
    "one-piece": {
        "C": 0, "UC": 1, "R": 2, "SR": 3, "SEC": 4, "L": 5,
        "SP": 6, "SP CARD": 6, "SPカード": 6, "SP P": 6, "TR": 7,
    },
    "hololive": {
        "C": 0, "U": 1, "R": 2, "RR": 3, "SR": 4, "OSR": 5, "SEC": 6, "HR": 6,
    },
}
RARITY_FALLBACK_RANK = 900

# Language-independent keys for Lorcana's icon-based rarity filter (one key covers both the
# English and German printed label, since a single card can appear under either depending on
# which language is being viewed).
LORCANA_RARITY_KEYS = {
    "common": 0, "uncommon": 1, "rare": 2, "super-rare": 3, "legendary": 4,
    "epic": 5, "enchanted": 6, "iconic": 7, "special": 8,
}


def rarity_rank(game_id, rarity):
    return RARITY_ORDER.get(game_id, {}).get(rarity, RARITY_FALLBACK_RANK)


def rarity_case_sql(game_id, column):
    """SQL CASE mirroring rarity_rank(), for ORDER BY on paginated queries. Rarity labels are our
    own constants (never user input), so inlining them as string literals is safe."""
    order = RARITY_ORDER.get(game_id)
    if not order:
        return f"{column} COLLATE NOCASE"
    whens = " ".join(f"WHEN '{rarity.replace(chr(39), chr(39) * 2)}' THEN {rank}" for rarity, rank in order.items())
    return f"CASE {column} {whens} ELSE {RARITY_FALLBACK_RANK} END"


GAME_COLUMNS = "id, module_id, name, short_name, module_version, languages, accent, enabled"
# (minimum_sets, minimum_cards, timeout_seconds) per shipped Tier-2 provider.
# Code itself lives in providers/<id>.py -- read at seed time, not embedded
# here, so it stays normal syntax-highlighted, lintable Python in the repo.
DEFAULT_PROVIDERS = {
    "lorcana": (15, 2500, 300),
    "one-piece": (20, 1000, 600),
    "hololive": (10, 500, 600),
}


def default_provider_code(game_id: str) -> str:
    path = Path(__file__).with_name("providers") / f"{game_id.replace('-', '_')}.py"
    return path.read_text(encoding="utf-8")


def seed_default_providers(connection):
    for game_id, (minimum_sets, minimum_cards, timeout_seconds) in DEFAULT_PROVIDERS.items():
        code = default_provider_code(game_id)
        version = digest(code)
        existing = connection.execute("SELECT kind FROM catalog_providers WHERE id=?", (game_id,)).fetchone()
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


DEFAULT_DECK_RULESETS = {"lorcana": "lorcana-standard", "one-piece": "one-piece-standard", "hololive": "hololive-standard"}
DEFAULT_PRICE_METHODS = {"lorcana": "cardmarket", "one-piece": "cardmarket", "hololive": "tcgcsv"}
DEFAULT_PRICE_OVERRIDES = [("hololive", "JP", "yuyutei")]
# Cardmarket's numeric idGame per bootstrapped game -- unauthenticated and stable,
# but there is no discovery endpoint for it, so a new TCG's id is a one-time
# manual lookup on cardmarket.com, entered once via the admin UI (games.cardmarket_game_id).
DEFAULT_CARDMARKET_GAME_IDS = {"lorcana": 19, "one-piece": 18}


def seed_default_deck_rulesets(connection):
    for game_id, ruleset in DEFAULT_DECK_RULESETS.items():
        connection.execute("UPDATE games SET deck_ruleset=? WHERE id=? AND deck_ruleset IS NULL", (ruleset, game_id))


def seed_default_price_methods(connection):
    for game_id, method in DEFAULT_PRICE_METHODS.items():
        connection.execute("UPDATE games SET price_method=? WHERE id=? AND price_method IS NULL", (method, game_id))
    for game_id, language, method in DEFAULT_PRICE_OVERRIDES:
        connection.execute(
            "INSERT OR IGNORE INTO game_price_overrides(game_id, language, price_method) VALUES(?,?,?)",
            (game_id, language, method),
        )


def seed_default_cardmarket_game_ids(connection):
    for game_id, cardmarket_id in DEFAULT_CARDMARKET_GAME_IDS.items():
        connection.execute(
            "UPDATE games SET cardmarket_game_id=? WHERE id=? AND cardmarket_game_id IS NULL", (cardmarket_id, game_id),
        )


def seed_database(connection):
    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        connection.executemany(
            f"INSERT OR IGNORE INTO games({GAME_COLUMNS}) VALUES(?,?,?,?,?,?,?,1)",
            [(a,b,c,d,e,json.dumps(f),g) for a,b,c,d,e,f,g in GAME_DATA],
        )
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
    connection.executemany(f"INSERT INTO games({GAME_COLUMNS}) VALUES(?,?,?,?,?,?,?,1)", [(a,b,c,d,e,json.dumps(f),g) for a,b,c,d,e,f,g in GAME_DATA])
    seed_default_providers(connection)
    seed_default_deck_rulesets(connection)
    seed_default_price_methods(connection)
    seed_default_cardmarket_game_ids(connection)


def init_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.executescript(SCHEMA)
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
    for table in ("sets", "card_identities", "printings"):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "source_type" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT NOT NULL DEFAULT 'imported'")
    # Gunicorn workers can import the app concurrently on a fresh volume.
    # Serialize the one-time seed so both workers never insert the demo user.
    connection.execute("BEGIN IMMEDIATE")
    seed_database(connection)
    # Preserve the original single watchlist while upgrading to named,
    # game-scoped lists. The INSERTs are idempotent for every worker restart.
    connection.execute("""INSERT OR IGNORE INTO named_watchlists(user_id,game_id,name,is_default,created_at)
      SELECT u.id,g.id,'Merkliste',1,? FROM users u CROSS JOIN games g""", (now_iso(),))
    connection.execute("""INSERT OR IGNORE INTO named_watchlist_entries(list_id,variant_id,created_at)
      SELECT nw.id,w.variant_id,w.created_at FROM watchlist_entries w
      JOIN variants v ON v.id=w.variant_id
      JOIN named_watchlists nw ON nw.user_id=w.user_id AND nw.game_id=v.game_id AND nw.is_default=1""")
    connection.commit()
    connection.close()


init_database()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        row = db().execute("SELECT role FROM users WHERE id=?", (user_id(),)).fetchone()
        if not row or row["role"] != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "admin access required"}), 403
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def user_id():
    return int(session["user_id"])


def jload(value, default=None):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            return redirect(url_for("index"))
        return render_template("login.html", error="Benutzername oder Passwort ist nicht korrekt."), 401
    if session.get("user_id"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    return render_template("index.html")


def latest_price_sql(alias="v", metric="trend"):
    return f"""(SELECT po.amount FROM price_observations po
      JOIN marketplace_products mp ON mp.variant_id=po.variant_id AND mp.provider_id=po.provider_id
      WHERE po.variant_id={alias}.id AND po.metric='{metric}'
      ORDER BY CASE po.provider_id WHEN 'cardmarket' THEN 0 ELSE 9 END,observed_at DESC LIMIT 1)"""


def latest_price_meta_sql(alias="v", field="provider_id"):
    return f"""(SELECT po.{field} FROM price_observations po
      JOIN marketplace_products mp ON mp.variant_id=po.variant_id AND mp.provider_id=po.provider_id
      WHERE po.variant_id={alias}.id AND po.metric='trend'
      ORDER BY CASE po.provider_id WHEN 'cardmarket' THEN 0 ELSE 9 END,observed_at DESC LIMIT 1)"""


@app.get("/api/bootstrap")
@login_required
def bootstrap():
    uid = user_id()
    current = db().execute("SELECT id,username,display_name,role FROM users WHERE id=?", (uid,)).fetchone()
    settings = {r["key"]: jload(r["value"], r["value"]) for r in db().execute("SELECT key,value FROM user_settings WHERE user_id=?", (uid,))}
    default_languages = settings.get("defaultLanguages") or {}
    games = []
    for row in db().execute("SELECT * FROM games WHERE enabled=1 ORDER BY name"):
        game_id = row["id"]
        languages = jload(row["languages"], [])
        lang = default_languages.get(game_id) or (languages[0] if languages else None)
        stats = db().execute(
            f"""SELECT COALESCE(SUM(c.quantity),0) copies, COUNT(DISTINCT CASE WHEN c.quantity>0 THEN v.id END) unique_cards,
                 COALESCE(SUM(c.quantity * {latest_price_sql('v')}),0) value
                 FROM variants v JOIN printings p ON p.id=v.printing_id
                 LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
                 WHERE v.game_id=? AND (? IS NULL OR p.language=?)""",
            (uid, game_id, lang, lang),
        ).fetchone()
        total = db().execute(
            "SELECT COUNT(*) FROM variants v JOIN printings p ON p.id=v.printing_id WHERE v.game_id=? AND (? IS NULL OR p.language=?)",
            (game_id, lang, lang),
        ).fetchone()[0]
        game = dict(row)
        game["languages"] = languages
        game.update({"copies": stats["copies"], "unique_cards": stats["unique_cards"], "value": round(stats["value"], 2), "completion": round(stats["unique_cards"] / total * 100) if total else 0})
        games.append(game)
    imports = [dict(r) for r in db().execute("SELECT id,created_at,game_id,undone_at FROM import_operations WHERE user_id=? ORDER BY id DESC LIMIT 4", (uid,))]
    price_sync = db().execute("SELECT MAX(observed_at) FROM price_observations").fetchone()[0]
    return jsonify({"user": dict(current), "games": games, "settings": settings, "imports": imports, "price_sync": price_sync})


def fetch_banner_cards(game_id, mode, uid):
    """Pick 20 owned cards for the dashboard banner, one representative variant per identity."""
    if mode == "value":
        order_sql, extra_where = f"{latest_price_sql('v')} DESC", f"AND {latest_price_sql('v')} IS NOT NULL"
    else:
        order_sql, extra_where = "s.release_date DESC", ""
    rows = db().execute(
        f"""SELECT v.id variant_id,i.id identity_id,i.canonical_name,s.name set_name,s.code set_code,
              v.finish,p.language,p.collector_number,{latest_price_sql('v')} price
            FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
              JOIN sets s ON s.id=p.set_id
              JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? AND c.quantity>0
            WHERE v.game_id=? {extra_where}
            ORDER BY {order_sql}, CASE WHEN v.finish='Normal' THEN 0 ELSE 1 END
            LIMIT 300""",
        (uid, game_id)
    ).fetchall()
    seen, result = set(), []
    for row in rows:
        if row["identity_id"] in seen: continue
        seen.add(row["identity_id"]); result.append(dict(row))
        if len(result) >= 20: break
    return result


@app.get("/api/home-banner")
@login_required
def home_banner():
    uid = user_id()
    raw = db().execute("SELECT value FROM user_settings WHERE user_id=? AND key='homeBanner'", (uid,)).fetchone()
    banner_settings = jload(raw["value"], {}) if raw else {}
    modes = [m for m in (banner_settings.get("modes") or ["newest"]) if m in ("newest", "value")] or ["newest"]
    excluded = set(banner_settings.get("excludedGames") or [])
    games = [dict(r) for r in db().execute("SELECT id,name,short_name,accent FROM games WHERE enabled=1 ORDER BY name") if r["id"] not in excluded]
    slides = []
    for game in games:
        for mode in modes:
            cards = fetch_banner_cards(game["id"], mode, uid)
            if cards:
                slides.append({"game_id": game["id"], "game_name": game["name"], "game_short_name": game["short_name"], "accent": game["accent"], "mode": mode, "cards": cards})
    return jsonify({"slides": slides})


@app.post("/api/prices/sync")
@login_required
def refresh_prices():
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("price_sync.py"))],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Der Preisimport hat das Zeitlimit überschritten."}), 504
    if process.returncode:
        app.logger.error("Price sync failed: %s", process.stderr[-2000:])
        return jsonify({"error": "Preisimport fehlgeschlagen; die letzten gültigen Preise bleiben erhalten."}), 502
    counts = db().execute("SELECT value FROM catalog_metadata WHERE key='price_sync_counts'").fetchone()
    synced = db().execute("SELECT value FROM catalog_metadata WHERE key='price_sync_last_success'").fetchone()
    return jsonify({
        "counts": jload(counts[0], {}) if counts else {},
        "synced_at": jload(synced[0]) if synced else None,
    })


@app.get("/api/admin/games")
@admin_required
def admin_list_games():
    rows = [dict(r) for r in db().execute("SELECT * FROM games ORDER BY name")]
    for row in rows:
        row["languages"] = jload(row["languages"], [])
        row["rarity_order"] = jload(row["rarity_order"], {})
    return jsonify(rows)


@app.post("/api/admin/games")
@admin_required
def admin_create_game():
    p = request.get_json(force=True)
    name = (p.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name ist erforderlich"}), 400
    game_id = slug(p.get("id") or name)
    if not re.fullmatch(r"[a-z0-9-]+", game_id):
        return jsonify({"error": "Ungültige Spiel-ID"}), 400
    if db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "Diese Spiel-ID existiert bereits"}), 409
    languages = p.get("languages") or ["EN"]
    short_name = (p.get("short_name") or name)[:40]
    accent = p.get("accent") or "#6366f1"
    db().execute(
        "INSERT INTO games(id, module_id, name, short_name, module_version, languages, accent, enabled) VALUES(?,?,?,?,?,?,?,1)",
        (game_id, game_id, name, short_name, "0.1.0", json.dumps(languages), accent),
    )
    db().commit()
    return jsonify({"id": game_id}), 201


@app.patch("/api/admin/games/<game_id>")
@admin_required
def admin_update_game(game_id):
    if not db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "game not found"}), 404
    p = request.get_json(force=True)
    fields, values = [], []
    for key in ("name", "short_name", "accent", "price_method", "deck_ruleset"):
        if key in p:
            fields.append(f"{key}=?")
            values.append(p[key] or None)
    if "cardmarket_game_id" in p:
        fields.append("cardmarket_game_id=?")
        values.append(int(p["cardmarket_game_id"]) if p["cardmarket_game_id"] not in (None, "") else None)
    if "languages" in p:
        fields.append("languages=?")
        values.append(json.dumps(p["languages"]))
    if "rarity_order" in p:
        fields.append("rarity_order=?")
        values.append(json.dumps(p["rarity_order"]))
    if "enabled" in p:
        fields.append("enabled=?")
        values.append(1 if p["enabled"] else 0)
    if not fields:
        return jsonify({"error": "keine Felder zum Aktualisieren"}), 400
    values.append(game_id)
    db().execute(f"UPDATE games SET {','.join(fields)} WHERE id=?", values)
    db().commit()
    return jsonify({"saved": True})


@app.delete("/api/admin/games/<game_id>")
@admin_required
def admin_delete_game(game_id):
    if not db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "game not found"}), 404
    variant_filter = "variant_id IN (SELECT id FROM variants WHERE game_id=?)"
    for table in ("collection_entries", "watchlist_entries", "marketplace_products", "price_observations"):
        db().execute(f"DELETE FROM {table} WHERE {variant_filter}", (game_id,))
    db().execute("DELETE FROM deck_cards WHERE deck_id IN (SELECT id FROM decks WHERE game_id=?)", (game_id,))
    db().execute("DELETE FROM named_watchlist_entries WHERE list_id IN (SELECT id FROM named_watchlists WHERE game_id=?)", (game_id,))
    db().execute("DELETE FROM decks WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM named_watchlists WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM variants WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM printings WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM card_identities WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM sets WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM catalog_providers WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM game_price_overrides WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM import_operations WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM games WHERE id=?", (game_id,))
    db().commit()
    (CARD_BACK_UPLOAD_DIR / f"{game_id}.jpg").unlink(missing_ok=True)
    return jsonify({"deleted": True})


CARD_BACK_UPLOAD_DIR = Path(os.path.dirname(DB_PATH)) / "card-backs"


@app.post("/api/admin/games/<game_id>/card-back")
@admin_required
def admin_upload_card_back(game_id):
    if not db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "game not found"}), 404
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Keine Datei übermittelt"}), 400
    CARD_BACK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file.save(CARD_BACK_UPLOAD_DIR / f"{game_id}.jpg")
    return jsonify({"saved": True})


@app.get("/api/admin/providers")
@admin_required
def admin_list_providers():
    rows = [dict(r) for r in db().execute("SELECT * FROM catalog_providers ORDER BY game_id")]
    for row in rows:
        row["last_summary"] = jload(row.get("last_summary"), {})
    return jsonify(rows)


@app.post("/api/admin/providers")
@admin_required
def admin_create_provider():
    p = request.get_json(force=True)
    game_id = p.get("game_id")
    if not db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "Spiel nicht gefunden"}), 404
    code = p.get("code") or ""
    if not code.strip():
        return jsonify({"error": "Code darf nicht leer sein"}), 400
    provider_id = slug(p.get("id") or game_id)
    if db().execute("SELECT 1 FROM catalog_providers WHERE id=?", (provider_id,)).fetchone():
        return jsonify({"error": "Diese Provider-ID existiert bereits"}), 409
    now = now_iso()
    db().execute(
        """INSERT INTO catalog_providers
           (id, game_id, label, kind, code, minimum_sets, minimum_cards, timeout_seconds, provider_version, enabled, created_at, updated_at)
           VALUES (?,?,?,'custom_code',?,?,?,?,?,1,?,?)""",
        (provider_id, game_id, p.get("label") or game_id, code,
         int(p.get("minimum_sets") or 0), int(p.get("minimum_cards") or 0), int(p.get("timeout_seconds") or 300),
         digest(code), now, now),
    )
    db().commit()
    return jsonify({"id": provider_id}), 201


@app.patch("/api/admin/providers/<provider_id>")
@admin_required
def admin_update_provider(provider_id):
    row = db().execute("SELECT * FROM catalog_providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return jsonify({"error": "provider not found"}), 404
    p = request.get_json(force=True)
    fields, values = [], []
    if "code" in p:
        code = p["code"] or ""
        if not code.strip():
            return jsonify({"error": "Code darf nicht leer sein"}), 400
        fields += ["code=?", "provider_version=?"]
        values += [code, digest(code)]
    for key in ("label", "minimum_sets", "minimum_cards", "timeout_seconds"):
        if key in p:
            fields.append(f"{key}=?")
            values.append(p[key])
    if "enabled" in p:
        fields.append("enabled=?")
        values.append(1 if p["enabled"] else 0)
    if not fields:
        return jsonify({"error": "keine Felder zum Aktualisieren"}), 400
    fields.append("updated_at=?")
    values.append(now_iso())
    values.append(provider_id)
    db().execute(f"UPDATE catalog_providers SET {','.join(fields)} WHERE id=?", values)
    db().commit()
    return jsonify({"saved": True})


@app.delete("/api/admin/providers/<provider_id>")
@admin_required
def admin_delete_provider(provider_id):
    db().execute("DELETE FROM catalog_providers WHERE id=?", (provider_id,))
    db().commit()
    return jsonify({"deleted": True})


@app.post("/api/admin/providers/<provider_id>/run")
@admin_required
def admin_run_provider(provider_id):
    row = db().execute("SELECT timeout_seconds FROM catalog_providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return jsonify({"error": "provider not found"}), 404
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("catalog_sync.py")), "--provider", provider_id],
            capture_output=True, text=True, timeout=row["timeout_seconds"] + 30, check=False,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Der Import hat das Zeitlimit überschritten."}), 504
    updated = db().execute("SELECT last_status, last_summary, last_error, last_run_at FROM catalog_providers WHERE id=?", (provider_id,)).fetchone()
    return jsonify({
        "returncode": process.returncode,
        "status": updated["last_status"] if updated else None,
        "summary": jload(updated["last_summary"], {}) if updated else {},
        "error": updated["last_error"] if updated else None,
        "run_at": updated["last_run_at"] if updated else None,
        "log": (process.stdout[-2000:] + process.stderr[-2000:]) if process.returncode else None,
    })


@app.get("/api/admin/games/<game_id>/manual-cards")
@admin_required
def admin_list_manual_cards(game_id):
    identities = [dict(r) for r in db().execute(
        "SELECT * FROM card_identities WHERE game_id=? AND source_type='manual-override' ORDER BY canonical_name", (game_id,)
    )]
    for identity in identities:
        identity["attributes"] = jload(identity["attributes"], {})
        printings = [dict(p) for p in db().execute("SELECT * FROM printings WHERE identity_id=?", (identity["id"],))]
        for printing in printings:
            printing["attributes"] = jload(printing["attributes"], {})
            variants = [dict(v) for v in db().execute("SELECT * FROM variants WHERE printing_id=?", (printing["id"],))]
            for variant in variants:
                variant["attributes"] = jload(variant["attributes"], {})
            printing["variants"] = variants
        identity["printings"] = printings
    return jsonify(identities)


@app.post("/api/admin/games/<game_id>/manual-cards")
@admin_required
def admin_create_manual_card(game_id):
    if not db().execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone():
        return jsonify({"error": "game not found"}), 404
    p = request.get_json(force=True)
    name = (p.get("canonical_name") or "").strip()
    if not name:
        return jsonify({"error": "Kartenname ist erforderlich"}), 400
    key = slug(p.get("key") or name)

    set_id = p.get("set_id")
    if set_id:
        if not db().execute("SELECT 1 FROM sets WHERE id=? AND game_id=?", (set_id, game_id)).fetchone():
            return jsonify({"error": "Set nicht gefunden"}), 404
    else:
        set_code = (p.get("new_set_code") or "").strip()
        if not set_code:
            return jsonify({"error": "Set auswählen oder neuen Set-Code angeben"}), 400
        set_id = f"{game_id}-{slug(set_code)}"
        if not db().execute("SELECT 1 FROM sets WHERE id=?", (set_id,)).fetchone():
            db().execute(
                "INSERT INTO sets VALUES(?,?,?,?,?,?,?,?,?,'manual-override')",
                (set_id, game_id, set_code, p.get("new_set_name") or set_code, "Set", None, None, "[]", "#6366f1"),
            )

    identity_id = f"{game_id}-card-{key}"
    existing_identity = db().execute("SELECT source_type FROM card_identities WHERE id=?", (identity_id,)).fetchone()
    if existing_identity and existing_identity["source_type"] != "manual-override":
        return jsonify({"error": "Dieser Kartenschlüssel gehört zu einer importierten Karte – bitte einen anderen Schlüssel wählen."}), 409
    db().execute(
        """INSERT INTO card_identities VALUES(?,?,?,?,?,?,'manual-override') ON CONFLICT(id) DO UPDATE SET
           canonical_name=excluded.canonical_name,rules_text=excluded.rules_text,card_type=excluded.card_type,attributes=excluded.attributes""",
        (identity_id, game_id, name, p.get("rules_text") or "", p.get("card_type") or "Unknown", json.dumps(p.get("attributes") or {}, ensure_ascii=False)),
    )

    language = p.get("language") or "EN"
    collector_number = p.get("collector_number") or key
    printing_id = f"{game_id}-print-{slug(set_id)}-{key}-{language.lower()}"
    existing_printing = db().execute("SELECT source_type FROM printings WHERE id=?", (printing_id,)).fetchone()
    if existing_printing and existing_printing["source_type"] != "manual-override":
        return jsonify({"error": "Dieses Printing gehört zu importierten Daten – bitte einen anderen Schlüssel oder eine andere Sprache wählen."}), 409
    db().execute(
        """INSERT INTO printings VALUES(?,?,?,?,?,?,?,?,'manual-override') ON CONFLICT(id) DO UPDATE SET
           identity_id=excluded.identity_id,set_id=excluded.set_id,collector_number=excluded.collector_number,rarity=excluded.rarity""",
        (printing_id, identity_id, game_id, set_id, collector_number, language, p.get("rarity") or "Unknown", json.dumps({}, ensure_ascii=False)),
    )

    finish = p.get("finish") or "Normal"
    is_parallel = 1 if p.get("is_parallel") else 0
    variant_code = "normal" if finish in ("Normal", "None") else slug(finish)
    variant_id = f"{printing_id}-{variant_code}"
    db().execute(
        """INSERT INTO variants VALUES(?,?,?,?,?,?,?,'manual-override',?) ON CONFLICT(id) DO UPDATE SET
           finish=excluded.finish,is_parallel=excluded.is_parallel,attributes=excluded.attributes""",
        (variant_id, printing_id, game_id, variant_code, finish, key, is_parallel,
         json.dumps({"imageUrl": p["image_url"]} if p.get("image_url") else {}, ensure_ascii=False)),
    )
    db().commit()
    return jsonify({"identity_id": identity_id, "printing_id": printing_id, "variant_id": variant_id}), 201


@app.delete("/api/admin/games/<game_id>/manual-cards/<identity_id>")
@admin_required
def admin_delete_manual_card(game_id, identity_id):
    row = db().execute("SELECT source_type FROM card_identities WHERE id=? AND game_id=?", (identity_id, game_id)).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["source_type"] != "manual-override":
        return jsonify({"error": "Nur manuell angelegte Karten können hier gelöscht werden."}), 400
    variant_filter = "variant_id IN (SELECT v.id FROM variants v JOIN printings p ON p.id=v.printing_id WHERE p.identity_id=?)"
    for table in ("collection_entries", "deck_cards", "watchlist_entries", "named_watchlist_entries", "marketplace_products", "price_observations"):
        db().execute(f"DELETE FROM {table} WHERE {variant_filter}", (identity_id,))
    db().execute("DELETE FROM variants WHERE printing_id IN (SELECT id FROM printings WHERE identity_id=?)", (identity_id,))
    db().execute("DELETE FROM printings WHERE identity_id=?", (identity_id,))
    db().execute("DELETE FROM card_identities WHERE id=?", (identity_id,))
    db().commit()
    return jsonify({"deleted": True})




@app.get("/api/games/<game_id>/sets")
@login_required
def game_sets(game_id):
    uid = user_id()
    rows = []
    for s in db().execute("SELECT * FROM sets WHERE game_id=? ORDER BY release_date DESC", (game_id,)):
        values = db().execute(
            f"""SELECT COUNT(DISTINCT p.identity_id) total,
                COUNT(DISTINCT CASE WHEN c.quantity>0 THEN p.identity_id END) owned,
                COALESCE(SUM(c.quantity * {latest_price_sql('v')}),0) value
                FROM printings p JOIN variants v ON v.printing_id=p.id
                LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
                WHERE p.set_id=?""",
            (uid, s["id"]),
        ).fetchone()
        variants_total = db().execute("SELECT COUNT(*) FROM variants v JOIN printings p ON p.id=v.printing_id WHERE p.set_id=?", (s["id"],)).fetchone()[0]
        variants_owned = db().execute("SELECT COUNT(DISTINCT v.id) FROM variants v JOIN printings p ON p.id=v.printing_id JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? AND c.quantity>0 WHERE p.set_id=?", (uid, s["id"])).fetchone()[0]
        playset_owned = db().execute(
            """SELECT COUNT(*) FROM (
                SELECT p.identity_id, COALESCE(SUM(c.quantity),0) qty
                FROM printings p JOIN variants v ON v.printing_id=p.id
                LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
                WHERE p.set_id=?
                GROUP BY p.identity_id
                HAVING qty>=4
            )""",
            (uid, s["id"]),
        ).fetchone()[0]
        item = dict(s)
        item["classifications"] = jload(item["classifications"], [])
        item["release_dates"] = release_dates_for_set(s["id"], item["release_date"])
        item["visual_version"] = set_visual_version(s)
        item.update({"owned": values["owned"], "total": values["total"], "value": round(values["value"], 2), "base_completion": round(values["owned"] / values["total"] * 100) if values["total"] else 0, "foil_completion": round(variants_owned / variants_total * 100) if variants_total else 0, "master_completion": round(variants_owned / variants_total * 100) if variants_total else 0, "playset_completion": round(playset_owned / values["total"] * 100) if values["total"] else 0})
        rows.append(item)
    rows.sort(key=cmp_to_key(lambda a, b: compare_set_release(a, b, "desc")))
    return jsonify(rows)


def release_dates_for_set(set_id, fallback=None):
    product_dates = [row[0] for row in db().execute(
        """SELECT DISTINCT json_extract(attributes,'$.releaseProductReleaseDate')
           FROM printings WHERE set_id=?
             AND json_extract(attributes,'$.releaseProductReleaseDate') IS NOT NULL
           ORDER BY 1""", (set_id,)
    )]
    return product_dates or ([fallback] if fallback else [])


def natural_code_key(value):
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"(\d+)", value or "") if part)


def compare_set_release(a, b, direction="desc"):
    """Sort by latest release, then natural set code in the same direction."""
    a_date = max(a.get("release_dates") or ([a.get("release_date")] if a.get("release_date") else []), default=None)
    b_date = max(b.get("release_dates") or ([b.get("release_date")] if b.get("release_date") else []), default=None)
    if bool(a_date) != bool(b_date):
        return -1 if a_date else 1
    multiplier = -1 if direction == "desc" else 1
    if a_date != b_date:
        return (-1 if a_date < b_date else 1) * multiplier
    a_code, b_code = natural_code_key(a.get("code")), natural_code_key(b.get("code"))
    if a_code == b_code:
        return 0
    return (-1 if a_code < b_code else 1) * multiplier


def card_rows(set_id, uid):
    return db().execute(
        f"""SELECT i.id identity_id,i.canonical_name,i.rules_text,i.card_type,i.attributes identity_attrs,
            p.id printing_id,p.collector_number,p.language,p.rarity,p.set_id,
            v.id variant_id,v.variant_code,v.finish,v.is_parallel,v.source_type,
            COALESCE(SUM(c.quantity),0) quantity,MAX(c.condition) condition,
            CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted,
            {latest_price_sql('v')} price
            FROM card_identities i JOIN printings p ON p.identity_id=i.id
            JOIN variants v ON v.printing_id=p.id
            LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
            WHERE p.set_id=?
            GROUP BY v.id""", (uid, uid, set_id)
    ).fetchall()


def game_card_rows(game_id, uid):
    return db().execute(
        f"""SELECT i.id identity_id,i.canonical_name,i.rules_text,i.card_type,i.attributes identity_attrs,
            p.id printing_id,p.collector_number,p.language,p.rarity,p.set_id,
            v.id variant_id,v.variant_code,v.finish,v.is_parallel,v.source_type,
            COALESCE(SUM(c.quantity),0) quantity,MAX(c.condition) condition,
            CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted,
            {latest_price_sql('v')} price
            FROM card_identities i JOIN printings p ON p.identity_id=i.id
            JOIN variants v ON v.printing_id=p.id
            LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
            WHERE p.game_id=?
            GROUP BY v.id""", (uid, uid, game_id)
    ).fetchall()


def serialize_card_rows(raw, language, mode, query, sort, game_id, rarity="", foil_mode="", rarities=None, costs=None, colors=None, inkwell=""):
    raw = [dict(row) for row in raw]
    if language != "combined":
        raw = [row for row in raw if row["language"] == language]
    if query:
        raw = [row for row in raw if query in row["canonical_name"].lower() or query in row["collector_number"].lower()]
    identities = {}
    for row in raw:
        row["identity_attrs"] = jload(row["identity_attrs"], {})
        identities.setdefault(row["identity_id"], []).append(row)
    # When the foil filter is engaged, cards should be represented by their foil (Silver)
    # printing -- both the shown art/price and the "owned" count -- instead of the normal one.
    foil_display = bool(foil_mode)
    cards = []
    for variants in identities.values():
        preferred_language = language if language != "combined" else ("EN" if any(v["language"] == "EN" for v in variants) else variants[0]["language"])
        language_variants = [v for v in variants if v["language"] == preferred_language]
        representative = next((v for v in language_variants if v["variant_code"] in ("standard", "normal")), language_variants[0])
        foil_variant = next((v for v in language_variants if v["finish"] == "Silver"), None)
        if foil_display and foil_variant:
            representative = foil_variant
        quantity = foil_variant["quantity"] if (foil_display and foil_variant) else sum(v["quantity"] for v in variants)
        cards.append({
            **representative,
            "variants": variants,
            "languages": sorted({v["language"] for v in variants}),
            "language_count": len({v["language"] for v in variants}),
            "quantity": quantity,
            "owned_variants": sum(1 for v in variants if v["quantity"] > 0),
            "variant_count": len(language_variants),
            "value": round(sum(v["quantity"] * (v["price"] or 0) for v in variants), 2),
            "watchlisted": any(v["watchlisted"] for v in variants),
            "foil_quantity": foil_variant["quantity"] if foil_variant else 0,
        })
    unfiltered_cards = cards
    if mode == "owned": cards = [card for card in cards if card["quantity"] > 0]
    if mode == "missing": cards = [card for card in cards if card["quantity"] == 0]
    if rarity: cards = [card for card in cards if card["rarity"] == rarity]
    if foil_mode == "owned": cards = [card for card in cards if card["foil_quantity"] > 0]
    if foil_mode == "missing": cards = [card for card in cards if card["foil_quantity"] == 0]
    if rarities:
        selected_ranks = {LORCANA_RARITY_KEYS[key] for key in rarities if key in LORCANA_RARITY_KEYS}
        cards = [card for card in cards if rarity_rank(game_id, card["rarity"]) in selected_ranks]
    if costs:
        cards = [card for card in cards if str(card["identity_attrs"].get("cost")) in costs]
    if colors:
        cards = [card for card in cards if any(part in (card["identity_attrs"].get("color") or "").split("-") for part in colors)]
    if inkwell in ("true", "false"):
        want = inkwell == "true"
        cards = [card for card in cards if bool(card["identity_attrs"].get("inkwell")) == want]
    sorters = {
        "name": lambda card: card["canonical_name"],
        "rarity": lambda card: (rarity_rank(game_id, card["rarity"]), card["collector_number"]),
        "value": lambda card: -max((variant["price"] or 0) for variant in card["variants"]),
        "quantity": lambda card: -card["quantity"],
        "missing": lambda card: (card["quantity"] > 0, card["collector_number"]),
        "number": lambda card: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", card["collector_number"])],
    }
    cards.sort(key=sorters.get(sort, sorters["number"]))
    all_variants = [variant for variants in identities.values() for variant in variants]
    total = len(unfiltered_cards)
    owned = sum(1 for card in unfiltered_cards if card["quantity"] > 0)
    foil_variants = [variant for variant in all_variants if variant["finish"] != "Normal"]
    playset_owned = sum(1 for card in unfiltered_cards if card["quantity"] >= 4)
    stats = {
        "owned": owned,
        "total": total,
        "base": round(owned / total * 100) if total else 0,
        "foil": round(sum(1 for variant in foil_variants if variant["quantity"] > 0) / max(1, len(foil_variants)) * 100),
        "master": round(sum(1 for variant in all_variants if variant["quantity"] > 0) / max(1, len(all_variants)) * 100),
        "value": round(sum(variant["quantity"] * (variant["price"] or 0) for variant in all_variants), 2),
        "missing": max(0, total - owned),
        "variant_total": len(all_variants),
        "variant_owned": sum(1 for variant in all_variants if variant["quantity"] > 0),
        "foil_total": len(foil_variants),
        "foil_owned": sum(1 for variant in foil_variants if variant["quantity"] > 0),
        "playset_total": total,
        "playset_owned": playset_owned,
        "playset": round(playset_owned / total * 100) if total else 0,
    }
    return cards, stats


@app.get("/api/sets/<set_id>/cards")
@login_required
def set_cards(set_id):
    uid = user_id()
    set_row = db().execute("SELECT s.*,g.name game_name,g.short_name,g.languages,g.accent game_accent FROM sets s JOIN games g ON g.id=s.game_id WHERE s.id=?", (set_id,)).fetchone()
    if not set_row:
        return jsonify({"error": "set not found"}), 404
    language = request.args.get("language", "combined")
    mode = request.args.get("mode", "all")
    query = request.args.get("q", "").strip().lower()
    sort = request.args.get("sort", "number")
    rarity = request.args.get("rarity", "")
    foil = request.args.get("foil", "")
    selected_rarities = [value for value in request.args.get("rarities", "").split(",") if value]
    selected_costs = [value for value in request.args.get("costs", "").split(",") if value]
    selected_colors = [value for value in request.args.get("colors", "").split(",") if value]
    inkwell = request.args.get("inkwell", "")
    cards, stats = serialize_card_rows(card_rows(set_id, uid), language, mode, query, sort, set_row["game_id"], rarity, foil, selected_rarities, selected_costs, selected_colors, inkwell)
    rarity_sql = "SELECT DISTINCT rarity FROM printings WHERE set_id=?" + ("" if language == "combined" else " AND language=?")
    rarity_params = (set_id,) if language == "combined" else (set_id, language)
    rarity_options = sorted({r["rarity"] for r in db().execute(rarity_sql, rarity_params)}, key=lambda r: rarity_rank(set_row["game_id"], r))
    meta = dict(set_row)
    meta["classifications"] = jload(meta["classifications"], [])
    meta["languages"] = jload(meta["languages"], [])
    meta["release_dates"] = release_dates_for_set(set_id, meta["release_date"])
    return jsonify({"set": meta, "cards": cards, "stats": stats, "rarities": rarity_options})


@app.get("/api/games/<game_id>/cards")
@login_required
def game_cards(game_id):
    uid = user_id()
    game = db().execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not game:
        return jsonify({"error": "game not found"}), 404
    language = request.args.get("language", "combined")
    mode = request.args.get("mode", "all")
    query = request.args.get("q", "").strip().lower()
    sort = request.args.get("sort", "number")
    set_order = request.args.get("set_order", "desc")
    rarity = request.args.get("rarity", "")
    foil = request.args.get("foil", "")
    selected_rarities = [value for value in request.args.get("rarities", "").split(",") if value]
    selected_costs = [value for value in request.args.get("costs", "").split(",") if value]
    selected_colors = [value for value in request.args.get("colors", "").split(",") if value]
    inkwell = request.args.get("inkwell", "")
    if set_order not in {"asc", "desc"}:
        set_order = "desc"
    grouped_raw = {}
    for row in game_card_rows(game_id, uid):
        grouped_raw.setdefault(row["set_id"], []).append(row)
    groups = []
    aggregate = {"owned": 0, "total": 0, "value": 0.0, "variant_total": 0, "variant_owned": 0, "foil_total": 0, "foil_owned": 0, "playset_total": 0, "playset_owned": 0}
    sets = []
    for row in db().execute("SELECT * FROM sets WHERE game_id=?", (game_id,)).fetchall():
        item = dict(row)
        item["release_dates"] = release_dates_for_set(row["id"], row["release_date"])
        sets.append(item)
    sets.sort(key=cmp_to_key(lambda a, b: compare_set_release(a, b, set_order)))
    rarity_sql = "SELECT DISTINCT p.rarity FROM printings p JOIN sets s ON s.id=p.set_id WHERE s.game_id=?" + ("" if language == "combined" else " AND p.language=?")
    rarity_params = (game_id,) if language == "combined" else (game_id, language)
    rarity_options = sorted({r["rarity"] for r in db().execute(rarity_sql, rarity_params)}, key=lambda r: rarity_rank(game_id, r))
    for set_row in sets:
        cards, stats = serialize_card_rows(grouped_raw.get(set_row["id"], []), language, mode, query, sort, game_id, rarity, foil, selected_rarities, selected_costs, selected_colors, inkwell)
        meta = dict(set_row)
        meta["classifications"] = jload(meta["classifications"], [])
        meta["visual_version"] = set_visual_version(set_row)
        for key in aggregate:
            aggregate[key] += stats[key]
        if not cards:
            continue
        groups.append({"set": meta, "cards": cards, "stats": stats})
    aggregate.update({
        "missing": max(0, aggregate["total"] - aggregate["owned"]),
        "base": round(aggregate["owned"] / aggregate["total"] * 100) if aggregate["total"] else 0,
        "foil": round(aggregate["foil_owned"] / max(1, aggregate["foil_total"]) * 100),
        "master": round(aggregate["variant_owned"] / max(1, aggregate["variant_total"]) * 100),
        "playset": round(aggregate["playset_owned"] / max(1, aggregate["playset_total"]) * 100),
        "value": round(aggregate["value"], 2),
    })
    return jsonify({"game": {**dict(game), "languages": jload(game["languages"], [])}, "groups": groups, "stats": aggregate, "rarities": rarity_options})


@app.get("/api/cards/<identity_id>")
@login_required
def card_detail(identity_id):
    uid = user_id()
    identity = db().execute("SELECT * FROM card_identities WHERE id=?", (identity_id,)).fetchone()
    if not identity: return jsonify({"error":"card not found"}), 404
    variants = db().execute(
        f"""SELECT v.*,p.collector_number,p.language,p.rarity,p.set_id,s.name set_name,s.code set_code,
            COALESCE(c.quantity,0) quantity,COALESCE(c.condition,'Near Mint') condition,c.notes,
            CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted,
            {latest_price_sql('v')} price,{latest_price_sql('v','low')} price_low,
            {latest_price_sql('v','avg30')} price_avg30,{latest_price_meta_sql('v','provider_id')} price_provider,
            {latest_price_meta_sql('v','currency')} price_currency,{latest_price_meta_sql('v','observed_at')} price_observed_at
            FROM variants v JOIN printings p ON p.id=v.printing_id JOIN sets s ON s.id=p.set_id
            LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
            WHERE p.identity_id=? ORDER BY p.language,s.release_date,s.code,p.collector_number,v.is_parallel,v.variant_code""", (uid,uid,identity_id)
    ).fetchall()
    variant_rows = []
    for item in variants:
        variant = dict(item)
        variant_attrs = jload(variant.get("attributes"), {})
        search_term = f'{identity["canonical_name"]} {variant["collector_number"]} {variant["finish"]}'
        market_mapping = db().execute(
            "SELECT * FROM marketplace_products WHERE variant_id=? AND provider_id=?",
            (variant["id"], variant.get("price_provider")),
        ).fetchone() if variant.get("price_provider") else None
        market_mapping = dict(market_mapping) if market_mapping else None
        mapping_attrs = jload((market_mapping or {}).get("attributes"), {})
        variant["price_native_currency"] = mapping_attrs.get("sourceCurrency")
        variant["price_native"] = mapping_attrs.get("sourceTrend")
        variant["price_native_low"] = mapping_attrs.get("sourceLow")
        variant["price_exchange_rate"] = mapping_attrs.get("eurRate")
        variant["price_exchange_date"] = mapping_attrs.get("exchangeDate")
        if variant.get("price_provider"):
            provider_metrics = {}
            for metric_row in db().execute(
                """SELECT metric,amount FROM price_observations
                   WHERE variant_id=? AND provider_id=? AND metric IN ('low','avg30')
                   ORDER BY observed_at DESC""",
                (variant["id"], variant["price_provider"]),
            ):
                provider_metrics.setdefault(metric_row["metric"], metric_row["amount"])
            variant["price_low"] = provider_metrics.get("low")
            variant["price_avg30"] = provider_metrics.get("avg30")
        if variant["game_id"] == "lorcana":
            variant["price_source"] = "Cardmarket" if variant.get("price_provider") == "cardmarket" else variant_attrs.get("priceSource") or "Cardmarket"
            variant["price_url"] = (market_mapping or {}).get("source_url") or variant_attrs.get("priceUrl") or f"https://www.cardmarket.com/en/Lorcana/Products/Search?searchString={quote_plus(search_term)}"
            variant["image_source"] = variant_attrs.get("imageSource") or "Ravensburger-Kartenbild via LorcanaJSON"
            variant["image_source_url"] = variant_attrs.get("imageSourceUrl") or "https://lorcanajson.org/"
        elif variant["game_id"] == "one-piece":
            variant["price_source"] = "Cardmarket"
            variant["price_url"] = (market_mapping or {}).get("source_url") or f"https://www.cardmarket.com/en/OnePiece/Products/Search?searchString={quote_plus(search_term)}"
            variant["image_source"] = variant_attrs.get("imageSource") or "Offizieller One Piece Card-Katalog"
            variant["image_source_url"] = variant_attrs.get("imageSourceUrl") or "https://en.onepiece-cardgame.com/cardlist/"
        else:
            provider_labels = {"tcgplayer": "TCGplayer", "yuyutei": "Yuyutei"}
            variant["price_source"] = provider_labels.get(variant.get("price_provider")) or ("Yuyutei" if variant["language"] == "JP" else "TCGplayer")
            fallback_url = (
                f"https://yuyu-tei.jp/sell/hocg/s/{variant['set_code'].lower()}"
                if variant["language"] == "JP"
                else f"https://www.tcgplayer.com/search/all/product?q={quote_plus(search_term)}&view=grid"
            )
            variant["price_url"] = (market_mapping or {}).get("source_url") or fallback_url
            variant["image_source"] = "Offizieller hololive Card-Katalog"
            variant["image_source_url"] = variant_attrs.get("imageSourceUrl") or "https://en.hololive-official-cardgame.com/cardlist/"
        variant["edition_label"] = variant_attrs.get("editionLabel")
        variant_rows.append(variant)
    result = dict(identity)
    result["attributes"] = jload(result["attributes"], {})
    result["variants"] = variant_rows
    result["language_variations"] = [
        {
            "language": language,
            "printing_count": len({v["printing_id"] for v in variant_rows if v["language"] == language}),
            "variant_ids": [v["id"] for v in variant_rows if v["language"] == language],
        }
        for language in sorted({v["language"] for v in variant_rows})
    ]
    return jsonify(result)


@app.post("/api/collection")
@login_required
def update_collection():
    payload = request.get_json(force=True)
    variant_id = payload.get("variant_id")
    condition = payload.get("condition", "Near Mint")
    existing = db().execute("SELECT * FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=?", (user_id(), variant_id, condition)).fetchone()
    before = existing["quantity"] if existing else 0
    quantity = max(0, int(payload.get("quantity", before + int(payload.get("delta", 0)))))
    if quantity == 0:
        db().execute("DELETE FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=?", (user_id(), variant_id, condition))
    elif existing:
        db().execute("UPDATE collection_entries SET quantity=?,notes=COALESCE(?,notes) WHERE id=?", (quantity,payload.get("notes"),existing["id"]))
    else:
        db().execute("INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes) VALUES(?,?,?,?,?)", (user_id(),variant_id,condition,quantity,payload.get("notes")))
    db().commit()
    return jsonify({"variant_id": variant_id, "before": before, "quantity": quantity})


@app.post("/api/watchlist")
@login_required
def toggle_watchlist():
    payload = request.get_json(force=True)
    variant_id = payload.get("variant_id")
    list_id = payload.get("list_id")
    if not list_id:
        variant = db().execute("SELECT game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
        if not variant: return jsonify({"error":"variant not found"}), 404
        target = db().execute("SELECT id FROM named_watchlists WHERE user_id=? AND game_id=? ORDER BY is_default DESC,id LIMIT 1", (user_id(),variant["game_id"])).fetchone()
        list_id = target["id"] if target else None
    owned_list = db().execute("SELECT id FROM named_watchlists WHERE id=? AND user_id=?", (list_id,user_id())).fetchone()
    if not owned_list: return jsonify({"error":"watchlist not found"}), 404
    existing = db().execute("SELECT id FROM named_watchlist_entries WHERE list_id=? AND variant_id=?", (list_id,variant_id)).fetchone()
    if existing:
        db().execute("DELETE FROM named_watchlist_entries WHERE id=?", (existing["id"],)); active = False
    else:
        db().execute("INSERT INTO named_watchlist_entries(list_id,variant_id,created_at) VALUES(?,?,?)", (list_id,variant_id,now_iso())); active = True
    db().commit()
    return jsonify({"active": active,"list_id":list_id})


@app.route("/api/watchlists", methods=["GET","POST"])
@login_required
def watchlists():
    if request.method == "POST":
        payload=request.get_json(force=True); game_id=payload.get("game_id"); name=payload.get("name","Neue Watchlist").strip()[:80]
        if not name: return jsonify({"error":"name required"}),400
        try:
            cur=db().execute("INSERT INTO named_watchlists(user_id,game_id,name,is_default,created_at) VALUES(?,?,?,?,?)",(user_id(),game_id,name,0,now_iso()));db().commit()
        except sqlite3.IntegrityError: return jsonify({"error":"Eine Watchlist mit diesem Namen existiert bereits."}),409
        return jsonify({"id":cur.lastrowid,"name":name,"game_id":game_id,"count":0,"value":0}),201
    game_id=request.args.get("game_id")
    rows=db().execute(f"""SELECT nw.*,COUNT(nwe.id) count,COALESCE(SUM({latest_price_sql('v')}),0) value
      FROM named_watchlists nw LEFT JOIN named_watchlist_entries nwe ON nwe.list_id=nw.id
      LEFT JOIN variants v ON v.id=nwe.variant_id WHERE nw.user_id=? AND nw.game_id=?
      GROUP BY nw.id ORDER BY nw.is_default DESC,nw.created_at""",(user_id(),game_id)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlists/<int:list_id>",methods=["PATCH","DELETE"])
@login_required
def manage_watchlist(list_id):
    row=db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?",(list_id,user_id())).fetchone()
    if not row:return jsonify({"error":"watchlist not found"}),404
    if request.method=="DELETE":
        if row["is_default"]: return jsonify({"error":"Die Standardliste kann nicht gelöscht werden."}),400
        db().execute("DELETE FROM named_watchlists WHERE id=?",(list_id,));db().commit();return jsonify({"deleted":True})
    name=request.get_json(force=True).get("name","").strip()[:80]
    if not name:return jsonify({"error":"name required"}),400
    db().execute("UPDATE named_watchlists SET name=? WHERE id=?",(name,list_id));db().commit();return jsonify({"saved":True})


@app.get("/api/watchlists/<int:list_id>/cards")
@login_required
def watchlist_cards(list_id):
    owned=db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?",(list_id,user_id())).fetchone()
    if not owned:return jsonify({"error":"watchlist not found"}),404
    q=request.args.get("q","").strip(); language=request.args.get("language","all"); set_id=request.args.get("set_id",""); finish=request.args.get("finish",""); sort=request.args.get("sort","added")
    rows = db().execute(
        f"""SELECT v.id variant_id,v.finish,i.id identity_id,i.canonical_name,p.collector_number,p.language,
            p.rarity,s.id set_id,s.name set_name,g.id game_id,g.short_name game_name,g.accent,{latest_price_sql('v')} price,
            COALESCE(c.quantity,0) quantity,nwe.created_at
            FROM named_watchlist_entries nwe JOIN variants v ON v.id=nwe.variant_id JOIN printings p ON p.id=v.printing_id
            JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
            LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? WHERE nwe.list_id=? GROUP BY v.id""", (user_id(),list_id)
    ).fetchall()
    result=[dict(r) for r in rows]
    if q: result=[r for r in result if q.lower() in r["canonical_name"].lower() or q.lower() in r["collector_number"].lower()]
    if language!="all":result=[r for r in result if r["language"]==language]
    if set_id:result=[r for r in result if r["set_id"]==set_id]
    if finish:result=[r for r in result if r["finish"]==finish]
    sorters={"name":lambda r:r["canonical_name"],"number":lambda r:r["collector_number"],"price_high":lambda r:-(r["price"] or 0),"price_low":lambda r:r["price"] if r["price"] is not None else float("inf"),"added":lambda r:r["created_at"]}
    result.sort(key=sorters.get(sort,sorters["added"]),reverse=sort=="added")
    return jsonify({"list":dict(owned),"cards":result})


@app.get("/api/watchlist")
@login_required
def legacy_watchlist():
    game_id=request.args.get("game_id") or db().execute("SELECT id FROM games ORDER BY name LIMIT 1").fetchone()[0]
    target=db().execute("SELECT id FROM named_watchlists WHERE user_id=? AND game_id=? ORDER BY is_default DESC,id LIMIT 1",(user_id(),game_id)).fetchone()
    if not target:return jsonify([])
    response=watchlist_cards(target["id"])
    return jsonify(response.json["cards"] if hasattr(response,"json") else [])


@app.get("/api/collection")
@login_required
def collection_browser():
    game_id=request.args.get("game_id"); q=request.args.get("q","").strip().lower(); set_id=request.args.get("set_id","")
    language=request.args.get("language","all"); rarity=request.args.get("rarity",""); finish=request.args.get("finish","")
    mode=request.args.get("mode","all"); sort=request.args.get("sort","number")
    rows=db().execute(f"""SELECT v.id variant_id,v.variant_code,v.finish,v.is_parallel,v.source_type,p.id printing_id,
      i.id identity_id,i.canonical_name,i.card_type,i.attributes identity_attrs,p.collector_number,p.language,p.rarity,
      s.id set_id,s.name set_name,s.code set_code,s.release_date,g.accent,
      SUM(c.quantity) quantity,MAX(c.condition) condition,
      COALESCE(SUM(c.quantity*COALESCE({latest_price_sql('v')},0)),0) value,{latest_price_sql('v')} price,
      CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted
      FROM collection_entries c JOIN variants v ON v.id=c.variant_id JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
      WHERE c.user_id=? AND v.game_id=? AND c.quantity>0 GROUP BY v.id""",(user_id(),user_id(),game_id)).fetchall()
    cards=[]
    for item in rows:
        r=dict(item);r["identity_attrs"]=jload(r["identity_attrs"],{})
        if q and q not in r["canonical_name"].lower() and q not in r["collector_number"].lower():continue
        if set_id and r["set_id"]!=set_id:continue
        if language!="all" and r["language"]!=language:continue
        if rarity and r["rarity"]!=rarity:continue
        if finish and r["finish"]!=finish:continue
        if mode=="duplicates" and r["quantity"]<2:continue
        if mode=="watchlisted" and not r["watchlisted"]:continue
        r.update({"variants":[dict(r)],"variant_count":1,"owned_variants":1})
        cards.append(r)
    sorters={"number":lambda c:[int(x) if x.isdigit() else x for x in re.split(r"(\d+)",c["collector_number"])],"name":lambda c:c["canonical_name"],"set":lambda c:(c["release_date"],c["collector_number"]),"rarity":lambda c:(rarity_rank(game_id,c["rarity"]),c["collector_number"]),"value":lambda c:-c["value"],"quantity":lambda c:-c["quantity"]}
    cards.sort(key=sorters.get(sort,sorters["number"]))
    sets=[dict(r) for r in db().execute("SELECT id,code,name FROM sets WHERE game_id=? ORDER BY release_date DESC",(game_id,))]
    return jsonify({"cards":cards,"sets":sets,"stats":{"variants":len(cards),"copies":sum(c["quantity"] for c in cards),"value":round(sum((c["value"] or 0) for c in cards),2)}})


@app.get("/api/games/<game_id>/formats")
@login_required
def game_formats(game_id):
    return jsonify(FORMAT_PROFILES.get(game_id,[]))




@app.get("/api/deckbuilder/catalog")
@login_required
def deck_catalog():
    game_id = request.args.get("game_id")
    q = request.args.get("q", "").strip().lower()
    set_id = request.args.get("set_id", "")
    language = request.args.get("language", "EN" if game_id == "one-piece" else "all")
    card_type = request.args.get("type", "")
    color = request.args.get("color", "")
    selected_types = [value for value in request.args.get("types", "").split(",") if value]
    selected_colors = [value for value in request.args.get("colors", "").split(",") if value]
    selected_costs = [value for value in request.args.get("costs", "").split(",") if value.isdigit()]
    selected_attributes = [value for value in request.args.get("attributes", "").split(",") if value]
    selected_kinds = [value for value in request.args.get("kinds", "").split(",") if value]
    selected_bloom_levels = [value for value in request.args.get("bloomLevels", "").split(",") if value]
    inkwell = request.args.get("inkwell", "")
    sort = request.args.get("sort", "number")
    limit = min(20_000, max(24, request.args.get("limit", 72, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))

    # The deck catalogue shows one base printing per gameplay identity and
    # language. Alternate art, foil and reprint variants stay available in the
    # card detail view, but do not flood the builder browser.
    if game_id == "one-piece":
        base_variant_filter = (
            "v.id=(SELECT v2.id FROM variants v2 JOIN printings p2 ON p2.id=v2.printing_id "
            "JOIN sets s2 ON s2.id=p2.set_id WHERE p2.identity_id=i.id AND p2.language=p.language "
            "ORDER BY CASE WHEN v2.variant_code IN ('standard','normal') THEN 0 "
            "WHEN v2.is_parallel=0 THEN 1 ELSE 2 END,COALESCE(s2.release_date,'9999-12-31'),p2.id,v2.id LIMIT 1)"
        )
    else:
        base_variant_filter = (
            "v.id=(SELECT v2.id FROM variants v2 WHERE v2.printing_id=p.id "
            "ORDER BY CASE WHEN v2.variant_code IN ('standard','normal') THEN 0 "
            "WHEN v2.is_parallel=0 THEN 1 ELSE 2 END,v2.id LIMIT 1)"
        )
    filters = ["v.game_id=?", base_variant_filter]
    values = [game_id]
    if game_id == "lorcana":
        filters.append("lower(s.set_type)<>'quest'")
    if q:
        filters.append("(lower(i.canonical_name) LIKE ? OR lower(p.collector_number) LIKE ?)")
        values.extend((f"%{q}%", f"%{q}%"))
    if set_id:
        filters.append("p.set_id=?")
        values.append(set_id)
    if language != "all":
        filters.append("p.language=?")
        values.append(language)
    if card_type:
        filters.append("i.card_type=?")
        values.append(card_type)
    if color:
        filters.append("json_extract(i.attributes,'$.color')=?")
        values.append(color)
    if selected_types:
        filters.append(f"i.card_type IN ({','.join('?' for _ in selected_types)})")
        values.extend(selected_types)
    if selected_colors:
        filters.append("("+" OR ".join(
            "instr('-'||replace(COALESCE(json_extract(i.attributes,'$.color'),''),'/','-')||'-', '-'||?||'-')>0"
            for _ in selected_colors
        )+")")
        values.extend(selected_colors)
    if selected_costs:
        filters.append(f"CAST(json_extract(i.attributes,'$.cost') AS INTEGER) IN ({','.join('?' for _ in selected_costs)})")
        values.extend(selected_costs)
    if selected_attributes:
        filters.append("("+" OR ".join(
            "instr('/'||COALESCE(json_extract(i.attributes,'$.attribute'),'')||'/', '/'||?||'/')>0"
            for _ in selected_attributes
        )+")")
        values.extend(selected_attributes)
    if inkwell in {"true", "false"}:
        filters.append("CAST(json_extract(i.attributes,'$.inkwell') AS INTEGER)=?")
        values.append(1 if inkwell == "true" else 0)
    if selected_bloom_levels:
        filters.append(f"json_extract(i.attributes,'$.bloomLevel') IN ({','.join('?' for _ in selected_bloom_levels)})")
        values.extend(selected_bloom_levels)
    if selected_kinds:
        kind_clauses = []
        for kind in selected_kinds:
            if kind == "oshi":
                kind_clauses.append("i.card_type IN ('Oshi','Oshi holomem','推しホロメン')")
            elif kind == "holomem":
                kind_clauses.append("(lower(i.card_type)='holomem' OR i.card_type='ホロメン')")
            elif kind == "buzz":
                kind_clauses.append("(lower(i.card_type)='buzz holomem' OR i.card_type='Buzzホロメン')")
            elif kind == "support":
                kind_clauses.append("(lower(i.card_type) LIKE 'support%' OR i.card_type LIKE 'サポート%')")
            elif kind == "cheer":
                kind_clauses.append("i.card_type IN ('Cheer','エール')")
        if kind_clauses:
            filters.append("(" + " OR ".join(kind_clauses) + ")")
    where = " AND ".join(filters)
    order_by = {
        "number": "p.collector_number COLLATE NOCASE,i.canonical_name COLLATE NOCASE",
        "name": "i.canonical_name COLLATE NOCASE,p.collector_number COLLATE NOCASE",
        "cost": "CAST(COALESCE(json_extract(i.attributes,'$.cost'),0) AS REAL),i.canonical_name COLLATE NOCASE",
        "rarity": f"{rarity_case_sql(game_id, 'p.rarity')},p.collector_number COLLATE NOCASE",
    }.get(sort, "p.collector_number COLLATE NOCASE,i.canonical_name COLLATE NOCASE")
    joins = """FROM variants v JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id"""
    total = db().execute(f"SELECT COUNT(*) {joins} WHERE {where}", values).fetchone()[0]
    rows = db().execute(
        f"""SELECT v.id variant_id,v.finish,v.is_parallel,p.id printing_id,i.id identity_id,
          i.canonical_name,i.card_type,i.attributes identity_attrs,p.collector_number,p.language,
          p.rarity,p.set_id,s.name set_name,s.code set_code,{latest_price_sql('v')} price,
          COALESCE(SUM(c.quantity),0) owned
          {joins} LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
          WHERE {where} GROUP BY v.id ORDER BY {order_by} LIMIT ? OFFSET ?""",
        [user_id(), *values, limit, offset],
    ).fetchall()
    deck_ruleset = game_deck_ruleset(game_id)
    result = []
    for item in rows:
        card = dict(item)
        card["attributes"] = jload(card.pop("identity_attrs"), {})
        card["suggested_zone"] = zone_for_card_type(deck_ruleset, card["card_type"])
        result.append(card)
    sets = [dict(row) for row in db().execute(
        "SELECT id,code,name FROM sets WHERE game_id=? ORDER BY release_date DESC", (game_id,)
    )]
    types = [row[0] for row in db().execute(
        "SELECT DISTINCT card_type FROM card_identities WHERE game_id=? AND card_type<>'' ORDER BY card_type", (game_id,)
    )]
    colors = [row[0] for row in db().execute(
        """SELECT DISTINCT json_extract(attributes,'$.color') color FROM card_identities
           WHERE game_id=? AND color IS NOT NULL AND color<>'' ORDER BY color""", (game_id,)
    )]
    return jsonify({
        "cards": result, "sets": sets, "types": types, "colors": colors,
        "pagination": {"offset": offset, "limit": limit, "total": total, "has_more": offset + len(result) < total},
    })


@app.route("/api/decks",methods=["GET","POST"])
@login_required
def decks():
    if request.method=="POST":
        p=request.get_json(force=True);game_id=p.get("game_id");profiles=FORMAT_PROFILES.get(game_id,[]);format_id=p.get("format_id") or (profiles[0]["id"] if profiles else "standard");stamp=now_iso()
        cur=db().execute("INSERT INTO decks(user_id,game_id,name,format_id,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(user_id(),game_id,p.get("name","Neues Deck").strip()[:100] or "Neues Deck",format_id,"",stamp,stamp));db().commit()
        return jsonify({"id":cur.lastrowid}),201
    game_id=request.args.get("game_id")
    rows=db().execute("""SELECT d.*,COALESCE(SUM(CASE WHEN dc.zone='main' THEN dc.quantity ELSE 0 END),0) main_count,
      COALESCE(SUM(CASE WHEN dc.zone='leader' THEN dc.quantity ELSE 0 END),0) leader_count,
      COALESCE(SUM(CASE WHEN dc.zone='don' THEN dc.quantity ELSE 0 END),0) don_count,
      COALESCE(SUM(CASE WHEN dc.zone='oshi' THEN dc.quantity ELSE 0 END),0) oshi_count,
      COALESCE(SUM(CASE WHEN dc.zone='cheer' THEN dc.quantity ELSE 0 END),0) cheer_count,
      COALESCE(SUM(dc.quantity),0) total_count,
      COALESCE(d.cover_variant_id,(SELECT dc2.variant_id FROM deck_cards dc2 WHERE dc2.deck_id=d.id
       ORDER BY CASE dc2.zone WHEN 'leader' THEN 0 WHEN 'oshi' THEN 0 WHEN 'main' THEN 1 ELSE 2 END,dc2.id LIMIT 1)) effective_cover_variant_id
      FROM decks d LEFT JOIN deck_cards dc ON dc.deck_id=d.id
      WHERE d.user_id=? AND d.game_id=? GROUP BY d.id ORDER BY d.updated_at DESC""",(user_id(),game_id)).fetchall()
    result=[dict(r) for r in rows]
    for row in result:row["cover_variant_id"]=row.pop("effective_cover_variant_id")
    summaries=deck_market_summaries([row["id"] for row in result],user_id())
    for row in result:row.update(summaries.get(row["id"],empty_deck_market_summary()))
    return jsonify(result)


def empty_deck_market_summary():
    return {
        "deck_value":0.0,"deck_unpriced_copies":0,"required_copies":0,"owned_copies":0,
        "missing_copies":0,"complete_entries":0,"missing_entries":0,"missing_cost":0.0,
        "missing_unpriced_copies":0,
    }


def deck_market_summaries(deck_ids,uid):
    """Calculate deck value and collection coverage once per physical variant."""
    if not deck_ids:return {}
    placeholders=",".join("?" for _ in deck_ids)
    rows=db().execute(
        f"""SELECT dc.deck_id,dc.variant_id,SUM(dc.quantity) required,{latest_price_sql('v')} price,
          COALESCE((SELECT SUM(c.quantity) FROM collection_entries c
                    WHERE c.user_id=? AND c.variant_id=dc.variant_id),0) collection_quantity
          FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id
          WHERE dc.deck_id IN ({placeholders}) GROUP BY dc.deck_id,dc.variant_id""",
        [uid,*deck_ids],
    ).fetchall()
    summaries={deck_id:empty_deck_market_summary() for deck_id in deck_ids}
    for row in rows:
        summary=summaries[row["deck_id"]]
        required=int(row["required"] or 0);available=int(row["collection_quantity"] or 0)
        owned=min(required,available);missing=max(0,required-owned);price=row["price"]
        summary["required_copies"]+=required;summary["owned_copies"]+=owned;summary["missing_copies"]+=missing
        summary["complete_entries"]+=int(missing==0);summary["missing_entries"]+=int(missing>0)
        if price is None:
            summary["deck_unpriced_copies"]+=required;summary["missing_unpriced_copies"]+=missing
        else:
            summary["deck_value"]+=required*price;summary["missing_cost"]+=missing*price
    for summary in summaries.values():
        summary["deck_value"]=round(summary["deck_value"],2);summary["missing_cost"]=round(summary["missing_cost"],2)
    return summaries


def deck_validation(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return None
    cards=[dict(r) for r in db().execute("""SELECT dc.*,i.canonical_name,i.attributes,p.collector_number,p.language,p.rarity,i.card_type,s.set_type
      FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id WHERE dc.deck_id=?""",(deck_id,))]
    game=deck["game_id"]
    profile=next((p for p in FORMAT_PROFILES.get(game,[]) if p["id"]==deck["format_id"]),FORMAT_PROFILES.get(game,[{}])[0])
    zone_ids=[z["id"] for z in profile.get("zones",[])] or ["main"]
    counts={zone:sum(c["quantity"] for c in cards if c["zone"]==zone) for zone in zone_ids}
    ruleset=DECK_RULESETS.get(game_deck_ruleset(game))
    if ruleset:
        errors,warnings=ruleset["validate"](deck,cards,counts)
    else:
        # No bespoke ruleset assigned: fall back to checking each zone's target
        # count from the format profile, without any game-specific extra rules.
        errors,warnings=[],[]
        for zone in profile.get("zones",[]):
            target=zone.get("target")
            if target and counts.get(zone["id"],0)!=target:
                errors.append(f'{zone["name"]}: {counts.get(zone["id"],0)}/{target} Karten.')
    return {"valid":not errors,"errors":errors,"warnings":warnings,"counts":counts,"rules_url":profile.get("rules_url"),"profile":profile}


def default_one_piece_don(quantity):
    if quantity<=0:return None
    row=db().execute(
        f"""SELECT v.id variant_id,v.finish,v.is_parallel,i.id identity_id,i.canonical_name,i.card_type,
          p.collector_number,p.language,p.rarity,p.set_id,s.code set_code,{latest_price_sql('v')} price
          FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
          JOIN sets s ON s.id=p.set_id WHERE v.id='one-piece-print-don-008-en-standard'"""
    ).fetchone()
    if not row:return None
    card=dict(row);card.update({
        "zone":"don","quantity":quantity,"collection_quantity":quantity,"owned_quantity":quantity,
        "missing_quantity":0,"auto_filled":True,
    })
    return card


@app.route("/api/decks/<int:deck_id>",methods=["GET","PATCH","DELETE"])
@login_required
def deck_detail_api(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    if request.method=="DELETE":db().execute("DELETE FROM decks WHERE id=?",(deck_id,));db().commit();return jsonify({"deleted":True})
    if request.method=="PATCH":
        p=request.get_json(force=True);cover_variant_id=deck["cover_variant_id"]
        if "cover_variant_id" in p:
            requested_cover=p.get("cover_variant_id") or None
            if requested_cover and not db().execute("SELECT 1 FROM deck_cards WHERE deck_id=? AND variant_id=? AND quantity>0",(deck_id,requested_cover)).fetchone():
                return jsonify({"error":"Als Cover kann nur eine Karte aus diesem Deck gewählt werden."}),400
            cover_variant_id=requested_cover
        db().execute("UPDATE decks SET name=?,format_id=?,notes=?,cover_variant_id=?,updated_at=? WHERE id=?",(p.get("name",deck["name"])[:100],p.get("format_id",deck["format_id"]),p.get("notes",deck["notes"] or ""),cover_variant_id,now_iso(),deck_id));db().commit();return jsonify({"saved":True,"cover_variant_id":cover_variant_id,"validation":deck_validation(deck_id)})
    cards=[dict(r) for r in db().execute(f"""SELECT dc.*,v.finish,i.id identity_id,i.canonical_name,i.card_type,p.collector_number,p.language,p.rarity,
      s.code set_code,{latest_price_sql('v')} price,
      COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=v.id),0) collection_quantity
      FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id
      JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
      WHERE dc.deck_id=? ORDER BY dc.zone,p.collector_number""",(user_id(),deck_id))]
    remaining_owned={}
    for card in cards:remaining_owned[card["variant_id"]]=int(card["collection_quantity"] or 0)
    for card in cards:
        available=remaining_owned[card["variant_id"]];owned=min(card["quantity"],available)
        card["owned_quantity"]=owned;card["missing_quantity"]=max(0,card["quantity"]-owned)
        remaining_owned[card["variant_id"]]=max(0,available-owned)
    summary=deck_market_summaries([deck_id],user_id()).get(deck_id,empty_deck_market_summary())
    explicit_don=sum(card["quantity"] for card in cards if card["zone"]=="don")
    default_don=default_one_piece_don(max(0,10-explicit_don)) if deck["game_id"]=="one-piece" else None
    return jsonify({"deck":dict(deck),"cards":cards,"validation":deck_validation(deck_id),"summary":summary,"default_don":default_don})


def parse_deck_text(text, game_id):
    """Match a pasted decklist (from this app or another deckbuilder) onto catalog variants.

    Accepts two line styles, tried in order: a collector-number code (the same syntax the
    collection importer uses, e.g. "4 OP01-016 EN") and, when that yields no candidate, a bare
    card name (what most external deckbuilder exports use, e.g. "4 Belle - Strange and Beautiful").
    Name matches can span multiple printings/finishes of the same card; the most recently
    released printing is picked automatically and the alternative count is surfaced so the result
    is inspectable in the preview rather than silently guessed.
    """
    deck_ruleset = game_deck_ruleset(game_id)
    results = []
    for line_no, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line or line.startswith("//") or line.startswith("#"): continue
        qty_match = re.match(r"^\s*(\d+)\s*[xX]?\s+(.+)$", line)
        qty = int(qty_match.group(1)) if qty_match else 1
        rest = qty_match.group(2).strip() if qty_match else line
        number_candidates = []
        number_match = re.match(r"^([A-Za-z0-9-]+(?:/\d+)?)\s*(DE|EN|JP)?\s*$", rest, re.I)
        if number_match:
            number, lang = number_match.group(1), (number_match.group(2) or "EN").upper()
            number_candidates = db().execute(
                """SELECT v.id variant_id,v.finish,i.canonical_name,i.card_type,p.collector_number,p.language,s.name set_name
                   FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE v.game_id=? AND UPPER(p.collector_number)=UPPER(?) AND p.language=?""",
                (game_id, number, lang)
            ).fetchall()
        selected, status, message, alt_count = None, "not_found", "Karte nicht im Katalog gefunden", 0
        if len(number_candidates) > 1:
            # The same collector_number string can be reused across unrelated products (e.g. a
            # starter-deck parallel reprint) — that's a genuine collision, not a card to guess at.
            status, message = "ambiguous", f"{len(number_candidates)} Karten teilen sich diese Nummer – bitte im Builder manuell hinzufügen."
        elif len(number_candidates) == 1:
            selected = dict(number_candidates[0])
        else:
            name_rows = db().execute(
                """SELECT v.id variant_id,v.finish,i.canonical_name,i.card_type,p.collector_number,p.language,s.name set_name
                   FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE v.game_id=? AND i.canonical_name=? COLLATE NOCASE
                   ORDER BY s.release_date DESC, CASE WHEN p.language='EN' THEN 0 ELSE 1 END, CASE WHEN v.finish='Normal' THEN 0 ELSE 1 END""",
                (game_id, rest)
            ).fetchall()
            if name_rows:
                selected = dict(name_rows[0])
                alt_count = max(0, len(name_rows) - 1)
        if selected:
            status, message = "matched", None
        results.append({
            "line": line_no, "original": original.strip(),
            "quantity": qty,
            "status": status,
            "message": message,
            "alt_printings": alt_count,
            "match": selected,
            "zone": zone_for_card_type(deck_ruleset, selected["card_type"]) if selected else None,
        })
    return results


@app.post("/api/decks/<int:deck_id>/import/preview")
@login_required
def deck_import_preview(deck_id):
    deck = db().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id())).fetchone()
    if not deck: return jsonify({"error": "deck not found"}), 404
    p = request.get_json(force=True)
    return jsonify(parse_deck_text(p.get("text", ""), deck["game_id"]))


@app.post("/api/decks/<int:deck_id>/import/apply")
@login_required
def deck_import_apply(deck_id):
    deck = db().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id())).fetchone()
    if not deck: return jsonify({"error": "deck not found"}), 404
    p = request.get_json(force=True)
    rows = parse_deck_text(p.get("text", ""), deck["game_id"])
    profile = next((item for item in FORMAT_PROFILES.get(deck["game_id"], []) if item["id"] == deck["format_id"]), None)
    allowed_zones = {item["id"] for item in (profile or {}).get("zones", [])} or {"main"}
    if p.get("strategy") == "replace":
        db().execute("DELETE FROM deck_cards WHERE deck_id=?", (deck_id,))
    applied, skipped_zone = 0, 0
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        if row["zone"] not in allowed_zones:
            skipped_zone += 1; continue
        vid = row["match"]["variant_id"]
        existing = db().execute("SELECT * FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?", (deck_id, vid, row["zone"])).fetchone()
        quantity = (existing["quantity"] if existing else 0) + row["quantity"]
        if existing:
            db().execute("UPDATE deck_cards SET quantity=? WHERE id=?", (quantity, existing["id"]))
        else:
            db().execute("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)", (deck_id, vid, row["zone"], quantity))
        applied += 1
    db().execute("UPDATE decks SET updated_at=? WHERE id=?", (now_iso(), deck_id)); db().commit()
    return jsonify({"applied": applied, "matched": sum(1 for r in rows if r["status"] == "matched"), "skipped_zone": skipped_zone, "total": len(rows)})


@app.post("/api/decks/<int:deck_id>/cards")
@login_required
def update_deck_card(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    p=request.get_json(force=True);variant_id=p.get("variant_id")
    card=db().execute("""SELECT v.game_id,i.card_type,s.set_type FROM variants v JOIN printings pr ON pr.id=v.printing_id
      JOIN card_identities i ON i.id=pr.identity_id JOIN sets s ON s.id=pr.set_id WHERE v.id=?""",(variant_id,)).fetchone()
    if not card or card["game_id"]!=deck["game_id"]:return jsonify({"error":"Karte gehört nicht zu diesem TCG."}),400
    if deck["game_id"]=="lorcana" and str(card["set_type"] or "").lower()=="quest":return jsonify({"error":"Quest-Karten sind nicht für Lorcana-Constructed-Decks zulässig."}),400
    profile=next((item for item in FORMAT_PROFILES.get(deck["game_id"],[]) if item["id"]==deck["format_id"]),None)
    allowed_zones={item["id"] for item in (profile or {}).get("zones",[])} or {"main"}
    suggested_zone=zone_for_card_type(game_deck_ruleset(deck["game_id"]),card["card_type"])
    zone=p.get("zone")
    if not zone or zone=="auto":zone=suggested_zone
    if zone not in allowed_zones:return jsonify({"error":"Diese Zone gehört nicht zum gewählten Regelprofil."}),400
    existing=db().execute("SELECT * FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?",(deck_id,variant_id,zone)).fetchone();before=existing["quantity"] if existing else 0;quantity=max(0,int(p.get("quantity",before+int(p.get("delta",0)))))
    if quantity==0:db().execute("DELETE FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?",(deck_id,variant_id,zone))
    elif existing:db().execute("UPDATE deck_cards SET quantity=? WHERE id=?",(quantity,existing["id"]))
    else:db().execute("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)",(deck_id,variant_id,zone,quantity))
    if quantity==0 and deck["cover_variant_id"]==variant_id and not db().execute("SELECT 1 FROM deck_cards WHERE deck_id=? AND variant_id=? AND quantity>0",(deck_id,variant_id)).fetchone():
        db().execute("UPDATE decks SET cover_variant_id=NULL WHERE id=?",(deck_id,))
    db().execute("UPDATE decks SET updated_at=? WHERE id=?",(now_iso(),deck_id));db().commit()
    return jsonify({"quantity":quantity,"before":before,"zone":zone,"validation":deck_validation(deck_id)})


@app.get("/api/search")
@login_required
def global_search():
    q = request.args.get("q", "").strip()
    game_id = request.args.get("game_id")
    if len(q) < 2: return jsonify([])
    like = f"%{q}%"
    rows = db().execute(
        f"""SELECT DISTINCT i.id identity_id,i.canonical_name,p.collector_number,p.language,p.set_id,s.name set_name,
          g.id game_id,g.short_name game_name,g.accent,v.id variant_id,v.finish,{latest_price_sql('v')} price,
          CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted
          FROM card_identities i JOIN printings p ON p.identity_id=i.id JOIN variants v ON v.printing_id=p.id
          JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=i.game_id
          WHERE (? IS NULL OR g.id=?) AND (i.canonical_name LIKE ? OR p.collector_number LIKE ? OR s.name LIKE ?) LIMIT 24""", (user_id(),game_id,game_id,like,like,like)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


def parse_import(text, game_id, language="EN", condition="Near Mint"):
    results = []
    for line_no, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line: continue
        parts = [p.strip() for p in line.split(";")]
        match = re.match(r"^\s*(?:(\d+)\s*[xX]?\s+)?([A-Za-z0-9-]+(?:/\d+)?)\s*(DE|EN|JP)?", parts[0], re.I)
        if not match:
            results.append({"line":line_no,"original":original,"status":"not_found","message":"Format nicht erkannt"}); continue
        qty = int(match.group(1) or (parts[1] if len(parts)>1 and parts[1].isdigit() else 1))
        number = match.group(2)
        lang = (match.group(3) or (parts[2] if len(parts)>2 and parts[2].upper() in ("DE","EN","JP") else language)).upper()
        variant_hint = parts[3].lower() if len(parts)>3 else "standard"
        cond = parts[4] if len(parts)>4 else condition
        candidates = db().execute(
            """SELECT v.id variant_id,v.variant_code,v.finish,p.collector_number,p.language,i.canonical_name,s.name set_name
               FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
               WHERE v.game_id=? AND UPPER(p.collector_number)=UPPER(?) AND p.language=?""", (game_id,number,lang)
        ).fetchall()
        exact = [dict(c) for c in candidates if variant_hint in (c["variant_code"].lower(),c["finish"].lower())]
        selected = exact[0] if exact else (dict(candidates[0]) if candidates else None)
        status = "matched" if selected and (exact or len(candidates)==1) else "ambiguous" if selected else "not_found"
        results.append({"line":line_no,"original":original,"quantity":qty,"number":number,"language":lang,"condition":cond,"status":status,"match":selected,"inferred":not bool(match.group(3))})
    return results


@app.post("/api/import/preview")
@login_required
def import_preview():
    p = request.get_json(force=True)
    return jsonify(parse_import(p.get("text",""),p.get("game_id","lorcana"),p.get("language","EN"),p.get("condition","Near Mint")))


def parse_json_backup(entries):
    """Match rows from a DeckLedger /api/export.json payload back onto current catalog variants.

    collector_number can legitimately be an empty string (e.g. One Piece DON!! tokens), so
    only language/finish/game/set_code are required. Matching also pins canonical_name because
    misprints and alternate-art tokens can otherwise share (game,set_code,number,language,finish)
    across genuinely different variants — without it, an ambiguous match would silently apply to
    the wrong physical card.
    """
    results = []
    for idx, entry in enumerate(entries, 1):
        number = str(entry.get("collector_number") or "").strip()
        lang = str(entry.get("language") or "").strip().upper()
        finish = str(entry.get("finish") or "").strip()
        game_name = str(entry.get("game") or "").strip()
        set_code = str(entry.get("set_code") or "").strip()
        name = str(entry.get("canonical_name") or "").strip()
        label = name or number or f"Zeile {idx}"
        if not (lang and finish and game_name and set_code and name):
            results.append({"line":idx,"original":label,"number":number,"language":lang,"status":"not_found","message":"Unvollständiger Eintrag"})
            continue
        candidates = db().execute(
            """SELECT v.id variant_id,v.finish,v.game_id,p.collector_number,p.language,i.canonical_name,s.name set_name
               FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
               JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
               WHERE g.name=? AND s.code=? AND UPPER(p.collector_number)=UPPER(?) AND p.language=? AND v.finish=? AND i.canonical_name=?""",
            (game_name, set_code, number, lang, finish, name)
        ).fetchall()
        selected = dict(candidates[0]) if len(candidates) == 1 else None
        status = "matched" if selected else ("ambiguous" if candidates else "not_found")
        results.append({
            "line": idx, "original": label, "number": number, "language": lang,
            "quantity": int(entry.get("quantity") or 0),
            "condition": entry.get("condition") or "Near Mint",
            "notes": entry.get("notes"),
            "status": status,
            "message": None if selected else ("Mehrdeutig – mehrere Varianten passen, manuell prüfen" if candidates else "Karte nicht im Katalog gefunden"),
            "match": selected,
        })
    return results


@app.post("/api/import/json/preview")
@login_required
def import_json_preview():
    p = request.get_json(force=True)
    return jsonify(parse_json_backup(p.get("collection") or []))


@app.post("/api/import/json/apply")
@login_required
def import_json_apply():
    p = request.get_json(force=True)
    rows = parse_json_backup(p.get("collection") or [])
    strategy = p.get("strategy", "add")
    changes = []
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        vid, cond, qty, notes = row["match"]["variant_id"], row["condition"], row["quantity"], row.get("notes")
        old = db().execute("SELECT quantity,notes FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=?", (user_id(),vid,cond)).fetchone()
        before, notes_before = (old["quantity"], old["notes"]) if old else (0, None)
        after = qty if strategy == "replace" else before + qty
        db().execute(
            "INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes) VALUES(?,?,?,?,?) ON CONFLICT(user_id,variant_id,condition) DO UPDATE SET quantity=excluded.quantity,notes=COALESCE(excluded.notes,collection_entries.notes)",
            (user_id(), vid, cond, after, notes)
        )
        changes.append({"variant_id":vid,"condition":cond,"before":before,"after":after,"notes_before":notes_before})
    games = sorted({row["match"]["game_id"] for row in rows if row.get("match")})
    cur = db().execute(
        "INSERT INTO import_operations(user_id,created_at,game_id,source_text,changes) VALUES(?,?,?,?,?)",
        (user_id(), now_iso(), ",".join(games) or "backup", "json-backup", json.dumps(changes))
    )
    db().commit()
    return jsonify({"operation_id":cur.lastrowid,"applied":len(changes),"matched":sum(1 for r in rows if r["status"]=="matched"),"total":len(rows)})


@app.post("/api/import/apply")
@login_required
def import_apply():
    p = request.get_json(force=True)
    rows = parse_import(p.get("text",""),p.get("game_id","lorcana"),p.get("language","EN"),p.get("condition","Near Mint"))
    changes = []
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        vid, cond, qty = row["match"]["variant_id"], row["condition"], row["quantity"]
        old = db().execute("SELECT quantity FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=?", (user_id(),vid,cond)).fetchone()
        before = old["quantity"] if old else 0
        after = qty if p.get("strategy") == "replace" else before + qty
        db().execute("INSERT INTO collection_entries(user_id,variant_id,condition,quantity) VALUES(?,?,?,?) ON CONFLICT(user_id,variant_id,condition) DO UPDATE SET quantity=excluded.quantity", (user_id(),vid,cond,after))
        changes.append({"variant_id":vid,"condition":cond,"before":before,"after":after})
    cur = db().execute("INSERT INTO import_operations(user_id,created_at,game_id,source_text,changes) VALUES(?,?,?,?,?)", (user_id(),now_iso(),p.get("game_id"),p.get("text",""),json.dumps(changes)))
    db().commit()
    return jsonify({"operation_id":cur.lastrowid,"applied":len(changes)})


@app.post("/api/import/<int:operation_id>/undo")
@login_required
def undo_import(operation_id):
    op = db().execute("SELECT * FROM import_operations WHERE id=? AND user_id=?", (operation_id,user_id())).fetchone()
    if not op or op["undone_at"]: return jsonify({"error":"operation unavailable"}), 404
    for change in jload(op["changes"],[]):
        if change["before"] == 0:
            db().execute("DELETE FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=?", (user_id(),change["variant_id"],change["condition"]))
        elif "notes_before" in change:
            db().execute("UPDATE collection_entries SET quantity=?,notes=? WHERE user_id=? AND variant_id=? AND condition=?", (change["before"],change["notes_before"],user_id(),change["variant_id"],change["condition"]))
        else:
            db().execute("UPDATE collection_entries SET quantity=? WHERE user_id=? AND variant_id=? AND condition=?", (change["before"],user_id(),change["variant_id"],change["condition"]))
    db().execute("UPDATE import_operations SET undone_at=? WHERE id=?", (now_iso(),operation_id)); db().commit()
    return jsonify({"undone":True})


@app.post("/api/settings")
@login_required
def save_settings():
    for key,value in request.get_json(force=True).items():
        db().execute("INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value", (user_id(),key,json.dumps(value)))
    db().commit(); return jsonify({"saved":True})


@app.get("/api/export.<fmt>")
@login_required
def export_collection(fmt):
    rows = db().execute(
        f"""SELECT g.name game,s.code set_code,s.name set_name,p.collector_number,i.canonical_name,p.language,v.finish,
          c.condition,c.quantity,c.notes,{latest_price_sql('v')} unit_price
          FROM collection_entries c JOIN variants v ON v.id=c.variant_id JOIN printings p ON p.id=v.printing_id
          JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
          WHERE c.user_id=? AND c.quantity>0 ORDER BY g.name,s.release_date,p.collector_number""", (user_id(),)
    ).fetchall()
    data = [dict(r) for r in rows]
    if fmt == "json":
        return Response(json.dumps({"exported_at":now_iso(),"collection":data},indent=2),mimetype="application/json",headers={"Content-Disposition":"attachment; filename=deckledger-collection.json"})
    out = io.StringIO(); writer = csv.DictWriter(out,fieldnames=data[0].keys() if data else ["game","set_code","collector_number","canonical_name","language","finish","condition","quantity"]); writer.writeheader(); writer.writerows(data)
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=deckledger-collection.csv"})


def remote_image_url(row):
    """Resolve a provider-owned card image without encoding provider rules in core data."""
    attributes = jload(row["variant_attributes"], {}) if "variant_attributes" in row.keys() else {}
    if attributes.get("imageUrl"):
        return attributes["imageUrl"]
    if row["game_id"] == "one-piece":
        host = "https://en.onepiece-cardgame.com" if row["language"] == "EN" else "https://www.onepiece-cardgame.com"
        suffix = "_p1" if row["variant_code"] in ("parallel", "manga") else ""
        return f'{host}/images/cardlist/card/{row["collector_number"]}{suffix}.png'

    if row["game_id"] == "hololive":
        expansion = row["set_code"]
        official_number = row["collector_number"]
        if expansion.startswith("BP"):
            expansion = f"h{expansion}"
            official_number = f"h{official_number}"
        host = "https://en.hololive-official-cardgame.com" if row["language"] == "EN" else "https://hololive-official-cardgame.com"
        listing = f"{host}/cardlist/cardsearch/?expansion={quote_plus(expansion)}"
        page = urlopen(Request(listing, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read().decode("utf-8", "ignore")
        prefix = "EN_" if row["language"] == "EN" else ""
        pattern = rf'(?:src|data-src)="([^"]*{re.escape(prefix + official_number)}[^"]*\.png[^"]*)"'
        match = re.search(pattern, page, re.I)
        if not match and row["language"] == "EN":
            # Some early cards were published only in the Japanese catalogue.
            fallback = f"https://hololive-official-cardgame.com/cardlist/cardsearch/?expansion={quote_plus(expansion)}"
            page = urlopen(Request(fallback, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read().decode("utf-8", "ignore")
            match = re.search(rf'(?:src|data-src)="([^"]*{re.escape(official_number)}[^"]*\.png[^"]*)"', page, re.I)
            return urljoin(fallback, match.group(1)) if match else None
        return urljoin(listing, match.group(1)) if match else None

    if row["game_id"] == "lorcana":
        # Lorcast exposes stable image URIs from its API; do not construct CDN URLs.
        base_name = re.sub(r"\s·\s(?:Awakened|New Journey|Altitude)$", "", row["canonical_name"])
        base_name = base_name.replace(" · ", " ")
        endpoint = f"https://api.lorcast.com/v0/cards/search?q={quote_plus(base_name)}&unique=prints"
        payload = json.loads(urlopen(Request(endpoint, headers={"User-Agent": "DeckLedger/0.1"}), timeout=12).read())
        results = payload.get("results", [])
        if results:
            return results[0].get("image_uris", {}).get("digital", {}).get("normal")
    return None


def cached_real_image(row, variant_id):
    """Return a local image path, deduplicated by provider URL when possible."""
    for directory in (IMAGE_CACHE, IMAGE_SOURCE_CACHE, IMAGE_LOCK_CACHE):
        directory.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", variant_id)
    legacy_data = IMAGE_CACHE / f"{safe_id}.img"
    legacy_mime = IMAGE_CACHE / f"{safe_id}.mime"
    attributes = jload(row["variant_attributes"], {}) if "variant_attributes" in row.keys() else {}
    if legacy_data.exists() and legacy_mime.exists() and not attributes.get("imageUrl"):
        return legacy_data, legacy_mime.read_text().strip(), f"variant-{safe_id}"
    url = remote_image_url(row)
    if not url:
        if legacy_data.exists() and legacy_mime.exists():
            return legacy_data, legacy_mime.read_text().strip(), f"variant-{safe_id}"
        return None

    source_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    data_path = IMAGE_SOURCE_CACHE / f"{source_key}.img"
    mime_path = IMAGE_SOURCE_CACHE / f"{source_key}.mime"
    if data_path.exists() and mime_path.exists():
        return data_path, mime_path.read_text().strip(), source_key

    lock_path = IMAGE_LOCK_CACHE / f"{source_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if data_path.exists() and mime_path.exists():
            return data_path, mime_path.read_text().strip(), source_key
        if legacy_data.exists() and legacy_mime.exists():
            # A hard link seeds the URL cache without duplicating 200–400 KB per
            # language/finish variant already present in the old cache.
            try:
                os.link(legacy_data, data_path)
            except FileExistsError:
                pass
            mime_path.write_text(legacy_mime.read_text().strip())
            return data_path, mime_path.read_text().strip(), source_key
        response = urlopen(
            Request(url, headers={"User-Agent": "DeckLedger/0.1", "Accept": "image/avif,image/webp,image/*"}),
            timeout=15,
        )
        payload = response.read(5_000_000)
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/") or len(payload) < 1000:
            return None
        data_path.write_bytes(payload)
        mime_path.write_text(content_type)
        return data_path, content_type, source_key


def cached_thumbnail(source_path: Path, cache_key: str) -> Path | None:
    """Create a compact list thumbnail once; full artwork stays untouched."""
    from PIL import Image, ImageOps

    IMAGE_THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    IMAGE_LOCK_CACHE.mkdir(parents=True, exist_ok=True)
    thumb_path = IMAGE_THUMB_CACHE / f"{cache_key}-360.webp"
    if thumb_path.exists():
        return thumb_path
    lock_path = IMAGE_LOCK_CACHE / f"thumb-{cache_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if thumb_path.exists():
            return thumb_path
        try:
            with Image.open(source_path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((360, 504), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(thumb_path, format="WEBP", quality=78, method=4)
            return thumb_path
        except Exception as error:
            app.logger.warning("Thumbnail generation failed for %s: %s", source_path, error)
            return None


def image_file_response(path: Path, content_type: str, source: str):
    response = send_file(path, mimetype=content_type, conditional=True, etag=True, max_age=31_536_000)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["X-Image-Source"] = source
    return response


@app.get("/art/<variant_id>.svg")
def card_art(variant_id):
    row = db().execute("""SELECT i.canonical_name,p.id printing_id,p.collector_number,p.rarity,p.language,
      v.variant_code,v.game_id,v.finish,v.attributes variant_attributes,g.accent,s.code set_code,s.accent set_accent
      FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
      JOIN games g ON g.id=v.game_id JOIN sets s ON s.id=p.set_id WHERE v.id=?""", (variant_id,)).fetchone()
    if not row: return Response(status=404)
    try:
        real_image = cached_real_image(row, variant_id)
        if real_image:
            image_path, content_type, cache_key = real_image
            if request.args.get("size") == "thumb":
                thumbnail = cached_thumbnail(image_path, cache_key)
                if thumbnail:
                    return image_file_response(thumbnail, "image/webp", "local-thumbnail-cache")
            return image_file_response(image_path, content_type, "local-provider-cache")
    except Exception as error:
        app.logger.warning("Card image provider failed for %s: %s", variant_id, error)
    title = xml_escape.escape(row["canonical_name"]); number=xml_escape.escape(row["collector_number"]); rarity=xml_escape.escape(row["rarity"]); finish=xml_escape.escape(row["finish"])
    seed=sum(ord(c) for c in variant_id); x=35+seed%160; y=70+(seed*7)%170
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="540" height="756" viewBox="0 0 540 756">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{row['accent']}"/><stop offset="1" stop-color="{row['set_accent']}"/></linearGradient><radialGradient id="r"><stop stop-color="#fff" stop-opacity=".7"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient><filter id="blur"><feGaussianBlur stdDeviation="28"/></filter></defs>
    <rect width="540" height="756" rx="28" fill="#111827"/><rect x="12" y="12" width="516" height="732" rx="22" fill="url(#g)"/>
    <path d="M12 470 C120 350 215 510 330 350 C420 226 482 286 528 180 V744 H12Z" fill="#050816" opacity=".66"/>
    <circle cx="{x}" cy="{y}" r="180" fill="url(#r)" filter="url(#blur)"/><circle cx="390" cy="250" r="120" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="3"/><circle cx="390" cy="250" r="82" fill="none" stroke="#fff" stroke-opacity=".15" stroke-width="2"/>
    <path d="M150 170 L255 85 L350 175 L436 115 L404 340 L267 430 L118 334Z" fill="#fff" opacity=".12" stroke="#fff" stroke-opacity=".35" stroke-width="3"/>
    <text x="38" y="58" font-family="Arial,sans-serif" font-size="20" font-weight="700" fill="#fff" opacity=".86">{number}</text><text x="500" y="58" text-anchor="end" font-family="Arial,sans-serif" font-size="17" fill="#fff" opacity=".8">{row['language']}</text>
    <rect x="28" y="540" width="484" height="176" rx="18" fill="#07101f" opacity=".9"/><text x="52" y="590" font-family="Arial,sans-serif" font-size="29" font-weight="800" fill="#fff">{title[:29]}</text><text x="52" y="624" font-family="Arial,sans-serif" font-size="16" fill="#cbd5e1">{rarity} · {finish}</text>
    <path d="M52 657 H460" stroke="#fff" stroke-opacity=".13"/><text x="52" y="687" font-family="Arial,sans-serif" font-size="13" fill="#94a3b8">DECKLEDGER CATALOGUE EDITION</text></svg>'''
    return Response(svg,mimetype="image/svg+xml",headers={"Cache-Control":"public, max-age=31536000, immutable"})


@app.get("/game-logo/<game_id>")
def game_logo(game_id):
    """Cache the official TCG wordmarks used by catalogue and game tiles."""
    source_url = GAME_LOGOS.get(game_id)
    if not source_url:
        return Response(status=404)
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    data_path = IMAGE_CACHE / f"brand-{game_id}.img"
    mime_path = IMAGE_CACHE / f"brand-{game_id}.mime"
    try:
        if data_path.exists() and mime_path.exists():
            payload, content_type = data_path.read_bytes(), mime_path.read_text().strip()
        else:
            response = urlopen(Request(source_url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "image/svg+xml,image/png,image/*"}), timeout=15)
            payload, content_type = response.read(1_000_000), response.headers.get_content_type()
            if not content_type.startswith("image/") or len(payload) < 100:
                return Response(status=502)
            data_path.write_bytes(payload)
            mime_path.write_text(content_type)
        return Response(payload, mimetype=content_type, headers={"Cache-Control": "public, max-age=604800", "X-Logo-Source": "official-provider-cache"})
    except Exception as error:
        app.logger.warning("Game logo provider failed for %s: %s", game_id, error)
        return Response(status=502)


def provider_html(url):
    request = Request(url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "text/html"})
    return urlopen(request, timeout=18).read(2_000_000).decode("utf-8", "ignore")


def remote_set_visual(set_row):
    """Resolve a set-specific visual from each game's official provider."""
    if set_row["game_id"] == "lorcana":
        path = LORCANA_PRODUCT_PATHS.get(set_row["code"])
        if not path:
            return None
        page_url = f"https://www.disneylorcana.com/en-US/product/{path}"
        page = provider_html(page_url)
        images = re.findall(r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"', page, re.I | re.S)
        header = next((src for src, alt in images if "header" in alt.lower() or "logo" in alt.lower()), None)
        return urljoin(page_url, header) if header else None

    if set_row["game_id"] == "hololive":
        for base in ("https://en.hololive-official-cardgame.com", "https://hololive-official-cardgame.com"):
            page_url = f"{base}/cardlist/"
            page = provider_html(page_url)
            pattern = rf'<a class="anchor" href="(/cardlist/cardsearch/\?expansion={re.escape(set_row["code"])})">(.*?)</a>'
            product = re.search(pattern, page, re.I | re.S)
            if product:
                image = re.search(r'<img[^>]+src="([^"]+)"', product.group(2), re.I)
                if image:
                    return urljoin(base, image.group(1))
        return None

    code = re.sub(r"[^a-z0-9]", "", set_row["code"].lower())
    category = "decks" if set_row["code"].startswith("ST-") else "boosters"
    filenames = [code]
    starter_number = re.fullmatch(r"st(\d{2})", code)
    if starter_number:
        number = int(starter_number.group(1))
        for first, last in ((1, 4), (8, 9), (15, 20), (23, 28), (31, 36)):
            if first <= number <= last:
                filenames.append(f"st{first:02d}-{last:02d}")
    for base in ("https://en.onepiece-cardgame.com", "https://www.onepiece-cardgame.com"):
        for filename in filenames:
            page_url = f"{base}/products/{category}/{filename}.php"
            try:
                page = provider_html(page_url)
            except Exception:
                continue
            images = re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', page, re.I)
            hero = next((src for src in images if "/images/products/" in src and re.search(r"/mv(?:_|\.)", src, re.I)), None)
            if hero:
                return urljoin(page_url, hero)
    return None


def set_wordmark(set_row):
    name = xml_escape.escape(set_row["name"])
    code = xml_escape.escape(set_row["code"])
    words = name.split()
    midpoint = max(1, math.ceil(len(words) / 2))
    first, second = " ".join(words[:midpoint]), " ".join(words[midpoint:])
    second_line = f'<text x="400" y="132" text-anchor="middle" class="name">{second}</text>' if second else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="220" viewBox="0 0 800 220">
      <style>.name{{font:700 40px Arial,sans-serif;fill:#eef3f9;letter-spacing:-1px}}.code{{font:700 16px Arial,sans-serif;fill:#9cabbc;letter-spacing:4px}}</style>
      <path d="M170 176h460" stroke="#64758a" stroke-opacity=".42"/><text x="400" y="86" text-anchor="middle" class="name">{first}</text>{second_line}<text x="400" y="202" text-anchor="middle" class="code">{code}</text>
    </svg>'''


def public_set_visual(set_row) -> Path | None:
    """Return a server-provided set visual before consulting any fallback."""
    stems = []
    for value in (set_row["id"], set_row["code"]):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip("-.")
        for candidate in (safe, safe.lower()):
            if candidate and candidate not in stems:
                stems.append(candidate)
    for stem in stems:
        for extension in PUBLIC_IMAGE_EXTENSIONS:
            candidate = PUBLIC_SET_DIR / f"{stem}{extension}"
            if candidate.is_file():
                return candidate
    return None


def set_visual_version(set_row) -> str:
    """Cache-bust set visuals whenever a public override changes in place."""
    visual = public_set_visual(set_row)
    if not visual:
        return "provider-v1"
    stat = visual.stat()
    return f"public-{stat.st_mtime_ns:x}-{stat.st_size:x}"


@app.get("/set-logo/<set_id>")
def set_logo(set_id):
    set_row = db().execute("SELECT id,game_id,code,name FROM sets WHERE id=?", (set_id,)).fetchone()
    if not set_row:
        return Response(status=404)
    public_visual = public_set_visual(set_row)
    if public_visual:
        response = send_file(public_visual, conditional=True, etag=True, max_age=0)
        # Public assets may be replaced in place. Browsers retain them locally
        # but revalidate cheaply, so a newly supplied file wins immediately.
        response.headers["Cache-Control"] = "public, no-cache"
        response.headers["X-Logo-Source"] = "public-set-asset"
        return response
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", set_id)
    data_path = IMAGE_CACHE / f"set-{safe_id}.img"
    mime_path = IMAGE_CACHE / f"set-{safe_id}.mime"
    try:
        if data_path.exists() and mime_path.exists():
            payload, content_type = data_path.read_bytes(), mime_path.read_text().strip()
        else:
            source_url = remote_set_visual(set_row)
            if not source_url:
                raise RuntimeError("no official set visual published")
            response = urlopen(Request(source_url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "image/avif,image/webp,image/*"}), timeout=18)
            payload, content_type = response.read(4_000_000), response.headers.get_content_type()
            if not content_type.startswith("image/") or len(payload) < 500:
                raise RuntimeError("invalid set visual response")
            data_path.write_bytes(payload)
            mime_path.write_text(content_type)
        return Response(payload, mimetype=content_type, headers={"Cache-Control":"public, max-age=604800", "X-Logo-Source":"official-set-provider-cache"})
    except Exception as error:
        app.logger.info("Set visual fallback for %s: %s", set_id, error)
        return Response(set_wordmark(set_row), mimetype="image/svg+xml", headers={"Cache-Control":"public, max-age=86400", "X-Logo-Source":"set-wordmark-fallback"})


@app.get("/op-filter-icon/<name>")
def op_filter_icon(name):
    if not re.fullmatch(r"(?:cost-(?:10|[1-9])\.png|(?:attribute-(?:slash|strike|special|ranged|wisdom)|color)\.svg)", name):
        return Response(status=404)
    path = PUBLIC_OP_ICON_DIR / name
    if not path.is_file():
        return Response(status=404)
    mimetype = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return send_file(path, mimetype=mimetype, conditional=True, etag=True, max_age=0)


@app.get("/lorcana-filter-icon/<name>")
def lorcana_filter_icon(name):
    if re.fullmatch(r"(?:amber|amethyst|emerald|ruby|sapphire|steel|cost|inkable)\.png", name):
        mimetype = "image/png"
    elif re.fullmatch(r"rarity-(?:common|uncommon|rare|super-rare|legendary|epic|enchanted|iconic|special)\.svg", name):
        mimetype = "image/svg+xml"
    else:
        return Response(status=404)
    path = PUBLIC_DIR / "icons" / "lorcana" / name
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype=mimetype, conditional=True, etag=True, max_age=0)


def card_back_placeholder(game_row):
    name = xml_escape.escape(game_row["short_name"] or game_row["name"])
    accent = game_row["accent"] or "#6366f1"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="560" viewBox="0 0 400 560">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{accent}" stop-opacity=".9"/><stop offset="1" stop-color="#11151f"/></linearGradient></defs>
      <rect width="400" height="560" rx="22" fill="url(#g)"/>
      <rect x="18" y="18" width="364" height="524" rx="14" fill="none" stroke="#ffffff" stroke-opacity=".28" stroke-width="3"/>
      <text x="200" y="290" text-anchor="middle" font-family="Arial,sans-serif" font-weight="700" font-size="34" fill="#ffffff">{name}</text>
    </svg>'''


@app.get("/card-back/<game_id>")
def card_back(game_id):
    if not re.fullmatch(r"[a-z0-9-]+", game_id):
        return Response(status=404)
    uploaded = CARD_BACK_UPLOAD_DIR / f"{game_id}.jpg"
    if uploaded.is_file():
        return send_file(uploaded, mimetype="image/jpeg", conditional=True, etag=True, max_age=0)
    path = PUBLIC_DIR / f"{game_id}-back.jpg"
    if path.is_file():
        return send_file(path, mimetype="image/jpeg", conditional=True, etag=True, max_age=604800)
    game_row = db().execute("SELECT short_name, name, accent FROM games WHERE id=?", (game_id,)).fetchone()
    if not game_row:
        return Response(status=404)
    return Response(card_back_placeholder(game_row), mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
def health():
    return jsonify({"status":"ok","database":os.path.basename(DB_PATH)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
