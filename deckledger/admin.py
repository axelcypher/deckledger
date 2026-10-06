"""Admin API: accounts, games, catalogue providers, manual cards, SSO settings."""

import json
import re
import subprocess
import sys
from pathlib import Path

from flask import jsonify, request
from werkzeug.security import generate_password_hash

from catalog_provider_contract import digest, slug

from .config import CARD_BACK_UPLOAD_DIR, OAUTH_CONFIG_DEFAULTS, OAUTH_CONFIG_PATH, ROOT, jload, now_iso
from .games import GAMES
from .web import admin_required, app, db, user_id
from .schema import default_provider_code, ensure_user_lists
from .auth import resolve_oauth_config
from .sheets import remove_user_backgrounds


# ---- User management (admin) -------------------------------------------------------------------
USERNAME_PATTERN = r"[a-zA-Z0-9._-]{3,32}"
EMAIL_PATTERN = r"[^@\s]+@[^@\s]+\.[^@\s]+"
USER_ROLES = ("user", "admin")


def user_fields_error(connection, payload, user_id_to_skip=0, require=()):
    """Validates the account fields present in `payload`; returns an error text or None."""
    for field in require:
        if not str(payload.get(field) or "").strip():
            return {"username": "Benutzername ist erforderlich.", "password": "Passwort ist erforderlich."}[field]
    if "username" in payload:
        username = str(payload["username"] or "").strip()
        if not re.fullmatch(USERNAME_PATTERN, username):
            return "Benutzername muss 3-32 Zeichen lang sein (Buchstaben, Zahlen, . _ -)."
        if connection.execute("SELECT 1 FROM users WHERE lower(username)=lower(?) AND id!=?", (username, user_id_to_skip)).fetchone():
            return "Benutzername ist bereits vergeben."
    if payload.get("email"):
        email = str(payload["email"]).strip()
        if not re.fullmatch(EMAIL_PATTERN, email):
            return "E-Mail-Adresse ist ungültig."
        if connection.execute("SELECT 1 FROM users WHERE email!='' AND lower(email)=lower(?) AND id!=?", (email, user_id_to_skip)).fetchone():
            return "E-Mail-Adresse wird bereits von einem anderen Konto verwendet."
    if payload.get("password") and len(str(payload["password"])) < 8:
        return "Das Passwort muss mindestens 8 Zeichen lang sein."
    if "role" in payload and payload["role"] not in USER_ROLES:
        return "Unbekannte Rolle."
    return None


def other_admins(connection, uid):
    return connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND id!=?", (uid,)).fetchone()[0]


@app.get("/api/admin/users")
@admin_required
def admin_list_users():
    rows = db().execute(
        """SELECT u.id,u.username,u.display_name,u.email,u.role,u.created_at,
                  u.oauth_subject!='' oauth_linked, u.password_hash!='' password_set,
                  (SELECT COALESCE(SUM(quantity),0) FROM collection_entries c WHERE c.user_id=u.id) copies,
                  (SELECT COUNT(*) FROM decks d WHERE d.user_id=u.id) decks
           FROM users u ORDER BY lower(u.username)"""
    ).fetchall()
    return jsonify([{**dict(row), "is_self": row["id"] == user_id()} for row in rows])


