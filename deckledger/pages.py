"""The page shell, the service worker and what the dashboard loads first."""

from pathlib import Path

from flask import jsonify, render_template, request, send_file

from .config import PUBLIC_DIR, jload
from .games import game as game_rules
from .web import app, compress_file, db, derived_from_file, login_required, user_id
from .prices import latest_price_sql, latest_prices
from .auth import resolve_oauth_config


def glass_plate_inline_markup():
    # The card-detail reflection mask needs glass_side (public/glass-plate.svg's front-edge
    # group) as a <use> target LIVE IN THIS DOCUMENT, not a cross-document reference -- confirmed
    # via isolated same-origin testing that <use href="/glass-plate.svg#glass_side"> renders
    # nothing (0 painted pixels, though its own getBoundingClientRect() is computed correctly)
    # once that external file is *also* referenced by an SVG <mask> used as a CSS mask-image
    # anywhere on the page; only a same-document id reference paints inside a mask reliably.
    # So: read the one canonical file (public/glass-plate.svg, never duplicated into static/)
    # and inline its raw content here at render time. No XML parsing/restructuring -- clip-path
    # resolution broke previously specifically because extracting/rebuilding a subtree detached
    # ids from their original document; string-slicing out everything between the outer <svg>
    # tags keeps the whole file's structure (defs, clip-paths, groups) byte-for-byte intact, so
    # nothing about how #glass_side's own clip-path references resolve has to change.
    path = PUBLIC_DIR / "glass-plate.svg"
    if not path.is_file():
        return ""
    # Cut out once per version of the file, not read again for every page load.
    return derived_from_file(path, _glass_plate_markup, svg_inner_markup)


_glass_plate_markup = {}


def svg_inner_markup(content):
    # Newlines as a text-mode read gives them.
    text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    start = text.find(">", text.find("<svg"))
    end = text.rfind("</svg>")
    if start == -1 or end == -1:
        return ""
    return text[start + 1:end]


@app.get("/")
@login_required
def index():
    return render_template("index.html", glass_plate_inline=glass_plate_inline_markup())


@app.get("/service-worker.js")
def service_worker():
    # Served at root (not /static/service-worker.js) so its default scope
    # covers the whole app, not just /static/. Not behind @login_required --
    # the browser fetches/updates service workers independent of app auth,
    # and gating this would make it 302 to /login instead of registering.
    path = Path(app.static_folder) / "service-worker.js"
    response = compress_file(send_file(path, mimetype="application/javascript", conditional=True, etag=True, max_age=0), path)
    response.headers["Service-Worker-Allowed"] = "/"
    return response


def collection_figures(uid, shown_language):
    """{game_id: {copies, unique_cards, value}} of the user's collection, each game in the language
    the dashboard shows for it ({game_id: language}, None for every language). One pass over the
    collection: asked game by game, every game read the whole collection again."""
    if not shown_language:
        return {}
    rows = db().execute(
        f"""WITH shown(game_id, language) AS (VALUES {','.join('(?,?)' for _ in shown_language)})
            SELECT v.game_id, COALESCE(SUM(c.quantity),0) copies, COUNT(DISTINCT CASE WHEN c.quantity>0 THEN v.id END) unique_cards,
              COALESCE(SUM(c.quantity * {latest_price_sql('v')}),0) value
            FROM collection_entries c JOIN variants v ON v.id=c.variant_id
            JOIN printings p ON p.id=v.printing_id JOIN shown ON shown.game_id=v.game_id
            WHERE c.user_id=? AND (shown.language IS NULL OR p.language=shown.language)
            GROUP BY v.game_id""",
        (*(value for pair in shown_language.items() for value in pair), uid),
    )
    return {row["game_id"]: {"copies": row["copies"], "unique_cards": row["unique_cards"], "value": row["value"]} for row in rows}


