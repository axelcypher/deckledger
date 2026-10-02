"""Watching the posts a sheet was published as: new comments end up in the user's inbox.

Reading only -- nothing here ever posts or replies. A user links a post to a sheet by its URL;
the watcher (post_watch.py, once a minute) then reads that post's comment feed and stores what
is new. So far the only source is Reddit, read through its public Atom feeds without a login.

Reddit allows an anonymous client about one request a minute and says so in its answer's
headers. Every request -- the background job's and a user's "check now" -- therefore goes through
one gate (next_request_at in app_settings), and each run asks for a single post: the one that
has waited longest.
"""

import html
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import jsonify, request

from .config import jload, now_iso
from .web import app, db, login_required, user_id
from .sheets import own_sheet

USER_AGENT = "DeckLedger/1.0 (self-hosted collection manager; reads comment feeds of the user's own posts)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
POSTS_PER_SHEET = 12
# A post nobody has commented on for this long is no longer asked for.
QUIET_DAYS = 30
# Accounts whose comments are not worth an inbox entry on any post.
IGNORED_AUTHORS = {"automoderator"}
GATE_KEY = "reddit_next_request_at"
FALLBACK_PAUSE_SECONDS = 65
POST_STATES = ("watching", "done")


class FeedError(Exception):
    """The feed could not be read; `retry_after` is how long the source wants us to wait."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ---- Reddit ---------------------------------------------------------------------------------------

REDDIT_URL = re.compile(r"^https?://(?:[a-z0-9-]+\.)?reddit\.com/(?:r/([A-Za-z0-9_]{2,24})/)?comments/([a-z0-9]{4,12})(?:[/?#]|$)", re.I)
REDDIT_SHORT_URL = re.compile(r"^https?://redd\.it/([a-z0-9]{4,12})(?:[/?#]|$)", re.I)


def parse_post_url(url):
    """(source, external id, community) of a post URL, or None when it is not one we can watch."""
    url = str(url or "").strip()
    match = REDDIT_URL.match(url)
    if match:
        return "reddit", match.group(2).lower(), match.group(1) or ""
    match = REDDIT_SHORT_URL.match(url)
    if match:
        return "reddit", match.group(1).lower(), ""
    return None


def feed_url(source, external_id):
    return f"https://www.reddit.com/comments/{external_id}/.rss?limit=100&sort=new"


def fetch_feed(url):
    """The feed's bytes and how many seconds to wait before the next request. Raises FeedError."""
    try:
        response = urlopen(Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"}), timeout=20)
        payload = response.read(2_000_000)
        return payload, pause_from(response.headers)
    except HTTPError as error:
        wait = pause_from(error.headers)
        if error.code == 429:
            raise FeedError("Reddit lässt gerade keine weitere Abfrage zu.", wait) from error
        if error.code in (403, 404):
            raise FeedError(f"Der Post ist nicht (mehr) lesbar ({error.code}).", wait) from error
        raise FeedError(f"Reddit antwortet mit {error.code}.", wait) from error
    except (URLError, OSError) as error:
        raise FeedError(f"Reddit ist nicht erreichbar: {error}") from error


def pause_from(headers):
    """Seconds until the next request is allowed, from the rate limit headers of an answer."""
    try:
        remaining = float(headers.get("x-ratelimit-remaining", "1"))
        reset = float(headers.get("x-ratelimit-reset") or headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        return FALLBACK_PAUSE_SECONDS
    return max(2, int(reset) + 2) if remaining < 1 else 2


def plain_text(markup):
    """Reddit delivers a comment as HTML; the inbox shows it as text."""
    text = re.sub(r"<!--.*?-->", "", markup or "", flags=re.S)
    text = re.sub(r"<(?:br\s*/?|/p|/li|/blockquote|/h\d)>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def parse_feed(payload):
    """(title of the post, community, [comment, ...]) from a post's comment feed. A comment is
    {external_id, author, body, url, posted_at}; the post itself is not one of them."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise FeedError("Die Antwort von Reddit ist kein lesbarer Feed.") from error
    title, community, comments = "", "", []
    for category in root.findall("a:category", ATOM):
        term = (category.get("term") or "").strip()
        if term and term != "reddit.com":
            community = term
    for entry in root.findall("a:entry", ATOM):
        external_id = (entry.findtext("a:id", default="", namespaces=ATOM) or "").strip()
        link = entry.find("a:link", ATOM)
        if external_id.startswith("t3_"):
            title = (entry.findtext("a:title", default="", namespaces=ATOM) or "").strip()
            continue
        if not external_id.startswith("t1_"):
            continue
        author = (entry.findtext("a:author/a:name", default="", namespaces=ATOM) or "").strip()
        comments.append({
            "external_id": external_id,
            "author": re.sub(r"^/?u/", "", author),
            "body": plain_text(entry.findtext("a:content", default="", namespaces=ATOM))[:4000],
            "url": link.get("href") if link is not None else "",
            "posted_at": (entry.findtext("a:updated", default="", namespaces=ATOM) or "").strip(),
        })
    return title, community, comments


# ---- Which cards a comment talks about --------------------------------------------------------

def words(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def cards_mentioned(body, cards):
    """The sheet's cards a comment names. `certain` when the whole name is there ("Monarch PL8")
    or the collector number next to part of it; otherwise the name without its bracketed part
    matched ("Monarch"), which several cards of a sheet can share."""
    text = f" {words(body)} "
    found = []
    for card in cards:
        full = words(card["canonical_name"])
        base = words(re.sub(r"\([^)]*\)", "", card["canonical_name"]))
        number = words(card["collector_number"]).lstrip("0")
        has_number = bool(number) and re.search(rf"(?<![0-9])0*{re.escape(number)}(?![0-9])", text) is not None
        if full and f" {full} " in text:
            certain = True
        elif base and len(base) >= 3 and f" {base} " in text:
            certain = has_number and base != full
        else:
            continue
        found.append({"variant_id": card["variant_id"], "name": card["canonical_name"], "finish": card["finish"], "certain": certain, "base": base})
    # "Ember PL8" names one Ember for certain; the other Embers of the sheet are not meant then.
    settled = {match["base"] for match in found if match["certain"]}
    return [{key: value for key, value in match.items() if key != "base"} for match in found if match["certain"] or match["base"] not in settled]


# ---- Checking a post ----------------------------------------------------------------------------

def setting(connection, uid, key, default=""):
    row = connection.execute("SELECT value FROM user_settings WHERE user_id=? AND key=?", (uid, key)).fetchone()
    return jload(row[0], default) if row else default


def wait_seconds(connection, now=None):
    """How long until the source may be asked again; 0 when it may be asked now."""
    row = connection.execute("SELECT value FROM app_settings WHERE key=?", (GATE_KEY,)).fetchone()
    if not row:
        return 0
    try:
        allowed = datetime.fromisoformat(row[0])
    except ValueError:
        return 0
    return max(0, int((allowed - (now or datetime.now(timezone.utc))).total_seconds()) + 1)


def close_gate(connection, seconds):
    until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()
    connection.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (GATE_KEY, until))


def check_post(connection, post, cards):
    """Reads one post's comments and stores the new ones. Returns how many were new. The caller
    commits. Raises FeedError; the post row then carries the reason."""
    stamp = now_iso()
    try:
        payload, pause = fetch_feed(feed_url(post["source"], post["external_id"]))
        title, community, comments = parse_feed(payload)
    except FeedError as error:
        close_gate(connection, error.retry_after or FALLBACK_PAUSE_SECONDS)
        connection.execute("UPDATE sheet_posts SET last_checked_at=?,last_error=? WHERE id=?", (stamp, str(error), post["id"]))
        raise
    close_gate(connection, pause)
    own_name = str(setting(connection, post["user_id"], "redditUsername") or "").strip().lower().removeprefix("u/")
    new = 0
    for comment in comments:
        author = comment["author"].lower()
        if author in IGNORED_AUTHORS or (own_name and author == own_name):
            continue
        inserted = connection.execute(
            """INSERT OR IGNORE INTO inbox_items(user_id,post_id,kind,external_id,author,body,url,posted_at,matches,created_at)
               VALUES(?,?,'comment',?,?,?,?,?,?,?)""",
            (post["user_id"], post["id"], comment["external_id"], comment["author"], comment["body"], comment["url"],
             comment["posted_at"], json.dumps(cards_mentioned(comment["body"], cards)), stamp),
        ).rowcount
        new += inserted
    connection.execute(
        "UPDATE sheet_posts SET last_checked_at=?,last_error='',title=CASE WHEN ?!='' THEN ? ELSE title END,community=CASE WHEN ?!='' THEN ? ELSE community END,last_activity_at=CASE WHEN ?>0 THEN ? ELSE last_activity_at END WHERE id=?",
        (stamp, title, title[:300], community, community, new, stamp, post["id"]),
    )
    return new


def cards_of_sheet(connection, sheet_id):
    return [dict(row) for row in connection.execute(
        """SELECT e.variant_id,i.canonical_name,p.collector_number,v.finish FROM trade_sheet_cards e
           JOIN variants v ON v.id=e.variant_id JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
           WHERE e.sheet_id=?""", (sheet_id,))]


def run_due(connection):
    """What the background job does each time: retire posts that have gone quiet, then read the
    one watched post that has waited longest -- if the source may be asked at all. Returns a
    short summary for the log."""
    connection.row_factory = sqlite3.Row
    quiet = (datetime.now(timezone.utc) - timedelta(days=QUIET_DAYS)).replace(microsecond=0).isoformat()
    retired = connection.execute(
        "UPDATE sheet_posts SET status='done' WHERE status='watching' AND COALESCE(last_activity_at,created_at)<?", (quiet,),
    ).rowcount
    connection.commit()
    waiting = wait_seconds(connection)
    if waiting:
        return {"retired": retired, "waiting": waiting}
    post = connection.execute(
        "SELECT * FROM sheet_posts WHERE status='watching' ORDER BY COALESCE(last_checked_at,'') ,id LIMIT 1",
    ).fetchone()
    if not post:
        return {"retired": retired, "watching": 0}
    try:
        new = check_post(connection, post, cards_of_sheet(connection, post["sheet_id"]))
        return {"retired": retired, "checked": post["id"], "new": new}
    except FeedError as error:
        return {"retired": retired, "checked": post["id"], "error": str(error)}
    finally:
        connection.commit()


# ---- API ------------------------------------------------------------------------------------------

def post_payload(row):
    post = {key: row[key] for key in ("id", "sheet_id", "source", "url", "community", "title", "status", "created_at", "last_checked_at", "last_activity_at", "last_error")}
    post["new"] = db().execute("SELECT COUNT(*) FROM inbox_items WHERE post_id=? AND state='new'", (row["id"],)).fetchone()[0]
    return post


def own_post(post_id):
    return db().execute("SELECT * FROM sheet_posts WHERE id=? AND user_id=?", (post_id, user_id())).fetchone()


@app.route("/api/trade-sheets/<int:sheet_id>/posts", methods=["GET", "POST"])
@login_required
def sheet_posts(sheet_id):
    sheet = own_sheet(sheet_id)
    if not sheet:
        return jsonify({"error": "sheet not found"}), 404
    if request.method == "POST":
        payload = request.get_json(force=True)
        url = str((payload if isinstance(payload, dict) else {}).get("url") or "").strip()
        parsed = parse_post_url(url)
        if not parsed:
            return jsonify({"error": "Das ist keine Adresse eines Reddit-Posts (…/comments/<id>/…)."}), 400
        if db().execute("SELECT COUNT(*) FROM sheet_posts WHERE sheet_id=?", (sheet_id,)).fetchone()[0] >= POSTS_PER_SHEET:
            return jsonify({"error": f"Ein Sheet kann höchstens {POSTS_PER_SHEET} Posts haben."}), 400
        source, external_id, community = parsed
        if db().execute("SELECT 1 FROM sheet_posts WHERE sheet_id=? AND source=? AND external_id=?", (sheet_id, source, external_id)).fetchone():
            return jsonify({"error": "Dieser Post ist mit dem Sheet schon verknüpft."}), 409
        cursor = db().execute(
            "INSERT INTO sheet_posts(user_id,sheet_id,source,external_id,url,community,created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id(), sheet_id, source, external_id, url[:500], community, now_iso()),
        )
        db().commit()
        return jsonify(post_payload(own_post(cursor.lastrowid))), 201
    rows = db().execute("SELECT * FROM sheet_posts WHERE sheet_id=? AND user_id=? ORDER BY id DESC", (sheet_id, user_id())).fetchall()
    return jsonify([post_payload(row) for row in rows])


@app.route("/api/sheet-posts/<int:post_id>", methods=["PATCH", "DELETE"])
@login_required
def sheet_post(post_id):
    post = own_post(post_id)
    if not post:
        return jsonify({"error": "post not found"}), 404
    if request.method == "DELETE":
        db().execute("DELETE FROM inbox_items WHERE post_id=?", (post_id,))
        db().execute("DELETE FROM sheet_posts WHERE id=?", (post_id,))
        db().commit()
        return jsonify({"deleted": True})
    status = (request.get_json(force=True) or {}).get("status")
    if status not in POST_STATES:
        return jsonify({"error": "Unbekannter Status."}), 400
    # Watching again starts the quiet period anew.
    db().execute("UPDATE sheet_posts SET status=?,last_activity_at=CASE WHEN ?='watching' THEN ? ELSE last_activity_at END WHERE id=?",
                 (status, status, now_iso(), post_id))
    db().commit()
    return jsonify(post_payload(own_post(post_id)))


@app.post("/api/sheet-posts/<int:post_id>/check")
@login_required
def check_sheet_post(post_id):
    post = own_post(post_id)
    if not post:
        return jsonify({"error": "post not found"}), 404
    waiting = wait_seconds(db())
    if waiting:
        return jsonify({"error": f"Reddit erlaubt die nächste Abfrage in {waiting} Sekunden.", "wait": waiting}), 429
    try:
        new = check_post(db(), post, cards_of_sheet(db(), post["sheet_id"]))
    except FeedError as error:
        db().commit()
        return jsonify({"error": str(error), "post": post_payload(own_post(post_id))}), 502
    db().commit()
    return jsonify({"new": new, "post": post_payload(own_post(post_id))})


def inbox_count(uid):
    return db().execute("SELECT COUNT(*) FROM inbox_items WHERE user_id=? AND state='new'", (uid,)).fetchone()[0]


@app.get("/api/inbox")
@login_required
def inbox():
    """Newest first. `state=all` includes what has been dealt with; `game_id` narrows to one game."""
    clauses, values = ["i.user_id=?"], [user_id()]
    if request.args.get("state") != "all":
        clauses.append("i.state='new'")
    if request.args.get("game_id"):
        clauses.append("t.game_id=?")
        values.append(request.args["game_id"])
    rows = db().execute(
        f"""SELECT i.*,p.url post_url,p.title post_title,p.community,p.source,t.id sheet_id,t.name sheet_name,t.kind sheet_kind,t.game_id
            FROM inbox_items i JOIN sheet_posts p ON p.id=i.post_id JOIN trade_sheets t ON t.id=p.sheet_id
            WHERE {' AND '.join(clauses)} ORDER BY i.posted_at DESC,i.id DESC LIMIT 200""", values,
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["matches"] = jload(item["matches"], [])
        items.append(item)
    return jsonify({"items": items, "new": inbox_count(user_id())})


@app.patch("/api/inbox/<int:item_id>")
@login_required
def update_inbox_item(item_id):
    state = (request.get_json(force=True) or {}).get("state")
    if state not in ("new", "done"):
        return jsonify({"error": "Unbekannter Status."}), 400
    changed = db().execute("UPDATE inbox_items SET state=? WHERE id=? AND user_id=?", (state, item_id, user_id())).rowcount
    db().commit()
    if not changed:
        return jsonify({"error": "entry not found"}), 404
    return jsonify({"saved": True, "new": inbox_count(user_id())})


@app.post("/api/inbox/done")
@login_required
def finish_inbox():
    """Marks everything new as dealt with -- of one post when `post_id` is given."""
    post_id = (request.get_json(silent=True) or {}).get("post_id")
    if post_id:
        db().execute("UPDATE inbox_items SET state='done' WHERE user_id=? AND post_id=? AND state='new'", (user_id(), post_id))
    else:
        db().execute("UPDATE inbox_items SET state='done' WHERE user_id=? AND state='new'", (user_id(),))
    db().commit()
    return jsonify({"saved": True, "new": inbox_count(user_id())})
