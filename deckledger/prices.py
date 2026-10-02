"""Reading prices, prices entered by hand, price history and triggering a price sync."""

import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

from flask import jsonify, request

from .config import ROOT, jload, now_iso
from .web import app, db, login_required


MANUAL_PRICE_PROVIDER = "manual"
MANUAL_PRICE_LIMIT = 1_000_000


def latest_observation_sql(alias, metric, field):
    """Newest observation of a variant: a price entered by hand if there is one, else Cardmarket's,
    else the newest of any other mapped provider. The inner MAX() is a single index seek per provider, so the cost stays flat
    however much price history piles up -- sorting a variant's whole history on every lookup (as
    this did before) got slower with each daily sync, on every page that shows a price."""
    return f"""(SELECT po.{field} FROM marketplace_products mp
      JOIN price_observations po ON po.variant_id=mp.variant_id AND po.provider_id=mp.provider_id AND po.metric='{metric}'
      WHERE mp.variant_id={alias}.id AND po.observed_at=(
        SELECT MAX(latest.observed_at) FROM price_observations latest
        WHERE latest.variant_id=mp.variant_id AND latest.provider_id=mp.provider_id AND latest.metric='{metric}')
      ORDER BY CASE po.provider_id WHEN '{MANUAL_PRICE_PROVIDER}' THEN -1 WHEN 'cardmarket' THEN 0 ELSE 9 END,po.observed_at DESC LIMIT 1)"""


def latest_price_sql(alias="v", metric="trend"):
    return latest_observation_sql(alias, metric, "amount")


def latest_price_meta_sql(alias="v", field="provider_id"):
    return latest_observation_sql(alias, "trend", field)


