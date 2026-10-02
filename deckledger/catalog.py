"""Browsing the catalogue: sets, cards of a set or a game, card details, search, number matching."""

import re
from functools import cmp_to_key

from flask import jsonify, request

from .config import jload
from .games import game as game_rules, playset_size, rarity_filter_ranks, rarity_rank
from .web import app, db, login_required, user_id
from .prices import MANUAL_PRICE_PROVIDER, latest_price_meta_sql, latest_price_sql
from .assets import set_visual_version


@app.get("/api/games/<game_id>/sets")
@login_required
def game_sets(game_id):
    uid = user_id()
    rows = []
    for s in db().execute("SELECT * FROM sets WHERE game_id=? ORDER BY release_date DESC", (game_id,)):
        # Ownership and value start from the user's own rows: the price lookup is a correlated
        # subquery, and running it for every variant of the set instead of only the owned ones
        # is what made this endpoint take about a second per game.
        owned_values = db().execute(
            f"""SELECT COUNT(DISTINCT CASE WHEN c.quantity>0 THEN p.identity_id END) owned,
                COALESCE(SUM(c.quantity * {latest_price_sql('v')}),0) value
                FROM collection_entries c JOIN variants v ON v.id=c.variant_id
                JOIN printings p ON p.id=v.printing_id
                WHERE c.user_id=? AND p.set_id=?""",
            (uid, s["id"]),
        ).fetchone()
        values = {
            "total": db().execute(
                "SELECT COUNT(DISTINCT p.identity_id) FROM printings p WHERE p.set_id=? AND EXISTS(SELECT 1 FROM variants v WHERE v.printing_id=p.id)",
                (s["id"],),
            ).fetchone()[0],
            "owned": owned_values["owned"], "value": owned_values["value"],
        }
        variants_total = db().execute("SELECT COUNT(*) FROM variants v JOIN printings p ON p.id=v.printing_id WHERE p.set_id=?", (s["id"],)).fetchone()[0]
        variants_owned = db().execute("SELECT COUNT(DISTINCT v.id) FROM variants v JOIN printings p ON p.id=v.printing_id JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? AND c.quantity>0 WHERE p.set_id=?", (uid, s["id"])).fetchone()[0]
        playset_owned = db().execute(
            """SELECT COUNT(*) FROM (
                SELECT p.identity_id, COALESCE(SUM(c.quantity),0) qty
                FROM printings p JOIN variants v ON v.printing_id=p.id
                LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
                WHERE p.set_id=?
                GROUP BY p.identity_id
                HAVING qty>=?
            )""",
            (uid, s["id"], playset_size(game_id)),
        ).fetchone()[0]
        if not values["total"]:
            # A `sets` row can exist (announced/synced set metadata) before any of its cards
            # have actual printings in the catalog -- e.g. a newly-announced set, or a non-card
            # promotional product that only ever gets metadata, never printings. Both the set
            # grid (renderGame) and the set-switcher dropdowns (renderSet/renderAllCards) read
            # this same endpoint, so filtering it out once here keeps a card-less set out of
            # both instead of just hiding it in one place and leaving an empty/broken tile in
            # the other.
            continue
        item = dict(s)
        item["classifications"] = jload(item["classifications"], [])
        item["release_dates"] = release_dates_for_set(s["id"], item["release_date"])
        item["visual_version"] = set_visual_version(s)
        item.update({"owned": values["owned"], "total": values["total"], "value": round(values["value"], 2), "base_completion": round(values["owned"] / values["total"] * 100) if values["total"] else 0, "foil_completion": round(variants_owned / variants_total * 100) if variants_total else 0, "master_completion": round(variants_owned / variants_total * 100) if variants_total else 0, "playset_completion": round(playset_owned / values["total"] * 100) if values["total"] else 0})
        # Lorcana's set `code` now holds the community abbreviation (providers/lorcana.py:
        # LORCANA_SET_ABBREVIATIONS), not the plain release number LorcanaJSON itself uses --
        # the set overview still wants that number visible too ("9 FAB", not just "FAB"), so
        # it's prepended here for numbered main sets specifically. set_id for those is exactly
        # "lorcana-<number>" (unchanged by the abbreviation override on purpose -- see that
        # module's own comment), so the number is recovered from there rather than needing a
        # second stored column. Promo/quest/challenge sets (ids like "lorcana-p1") don't match
        # and are left with their existing code untouched.
        number_match = re.match(r"^lorcana-(\d+)$", s["id"])
        if number_match:
            item["code"] = f"{number_match.group(1)} {item['code']}"
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
            p.id printing_id,p.collector_number,p.language,p.rarity,p.set_id,p.attributes printing_attrs,
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
            p.id printing_id,p.collector_number,p.language,p.rarity,p.set_id,p.attributes printing_attrs,
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