@app.get("/api/bootstrap")
@login_required
def bootstrap():
    uid = user_id()
    current = db().execute("SELECT id,username,display_name,role,email,oauth_subject,password_hash FROM users WHERE id=?", (uid,)).fetchone()
    settings = {r["key"]: jload(r["value"], r["value"]) for r in db().execute("SELECT key,value FROM user_settings WHERE user_id=?", (uid,))}
    default_languages = settings.get("defaultLanguages") or {}
    games = []
    game_rows = db().execute("SELECT * FROM games WHERE enabled=1 ORDER BY name").fetchall()
    languages_of = {row["id"]: jload(row["languages"], []) for row in game_rows}
    shown_language = {game_id: default_languages.get(game_id) or (languages[0] if languages else None) for game_id, languages in languages_of.items()}
    figures = collection_figures(uid, shown_language)
    for row in game_rows:
        game_id = row["id"]
        languages, lang = languages_of[game_id], shown_language[game_id]
        stats = figures.get(game_id) or {"copies": 0, "unique_cards": 0, "value": 0}
        total = db().execute(
            "SELECT COUNT(*) FROM variants v JOIN printings p ON p.id=v.printing_id WHERE v.game_id=? AND (? IS NULL OR p.language=?)",
            (game_id, lang, lang),
        ).fetchone()[0]
        rules = game_rules(game_id)
        types = rules.main_set_types
        if types:
            placeholders = ",".join("?" for _ in types)
            main_stats = db().execute(
                f"""SELECT COUNT(DISTINCT p.id) total,
                      COUNT(DISTINCT CASE WHEN c.quantity>0 THEN p.id END) owned
                    FROM printings p JOIN sets s ON s.id=p.set_id
                    JOIN variants v ON v.printing_id=p.id
                    LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
                    WHERE p.game_id=? AND lower(s.set_type) IN ({placeholders})
                      AND (? IS NULL OR p.language=?)""",
                (uid, game_id, *types, lang, lang),
            ).fetchone()
        else:
            main_stats = {"total": 0, "owned": 0}
        set_count = db().execute("SELECT COUNT(*) FROM sets WHERE game_id=?", (game_id,)).fetchone()[0]
        deck_count = db().execute("SELECT COUNT(*) FROM decks WHERE user_id=? AND game_id=?", (uid, game_id)).fetchone()[0]
        sheet_count = db().execute("SELECT COUNT(*) FROM trade_sheets WHERE user_id=? AND game_id=?", (uid, game_id)).fetchone()[0]
        watch_count = db().execute(
            """SELECT COUNT(DISTINCT nwe.variant_id) FROM named_watchlist_entries nwe
               JOIN named_watchlists nw ON nw.id=nwe.list_id
               WHERE nw.user_id=? AND nw.game_id=?""",
            (uid, game_id),
        ).fetchone()[0]
        main_total, main_owned = main_stats["total"], main_stats["owned"]
        game = dict(row)
        game["languages"] = languages
        game.update({
            "copies": stats["copies"], "unique_cards": stats["unique_cards"], "value": round(stats["value"], 2),
            "completion": round(stats["unique_cards"] / total * 100) if total else 0,
            "main_completion": round(main_owned / main_total * 100) if main_total else 0,
            "set_count": set_count, "deck_count": deck_count, "watch_count": watch_count, "sheet_count": sheet_count,
            **rules.client_rules(),
        })
        games.append(game)
    imports = [dict(r) for r in db().execute("SELECT id,created_at,game_id,undone_at FROM import_operations WHERE user_id=? ORDER BY id DESC LIMIT 4", (uid,))]
    price_sync = db().execute("SELECT MAX(observed_at) FROM price_observations").fetchone()[0]
    user_payload = {k: current[k] for k in ("id", "username", "display_name", "role", "email")}
    user_payload["oauth_linked"] = bool(current["oauth_subject"])
    user_payload["password_set"] = bool(current["password_hash"])
    oauth_config = resolve_oauth_config()
    oauth_payload = {"enabled": oauth_config["enabled"], "provider_name": oauth_config["provider_name"]}
    inbox_new = db().execute("SELECT COUNT(*) FROM inbox_items WHERE user_id=? AND state='new'", (uid,)).fetchone()[0]
    return jsonify({"user": user_payload, "games": games, "settings": settings, "imports": imports, "price_sync": price_sync, "oauth": oauth_payload, "inbox_new": inbox_new})


