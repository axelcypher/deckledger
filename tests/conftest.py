"""Shared test setup.

The app opens its database at import time, so DATABASE_PATH has to point at a throwaway file
before `app` is imported -- which is why that happens here, at module level, rather than in a
fixture. Every test then starts from the same small catalogue (see sample_catalog) with no user
data. Nothing in the suite touches the network.
"""
import atexit
import os
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import fcntl  # noqa: F401
except ImportError:
    # deckledger/images.py and price_sync.py use flock() to serialise image downloads and price runs. The
    # module does not exist on Windows; a no-op stand-in lets the suite run on a dev machine.
    sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=0, LOCK_SH=0, LOCK_UN=0, LOCK_NB=0, flock=lambda *args: None)

DATA_DIR = Path(tempfile.mkdtemp(prefix="deckledger-tests-"))
os.environ["DATABASE_PATH"] = str(DATA_DIR / "deckledger.web.db")
atexit.register(shutil.rmtree, DATA_DIR, ignore_errors=True)
os.environ.pop("SECRET_KEY", None)

import deckledger  # noqa: E402
import catalog_sync  # noqa: E402

DEMO = ("demo", "deckledger")
ADMIN = ("admin", "admin")


def card(catalog, game, set_id, key, name, card_type, number, rarity, finishes=("Normal", "Holo"), language="EN", **attributes):
    """Adds one identity + printing + its variants; the first finish is the base variant."""
    identity_id, printing_id = f"{game}-card-{key}", f"{game}-print-{key}-{language.lower()}"
    catalog["identities"][identity_id] = {
        "id": identity_id, "game_id": game, "canonical_name": name, "rules_text": f"Rules of {name}.",
        "card_type": card_type, "attributes": attributes,
    }
    catalog["printings"][printing_id] = {
        "id": printing_id, "identity_id": identity_id, "game_id": game, "set_id": set_id,
        "collector_number": number, "language": language, "rarity": rarity, "attributes": {},
    }
    for position, finish in enumerate(finishes):
        code = finish.lower().replace(" ", "-")
        catalog["variants"][f"{printing_id}-{code}"] = {
            "id": f"{printing_id}-{code}", "printing_id": printing_id, "game_id": game,
            "variant_code": "normal" if position == 0 else code, "finish": finish, "artwork_id": key,
            "is_parallel": 0, "source_type": "test", "attributes": {},
        }
    return f"{printing_id}-{finishes[0].lower().replace(' ', '-')}"