def query_matches_row(query, row):
    """Case-insensitive substring match used by every card search box -- canonical (English)
    name, collector number, and (since a search box that only understands English names is
    useless if you're looking at German/Japanese-localized cards) the printing's own localized
    name and localized rules text, plus the English rules text as a bonus "search by card text"
    field. `row` needs canonical_name/collector_number/rules_text and, when available,
    printing_attrs (the raw printings.attributes JSON, which carries localizedName/
    localizedRulesText) -- callers without that column just get the English-only fields."""
    printing_attrs = jload(row.get("printing_attrs"), {})
    fields = (
        row.get("canonical_name"), row.get("collector_number"), row.get("rules_text"),
        printing_attrs.get("localizedName"), printing_attrs.get("localizedRulesText"),
    )
    return any(query in str(field).lower() for field in fields if field)


def serialize_card_rows(raw, language, mode, query, sort, game_id, rarity="", foil_mode="", rarities=None, costs=None, colors=None, inkwell="", finish="normal"):
    rules = game_rules(game_id)
    raw = [dict(row) for row in raw]
    if language != "combined":
        raw = [row for row in raw if row["language"] == language]
    if query:
        raw = [row for row in raw if query_matches_row(query, row)]
    identities = {}
    for row in raw:
        row["identity_attrs"] = jload(row["identity_attrs"], {})
        identities.setdefault(row["identity_id"], []).append(row)
    # "finish" picks which printing (Normal or Silver) represents each card -- the shown
    # art/price and the "owned" count -- independent of "foil_mode", which filters by whether
    # that foil copy specifically is owned/missing (Alle leaves ownership unfiltered).
    foil_display = finish == "foil"
    cards = []
    premium_cards = []
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
        # Epic/Enchanted/Iconic reprints are a separate printing under the same gameplay
        # identity (their own collector number, e.g. #206 for an Enchanted reprint of a card
        # normally at #21) -- list each as its own card at that number too, instead of only
        # being reachable by hovering the base printing's tile. Kept in a separate bucket so
        # they're browsable/filterable like any other card but don't inflate Base/Playset% --
        # those track completion of the base+foil ladder, which premium tiers sit outside of.
        if rules.premium_ranks:
            premium_printing_ids = {v["printing_id"] for v in language_variants if rules.rarity_rank(v["rarity"]) in rules.premium_ranks and v["printing_id"] != representative["printing_id"]}
            for printing_id in premium_printing_ids:
                printing_variants = [v for v in variants if v["printing_id"] == printing_id]
                printing_language_variants = [v for v in printing_variants if v["language"] == preferred_language]
                if not printing_language_variants:
                    continue
                premium_representative = printing_language_variants[0]
                premium_foil_variant = next((v for v in printing_language_variants if v["finish"] == "Silver"), None)
                premium_cards.append({
                    **premium_representative,
                    "variants": printing_variants,
                    "languages": sorted({v["language"] for v in printing_variants}),
                    "language_count": len({v["language"] for v in printing_variants}),
                    "quantity": sum(v["quantity"] for v in printing_variants),
                    "owned_variants": sum(1 for v in printing_variants if v["quantity"] > 0),
                    "variant_count": len(printing_language_variants),
                    "value": round(sum(v["quantity"] * (v["price"] or 0) for v in printing_variants), 2),
                    "watchlisted": any(v["watchlisted"] for v in printing_variants),
                    "foil_quantity": premium_foil_variant["quantity"] if premium_foil_variant else 0,
                })
    unfiltered_cards = cards
    # Foil-ownership only means something for the Normal/Silver ladder -- premium
    # (Epic/Enchanted/Iconic) cards have no Silver-finish printing to be "owned" or
    # "missing" in, so foil_variant is always None for them and foil_quantity is
    # always 0. Filtering them by foil_mode would either hide every premium card a
    # user actually owns (foil_mode=owned) or always show them regardless of
    # ownership (foil_mode=missing) -- so foil_mode only applies before premium
    # cards are merged in, and premium cards pass through untouched.
    if foil_mode == "owned": cards = [card for card in cards if card["foil_quantity"] > 0]
    if foil_mode == "missing": cards = [card for card in cards if card["foil_quantity"] == 0]
    cards = cards + premium_cards
    if mode == "owned": cards = [card for card in cards if card["quantity"] > 0]
    if mode == "missing": cards = [card for card in cards if card["quantity"] == 0]
    if rarity: cards = [card for card in cards if card["rarity"] == rarity]
    if rarities:
        selected_ranks = rarity_filter_ranks(game_id, rarities)
        cards = [card for card in cards if rarity_rank(game_id, card["rarity"]) in selected_ranks]
    if costs:
        def cost_matches(card_cost):
            if card_cost is None:
                return False
            for value in costs:
                if rules.cost_filter_cap is not None and value == str(rules.cost_filter_cap):
                    if card_cost >= rules.cost_filter_cap:
                        return True
                elif str(card_cost) == value:
                    return True
            return False
        cards = [card for card in cards if cost_matches(card["identity_attrs"].get("cost"))]
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
    # What counts for Foil% is the game's call (see Game.is_foil).
    foil_variants = [variant for variant in all_variants if rules.is_foil(variant, rules)]
    playset_owned = sum(1 for card in unfiltered_cards if card["quantity"] >= playset_size(game_id))
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
    finish = request.args.get("finish", "normal")
    selected_rarities = [value for value in request.args.get("rarities", "").split(",") if value]
    selected_costs = [value for value in request.args.get("costs", "").split(",") if value]
    selected_colors = [value for value in request.args.get("colors", "").split(",") if value]
    inkwell = request.args.get("inkwell", "")
    cards, stats = serialize_card_rows(card_rows(set_id, uid), language, mode, query, sort, set_row["game_id"], rarity, foil, selected_rarities, selected_costs, selected_colors, inkwell, finish)
    if request.args.get("stats_only"):
        return jsonify({"stats": stats})
    rarity_sql = "SELECT DISTINCT rarity FROM printings WHERE set_id=?" + ("" if language == "combined" else " AND language=?")
    rarity_params = (set_id,) if language == "combined" else (set_id, language)
    rarity_options = sorted({r["rarity"] for r in db().execute(rarity_sql, rarity_params)}, key=lambda r: (rarity_rank(set_row["game_id"], r), r))
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
    finish = request.args.get("finish", "normal")
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
    rarity_options = sorted({r["rarity"] for r in db().execute(rarity_sql, rarity_params)}, key=lambda r: (rarity_rank(game_id, r), r))
    for set_row in sets:
        cards, stats = serialize_card_rows(grouped_raw.get(set_row["id"], []), language, mode, query, sort, game_id, rarity, foil, selected_rarities, selected_costs, selected_colors, inkwell, finish)
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
    if request.args.get("stats_only"):
        return jsonify({"stats": aggregate, "group_count": len(groups)})
    return jsonify({"game": {**dict(game), "languages": jload(game["languages"], [])}, "groups": groups, "stats": aggregate, "rarities": rarity_options})


