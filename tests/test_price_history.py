"""Price history: only changes are stored, old months are folded into averages, and the chart
endpoint turns the stored changes back into a daily-looking series."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import price_sync
from conftest import EMBER8, TIDE8, deckledger, query
from test_prices_and_assets import observe, price_of

TODAY = date(2026, 10, 1)


def connect():
    connection = sqlite3.connect(deckledger.config.DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def run(function, *args):
    connection = connect()
    try:
        result = function(connection, *args)
        connection.commit()
        return result
    finally:
        connection.close()


def stamp(day, offset="+0200"):
    return f"{day.isoformat()}T09:55:31{offset}"


def synced(day, amount, variant_id=EMBER8, provider="cardmarket", metric="trend"):
    """What one sync run hands to the store for a single series."""
    return (variant_id, provider, metric, amount, "EUR", stamp(day))


def rows(variant_id=EMBER8, metric="trend", provider="cardmarket"):
    return [(row["observed_at"][:10], row["amount"]) for row in query(
        "SELECT observed_at,amount FROM price_observations WHERE variant_id=? AND metric=? AND provider_id=? ORDER BY observed_at",
        (variant_id, metric, provider),
    )]


# ---- storing ------------------------------------------------------------------------------

def test_only_changes_are_stored():
    day = date(2026, 9, 1)
    prices = [1.0, 1.0, 1.0, 1.2, 1.2, 1.0, 1.0]
    written = [run(price_sync.store_changed_observations, [synced(day + timedelta(days=offset), amount)]) for offset, amount in enumerate(prices)]
    assert written == [1, 0, 0, 1, 0, 1, 0]
    assert rows() == [("2026-09-01", 1.0), ("2026-09-04", 1.2), ("2026-09-06", 1.0)]


def test_series_are_independent_and_reruns_write_nothing():
    day = date(2026, 9, 1)
    batch = [synced(day, 1.0), synced(day, 0.5, metric="low"), synced(day, 1.0, variant_id=TIDE8), synced(day, 1.0, provider="tcgplayer")]
    assert run(price_sync.store_changed_observations, batch) == 4
    assert run(price_sync.store_changed_observations, batch) == 0, "the same market file imported twice"
    # A changed price in an older file than the one already stored is not a new observation.
    assert run(price_sync.store_changed_observations, [synced(day - timedelta(days=1), 9.0)]) == 0
    assert run(price_sync.store_changed_observations, [synced(day + timedelta(days=1), 0.6, metric="low")]) == 1
    assert query("SELECT COUNT(*) n FROM price_observations")[0]["n"] == 5


def test_existing_daily_history_is_reduced_to_its_changes_once():
    day = date(2026, 8, 1)
    for offset, amount in enumerate([2.0, 2.0, 2.0, 2.5, 2.5, 2.0]):
        observe(EMBER8, "cardmarket", amount, stamp(day + timedelta(days=offset)))
        observe(TIDE8, "cardmarket", 7.0, stamp(day + timedelta(days=offset)))

    assert run(price_sync.drop_unchanged_observations) == 8

    assert rows() == [("2026-08-01", 2.0), ("2026-08-04", 2.5), ("2026-08-06", 2.0)]
    assert rows(TIDE8) == [("2026-08-01", 7.0)]
    assert run(price_sync.drop_unchanged_observations) == 0, "a database already in the new format is not scanned again"


# ---- folding old months ---------------------------------------------------------------------

@pytest.mark.parametrize("changes, carried, expected", [
    ([], 4.0, 4.0),                                               # nothing changed all month
    ([("2026-06-16", 6.0)], 4.0, 5.0),                            # 15 days at 4, 15 days at 6
    ([("2026-06-01", 3.0), ("2026-06-21", 6.0)], None, 4.0),      # 20 days at 3, 10 days at 6
    ([("2026-06-25", 9.0)], None, 9.0),                           # the series began on the 25th
    ([("2026-06-10", 1.0), ("2026-06-10", 2.0)], 2.0, 2.0),       # two files on one day: the later one counts
])
def test_month_average_is_weighted_by_how_long_a_price_held(changes, carried, expected):
    assert price_sync.month_average([(day + "T10:00:00+00:00", amount) for day, amount in changes], carried, 30) == expected


def test_months_before_the_daily_window_are_folded():
    # 92 days before 1 October is 1 July: April to June lie entirely before the window.
    run(price_sync.store_changed_observations, [synced(date(2026, 4, 1), 4.0)])
    run(price_sync.store_changed_observations, [synced(date(2026, 5, 16), 6.0)])       # May: 15 days at 4, 16 at 6
    run(price_sync.store_changed_observations, [synced(date(2026, 8, 10), 8.0)])

    result = run(price_sync.compact_price_history, TODAY)

    assert result["months"] == ["2026-04", "2026-05"]
    assert rows() == [
        ("2026-04-15", 4.0),
        ("2026-05-15", 5.03),
        ("2026-06-01", 6.0),      # May closed at 6: written as June's opening so the carry stays right
        ("2026-08-10", 8.0),
    ]


def test_folding_is_idempotent_and_continues_month_by_month():
    run(price_sync.store_changed_observations, [synced(date(2026, 5, 16), 6.0)])
    run(price_sync.store_changed_observations, [synced(date(2026, 8, 10), 8.0)])
    run(price_sync.compact_price_history, TODAY)
    snapshot = rows()

    assert run(price_sync.compact_price_history, TODAY) == {"months": [], "rows_removed": 0}
    assert rows() == snapshot

    # Three months on, August has aged out as well: 9 days still at 6, then 22 days at 8.
    later = run(price_sync.compact_price_history, date(2027, 1, 5))
    assert later["months"] == ["2026-08"]
    assert rows() == [("2026-05-15", 6.0), ("2026-08-15", 7.42), ("2026-09-01", 8.0)]


def test_recent_changes_and_the_current_price_are_untouched(client):
    run(price_sync.store_changed_observations, [synced(date(2026, 3, 1), 1.0)])
    run(price_sync.store_changed_observations, [synced(date(2026, 9, 20), 2.0)])
    observe(EMBER8, "cardmarket", 2.0, stamp(date(2026, 9, 20)), mapped=True)  # adds the product mapping; row itself is a duplicate date
    query("DELETE FROM price_observations WHERE id=(SELECT MAX(id) FROM price_observations)")
    run(price_sync.compact_price_history, TODAY)
    assert rows()[-1] == ("2026-09-20", 2.0)
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 2.0


# ---- reading --------------------------------------------------------------------------------

def history(client, days):
    return [(point["date"], point["amount"]) for point in client.get(f"/api/variants/{EMBER8}/price-history?days={days}").get_json()["points"]]


def test_history_draws_each_price_for_as_long_as_it_held(client):
    today = datetime.now(timezone.utc).date()  # the server's notion of today
    day = lambda offset: (today - timedelta(days=offset)).isoformat()
    observe(EMBER8, "cardmarket", 1.0, stamp(today - timedelta(days=40)))
    observe(EMBER8, "cardmarket", 2.0, stamp(today - timedelta(days=10)))
    observe(EMBER8, "cardmarket", 3.0, stamp(today - timedelta(days=9)))
    query("INSERT INTO catalog_metadata(key,value) VALUES('price_sync_checked',?)", (json.dumps({"cardmarket": stamp(today)}),))

    assert history(client, 30) == [
        (day(30), 1.0),      # the price the window opens with comes from the change before it
        (day(11), 1.0),      # ... and holds until the day before the next change
        (day(10), 2.0),
        (day(9), 3.0),
        (day(0), 3.0),       # still valid at the provider's last successful sync
    ]


def test_history_reads_cardmarket_timestamps(client):
    """Regression: SQLite's date() returns NULL for "+0200" offsets, which collapsed the whole
    Cardmarket history into a single point without a date."""
    today = datetime.now(timezone.utc).date()  # the server's notion of today
    for offset in range(4, -1, -1):
        observe(EMBER8, "cardmarket", float(offset + 1), stamp(today - timedelta(days=offset)))
    assert history(client, 30) == [((today - timedelta(days=offset)).isoformat(), float(offset + 1)) for offset in range(4, -1, -1)]


def test_history_uses_the_provider_of_the_current_price(client):
    today = datetime.now(timezone.utc).date()  # the server's notion of today
    observe(EMBER8, "tcgplayer", 50.0, stamp(today - timedelta(days=3)))
    observe(EMBER8, "cardmarket", 5.0, stamp(today - timedelta(days=5)))
    assert history(client, 30) == [((today - timedelta(days=5)).isoformat(), 5.0)]
    assert client.get(f"/api/variants/{TIDE8}/price-history").get_json()["points"] == []


def test_price_is_dated_by_the_last_successful_sync(client):
    """An unchanged price writes no row, so its date would otherwise look weeks old."""
    observe(EMBER8, "cardmarket", 5.0, "2026-08-01T06:00:00+0200")
    assert price_of(client, EMBER8, "vcard-card-ember8")["price_observed_at"].startswith("2026-08-01")
    query("INSERT INTO catalog_metadata(key,value) VALUES('price_sync_checked',?)", (json.dumps({"cardmarket": "2026-09-30T06:00:00+0200"}),))
    assert price_of(client, EMBER8, "vcard-card-ember8")["price_observed_at"].startswith("2026-09-30")
