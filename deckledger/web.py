"""The Flask app itself: database connection per request, request guards, login decorators."""

import gzip
import hashlib
import os
import sqlite3
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import orjson
from flask import Flask, g, jsonify, redirect, request, session, url_for
from flask.json.provider import DefaultJSONProvider
from werkzeug.middleware.proxy_fix import ProxyFix

from . import migrations, search
from .config import DB_PATH, ROOT


class DeckLedger(Flask):
    def send_static_file(self, filename):
        """Flask's static view, plus what lets browsers keep the files: a URL that carries the
        file's current content hash (asset_url) can never mean anything else, so it may be kept
        for a year without asking again -- every page load used to revalidate each of the
        twenty-odd scripts and stylesheets. Text is sent compressed (see compress_file)."""
        response = super().send_static_file(filename)
        version = request.args.get("v")
        if version and version == asset_version(filename):
            response.cache_control.no_cache = None
            response.cache_control.public = True
            response.cache_control.max_age = IMMUTABLE_MAX_AGE
            response.cache_control.immutable = True
        return compress_file(response, Path(self.static_folder) / filename)


class OrjsonProvider(DefaultJSONProvider):
    """Answers encoded with orjson: the catalogue of a game is some 28 MB of JSON, which the
    standard library took over 200 ms to write and orjson about 60. Keys stay sorted. Dates,
    decimals and the like still go through Flask's own conversion (the pass-through options and
    `default`), so what a client reads is the same; only the spelling differs: non-ASCII as UTF-8
    instead of \\u escapes, an exponent without '+', NaN as null (which a browser can parse at
    all), and number-like keys sorted as text ("10" before "2"; a browser orders those itself).
    Whatever orjson cannot write (an integer beyond 64 bits, a lone surrogate) is handed to Flask's
    own provider, as are answers with sort_keys switched off and debug mode's indented output."""

    OPTIONS = orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS | orjson.OPT_PASSTHROUGH_DATETIME | orjson.OPT_PASSTHROUGH_DATACLASS

    def response(self, *args, **kwargs):
        if (self.compact is None and self._app.debug) or self.compact is False or not self.sort_keys:
            return super().response(*args, **kwargs)
        if args and kwargs:
            raise TypeError("app.json.response() takes either args or kwargs, not both")
        obj = kwargs or (args[0] if len(args) == 1 else list(args) or None)
        try:
            payload = orjson.dumps(obj, default=self.default, option=self.OPTIONS)
        except orjson.JSONEncodeError:
            return super().response(*args, **kwargs)
        return self._app.response_class(payload + b"\n", mimetype=self.mimetype)


app = DeckLedger("deckledger", root_path=str(ROOT))
app.json = OrjsonProvider(app)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "deckledger-local-development-key")
if os.environ.get("TRUST_PROXY_HEADERS", "").lower() in ("1", "true", "yes"):
    # Opt-in only (never trust X-Forwarded-* from directly-exposed traffic, where a client could
    # spoof them) -- needed behind a TLS-terminating reverse proxy so url_for(_external=True) in
    # the OAuth redirect_uri comes out as https://, matching what's registered with the IdP,
    # instead of the plain http:// Flask would otherwise infer from the proxy's own request.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# Explicit instead of relying on each browser's own default for cookies without the attribute.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


IMMUTABLE_MAX_AGE = 31_536_000
# Text formats worth compressing; images and fonts come compressed already.
COMPRESSIBLE_FILES = frozenset({"text/css", "text/javascript", "application/javascript", "application/json", "application/manifest+json", "image/svg+xml"})
FILE_COMPRESS_MIN_BYTES = 1024
_asset_versions = {}
_compressed_files = {}


def derived_from_file(path, cache, derive):
    """derive(content) for a file that ships with the app, worked out once per version of the
    file: kept per path and redone when its modification time or size changes."""
    path = Path(path)
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    cached = cache.get(str(path))
    if not cached or cached[0] != key:
        cached = (key, derive(path.read_bytes()))
        cache[str(path)] = cached
    return cached[1]


def asset_version(filename):
    return derived_from_file(Path(app.static_folder) / filename, _asset_versions, lambda content: hashlib.sha256(content).hexdigest()[:12])


@app.template_global()
def asset_url(filename):
    """Static URL carrying a hash of the file's content. The service worker and browsers keep
    static files for good, keyed by URL; a hand-maintained ?v=N only helps when someone remembers
    to bump it, and a forgotten bump meant a stale stylesheet or script stayed in use indefinitely."""
    return f"{url_for('static', filename=filename)}?v={asset_version(filename)}"


def accepts_gzip():
    # Werkzeug weighs the q-values: "gzip;q=0" refuses it, "*" accepts it.
    return request.accept_encodings["gzip"] > 0


def compress_file(response, path):
    """A send_file() answer gzipped for a client that accepts it -- the stylesheets and scripts
    are about a megabyte as they are and a fifth of that compressed. Compressed once per version
    of the file and kept in memory (the files are the app's own, small and few), not on every
    request."""
    if response.mimetype not in COMPRESSIBLE_FILES:
        return response
    response.vary.add("Accept-Encoding")
    if response.status_code != 200 or not accepts_gzip():
        return response
    payload = derived_from_file(
        path, _compressed_files,
        lambda content: gzip.compress(content, compresslevel=9, mtime=0) if len(content) >= FILE_COMPRESS_MIN_BYTES else None,
    )
    if payload is None:
        return response
    response.close()  # the file send_file opened; the compressed copy is sent instead
    response.direct_passthrough = False
    response.set_data(payload)
    response.headers["Content-Encoding"] = "gzip"
    response.headers.pop("Accept-Ranges", None)  # byte ranges of the compressed copy are not served
    etag, weak = response.get_etag()
    if etag:
        # Another representation of the file needs a tag of its own.
        response.set_etag(f"{etag}-gzip", weak)
    return response.make_conditional(request)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.create_function("search_score", -1, search.score, deterministic=True)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
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


# Generated answers worth compressing: the catalogue's JSON and the page shell.
COMPRESSIBLE_ANSWERS = frozenset({"application/json", "text/html"})


@app.after_request
def compress_answer(response):
    """The catalogue endpoints return megabytes of JSON (every card of a game is ~28 MB) that
    shrinks to about a tenth, and the page shell -- loaded on every visit, half of it the inline
    glass-plate SVG -- to a half. Images and other binary answers are already compressed; files
    sent with send_file() go through compress_file() instead."""
    if (
        response.mimetype not in COMPRESSIBLE_ANSWERS or response.direct_passthrough
        or response.status_code != 200 or "Content-Encoding" in response.headers
    ):
        return response
    response.vary.add("Accept-Encoding")
    payload = response.get_data()
    if len(payload) < 2048 or not accepts_gzip():
        return response
    response.set_data(gzip.compress(payload, compresslevel=5))
    response.headers["Content-Encoding"] = "gzip"
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