@app.post("/api/prices/sync")
@login_required
def refresh_prices():
    try:
        process = subprocess.run(
            [sys.executable, str(ROOT / "price_sync.py")],
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


@app.route("/api/variants/<variant_id>/manual-price", methods=["PUT", "DELETE"])
@login_required
def manual_price(variant_id):
    """A price entered by hand, for cards no price feed covers (or covers wrongly). It is stored
    like any provider's price -- a mapping plus change-only observations -- and wins over the
    feeds until it is removed again, so everything that shows or sums prices picks it up without
    knowing about it. Prices belong to the catalogue, not to an account: one manual price per card."""
    variant = db().execute("SELECT id,game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not variant:
        return jsonify({"error": "variant not found"}), 404
    if request.method == "DELETE":
        db().execute("DELETE FROM price_observations WHERE variant_id=? AND provider_id=?", (variant_id, MANUAL_PRICE_PROVIDER))
        db().execute("DELETE FROM marketplace_products WHERE variant_id=? AND provider_id=?", (variant_id, MANUAL_PRICE_PROVIDER))
        db().commit()
        return jsonify({"removed": True})
    payload = request.get_json(force=True)
    try:
        amount = round(float(str((payload or {}).get("amount", "")).replace(",", ".")), 2)
    except (TypeError, ValueError, AttributeError):
        return jsonify({"error": "Der Preis muss eine Zahl sein."}), 400
    if not 0 < amount <= MANUAL_PRICE_LIMIT:
        return jsonify({"error": "Der Preis muss größer als 0 sein."}), 400
    stamp = now_iso()
    db().execute("BEGIN IMMEDIATE")
    db().execute(
        """INSERT OR IGNORE INTO marketplace_products(provider_id,external_product_id,variant_id,game_id,source_url,match_method,matched_at,attributes)
           VALUES(?,?,?,?,'','manual',?,'{}')""", (MANUAL_PRICE_PROVIDER, variant_id, variant_id, variant["game_id"], stamp),
    )
    latest = db().execute(
        "SELECT id,amount,observed_at FROM price_observations WHERE variant_id=? AND provider_id=? AND metric='trend' ORDER BY observed_at DESC,id DESC LIMIT 1",
        (variant_id, MANUAL_PRICE_PROVIDER),
    ).fetchone()
    if latest and latest["observed_at"] == stamp:
        # A correction within the same second replaces the entry: two rows with one timestamp
        # would leave "the latest price" undecided.
        db().execute("UPDATE price_observations SET amount=? WHERE id=?", (amount, latest["id"]))
    elif not latest or latest["amount"] != amount:
        db().execute(
            "INSERT INTO price_observations(variant_id,provider_id,metric,amount,currency,observed_at) VALUES(?,?,'trend',?,'EUR',?)",
            (variant_id, MANUAL_PRICE_PROVIDER, amount, stamp),
        )
    db().commit()
    return jsonify({"saved": True, "amount": amount})


@app.get("/api/variants/<variant_id>/price-history")
@login_required
def variant_price_history(variant_id):
    """One point per calendar day (price_sync runs every 6h plus whenever someone hits "Preise
    aktualisieren", so a given day usually has several raw observations) -- collapsed down with
    the SAME provider-priority tiebreak latest_price_sql() uses for "the current price" (prefer
    Cardmarket, else whichever provider was actually queried that day), so the chart's most
    recent point always agrees with the price shown elsewhere on the card. window function over
    a correlated subquery for the same reason latest_price_sql needs neither: this collapses ALL
    rows for the day at once instead of once per row.
    """
    variant = db().execute("SELECT id FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not variant:
        return jsonify({"error": "variant not found"}), 404
    metric = request.args.get("metric", "trend")
    if metric not in {"trend", "low", "avg30"}:
        metric = "trend"
    # The day is taken from the text itself: SQLite's date() returns NULL for Cardmarket's
    # "+0200" offsets (no colon), which lumped every Cardmarket observation into one dateless
    # point. Months older than the daily window hold one averaged point each (price_sync.py).
    try:
        days = min(3650, max(7, int(request.args.get("days", 180))))
    except (TypeError, ValueError):
        days = 180
    # One provider per chart -- the one the card's current price comes from -- so the line never
    # jumps between two marketplaces' price levels.
    provider = db().execute(f"SELECT {latest_price_meta_sql('v', 'provider_id')} provider FROM variants v WHERE v.id=?", (variant_id,)).fetchone()["provider"]
    if not provider:
        return jsonify({"variant_id": variant_id, "metric": metric, "points": []})
    today = datetime.now(timezone.utc).date()
    first_day = (today - timedelta(days=days)).isoformat()
    stored = db().execute(
        """SELECT substr(observed_at,1,10) day,amount,currency FROM price_observations
           WHERE variant_id=? AND provider_id=? AND metric=? ORDER BY observed_at""", (variant_id, provider, metric),
    ).fetchall()
    # Only changes are stored (price_sync.py), so the last row before the window is the price the
    # window starts with, and each price holds until the day before the next change. Emitting
    # both ends of every run draws the steps a daily series would have shown.
    points = []

    def add(day, row):
        if points and points[-1]["date"] == day:
            points[-1].update(amount=row["amount"], currency=row["currency"])
        else:
            points.append({"date": day, "amount": row["amount"], "currency": row["currency"]})

    previous = None
    for row in stored:
        if row["day"] < first_day:
            previous = row
            continue
        if previous is not None:
            if not points:
                add(first_day, previous)
            held_until = (date.fromisoformat(row["day"]) - timedelta(days=1)).isoformat()
            if held_until > points[-1]["date"]:
                add(held_until, previous)
        add(row["day"], row)
        previous = row
    if previous is not None:
        if not points:
            add(first_day, previous)
        # The price is known to hold up to the provider's last successful sync.
        checked = jload((db().execute("SELECT value FROM catalog_metadata WHERE key='price_sync_checked'").fetchone() or [None])[0], {}) or {}
        last_checked = min(today.isoformat(), str(checked.get(provider) or "")[:10]) if checked.get(provider) else ""
        if last_checked > points[-1]["date"]:
            add(last_checked, previous)
    return jsonify({"variant_id": variant_id, "metric": metric, "points": points})