@app.get("/api/cards/<identity_id>")
@login_required
def card_detail(identity_id):
    uid = user_id()
    identity = db().execute("SELECT * FROM card_identities WHERE id=?", (identity_id,)).fetchone()
    if not identity: return jsonify({"error":"card not found"}), 404
    # A variant can now have several collection_entries rows (one per condition, plus any
    # graded copies) instead of at most one -- aggregate them per variant_id first so the
    # main join stays 1:1 (a flat, unaggregated join here would return the same variant once
    # per row and silently corrupt everything downstream that assumes one row per variant).
    # The per-condition/grading breakdown itself is fetched separately for the Erweitert panel.
    variants = db().execute(
        f"""SELECT v.*,p.collector_number,p.language,p.rarity,p.set_id,s.name set_name,s.code set_code,
            s.printed_card_count printed_card_count,
            COALESCE(agg.quantity,0) quantity,agg.notes,
            COALESCE(agg.override_value,0) override_value,COALESCE(agg.unpriced_quantity,0) unpriced_quantity,
            CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted,
            {latest_price_sql('v')} price,{latest_price_sql('v','low')} price_low,
            {latest_price_sql('v','avg30')} price_avg30,{latest_price_meta_sql('v','provider_id')} price_provider,
            {latest_price_meta_sql('v','currency')} price_currency,{latest_price_meta_sql('v','observed_at')} price_observed_at
            FROM variants v JOIN printings p ON p.id=v.printing_id JOIN sets s ON s.id=p.set_id
            LEFT JOIN (SELECT variant_id,SUM(quantity) quantity,MAX(notes) notes,
              SUM(quantity*COALESCE(price_override,0)) override_value,
              SUM(CASE WHEN price_override IS NULL THEN quantity ELSE 0 END) unpriced_quantity
              FROM collection_entries WHERE user_id=? GROUP BY variant_id) agg ON agg.variant_id=v.id
            WHERE p.identity_id=? ORDER BY p.language,s.release_date,s.code,p.collector_number,v.is_parallel,v.variant_code""", (uid,uid,identity_id)
    ).fetchall()
    variant_rows = []
    checked_row = db().execute("SELECT value FROM catalog_metadata WHERE key='price_sync_checked'").fetchone()
    price_checked = jload(checked_row["value"], {}) if checked_row else {}
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
        variant.update(game_rules(variant["game_id"]).market_links(variant, variant_attrs, market_mapping, search_term))
        # price_source/price_url keep naming the marketplace, so its link stays useful next to
        # a price entered by hand.
        variant["price_manual"] = variant.get("price_provider") == MANUAL_PRICE_PROVIDER
        variant["edition_label"] = variant_attrs.get("editionLabel")
        # A price that did not change writes no new row; it is still as current as the provider's
        # last successful sync.
        if variant.get("price_provider") and str(price_checked.get(variant["price_provider"]) or "") > str(variant.get("price_observed_at") or ""):
            variant["price_observed_at"] = price_checked[variant["price_provider"]]
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