def fetch_banner_cards(game_id, mode, uid):
    """Pick 20 owned cards for the dashboard banner, one representative variant per identity."""
    if mode == "value":
        price_sql = latest_price_sql('v')
        order_sql, extra_where = f"{price_sql} DESC", f"AND {price_sql} IS NOT NULL"
    else:
        # The newest cards are picked without their price, which is looked up for the 20 shown
        # only (below) instead of for every owned card of the game.
        order_sql, extra_where, price_sql = "s.release_date DESC", "", "NULL"
    rows = db().execute(
        f"""SELECT v.id variant_id,i.id identity_id,i.canonical_name,s.name set_name,s.code set_code,
              v.finish,p.language,p.collector_number,{price_sql} price
            FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
              JOIN sets s ON s.id=p.set_id
              JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? AND c.quantity>0
            WHERE v.game_id=? {extra_where}
            ORDER BY {order_sql}, CASE WHEN v.finish='Normal' THEN 0 ELSE 1 END
            LIMIT 300""",
        (uid, game_id)
    ).fetchall()
    result = first_per_identity(rows, 20)
    if mode != "value":
        prices = latest_prices(card["variant_id"] for card in result)
        for card in result:
            card["price"] = prices.get(card["variant_id"])
    return result


def first_per_identity(rows, limit):
    """The first `limit` rows of distinct cards (identity_id), as dicts, in the given order."""
    seen, result = set(), []
    for row in rows:
        if row["identity_id"] in seen:
            continue
        seen.add(row["identity_id"])
        result.append(dict(row))
        if len(result) >= limit:
            break
    return result


@app.get("/api/home-banner")
@login_required
def home_banner():
    uid = user_id()
    raw = db().execute("SELECT value FROM user_settings WHERE user_id=? AND key='homeBanner'", (uid,)).fetchone()
    banner_settings = jload(raw["value"], {}) if raw else {}
    modes = [m for m in (banner_settings.get("modes") or ["newest"]) if m in ("newest", "value")] or ["newest"]
    # The dashboard shows the reel of the game it is on and asks for that one only (?game_id=);
    # without it every game's slides come back.
    game_id = request.args.get("game_id")
    games = [dict(r) for r in db().execute(
        "SELECT id,name,short_name,accent FROM games WHERE enabled=1 AND (?1 IS NULL OR id=?1) ORDER BY name", (game_id,),
    )]
    slides = []
    for game in games:
        for mode in modes:
            cards = fetch_banner_cards(game["id"], mode, uid)
            if cards:
                slides.append({"game_id": game["id"], "game_name": game["name"], "game_short_name": game["short_name"], "accent": game["accent"], "mode": mode, "cards": cards})
    return jsonify({"slides": slides})


@app.get("/api/home-recent")
@login_required
def home_recent():
    game_id = request.args.get("game_id")
    if not game_id:
        return jsonify([])
    # The price is looked up for the five cards shown only, not for every owned card of the game.
    rows = db().execute(
        """SELECT v.id variant_id,i.id identity_id,i.canonical_name,p.collector_number,
              s.name set_name,s.code set_code,SUM(c.quantity) quantity,
              MAX(c.last_added_at) added_at
            FROM collection_entries c
            JOIN variants v ON v.id=c.variant_id
            JOIN printings p ON p.id=v.printing_id
            JOIN card_identities i ON i.id=p.identity_id
            JOIN sets s ON s.id=p.set_id
            WHERE c.user_id=? AND v.game_id=? AND c.quantity>0
            GROUP BY v.id
            ORDER BY MAX(c.last_added_at) DESC,MAX(c.id) DESC
            LIMIT 30""",
        (user_id(), game_id),
    ).fetchall()
    recent = first_per_identity(rows, 5)
    prices = latest_prices(card["variant_id"] for card in recent)
    for card in recent:
        card["price"] = prices.get(card["variant_id"])
    return jsonify(recent)
