"""A public, shareable view of a user's collection: /u/<slug>, readable without signing in.

The user switches it on in Settings and picks which games it shows. A visitor sees those games'
sets and every card of a set -- the ones the user owns with their count, the others greyed out
as in the set overview -- and nothing else: no prices or values, no conditions, no watchlists,
no other game, no other part of the app. Everything public goes through this module, so what
leaves the server is decided here (public_card); a profile that is off or unknown is a 404,
the same answer either way.

The setting lives in user_settings under PROFILE_KEY and is only written through
/api/account/public-profile, which checks the address (slug) and keeps it unique.
"""
import json
import os
import re

from flask import Response, jsonify, render_template, request

from .catalog import card_rows, serialize_card_rows, sets_of_game
from .config import jload
from .web import app, db, login_required, user_id

PROFILE_KEY = "publicProfile"
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])")
NAME_LIMIT = 60
# Where profiles are shared from, e.g. https://deckledger.example.org -- the app itself may run
# under a private address. Without it the link uses the address the settings were opened on.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_PROFILE_URL", "").strip().rstrip("/")


def load_profile(connection, uid):
    row = connection.execute("SELECT value FROM user_settings WHERE user_id=? AND key=?", (uid, PROFILE_KEY)).fetchone()
    stored = jload(row["value"], {}) if row else {}
    stored = stored if isinstance(stored, dict) else {}
    return {"enabled": bool(stored.get("enabled")), "slug": str(stored.get("slug") or ""), "name": str(stored.get("name") or ""),
            "games": [str(game) for game in stored.get("games") or []]}


def profile_url(slug):
    return f"{PUBLIC_BASE_URL or request.host_url.rstrip('/')}/u/{slug}" if slug else ""


def public_owner(slug):
    """(user id, profile, display name) of the switched-on profile at this address, else None."""
    row = db().execute(
        "SELECT s.user_id,u.display_name FROM user_settings s JOIN users u ON u.id=s.user_id WHERE s.key=? AND json_extract(s.value,'$.slug')=?",
        (PROFILE_KEY, slug.lower())).fetchone()
    if not row:
        return None
    profile = load_profile(db(), row["user_id"])
    if not profile["enabled"] or not profile["games"]:
        return None
    return row["user_id"], profile, profile["name"] or row["display_name"]


def not_found():
    return jsonify({"error": "Dieses Profil gibt es nicht oder es ist nicht öffentlich."}), 404


def shared_games(profile):
    """The profile's games that exist, in the catalogue's order."""
    wanted = set(profile["games"])
    return [dict(row) for row in db().execute("SELECT id,name,short_name,accent FROM games ORDER BY rowid") if row["id"] in wanted]


def public_card(card):
    """What a visitor may see of a card tile: no price, value, condition or watchlist."""
    variant_fields = ("variant_id", "finish", "language", "quantity", "rarity", "collector_number")
    return {
        "identity_id": card["identity_id"], "canonical_name": card["canonical_name"], "collector_number": card["collector_number"],
        "rarity": card["rarity"], "language": card["language"], "variant_id": card["variant_id"], "finish": card["finish"],
        "quantity": card["quantity"], "languages": card["languages"],
        "variants": [{key: variant[key] for key in variant_fields} for variant in card["variants"]],
    }


# ---- The owner's setting ------------------------------------------------------------------------

@app.get("/api/account/public-profile")
@login_required
def get_public_profile():
    profile = load_profile(db(), user_id())
    return jsonify({**profile, "url": profile_url(profile["slug"])})