def split_set_prefix(token):
    """'TFC:16' -> ('TFC', '16'); a plain number comes back with no set."""
    set_code, separator, number = str(token).partition(":")
    return (set_code, number) if separator and set_code and number else (None, str(token))


def code_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def match_collector_number(game_id, number, language, set_code=None, variant_hint=None):
    """Resolve a collector number to exactly one variant, or say why that is not possible.

    A number identifies a printing, not a variant: one printing usually has several finishes, and
    several printings can share a number (Lorcana and VCard restart at 1 in every set; One Piece
    reprints a card under its original number in other products). Returns (variant, status,
    message, candidate_count) with status matched / ambiguous / not_found.

    - One printing: its base variant, or the finish named by `variant_hint`.
    - Several printings: the one in the set named with "SET:number"; otherwise the set the number
      itself is prefixed with ("OP01-016" belongs to OP-01), which is where a reprinted card
      originally comes from. Anything else is ambiguous -- never "whichever row came first".
    """
    rows = [dict(row) for row in db().execute(
        """SELECT v.id variant_id,v.variant_code,v.finish,v.is_parallel,v.game_id,p.id printing_id,p.collector_number,p.language,p.rarity,
                  i.canonical_name,i.card_type,s.name set_name,s.code set_code
           FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
           WHERE v.game_id=? AND UPPER(p.collector_number)=UPPER(?) AND p.language=?
           ORDER BY s.release_date,p.id,v.id""", (game_id, number, language)
    )]
    if set_code:
        rows = [row for row in rows if code_key(row["set_code"]) == code_key(set_code)]
    if not rows:
        return None, "not_found", "Karte nicht im Katalog gefunden", 0
    printings = {}
    for row in rows:
        printings.setdefault(row["printing_id"], []).append(row)
    if len(printings) > 1 and not set_code:
        prefix = code_key(str(number).split("-", 1)[0]) if "-" in str(number) else ""
        home = {key: variants for key, variants in printings.items() if prefix and code_key(variants[0]["set_code"]) == prefix}
        if len(home) == 1:
            printings = home
    if len(printings) > 1:
        sets = sorted({variants[0]["set_code"] for variants in printings.values()})
        hint = f' – mit Set angeben, z. B. „{sets[0]}:{number}“' if len(sets) > 1 else " im selben Set – bitte den Kartennamen verwenden"
        return rows[0], "ambiguous", f"{len(printings)} Karten teilen sich diese Nummer{hint}", len(rows)
    variants = next(iter(printings.values()))
    hint = str(variant_hint or "").strip().lower()
    if hint and hint not in ("standard", "normal"):
        named = [row for row in variants if hint in (row["variant_code"].lower(), row["finish"].lower())]
        if not named:
            return variants[0], "ambiguous", f'Ausführung „{variant_hint}“ gibt es für diese Karte nicht', len(variants)
        return named[0], "matched", None, len(variants)
    base = sorted(variants, key=lambda row: (row["variant_code"] not in ("standard", "normal"), row["finish"] != "Normal", row["is_parallel"], row["variant_id"]))[0]
    return base, "matched", None, len(variants)