def sample_catalog():
    catalog = {"sets": {}, "identities": {}, "printings": {}, "variants": {}, "sources": {}}
    for set_id, game, code, name, released in (
        ("vcard-test", "vcard", "1", "Test Set", "2025-01-31"), ("vcard-two", "vcard", "2", "Second Set", "2024-06-01"),
        ("lorcana-1", "lorcana", "TFC", "The First Chapter", "2025-01-31"),
        ("one-piece-op-01", "one-piece", "OP-01", "Romance Dawn", "2022-12-02"), ("one-piece-prb-01", "one-piece", "PRB-01", "Premium Booster", "2024-11-08"),
    ):
        catalog["sets"][set_id] = {
            "id": set_id, "game_id": game, "code": code, "name": name, "set_type": "Booster Set",
            "release_date": released, "printed_card_count": 0, "classifications": [], "accent": "#123456",
        }
    vt = dict(game="vcard", set_id="vcard-test", card_type="VT")
    card(catalog, key="ember8", name="Ember (PL8)", number="001", rarity="Uncommon", color="Fire", cost=8, finishes=("Normal", "Holo", "1st Edition"), **vt)
    card(catalog, key="ember9", name="Ember (PL9)", number="002", rarity="Rare", color="Fire", cost=9, **vt)
    card(catalog, key="tide8", name="Tide (PL8)", number="003", rarity="Uncommon", color="Water", cost=8, **vt)
    card(catalog, key="leaf8", name="Leaf (PL8)", number="004", rarity="Uncommon", color="Grass", cost=8, **vt)
    card(catalog, "vcard", "vcard-test", "sparky", "Sparky", "Mascot", "005", "Mascot", color="Fire", cost=None)
    card(catalog, "vcard", "vcard-test", "boost", "Boost", "Support", "006", "Support", color=None, cost=None)
    card(catalog, "vcard", "vcard-test", "boost-sr", "Boost", "Support", "007", "Secret Rare", finishes=("Holo",), color=None, cost=None)
    card(catalog, "vcard", "vcard-test", "topper", "Ember", "Box Topper", "BT-01", "Box Topper", finishes=("Normal",), color="Fire", cost=None)
    for index in range(1, 16):
        card(catalog, "vcard", "vcard-test", f"filler{index}", f"Filler {index}", "Support", f"{100 + index}", "Support", color=None, cost=None)
    # The same collector number in a second set (VCard and Lorcana restart at 1 in every set) ...
    card(catalog, "vcard", "vcard-two", "spark2", "Spark (PL8)", "VT", "001", "Uncommon", color="Electric", cost=8)
    # ... and a One Piece card reprinted under its original number in a later product.
    card(catalog, "one-piece", "one-piece-op-01", "op01-016", "Nami", "Character", "OP01-016", "R", finishes=("standard", "parallel"), color="Red", cost=1)
    card(catalog, "one-piece", "one-piece-prb-01", "prb-op01-016", "Nami", "Character", "OP01-016", "R", finishes=("standard",), color="Red", cost=1)
    card(catalog, "lorcana", "lorcana-1", "elsa", "Elsa - Snow Queen", "Character", "1", "Rare", finishes=("Normal", "Silver"), color="Amethyst", cost=4)
    card(catalog, "lorcana", "lorcana-1", "mickey", "Mickey Mouse - Detective", "Character", "2", "Common", finishes=("Normal", "Silver"), color="Sapphire", cost=2)
    return catalog


# Variant ids of the fixture catalogue the tests refer to by name.
EMBER8 = "vcard-print-ember8-en-normal"
EMBER8_HOLO = "vcard-print-ember8-en-holo"
EMBER9 = "vcard-print-ember9-en-normal"
TIDE8 = "vcard-print-tide8-en-normal"
LEAF8 = "vcard-print-leaf8-en-normal"
SPARKY = "vcard-print-sparky-en-normal"
BOOST = "vcard-print-boost-en-normal"
BOOST_SECRET = "vcard-print-boost-sr-en-holo"
TOPPER = "vcard-print-topper-en-normal"
ELSA = "lorcana-print-elsa-en-normal"
SPARK_SET_TWO = "vcard-print-spark2-en-normal"
NAMI = "one-piece-print-op01-016-en-standard"


def query(statement, args=()):
    connection = sqlite3.connect(deckledger.config.DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(statement, args)]
        connection.commit()
        return rows
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def fresh_database():
    connection = sqlite3.connect(deckledger.config.DB_PATH)
    connection.execute("PRAGMA foreign_keys=OFF")
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        connection.execute(f"DELETE FROM {table}")
    connection.commit()
    connection.close()
    deckledger.schema.init_database()
    catalog_sync.write_database(sample_catalog(), {"vcard", "lorcana", "one-piece"})
    yield


def login(credentials=DEMO):
    client = deckledger.app.test_client()
    if credentials:
        assert client.post("/login", data={"username": credentials[0], "password": credentials[1]}).status_code == 302
    return client


@pytest.fixture
def client():
    return login()


@pytest.fixture
def admin():
    return login(ADMIN)


@pytest.fixture
def anonymous():
    return login(None)


def stored_collection():
    return query(
        """SELECT variant_id,condition,quantity,notes,is_graded,grade_label,price_override,created_at,last_added_at
           FROM collection_entries ORDER BY user_id,variant_id,condition,is_graded,grade_label"""
    )