@app.put("/api/account/public-profile")
@login_required
def save_public_profile():
    uid, payload = user_id(), request.get_json(force=True) or {}
    profile = load_profile(db(), uid)
    if "slug" in payload:
        slug = str(payload["slug"] or "").strip().lower()
        if slug and not SLUG.fullmatch(slug):
            return jsonify({"error": "Die Adresse darf nur Kleinbuchstaben, Ziffern und Bindestriche enthalten (3 bis 32 Zeichen, kein Bindestrich am Anfang oder Ende)."}), 400
        taken = db().execute("SELECT 1 FROM user_settings WHERE key=? AND user_id!=? AND json_extract(value,'$.slug')=?", (PROFILE_KEY, uid, slug)).fetchone()
        if slug and taken:
            return jsonify({"error": "Diese Adresse ist schon vergeben."}), 400
        profile["slug"] = slug
    if "name" in payload:
        profile["name"] = str(payload["name"] or "").strip()[:NAME_LIMIT]
    if "games" in payload:
        known = {row["id"] for row in db().execute("SELECT id FROM games")}
        profile["games"] = [game for game in dict.fromkeys(str(game) for game in payload["games"] or []) if game in known]
    if "enabled" in payload:
        profile["enabled"] = bool(payload["enabled"])
    if profile["enabled"] and not profile["slug"]:
        return jsonify({"error": "Wähle eine Adresse für dein Profil."}), 400
    if profile["enabled"] and not profile["games"]:
        return jsonify({"error": "Gib mindestens ein TCG frei."}), 400
    db().execute("INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                 (uid, PROFILE_KEY, json.dumps(profile)))
    db().commit()
    return jsonify({**profile, "url": profile_url(profile["slug"])})


# ---- What visitors see --------------------------------------------------------------------------

@app.get("/u/<slug>")
def public_profile_page(slug):
    owner = public_owner(slug)
    if not owner:
        return render_template("public.html", slug=slug, name="", missing=True), 404
    return render_template("public.html", slug=slug.lower(), name=owner[2], missing=False)


@app.get("/api/public/<slug>")
def public_profile_data(slug):
    owner = public_owner(slug)
    if not owner:
        return not_found()
    uid, profile, name = owner
    games = []
    for game in shared_games(profile):
        counts = db().execute(
            """SELECT COUNT(DISTINCT p.identity_id) total,
                 COUNT(DISTINCT CASE WHEN EXISTS(SELECT 1 FROM collection_entries c JOIN variants cv ON cv.id=c.variant_id
                   WHERE c.user_id=? AND c.quantity>0 AND cv.printing_id=p.id) THEN p.identity_id END) owned
               FROM printings p WHERE p.game_id=? AND EXISTS(SELECT 1 FROM variants v WHERE v.printing_id=p.id)""", (uid, game["id"])).fetchone()
        copies = db().execute("SELECT COALESCE(SUM(c.quantity),0) FROM collection_entries c JOIN variants v ON v.id=c.variant_id WHERE c.user_id=? AND v.game_id=?",
                              (uid, game["id"])).fetchone()[0]
        games.append({**game, "total": counts["total"], "owned": counts["owned"], "copies": copies})
    return jsonify({"name": name, "slug": profile["slug"], "games": games})


@app.get("/api/public/<slug>/games/<game_id>/sets")
def public_game_sets(slug, game_id):
    owner = public_owner(slug)
    if not owner or game_id not in owner[1]["games"]:
        return not_found()
    keep = ("id", "game_id", "code", "name", "set_type", "accent", "release_date", "release_dates", "printed_card_count",
            "classifications", "visual_version", "owned", "total", "base_completion", "master_completion")
    return jsonify([{key: item.get(key) for key in keep} for item in sets_of_game(game_id, owner[0])])


@app.get("/api/public/<slug>/sets/<set_id>/cards")
def public_set_cards(slug, set_id):
    owner = public_owner(slug)
    set_row = db().execute("SELECT id,game_id,code,name,release_date,printed_card_count FROM sets WHERE id=?", (set_id,)).fetchone()
    if not owner or not set_row or set_row["game_id"] not in owner[1]["games"]:
        return not_found()
    mode = request.args.get("mode", "all") if request.args.get("mode") in ("all", "owned", "missing") else "all"
    cards, stats = serialize_card_rows(card_rows(set_id, owner[0]), "combined", mode, "", "number", set_row["game_id"])
    return jsonify({"set": dict(set_row), "cards": [public_card(card) for card in cards],
                    "stats": {key: stats[key] for key in ("owned", "total", "missing", "base", "master", "variant_owned", "variant_total")}})


def shared_image(slug, game_id, view, *args):
    owner = public_owner(slug)
    if not owner or game_id not in owner[1]["games"]:
        return Response(status=404)
    # The app's own image views, past their sign-in check (login_required keeps them as __wrapped__).
    return view.__wrapped__(*args)


@app.get("/u/<slug>/art/<variant_id>")
def public_card_art(slug, variant_id):
    from .images import card_art
    row = db().execute("SELECT game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
    return shared_image(slug, row["game_id"] if row else "", card_art, variant_id)


@app.get("/u/<slug>/set-logo/<set_id>")
def public_set_logo(slug, set_id):
    from .assets import set_logo
    row = db().execute("SELECT game_id FROM sets WHERE id=?", (set_id,)).fetchone()
    return shared_image(slug, row["game_id"] if row else "", set_logo, set_id)


@app.get("/u/<slug>/game-logo/<game_id>")
def public_game_logo(slug, game_id):
    from .assets import game_logo
    return shared_image(slug, game_id, game_logo, game_id)