@app.get("/api/search")
@login_required
def global_search():
    q = request.args.get("q", "").strip()
    game_id = request.args.get("game_id")
    if len(q) < 2: return jsonify([])
    limit = min(80, max(1, request.args.get("limit", 24, type=int)))
    like = f"%{q}%"
    rows = db().execute(
        f"""SELECT DISTINCT i.id identity_id,i.canonical_name,p.collector_number,p.language,p.set_id,p.rarity,s.name set_name,
          g.id game_id,g.short_name game_name,g.accent,v.id variant_id,v.finish,{latest_price_sql('v')} price,
          s.code set_code,v.variant_code,v.is_parallel,
          COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=v.id),0) quantity,
          CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted
          FROM card_identities i JOIN printings p ON p.identity_id=i.id JOIN variants v ON v.printing_id=p.id
          JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=i.game_id
          WHERE (? IS NULL OR g.id=?) AND (i.canonical_name LIKE ? OR p.collector_number LIKE ? OR s.name LIKE ?
            OR i.rules_text LIKE ? OR json_extract(p.attributes,'$.localizedName') LIKE ? OR json_extract(p.attributes,'$.localizedRulesText') LIKE ?)
          LIMIT ?""", (user_id(),user_id(),game_id,game_id,like,like,like,like,like,like,limit)
    ).fetchall()
    return jsonify([dict(r) for r in rows])
