"""Numbered schema migrations: each runs once, completely or not at all, and a database of any
age ends up with the same schema as a new one."""
import re
import sqlite3
import threading
import types
from pathlib import Path

import pytest

from conftest import deckledger
from deckledger import migrations

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(deckledger.__file__).parent.parent


def connect(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def run(path, **options):
    connection = connect(path)
    try:
        return migrations.migrate(connection, path, log=lambda message: None, **options)
    finally:
        connection.close()


def shape(path):
    """Tables with their columns, and indexes with theirs -- regardless of column order."""
    connection = connect(path)
    try:
        tables = {
            name: sorted((column["name"], column["type"], column["notnull"], column["dflt_value"], column["pk"]) for column in connection.execute(f"PRAGMA table_info({name})"))
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        }
        indexes = {}
        for table in tables:
            for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
                columns = tuple(row["name"] for row in connection.execute(f"PRAGMA index_info({index['name']})"))
                # Constraint indexes are named by position (sqlite_autoindex_x_1); what they cover is what counts.
                name = index["name"] if index["origin"] == "c" else f"{table}:{index['origin']}"
                indexes.setdefault(name, []).append((columns, index["unique"], index["partial"]))
        return tables, {name: sorted(entries) for name, entries in indexes.items()}
    finally:
        connection.close()


def step(name, statement=None, fail=False):
    def apply(connection, context):
        if statement:
            connection.execute(statement)
        if fail:
            raise RuntimeError("boom")
    return types.SimpleNamespace(NAME=name, apply=apply)


@pytest.fixture
def fresh(tmp_path):
    path = tmp_path / "fresh.db"
    run(path)
    return path


@pytest.fixture
def first_release(tmp_path):
    """A database as the first release created it, with a little of everything in it."""
    path = tmp_path / "old.db"
    connection = connect(path)
    connection.executescript((FIXTURES / "schema_first_release.sql").read_text(encoding="utf-8"))
    connection.executescript("""
      INSERT INTO users(id,username,display_name,password_hash,role,created_at) VALUES(1,'tobi','Tobi','hash','admin','2026-08-02T10:00:00+00:00');
      INSERT INTO games(id,module_id,name,short_name,module_version,languages,accent) VALUES('lorcana','disney-lorcana','Disney Lorcana','Lorcana','1.0.0','["DE","EN"]','#8b5cf6');
      INSERT INTO sets(id,game_id,code,name,set_type,release_date,printed_card_count,classifications,accent) VALUES('lorcana-1','lorcana','1','The First Chapter','Expansion','2023-08-18',204,'[]','#123456');
      INSERT INTO card_identities(id,game_id,canonical_name,rules_text,card_type,attributes) VALUES('elsa','lorcana','Elsa','','Character','{}');
      INSERT INTO printings(id,identity_id,game_id,set_id,collector_number,language,rarity,attributes) VALUES('elsa-en','elsa','lorcana','lorcana-1','1','EN','Rare','{}');
      INSERT INTO variants(id,printing_id,game_id,variant_code,finish,artwork_id,attributes) VALUES('elsa-en-normal','elsa-en','lorcana','normal','Normal','elsa','{}'),('elsa-en-silver','elsa-en','lorcana','silver','Silver','elsa','{}');
      INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes) VALUES(1,'elsa-en-normal','Near Mint',3,'signed'),(1,'elsa-en-normal','Played',1,NULL),(1,'elsa-en-silver','Near Mint',2,NULL);
      INSERT INTO watchlist_entries(user_id,variant_id,created_at) VALUES(1,'elsa-en-silver','2026-08-03T10:00:00+00:00');
      INSERT INTO decks(user_id,game_id,name,format_id,created_at,updated_at) VALUES(1,'lorcana','Eis','core','2026-08-04T10:00:00+00:00','2026-08-04T10:00:00+00:00');
      INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(1,'elsa-en-normal','main',4);
    """)
    connection.commit()
    connection.close()
    return path


# ---- the migrations themselves -------------------------------------------------------------------

def test_a_new_database_gets_every_migration_once(fresh):
    connection = connect(fresh)
    recorded = [(row["version"], row["name"]) for row in connection.execute("SELECT version,name FROM schema_migrations ORDER BY version")]
    connection.close()
    assert recorded == [(index + 1, module.NAME) for index, module in enumerate(migrations.MIGRATIONS)]
    assert run(fresh) == []


def test_a_database_of_the_first_release_ends_up_like_a_new_one(first_release, fresh):
    assert run(first_release) == list(range(1, len(migrations.MIGRATIONS) + 1))
    assert shape(first_release) == shape(fresh)


def test_upgrading_keeps_the_data(first_release):
    run(first_release)
    connection = connect(first_release)
    try:
        owned = [tuple(row) for row in connection.execute(
            "SELECT variant_id,condition,quantity,notes,is_graded,grade_label,price_override FROM collection_entries ORDER BY id")]
        assert owned == [("elsa-en-normal", "Near Mint", 3, "signed", 0, "", None), ("elsa-en-normal", "Played", 1, None, 0, "", None), ("elsa-en-silver", "Near Mint", 2, None, 0, "", None)]
        assert all(row[0] and row[0] == row[1] for row in connection.execute("SELECT created_at,last_added_at FROM collection_entries"))
        # The single watchlist of that release lives on as the default list of the card's game.
        listed = [tuple(row) for row in connection.execute(
            "SELECT w.name,w.is_default,e.variant_id,e.quantity FROM named_watchlists w JOIN named_watchlist_entries e ON e.list_id=w.id")]
        assert listed == [("Merkliste", 1, "elsa-en-silver", 1)]
        assert connection.execute("SELECT quantity FROM deck_cards").fetchone()[0] == 4
        assert connection.execute("SELECT email,oauth_subject FROM users").fetchone()[:] == ("", "")
        # A graded copy next to the ungraded one of the same condition: what the rebuilt key is for.
        connection.execute("INSERT INTO collection_entries(user_id,variant_id,condition,quantity,is_graded,grade_label) VALUES(1,'elsa-en-normal','Near Mint',1,1,'PSA 10')")
    finally:
        connection.close()


def test_the_app_starts_on_an_upgraded_database(first_release, monkeypatch):
    """Seeds and default lists come after the migrations and find what they need."""
    monkeypatch.setattr(deckledger.schema, "DB_PATH", str(first_release))
    deckledger.schema.init_database()
    connection = connect(first_release)
    try:
        assert {row[0] for row in connection.execute("SELECT id FROM games")} == set(deckledger.games.GAMES)
        assert connection.execute("SELECT COUNT(*) FROM named_watchlists WHERE user_id=1 AND is_default=1").fetchone()[0] == len(deckledger.games.GAMES)
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1, "an existing installation gets no demo accounts"
    finally:
        connection.close()


# ---- the runner --------------------------------------------------------------------------------

def test_a_failing_migration_leaves_no_trace_and_is_not_recorded(tmp_path):
    path = tmp_path / "db.sqlite"
    steps = [
        step("one", "CREATE TABLE one (id INTEGER)"),
        step("two", "CREATE TABLE two (id INTEGER)", fail=True),
        step("three", "CREATE TABLE three (id INTEGER)"),
    ]
    with pytest.raises(RuntimeError, match="boom"):
        run(path, migrations=steps)
    connection = connect(path)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
    connection.close()
    assert tables == {"schema_migrations", "one"} and versions == [1]

    steps[1] = step("two", "CREATE TABLE two (id INTEGER)")
    assert run(path, migrations=steps) == [2, 3], "the next start picks up where it stopped"


def test_only_what_is_new_runs(tmp_path):
    path = tmp_path / "db.sqlite"
    first = [step("one", "CREATE TABLE log (entry TEXT)"), step("two", "INSERT INTO log VALUES('two')")]
    assert run(path, migrations=first) == [1, 2]
    assert run(path, migrations=first + [step("three", "INSERT INTO log VALUES('three')")]) == [3]
    connection = connect(path)
    assert [row[0] for row in connection.execute("SELECT entry FROM log")] == ["two", "three"]
    connection.close()


def test_processes_starting_together_apply_each_migration_once(tmp_path):
    path = tmp_path / "db.sqlite"
    steps = [step("one", "CREATE TABLE log (entry TEXT)")] + [step(f"step {number}", f"INSERT INTO log VALUES('{number}')") for number in range(2, 8)]
    barrier, results, errors = threading.Barrier(4), [], []

    def worker():
        try:
            barrier.wait()
            results.append(run(path, migrations=steps))
        except Exception as error:  # noqa: BLE001 -- reported below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(version for applied in results for version in applied) == list(range(1, 8))
    connection = connect(path)
    assert [row[0] for row in connection.execute("SELECT entry FROM log ORDER BY entry")] == [str(number) for number in range(2, 8)]
    connection.close()


def test_a_database_from_a_newer_version_is_refused(fresh):
    connection = connect(fresh)
    connection.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(99,'from the future','2027-01-01T00:00:00+00:00')")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="Schema-Version 99"):
        run(fresh)


def test_the_database_is_copied_before_it_is_migrated(first_release, tmp_path):
    assert list(tmp_path.glob("*.before-migration-*")) == []
    run(first_release)
    backup = tmp_path / "old.db.before-migration-0001"
    assert backup.is_file()
    connection = connect(backup)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(collection_entries)")}
    quantity = connection.execute("SELECT SUM(quantity) FROM collection_entries").fetchone()[0]
    connection.close()
    assert "is_graded" not in columns and quantity == 6, "the copy is the database as it was"
    run(first_release)
    assert len(list(tmp_path.glob("*.before-migration-*"))) == 1, "nothing to migrate, nothing to copy"


def test_a_new_database_is_not_copied_and_old_copies_are_cleared(tmp_path):
    path = tmp_path / "db.sqlite"
    steps = [step("one", "CREATE TABLE users (id INTEGER)")]
    run(path, migrations=steps)
    assert list(tmp_path.glob("*.before-migration-*")) == []
    for number in range(2, 6):
        steps.append(step(f"step {number}"))
        run(path, migrations=steps)
    assert sorted(item.name for item in tmp_path.glob("*.before-migration-*")) == ["db.sqlite.before-migration-0004", "db.sqlite.before-migration-0005"]


def test_scripts_run_inside_the_migrations_transaction(tmp_path):
    path = tmp_path / "db.sqlite"

    def apply(connection, context):
        context.run_script("""
          -- a comment; with a semicolon
          CREATE TABLE a (id INTEGER, note TEXT DEFAULT 'x;y');
          CREATE TABLE b (id INTEGER);
        """)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run(path, migrations=[types.SimpleNamespace(NAME="script", apply=apply)])
    connection = connect(path)
    assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"schema_migrations"}
    with pytest.raises(ValueError, match="incomplete"):
        migrations.Context(connection, "now").run_script("CREATE TABLE c (id INTEGER")
    connection.close()


# ---- how the app uses them -----------------------------------------------------------------------

def test_health_reports_the_schema_version(anonymous):
    health = anonymous.get("/health").get_json()
    assert health["schema_version"] == health["schema_expected"] == len(migrations.MIGRATIONS)


def test_migrations_are_numbered_without_gaps():
    numbers = [int(re.match(r"m(\d{4})_", module.__name__.rsplit(".", 1)[1]).group(1)) for module in migrations.MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1))
    files = sorted(path.stem for path in (ROOT / "deckledger" / "migrations").glob("m[0-9]*.py"))
    assert files == [module.__name__.rsplit(".", 1)[1] for module in migrations.MIGRATIONS], "a migration file is not listed in MIGRATIONS"


def test_nothing_but_a_migration_changes_the_schema():
    pattern = re.compile(r"\b(CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)|ALTER\s+TABLE|DROP\s+(TABLE|INDEX|COLUMN))\b", re.I)
    sources = [*(ROOT / "deckledger").glob("*.py"), *(ROOT / "deckledger" / "games").glob("*.py"), ROOT / "catalog_sync.py", ROOT / "price_sync.py"]
    offenders = [
        f"{path.name}:{number}" for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and "TEMP TABLE" not in line
    ]
    assert offenders == []