@app.post("/api/admin/users")
@admin_required
def admin_create_user():
    p = request.get_json(force=True)
    if not isinstance(p, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    error = user_fields_error(db(), p, require=("username", "password"))
    if error:
        return jsonify({"error": error}), 400
    username = str(p["username"]).strip()
    cur = db().execute(
        "INSERT INTO users(username,display_name,password_hash,role,created_at,email) VALUES(?,?,?,?,?,?)",
        (username, str(p.get("display_name") or "").strip() or username, generate_password_hash(str(p["password"])),
         p.get("role") or "user", now_iso(), str(p.get("email") or "").strip()),
    )
    ensure_user_lists(db(), cur.lastrowid)
    db().commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.patch("/api/admin/users/<int:target_id>")
@admin_required
def admin_update_user(target_id):
    target = db().execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    if not target:
        return jsonify({"error": "Konto nicht gefunden."}), 404
    p = request.get_json(force=True)
    if not isinstance(p, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    error = user_fields_error(db(), p, user_id_to_skip=target_id)
    if error:
        return jsonify({"error": error}), 400
    if p.get("role") == "user" and target["role"] == "admin" and not other_admins(db(), target_id):
        return jsonify({"error": "Das letzte Admin-Konto kann nicht herabgestuft werden."}), 400
    fields, values = [], []
    for key in ("username", "display_name", "email", "role"):
        if key in p:
            value = str(p[key] or "").strip()
            if key == "display_name" and not value:
                continue
            fields.append(f"{key}=?")
            values.append(value)
    if p.get("password"):
        fields.append("password_hash=?")
        values.append(generate_password_hash(str(p["password"])))
    if p.get("unlink_oauth"):
        fields += ["oauth_provider=''", "oauth_subject=''"]
    if not fields:
        return jsonify({"error": "Keine Änderungen übermittelt."}), 400
    db().execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", (*values, target_id))
    db().commit()
    return jsonify({"saved": True})


@app.delete("/api/admin/users/<int:target_id>")
@admin_required
def admin_delete_user(target_id):
    target = db().execute("SELECT role FROM users WHERE id=?", (target_id,)).fetchone()
    if not target:
        return jsonify({"error": "Konto nicht gefunden."}), 404
    if target_id == user_id():
        return jsonify({"error": "Das eigene Konto kann hier nicht gelöscht werden."}), 400
    if target["role"] == "admin" and not other_admins(db(), target_id):
        return jsonify({"error": "Das letzte Admin-Konto kann nicht gelöscht werden."}), 400
    # Everything the account entered goes with it; the catalogue and prices are shared.
    db().execute("DELETE FROM deck_cards WHERE deck_id IN (SELECT id FROM decks WHERE user_id=?)", (target_id,))
    db().execute("DELETE FROM named_watchlist_entries WHERE list_id IN (SELECT id FROM named_watchlists WHERE user_id=?)", (target_id,))
    db().execute("DELETE FROM inbox_items WHERE user_id=?", (target_id,))
    db().execute("DELETE FROM sheet_posts WHERE user_id=?", (target_id,))
    db().execute("DELETE FROM watch_communities WHERE user_id=?", (target_id,))
    db().execute("DELETE FROM deals WHERE user_id=?", (target_id,))  # their cards and events go with them (ON DELETE CASCADE)
    db().execute("DELETE FROM trade_sheet_cards WHERE sheet_id IN (SELECT id FROM trade_sheets WHERE user_id=?)", (target_id,))
    remove_user_backgrounds(target_id)
    for table in ("decks", "named_watchlists", "trade_sheets", "watchlist_entries", "collection_entries", "import_operations", "user_settings", "applied_requests",
                  "ebay_accounts", "ebay_listings", "ebay_sales", "ebay_drafts"):
        db().execute(f"DELETE FROM {table} WHERE user_id=?", (target_id,))
    db().execute("DELETE FROM users WHERE id=?", (target_id,))
    db().commit()
    return jsonify({"deleted": True})


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
    db().execute("DELETE FROM trade_sheets WHERE game_id=?", (game_id,))  # its cards go with it (ON DELETE CASCADE)
    db().execute("DELETE FROM deals WHERE game_id=?", (game_id,))
    db().execute("DELETE FROM watch_communities WHERE game_id=?", (game_id,))
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
           (id, game_id, label, kind, code, minimum_sets, minimum_cards, timeout_seconds, provider_version, enabled, created_at, updated_at, customized)
           VALUES (?,?,?,'custom_code',?,?,?,?,?,1,?,?,1)""",
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
        shipped = default_provider_code(provider_id) if provider_id in GAMES and GAMES[provider_id].provider else None
        fields += ["code=?", "provider_version=?", "customized=?"]
        values += [code, digest(code), 0 if code == shipped else 1]
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
            [sys.executable, str(ROOT / "catalog_sync.py"), "--provider", provider_id],
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
    for table in ("collection_entries", "deck_cards", "watchlist_entries", "named_watchlist_entries", "trade_sheet_cards", "marketplace_products", "price_observations", "ebay_tracked_items", "ebay_drafts"):
        db().execute(f"DELETE FROM {table} WHERE {variant_filter}", (identity_id,))
    db().execute("DELETE FROM variants WHERE printing_id IN (SELECT id FROM printings WHERE identity_id=?)", (identity_id,))
    db().execute("DELETE FROM printings WHERE identity_id=?", (identity_id,))
    db().execute("DELETE FROM card_identities WHERE id=?", (identity_id,))
    db().commit()
    return jsonify({"deleted": True})


@app.get("/api/admin/oauth")
@admin_required
def admin_get_oauth():
    config = resolve_oauth_config()
    response = {key: value for key, value in config.items() if key != "client_secret"}
    response["client_secret_set"] = bool(config.get("client_secret"))
    response["config_path"] = OAUTH_CONFIG_PATH
    return jsonify(response)


@app.post("/api/admin/oauth")
@admin_required
def admin_save_oauth():
    if Path(OAUTH_CONFIG_PATH).is_file():
        return jsonify({"error": f"OAuth wird über die Config-Datei {OAUTH_CONFIG_PATH} verwaltet und kann hier nicht bearbeitet werden."}), 400
    p = request.get_json(force=True) or {}
    if p.get("account_matching") not in ("manual", "email", "auto_provision"):
        return jsonify({"error": "Ungültiger Wert für account_matching."}), 400
    row = db().execute("SELECT value FROM app_settings WHERE key='oauth_config'").fetchone()
    current = jload(row["value"], {}) if row else {}
    config = {**OAUTH_CONFIG_DEFAULTS, **current}
    for key in ("enabled",):
        config[key] = bool(p.get(key))
    for key in ("provider_name", "client_id", "discovery_url", "authorize_url", "token_url", "userinfo_url",
                "scopes", "username_claim", "email_claim", "subject_claim", "account_matching"):
        if key in p:
            config[key] = str(p[key] or "").strip()
    # Blank client_secret in the payload means "leave the stored secret unchanged" -- the GET
    # response above never sends the real secret back, so the form field is blank by default
    # and a save that doesn't touch it must not wipe it out.
    if p.get("client_secret"):
        config["client_secret"] = p["client_secret"]
    db().execute(
        "INSERT INTO app_settings(key,value) VALUES('oauth_config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(config),),
    )
    db().commit()
    return jsonify({"saved": True})
