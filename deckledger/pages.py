"""The page shell, the service worker and what the dashboard loads first."""

from pathlib import Path

from flask import jsonify, render_template, request, send_file

from .config import PUBLIC_DIR, jload
from .games import game as game_rules
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
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
    text = path.read_text(encoding="utf-8")
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
    response = send_file(Path(app.static_folder) / "service-worker.js", mimetype="application/javascript", conditional=True, etag=True, max_age=0)
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/bootstrap")
@login_required
def bootstrap():
    uid = user_id()
    current = db().execute("SELECT id,username,display_name,role,email,oauth_subject,password_hash FROM users WHERE id=?", (uid,)).fetchone()
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
                 FROM collection_entries c JOIN variants v ON v.id=c.variant_id
                 JOIN printings p ON p.id=v.printing_id
                 WHERE c.user_id=? AND v.game_id=? AND (? IS NULL OR p.language=?)""",
            (uid, game_id, lang, lang),
        ).fetchone()
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
    games = [dict(r) for r in db().execute("SELECT id,name,short_name,accent FROM games WHERE enabled=1 ORDER BY name")]
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
    rows = db().execute(
        f"""SELECT v.id variant_id,i.id identity_id,i.canonical_name,p.collector_number,
              s.name set_name,s.code set_code,SUM(c.quantity) quantity,
              MAX(c.last_added_at) added_at,{latest_price_sql('v')} price
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
    recent, seen = [], set()
    for row in rows:
        if row["identity_id"] in seen:
            continue
        seen.add(row["identity_id"])
        recent.append(dict(row))
        if len(recent) == 5:
            break
    return jsonify(recent)
