"""Price history: the chart endpoint and the monthly averaging of old observations."""
import sqlite3
from datetime import date, timedelta

import price_sync
from conftest import EMBER8, TIDE8, deckledger, query
from test_prices_and_assets import observe, price_of

TODAY = date(2026, 10, 1)


def compact(today=TODAY):
    connection = sqlite3.connect(deckledger.DB_PATH)
    try:
        result = price_sync.compact_price_history(connection, today)
        connection.commit()
        return result
    finally:
        connection.close()


def daily(variant_id, first, last, amount, provider="cardmarket", metric="trend", offset="+0200"):
    day = first
    while day <= last:
        observe(variant_id, provider, amount(day) if callable(amount) else amount, f"{day.isoformat()}T09:55:31{offset}", metric=metric)
        day += timedelta(days=1)


def rows(variant_id, metric="trend"):
    return query("SELECT observed_at,amount FROM price_observations WHERE variant_id=? AND metric=? ORDER BY observed_at", (variant_id, metric))


def test_months_older_than_the_daily_window_become_one_average_each():
    daily(EMBER8, date(2026, 4, 1), TODAY, lambda day: float(day.month))

    result = compact()

    # 92 days before 1 October is 1 July: April to June lie entirely before the daily window.
    assert result["months"] == ["2026-04", "2026-05", "2026-06"]
    stored = rows(EMBER8)
    assert [(row["observed_at"], row["amount"]) for row in stored[:3]] == [
        ("2026-04-15T12:00:00+00:00", 4.0), ("2026-05-15T12:00:00+00:00", 5.0), ("2026-06-15T12:00:00+00:00", 6.0),
    ]
    assert stored[3]["observed_at"].startswith("2026-07-01")
    assert len(stored) == 3 + (TODAY - date(2026, 7, 1)).days + 1


def test_average_is_per_card_provider_and_metric():
    daily(EMBER8, date(2026, 3, 1), date(2026, 3, 10), lambda day: 1.0 if day.day <= 5 else 2.0)
    daily(EMBER8, date(2026, 3, 1), date(2026, 3, 10), 9.0, metric="low")
    daily(EMBER8, date(2026, 3, 1), date(2026, 3, 4), 20.0, provider="tcgplayer")
    daily(TIDE8, date(2026, 3, 1), date(2026, 3, 2), 7.0)

    compact()

    assert query("SELECT variant_id,provider_id,metric,amount FROM price_observations ORDER BY 1,2,3") == [
        {"variant_id": EMBER8, "provider_id": "cardmarket", "metric": "low", "amount": 9.0},
        {"variant_id": EMBER8, "provider_id": "cardmarket", "metric": "trend", "amount": 1.5},
        {"variant_id": EMBER8, "provider_id": "tcgplayer", "metric": "trend", "amount": 20.0},
        {"variant_id": TIDE8, "provider_id": "cardmarket", "metric": "trend", "amount": 7.0},
    ]


def test_compaction_is_idempotent_and_moves_on_month_by_month():
    daily(EMBER8, date(2026, 4, 1), TODAY, 2.5)
    compact()
    snapshot = rows(EMBER8)

    assert compact() == {"months": [], "rows_removed": 0}
    assert rows(EMBER8) == snapshot

    # A month later the next calendar month has aged out of the window.
    assert compact(date(2026, 11, 2))["months"] == ["2026-07"]
    assert [row["observed_at"][:7] for row in rows(EMBER8)[:4]] == ["2026-04", "2026-05", "2026-06", "2026-07"]


def test_recent_history_and_the_current_price_are_untouched(client):
    daily(EMBER8, date(2026, 9, 1), TODAY, lambda day: day.day / 10)
    before = rows(EMBER8)
    assert compact() == {"months": [], "rows_removed": 0}
    assert rows(EMBER8) == before
    assert price_of(client, EMBER8, "vcard-card-ember8")["price"] == 0.1


def test_history_endpoint_reads_cardmarket_timestamps(client):
    """Regression: SQLite's date() returns NULL for "+0200" offsets, which collapsed the whole
    Cardmarket history into a single point without a date."""
    today = date.today()
    daily(EMBER8, today - timedelta(days=9), today, lambda day: float(day.day))
    points = client.get(f"/api/variants/{EMBER8}/price-history?days=30").get_json()["points"]
    assert len(points) == 10
    assert [point["date"] for point in points] == [(today - timedelta(days=offset)).isoformat() for offset in range(9, -1, -1)]


def test_history_endpoint_includes_monthly_points(client):
    today = date.today()
    daily(EMBER8, today - timedelta(days=200), today, 3.0)
    compact(today)
    points = client.get(f"/api/variants/{EMBER8}/price-history?days=365").get_json()["points"]
    monthly = [point for point in points if point["date"].endswith("-15") and point["date"] < (today - timedelta(days=125)).isoformat()]
    assert monthly and all(point["amount"] == 3.0 for point in points)
    assert len(points) < 200
