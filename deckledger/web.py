"""The Flask app itself: database connection per request, request guards, login decorators."""

import gzip
import hashlib
import os
import re
import sqlite3
from functools import lru_cache, wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, g, jsonify, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import migrations
from .config import DB_PATH, ROOT


app = Flask("deckledger", root_path=str(ROOT))
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "deckledger-local-development-key")
if os.environ.get("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
    # Opt-in only (never trust X-Forwarded-* from directly-exposed traffic, where a client could
    # spoof them) -- needed behind a TLS-terminating reverse proxy so url_for(_external=True) in
    # the OAuth redirect_uri comes out as https://, matching what's registered with the IdP,
    # instead of the plain http:// Flask would otherwise infer from the proxy's own request.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Explicit instead of relying on each browser's own default for cookies without the attribute.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


_asset_versions = {}


@app.template_global()
def asset_url(filename):
    """Static URL carrying a hash of the file's content. The service worker and browsers keep
    static files for good, keyed by URL; a hand-maintained ?v=N only helps when someone remembers
    to bump it, and a forgotten bump meant a stale stylesheet or script stayed in use indefinitely."""
    path = Path(app.static_folder) / filename
    stat = path.stat()
    cached = _asset_versions.get(filename)
    if not cached or cached[0] != (stat.st_mtime_ns, stat.st_size):
        cached = ((stat.st_mtime_ns, stat.st_size), hashlib.sha256(path.read_bytes()).hexdigest()[:12])
        _asset_versions[filename] = cached
    return f"{url_for('static', filename=filename)}?v={cached[1]}"


SEARCH_PATTERN_MAX = 200


@lru_cache(maxsize=256)
def search_pattern(query):
    """What a search box's text matches, ignoring case: the text as typed, or the text read as a
    regular expression -- so "PL9|PL10" finds either and "Monarch (PL8)" still finds the card of
    that name. Text that is no valid expression is only ever taken literally."""
    literal = re.escape(query)
    if len(query) <= SEARCH_PATTERN_MAX:
        try:
            return re.compile(f"{literal}|(?:{query})", re.I)
        except re.error:
            pass
    return re.compile(literal, re.I)


def search_matches(query, text):
    """SQL: search_matches(?, column)."""
    return 0 if text is None else int(search_pattern(query).search(str(text)) is not None)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.create_function("search_matches", 2, search_matches, deterministic=True)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        # Without this, two connections writing at the same instant (a web
        # request racing a background catalog_sync.py/price_sync.py run, or
        # just two concurrent gunicorn threads) fail immediately with
        # "database is locked" instead of one briefly waiting its turn.
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.before_request
def reject_cross_site_writes():
    """The API reads JSON regardless of Content-Type, so a form on any other website could
    otherwise post to it with the visitor's session. Browsers label where a request comes from;
    scripts and tools that send neither header are not a cross-site browser request."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        allowed = fetch_site in ("same-origin", "none")
    else:
        origin = request.headers.get("Origin")
        allowed = not origin or urlparse(origin).netloc == request.host
    if not allowed:
        return jsonify({"error": "Anfrage von einer fremden Seite abgelehnt."}), 403
    return None


@app.after_request
def compress_json(response):
    """The catalogue endpoints return megabytes of JSON (every card of a game is ~17 MB) that
    shrinks to about a tenth. Images and other binary answers are already compressed."""
    if (
        response.mimetype != "application/json" or response.direct_passthrough
        or response.status_code != 200 or "Content-Encoding" in response.headers
        or "gzip" not in request.headers.get("Accept-Encoding", "")
    ):
        return response
    payload = response.get_data()
    if len(payload) < 2048:
        return response
    response.set_data(gzip.compress(payload, compresslevel=5))
    response.headers["Content-Encoding"] = "gzip"
    response.headers.add("Vary", "Accept-Encoding")
    return response


@app.errorhandler(sqlite3.IntegrityError)
def integrity_error(error):
    # A write that references a card, list or game that does not exist (any more).
    db().rollback()
    if request.path.startswith("/api/"):
        return jsonify({"error": "Der Eintrag verweist auf Daten, die es nicht (mehr) gibt."}), 400
    raise error


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # The cookie is only a claim: an account an admin has deleted must stop working at once,
        # not when its session cookie happens to be dropped.
        if session.get("user_id") and not db().execute("SELECT 1 FROM users WHERE id=?", (session["user_id"],)).fetchone():
            session.clear()
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "authentication required"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        row = db().execute("SELECT role FROM users WHERE id=?", (user_id(),)).fetchone()
        if not row or row["role"] != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "admin access required"}), 403
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def user_id():
    return int(session["user_id"])


@app.get("/health")
def health():
    version, expected = migrations.status(db())
    return jsonify({"status": "ok", "database": os.path.basename(DB_PATH), "schema_version": version, "schema_expected": expected})
