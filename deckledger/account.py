"""A user's own settings, account data and password."""

import json
import re

from flask import jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .web import app, db, login_required, user_id


@app.post("/api/settings")
@login_required
def save_settings():
    payload = request.get_json(force=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    for key,value in payload.items():
        db().execute("INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value", (user_id(),key,json.dumps(value)))
    db().commit(); return jsonify({"saved":True})


@app.patch("/api/account")
@login_required
def update_account():
    payload = request.get_json(force=True) or {}
    uid = user_id()
    updates, params = [], []
    if "display_name" in payload:
        display_name = str(payload["display_name"] or "").strip()
        if not display_name:
            return jsonify({"error": "Anzeigename darf nicht leer sein."}), 400
        updates.append("display_name=?"); params.append(display_name)
    if "username" in payload:
        username = str(payload["username"] or "").strip()
        if not re.match(r"^[a-zA-Z0-9._-]{3,32}$", username):
            return jsonify({"error": "Benutzername muss 3-32 Zeichen lang sein (Buchstaben, Zahlen, . _ -)."}), 400
        if db().execute("SELECT 1 FROM users WHERE lower(username)=lower(?) AND id!=?", (username, uid)).fetchone():
            return jsonify({"error": "Benutzername ist bereits vergeben."}), 409
        updates.append("username=?"); params.append(username)
    if "email" in payload:
        email = str(payload["email"] or "").strip()
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return jsonify({"error": "E-Mail-Adresse ist ungültig."}), 400
        if email and db().execute("SELECT 1 FROM users WHERE email!='' AND lower(email)=lower(?) AND id!=?", (email, uid)).fetchone():
            return jsonify({"error": "E-Mail-Adresse wird bereits von einem anderen Konto verwendet."}), 409
        updates.append("email=?"); params.append(email)
    if not updates:
        return jsonify({"error": "Keine Änderungen übermittelt."}), 400
    params.append(uid)
    db().execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", params)
    db().commit()
    row = db().execute("SELECT id,username,display_name,email,role FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify(dict(row))


@app.post("/api/account/password")
@login_required
def update_account_password():
    payload = request.get_json(force=True) or {}
    new_password = str(payload.get("new_password") or "")
    current_password = str(payload.get("current_password") or "")
    if len(new_password) < 8:
        return jsonify({"error": "Das neue Passwort muss mindestens 8 Zeichen lang sein."}), 400
    row = db().execute("SELECT password_hash FROM users WHERE id=?", (user_id(),)).fetchone()
    # A blank stored hash means an SSO-only account with no local password yet -- skip the
    # current-password check so it can SET one for the first time, not just change one.
    if row["password_hash"] and not check_password_hash(row["password_hash"], current_password):
        return jsonify({"error": "Aktuelles Passwort ist nicht korrekt."}), 401
    db().execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user_id()))
    db().commit()
    return jsonify({"saved": True})


@app.post("/api/account/oauth/unlink")
@login_required
def unlink_account_oauth():
    row = db().execute("SELECT password_hash FROM users WHERE id=?", (user_id(),)).fetchone()
    if not row["password_hash"]:
        return jsonify({"error": "Bitte zuerst ein Passwort festlegen -- sonst ist nach dem Trennen kein Login mehr möglich."}), 400
    db().execute("UPDATE users SET oauth_provider='', oauth_subject='' WHERE id=?", (user_id(),))
    db().commit()
    return jsonify({"unlinked": True})
