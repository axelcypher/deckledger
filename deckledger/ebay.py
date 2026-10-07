"""eBay: a connected seller account and its listings, listing drafts made from cards, and other
people's eBay listings followed as a card's price.

Everything goes through eBay's official APIs, which need an application registered in eBay's
developer program (client id, client secret and the "RuName" that names the address eBay sends a
user back to). An admin enters those once (Admin -> eBay) or the container gets them as
EBAY_CLIENT_ID / EBAY_CLIENT_SECRET / EBAY_RU_NAME.

* Connecting: the user signs in at eBay and allows DeckLedger access (OAuth authorization code).
  The refresh token is kept per user in ebay_accounts; access tokens live two hours and are
  renewed on demand.
* Own listings: the Trading API's GetMyeBaySelling lists active, sold and unsold listings. The
  background job (ebay_sync.py) asks every LISTING_SYNC_MINUTES; "Jetzt abgleichen" asks at once.
* Drafts: made from cards (a sheet, the collection's selection) with the user's preset
  ("Angebotsvorlage"). They stay in DeckLedger until the user publishes one; publishing uploads
  the card image (UploadSiteHostedPictures) and lists it (AddFixedPriceItem) with the seller's
  business policies. eBay's own API cannot create drafts in Seller Hub.
* Tracked listings: any listing URL entered in a card's price tab is read through the Browse API
  with an application token (no user account needed). The active ones' median and lowest price
  are stored as the card's "ebay" provider price, change-only like every other price feed, so the
  totals, the price history and the price tab pick them up without knowing about eBay.
"""

import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import statistics
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

import requests
from flask import jsonify, redirect, request, session, url_for

import sheet_render

from .config import jload, now_iso
from .web import admin_required, app, db, login_required, user_id
from .prices import latest_price_sql
from .images import card_image


PRICE_PROVIDER = "ebay"
CONFIG_KEY = "ebay_config"
APP_TOKEN_KEY = "ebay_app_token"
DELETION_TOKEN_KEY = "ebay_deletion_token"
# eBay's notification keys by id; they do not change, so they are fetched once per process.
PUBLIC_KEYS = {}
PRESET_SETTING = "ebayPreset"
CONFIG_DEFAULTS = {"client_id": "", "client_secret": "", "ru_name": "", "environment": "production", "marketplace": "EBAY_DE"}
ENVIRONMENTS = {
    "production": {"auth": "https://auth.ebay.com/oauth2/authorize", "api": "https://api.ebay.com", "apiz": "https://apiz.ebay.com"},
    "sandbox": {"auth": "https://auth.sandbox.ebay.com/oauth2/authorize", "api": "https://api.sandbox.ebay.com", "apiz": "https://apiz.sandbox.ebay.com"},
}
MARKETPLACES = {
    "EBAY_DE": {"label": "eBay.de", "site_id": 77, "site": "Germany", "currency": "EUR", "country": "DE", "domain": "www.ebay.de", "language": "de_DE"},
    "EBAY_AT": {"label": "eBay.at", "site_id": 16, "site": "Austria", "currency": "EUR", "country": "AT", "domain": "www.ebay.at", "language": "de_AT"},
    "EBAY_FR": {"label": "eBay.fr", "site_id": 71, "site": "France", "currency": "EUR", "country": "FR", "domain": "www.ebay.fr", "language": "fr_FR"},
    "EBAY_IT": {"label": "eBay.it", "site_id": 101, "site": "Italy", "currency": "EUR", "country": "IT", "domain": "www.ebay.it", "language": "it_IT"},
    "EBAY_ES": {"label": "eBay.es", "site_id": 186, "site": "Spain", "currency": "EUR", "country": "ES", "domain": "www.ebay.es", "language": "es_ES"},
    "EBAY_GB": {"label": "eBay.co.uk", "site_id": 3, "site": "UK", "currency": "GBP", "country": "GB", "domain": "www.ebay.co.uk", "language": "en_GB"},
    "EBAY_US": {"label": "eBay.com", "site_id": 0, "site": "US", "currency": "USD", "country": "US", "domain": "www.ebay.com", "language": "en_US"},
}
USER_SCOPES = (
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account.readonly",
    "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
)
APP_SCOPE = "https://api.ebay.com/oauth/api_scope"
TRADING_COMPATIBILITY_LEVEL = "1349"
TRADING_NS = "urn:ebay:apis:eBLBaseComponents"
# Trading cards in eBay's "CCG Individual Cards" category are described by the card's condition
# (ConditionID 4000, "ungraded") plus this descriptor (name 40001).
CARD_CONDITION_DESCRIPTOR = "40001"
CARD_CONDITIONS = {
    "400010": "Near Mint oder besser", "400011": "Leicht bespielt (Excellent)",
    "400012": "Mäßig bespielt (Very Good)", "400013": "Stark bespielt (Poor)",
}
COLLECTION_CONDITIONS = {
    "Mint": "400010", "Near Mint": "400010", "Excellent": "400011", "Light Played": "400011",
    "Good": "400012", "Played": "400012", "Poor": "400013",
}
UNGRADED_CONDITION_ID = "4000"
LANGUAGE_NAMES = {
    "EN": "Englisch", "DE": "Deutsch", "JP": "Japanisch", "JA": "Japanisch", "FR": "Französisch", "IT": "Italienisch",
    "ES": "Spanisch", "PT": "Portugiesisch", "KO": "Koreanisch", "ZH": "Chinesisch", "CN": "Chinesisch",
}
# The preset: what a draft is made of. Shipping, payment and where the cards are sent from are the
# seller's and hold for every game; everything else -- above all the item specifics, which name a
# game's manufacturer and its foil -- is kept per game.
PRESET_DEFAULTS = {
    "category_id": "183454",
    "condition": "collection",
    "title_template": "{name} {set_code} {number} {finish} {language} {game}",
    "description_template": "<h2>{name}</h2>\n<p>{set_name} ({set_code}) · Nr. {number}<br>\nSeltenheit: {rarity} · Sprache: {language_name}</p>\n<p>Zustand: {condition}</p>\n<p>Versand gut geschützt in Sleeve und Toploader.</p>",
    "aspects": "Spiel: {game}\nEdition: {set_name}\nKartenname: {name}\nCharacter: {character}\nSeltenheit: {rarity}\nHersteller: {manufacturer}\n"
               "Besonderheiten: {features}\nOberflächeneffekt: {surface}\nSprache: {language_name}\nHerstellungsjahr: {year}\nKartenzustand: {condition}\nBewertet: {graded}",
    "game_label": "", "manufacturer": "", "surface_foil": "Foil", "surface_normal": "Normal",
    "price_factor": 100, "price_min": 1.0, "price_rounding": "none", "price_fallback": None,
    "quantity": "one", "best_offer": False,
    "fulfillment_policy_id": "", "payment_policy_id": "", "return_policy_id": "",
    "postal_code": "", "location": "",
}
SHARED_PRESET_KEYS = ("fulfillment_policy_id", "payment_policy_id", "return_policy_id", "postal_code", "location", "best_offer")
GAME_PRESET_DEFAULTS = {
    "lorcana": {"game_label": "Disney Lorcana", "manufacturer": "Ravensburger"},
    "one-piece": {"game_label": "One Piece Card Game", "manufacturer": "Bandai"},
    "hololive": {"game_label": "hololive OFFICIAL CARD GAME", "manufacturer": "Bushiroad"},
    "vcard": {"game_label": "VCard", "manufacturer": "Gamer Supps", "surface_foil": "Holo"},
}
HTML_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>")
PRICE_ROUNDINGS = ("none", "up99", "up49")
TITLE_LIMIT = 80
DESCRIPTION_LIMIT = 4000
DRAFT_BATCH_LIMIT = 400
TRACKED_PER_CARD = 20
# How often the background job looks at what changed.
TRACKED_REFRESH_HOURS = 6
LISTING_SYNC_MINUTES = 30
SYNC_PAGES = 10
ITEM_URL = re.compile(r"(?:/itm/(?:[^/?#]+/)?|[?&](?:item|itm|itemId)=)(\d{9,15})(?:\D|$)", re.I)
PLAIN_ITEM_ID = re.compile(r"^\s*(\d{9,15})\s*$")


class EbayError(Exception):
    """Something eBay or the setup refused; the message is shown to the user as it is."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def http(method, url, **kwargs):
    """Every request to eBay goes through here (the tests replace it)."""
    return requests.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)


def utc(text):
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=int(seconds or 0))).replace(microsecond=0).isoformat()


def is_past(text, margin_seconds=0):
    moment = utc(text)
    return moment is None or moment <= datetime.now(timezone.utc) + timedelta(seconds=margin_seconds)


# ---- Configuration ------------------------------------------------------------------------------

def ebay_config(connection):
    """The application's keys: from the environment when EBAY_CLIENT_ID is set (then read-only in
    the admin UI), else from app_settings."""
    if os.environ.get("EBAY_CLIENT_ID"):
        config = {
            "client_id": os.environ.get("EBAY_CLIENT_ID", ""), "client_secret": os.environ.get("EBAY_CLIENT_SECRET", ""),
            "ru_name": os.environ.get("EBAY_RU_NAME", ""), "environment": os.environ.get("EBAY_ENVIRONMENT", "production"),
            "marketplace": os.environ.get("EBAY_MARKETPLACE", "EBAY_DE"), "source": "environment",
        }
    else:
        row = connection.execute("SELECT value FROM app_settings WHERE key=?", (CONFIG_KEY,)).fetchone()
        config = {**CONFIG_DEFAULTS, **(jload(row[0], {}) if row else {}), "source": "database"}
    if config["environment"] not in ENVIRONMENTS:
        config["environment"] = "production"
    if config["marketplace"] not in MARKETPLACES:
        config["marketplace"] = "EBAY_DE"
    return config


def is_configured(config):
    return bool(config["client_id"] and config["client_secret"])


def hosts(config):
    return ENVIRONMENTS[config["environment"]]


def marketplace(config):
    return MARKETPLACES[config["marketplace"]]


def require_config(connection, for_users=False):
    config = ebay_config(connection)
    if not is_configured(config) or (for_users and not config["ru_name"]):
        raise EbayError("Die eBay-Anbindung ist auf diesem Server nicht eingerichtet. Ein Admin trägt die Schlüssel der eBay-Anwendung unter Admin → eBay ein.", 409)
    return config


# ---- Tokens -------------------------------------------------------------------------------------

def token_request(config, form):
    credentials = base64.b64encode(f'{config["client_id"]}:{config["client_secret"]}'.encode()).decode()
    try:
        response = http("POST", f'{hosts(config)["api"]}/identity/v1/oauth2/token', data=form,
                        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {credentials}"})
    except requests.RequestException as error:
        raise EbayError(f"eBay ist nicht erreichbar ({error.__class__.__name__}).", 502)
    payload = response_json(response)
    if response.status_code != 200 or "access_token" not in payload:
        raise EbayError(f'eBay hat die Anmeldung abgelehnt: {payload.get("error_description") or payload.get("error") or response.status_code}', 502)
    return payload


def app_token(connection):
    """An application token (client credentials) for reading public listings; cached until shortly
    before it runs out."""
    config = require_config(connection)
    row = connection.execute("SELECT value FROM app_settings WHERE key=?", (APP_TOKEN_KEY,)).fetchone()
    cached = jload(row[0], {}) if row else {}
    if cached.get("token") and cached.get("environment") == config["environment"] and cached.get("client_id") == config["client_id"] and not is_past(cached.get("expires_at"), 120):
        return cached["token"]
    payload = token_request(config, {"grant_type": "client_credentials", "scope": APP_SCOPE})
    connection.execute(
        "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (APP_TOKEN_KEY, json.dumps({"token": payload["access_token"], "expires_at": later(payload.get("expires_in", 7200)),
                                    "environment": config["environment"], "client_id": config["client_id"]})),
    )
    connection.commit()
    return payload["access_token"]


def account(connection, uid):
    return connection.execute("SELECT * FROM ebay_accounts WHERE user_id=?", (uid,)).fetchone()


def user_token(connection, uid):
    """The user's access token, renewed with the refresh token when it has run out."""
    config = require_config(connection, for_users=True)
    row = account(connection, uid)
    if not row or not row["refresh_token"]:
        raise EbayError("Es ist kein eBay-Konto verbunden. Verbinde es unter Einstellungen → eBay.", 409)
    if row["access_token"] and not is_past(row["access_expires_at"], 120):
        return row["access_token"]
    if is_past(row["refresh_expires_at"]):
        raise EbayError("Die Verbindung zu eBay ist abgelaufen. Bitte unter Einstellungen → eBay neu verbinden.", 409)
    try:
        payload = token_request(config, {"grant_type": "refresh_token", "refresh_token": row["refresh_token"], "scope": " ".join(USER_SCOPES)})
    except EbayError as error:
        connection.execute("UPDATE ebay_accounts SET last_error=? WHERE user_id=?", (str(error), uid))
        connection.commit()
        raise
    connection.execute(
        "UPDATE ebay_accounts SET access_token=?,access_expires_at=?,last_error='' WHERE user_id=?",
        (payload["access_token"], later(payload.get("expires_in", 7200)), uid),
    )
    connection.commit()
    return payload["access_token"]


def response_json(response):
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def rest_error(payload, fallback):
    errors = payload.get("errors") or []
    if errors and isinstance(errors, list):
        return "; ".join(str(error.get("longMessage") or error.get("message") or error.get("errorId")) for error in errors[:3])
    return payload.get("error_description") or fallback


def rest_get(connection, url, token, config, params=None):
    try:
        response = http("GET", url, params=params, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "X-EBAY-C-MARKETPLACE-ID": config["marketplace"], "Accept-Language": marketplace(config)["language"].replace("_", "-"),
        })
    except requests.RequestException as error:
        raise EbayError(f"eBay ist nicht erreichbar ({error.__class__.__name__}).", 502)
    return response, response_json(response)


# ---- Trading API (XML) --------------------------------------------------------------------------

def strip_namespaces(root):
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def text(element, path, default=""):
    found = element.find(path) if element is not None else None
    return found.text.strip() if found is not None and found.text else default


def number(element, path):
    try:
        return float(text(element, path))
    except ValueError:
        return None


def trading_call(connection, uid, call, body, image=None):
    """One Trading API call with the user's OAuth token. Returns the answer's root element (without
    namespaces) and its warnings; eBay's errors become an EbayError."""
    config = require_config(connection, for_users=True)
    token = user_token(connection, uid)
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": TRADING_COMPATIBILITY_LEVEL, "X-EBAY-API-CALL-NAME": call,
        "X-EBAY-API-SITEID": str(marketplace(config)["site_id"]), "X-EBAY-API-IAF-TOKEN": token,
    }
    document = (f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="{TRADING_NS}">'
                f'<ErrorLanguage>{marketplace(config)["language"]}</ErrorLanguage><WarningLevel>High</WarningLevel>{body}</{call}Request>')
    url = f'{hosts(config)["api"]}/ws/api.dll'
    try:
        if image:
            response = http("POST", url, headers=headers, files={"XML Payload": (None, document.encode("utf-8"), "text/xml;charset=utf-8"), "image": image}, timeout=90)
        else:
            response = http("POST", url, headers={**headers, "Content-Type": "text/xml;charset=utf-8"}, data=document.encode("utf-8"))
    except requests.RequestException as error:
        raise EbayError(f"eBay ist nicht erreichbar ({error.__class__.__name__}).", 502)
    try:
        root = strip_namespaces(ET.fromstring(response.content))
    except ET.ParseError:
        raise EbayError(f"eBay hat unverständlich geantwortet (HTTP {response.status_code}).", 502)
    errors, warnings = [], []
    for error in root.findall("Errors"):
        message = text(error, "LongMessage") or text(error, "ShortMessage") or text(error, "ErrorCode")
        (warnings if text(error, "SeverityCode") == "Warning" else errors).append(message)
    if text(root, "Ack") == "Failure" or errors:
        raise EbayError("eBay: " + ("; ".join(dict.fromkeys(errors)) or "Anfrage abgelehnt."), 502)
    return root, warnings


# ---- Connecting an account ----------------------------------------------------------------------

def status_payload(connection, uid):
    config = ebay_config(connection)
    row = account(connection, uid)
    connected = bool(row and row["refresh_token"] and not is_past(row["refresh_expires_at"]))
    return {
        "configured": is_configured(config) and bool(config["ru_name"]), "can_track": is_configured(config),
        "marketplace": config["marketplace"], "marketplace_label": marketplace(config)["label"], "currency": marketplace(config)["currency"],
        "environment": config["environment"], "connected": connected, "username": row["username"] if row else "",
        "expired": bool(row and row["refresh_token"] and not connected),
        "listings_synced_at": row["listings_synced_at"] if row else None, "last_error": row["last_error"] if row else "",
        "refresh_expires_at": row["refresh_expires_at"] if row else None, "conditions": CARD_CONDITIONS,
    }


@app.get("/api/ebay/status")
@login_required
def ebay_status():
    return jsonify(status_payload(db(), user_id()))


@app.get("/ebay/connect")
@login_required
def ebay_connect():
    try:
        config = require_config(db(), for_users=True)
    except EbayError:
        return redirect("/?ebay=not_configured")
    state = secrets.token_urlsafe(24)
    session["ebay_oauth_state"] = state
    query = urlencode({"client_id": config["client_id"], "response_type": "code", "redirect_uri": config["ru_name"],
                       "scope": " ".join(USER_SCOPES), "state": state, "prompt": "login"})
    return redirect(f'{hosts(config)["auth"]}?{query}')


@app.get("/ebay/callback")
@login_required
def ebay_callback():
    """Where eBay sends the user back to (the RuName's "accept" and "decline" URL)."""
    expected = session.pop("ebay_oauth_state", None)
    if request.args.get("error") or not request.args.get("code"):
        return redirect("/?ebay=denied")
    if not expected or not secrets.compare_digest(expected, request.args.get("state", "")):
        return redirect("/?ebay=state")
    try:
        config = require_config(db(), for_users=True)
        payload = token_request(config, {"grant_type": "authorization_code", "code": request.args["code"], "redirect_uri": config["ru_name"]})
        username = ""
        try:
            response, identity = rest_get(db(), f'{hosts(config)["apiz"]}/commerce/identity/v1/user/', payload["access_token"], config)
            username = str(identity.get("username") or "") if response.status_code == 200 else ""
        except EbayError:
            pass
    except EbayError as error:
        app.logger.warning("eBay connection failed: %s", error)
        return redirect("/?ebay=failed")
    stamp = now_iso()
    db().execute(
        """INSERT INTO ebay_accounts(user_id,username,access_token,access_expires_at,refresh_token,refresh_expires_at,environment,connected_at,last_error)
           VALUES(?,?,?,?,?,?,?,?,'') ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,access_token=excluded.access_token,
           access_expires_at=excluded.access_expires_at,refresh_token=excluded.refresh_token,refresh_expires_at=excluded.refresh_expires_at,
           environment=excluded.environment,connected_at=excluded.connected_at,last_error=''""",
        (user_id(), username, payload["access_token"], later(payload.get("expires_in", 7200)), payload.get("refresh_token", ""),
         later(payload.get("refresh_token_expires_in", 47304000)), config["environment"], stamp),
    )
    db().commit()
    return redirect("/?ebay=connected")


@app.post("/api/ebay/disconnect")
@login_required
def ebay_disconnect():
    """Forgets the tokens. The listings and sales seen so far stay, as history."""
    db().execute("DELETE FROM ebay_accounts WHERE user_id=?", (user_id(),))
    db().commit()
    return jsonify({"disconnected": True})


@app.errorhandler(EbayError)
def ebay_error(error):
    return jsonify({"error": str(error)}), error.status


# ---- Admin: the application's keys --------------------------------------------------------------

@app.get("/api/admin/ebay")
@admin_required
def admin_get_ebay():
    config = ebay_config(db())
    return jsonify({
        **{key: value for key, value in config.items() if key != "client_secret"}, "client_secret_set": bool(config["client_secret"]),
        "callback_url": url_for("ebay_callback", _external=True), "deletion_endpoint": deletion_endpoint(),
        "deletion_token": deletion_token(db()), "marketplaces": {key: value["label"] for key, value in MARKETPLACES.items()},
    })


@app.post("/api/admin/ebay")
@admin_required
def admin_save_ebay():
    if ebay_config(db())["source"] == "environment":
        return jsonify({"error": "Die eBay-Anwendung wird über Umgebungsvariablen (EBAY_CLIENT_ID …) eingerichtet und kann hier nicht bearbeitet werden."}), 400
    payload = request.get_json(force=True) or {}
    row = db().execute("SELECT value FROM app_settings WHERE key=?", (CONFIG_KEY,)).fetchone()
    config = {**CONFIG_DEFAULTS, **(jload(row[0], {}) if row else {})}
    for key in ("client_id", "ru_name"):
        if key in payload:
            config[key] = str(payload[key] or "").strip()
    if payload.get("environment") in ENVIRONMENTS:
        config["environment"] = payload["environment"]
    if payload.get("marketplace") in MARKETPLACES:
        config["marketplace"] = payload["marketplace"]
    # Blank means "keep the stored secret" -- the form never receives it (as with the SSO secret).
    if payload.get("client_secret"):
        config["client_secret"] = str(payload["client_secret"]).strip()
    db().execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CONFIG_KEY, json.dumps(config)))
    db().execute("DELETE FROM app_settings WHERE key=?", (APP_TOKEN_KEY,))
    db().commit()
    return jsonify({"saved": True})


# ---- Marketplace account deletion ---------------------------------------------------------------
# eBay only hands out production keys to applications that listen for "this eBay account was
# deleted": the developer portal registers an endpoint URL and a verification token, checks the
# endpoint once with a challenge, and from then on posts every deleted account on all of eBay --
# thousands a day. Only those naming an account DeckLedger knows (a connected seller, a buyer in
# the sales) are verified against eBay's signature and acted on; everything else is just
# acknowledged.

def deletion_token(connection):
    """The verification token entered in the developer portal: EBAY_VERIFICATION_TOKEN, else one
    made up once (64 characters of the alphabet eBay allows)."""
    if os.environ.get("EBAY_VERIFICATION_TOKEN"):
        return os.environ["EBAY_VERIFICATION_TOKEN"]
    row = connection.execute("SELECT value FROM app_settings WHERE key=?", (DELETION_TOKEN_KEY,)).fetchone()
    if row:
        return row[0]
    token = secrets.token_urlsafe(48)
    connection.execute("INSERT INTO app_settings(key,value) VALUES(?,?)", (DELETION_TOKEN_KEY, token))
    connection.commit()
    return token


def deletion_endpoint():
    """The URL as registered at eBay -- it is part of the challenge's hash. Behind a proxy that
    rewrites the host, EBAY_DELETION_ENDPOINT names it."""
    return os.environ.get("EBAY_DELETION_ENDPOINT") or url_for("ebay_account_deletion", _external=True)


def public_key(connection, kid):
    if kid not in PUBLIC_KEYS:
        config = require_config(connection)
        response, payload = rest_get(connection, f'{hosts(config)["api"]}/commerce/notification/v1/public_key/{kid}', app_token(connection), config)
        if response.status_code != 200 or not payload.get("key"):
            raise EbayError(rest_error(payload, "eBay hat den Schlüssel der Benachrichtigung nicht geliefert."), 502)
        body = re.sub(r"-----(BEGIN|END) PUBLIC KEY-----|\s", "", payload["key"])
        PUBLIC_KEYS[kid] = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + "\n-----END PUBLIC KEY-----\n"
    return PUBLIC_KEYS[kid]


def signed_by_ebay(connection, body, header):
    """X-EBAY-SIGNATURE is base64 JSON naming the key (kid) and an ECDSA/SHA1 signature of the body."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        signature = json.loads(base64.b64decode(header or ""))
        key = serialization.load_pem_public_key(public_key(connection, str(signature["kid"])).encode())
        key.verify(base64.b64decode(signature["signature"]), body, ec.ECDSA(hashes.SHA1()))
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError, EbayError) as error:
        app.logger.warning("eBay account deletion notice not verified: %s", error or type(error).__name__)
        return False


@app.route("/ebay/account-deletion", methods=["GET", "POST"])
def ebay_account_deletion():
    """Open to eBay without a login. GET is the portal's challenge, POST a deleted account."""
    connection = db()
    if request.method == "GET":
        challenge = request.args.get("challenge_code", "")
        if not challenge:
            return jsonify({"error": "challenge_code fehlt"}), 400
        digest = hashlib.sha256((challenge + deletion_token(connection) + deletion_endpoint()).encode()).hexdigest()
        return jsonify({"challengeResponse": digest})
    data = ((request.get_json(silent=True) or {}).get("notification") or {}).get("data") or {}
    username = str(data.get("username") or "")
    known = username and (
        connection.execute("SELECT 1 FROM ebay_accounts WHERE username=?", (username,)).fetchone()
        or connection.execute("SELECT 1 FROM ebay_sales WHERE buyer=? LIMIT 1", (username,)).fetchone())
    if not known:
        return "", 204
    if not signed_by_ebay(connection, request.get_data(), request.headers.get("X-EBAY-SIGNATURE")):
        return "", 412
    connection.execute("DELETE FROM ebay_accounts WHERE username=?", (username,))
    connection.execute("UPDATE ebay_sales SET buyer='' WHERE buyer=?", (username,))
    connection.commit()
    app.logger.info("eBay account deletion: removed what DeckLedger stored about an eBay account")
    return "", 204


# ---- The listing preset -------------------------------------------------------------------------

def preset_defaults(game_id, game_name=""):
    return {**PRESET_DEFAULTS, "game_label": game_name, **GAME_PRESET_DEFAULTS.get(game_id, {})}


def clean_preset(raw, defaults):
    raw = raw if isinstance(raw, dict) else {}
    preset = dict(defaults)
    for key in ("title_template", "description_template", "aspects", "fulfillment_policy_id", "payment_policy_id",
                "return_policy_id", "postal_code", "location", "game_label", "manufacturer", "surface_foil", "surface_normal"):
        if key in raw:
            preset[key] = str(raw[key] or "").strip()[:DESCRIPTION_LIMIT]
    if re.fullmatch(r"\d{1,10}", str(raw.get("category_id", "")).strip()):
        preset["category_id"] = str(raw["category_id"]).strip()
    if raw.get("condition") in ("collection", *CARD_CONDITIONS):
        preset["condition"] = raw["condition"]
    if raw.get("price_rounding") in PRICE_ROUNDINGS:
        preset["price_rounding"] = raw["price_rounding"]
    if raw.get("quantity") in ("one", "owned"):
        preset["quantity"] = raw["quantity"]
    preset["best_offer"] = bool(raw.get("best_offer", preset["best_offer"]))
    for key, low, high in (("price_factor", 1, 1000), ("price_min", 0, 100000)):
        try:
            preset[key] = min(high, max(low, round(float(str(raw.get(key, preset[key])).replace(",", ".")), 2)))
        except ValueError:
            pass
    try:
        fallback = raw.get("price_fallback", preset["price_fallback"])
        preset["price_fallback"] = round(float(str(fallback).replace(",", ".")), 2) if fallback not in (None, "") else None
    except ValueError:
        preset["price_fallback"] = None
    for key in ("title_template", "game_label"):
        if not preset[key]:
            preset[key] = defaults[key]
    return preset


def stored_presets(connection, uid):
    """{"shared": {...}, "games": {game_id: {...}}}. The first version kept one flat preset for all
    games; it stands for the shared part and for every game until a game gets its own."""
    row = connection.execute("SELECT value FROM user_settings WHERE user_id=? AND key=?", (uid, PRESET_SETTING)).fetchone()
    stored = jload(row[0], {}) if row else {}
    stored = stored if isinstance(stored, dict) else {}
    if stored and "games" not in stored and "shared" not in stored:
        return {"shared": stored, "games": {}, "legacy": stored}
    return {"shared": stored.get("shared") or {}, "games": stored.get("games") or {}}


def game_name(connection, game_id):
    row = connection.execute("SELECT name FROM games WHERE id=?", (game_id,)).fetchone()
    return row[0] if row else None


def load_preset(connection, uid, game_id):
    stored = stored_presets(connection, uid)
    own = stored["games"].get(game_id) or stored.get("legacy") or {}
    shared = {key: stored["shared"][key] for key in SHARED_PRESET_KEYS if key in stored["shared"]}
    return clean_preset({**own, **shared}, preset_defaults(game_id, game_name(connection, game_id) or ""))


@app.route("/api/ebay/preset", methods=["GET", "PUT"])
@login_required
def ebay_preset():
    payload = request.get_json(force=True) if request.method == "PUT" else request.args
    game_id = (payload or {}).get("game_id")
    name = game_name(db(), game_id)
    if name is None:
        return jsonify({"error": "Spiel nicht gefunden."}), 404
    defaults = preset_defaults(game_id, name)
    if request.method == "PUT":
        preset = clean_preset(payload, defaults)
        stored = stored_presets(db(), user_id())
        stored.pop("legacy", None)
        stored["shared"] = {key: preset[key] for key in SHARED_PRESET_KEYS}
        stored["games"][game_id] = {key: value for key, value in preset.items() if key not in SHARED_PRESET_KEYS}
        db().execute("INSERT INTO user_settings(user_id,key,value) VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                     (user_id(), PRESET_SETTING, json.dumps(stored)))
        db().commit()
    return jsonify({**load_preset(db(), user_id(), game_id), "game_id": game_id, "defaults": defaults, "shared_keys": SHARED_PRESET_KEYS})


@app.get("/api/ebay/policies")
@login_required
def ebay_policies():
    """The seller's business policies (shipping, payment, returns) to pick in the preset."""
    config = require_config(db(), for_users=True)
    token = user_token(db(), user_id())
    result = {}
    for kind, path, key, id_key in (("fulfillment", "fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
                                    ("payment", "payment_policy", "paymentPolicies", "paymentPolicyId"),
                                    ("return", "return_policy", "returnPolicies", "returnPolicyId")):
        response, payload = rest_get(db(), f'{hosts(config)["api"]}/sell/account/v1/{path}', token, config, {"marketplace_id": config["marketplace"]})
        if response.status_code != 200:
            raise EbayError(f"Die Richtlinien konnten nicht geladen werden: {rest_error(payload, response.status_code)}", 502)
        result[kind] = [{"id": str(policy.get(id_key)), "name": policy.get("name") or str(policy.get(id_key))} for policy in payload.get(key) or []]
    return jsonify(result)


# ---- Drafts -------------------------------------------------------------------------------------

CARD_SQL = f"""SELECT v.id variant_id,v.game_id,v.finish,v.variant_code,v.is_parallel,v.attributes variant_attributes,
      i.id identity_id,i.canonical_name,p.id printing_id,p.collector_number,p.language,p.rarity,
      s.code set_code,s.name set_name,s.accent set_accent,s.release_date,g.name game_name,g.short_name game_short_name,g.accent,
      {latest_price_sql('v')} price
    FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
      JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id"""


def fill(template, values):
    """{placeholder} -> value; unknown placeholders vanish, everything else stays as written."""
    return re.sub(r"\{(\w+)\}", lambda match: str(values.get(match.group(1), "")), template)


def round_price(amount, rounding):
    if rounding == "up99":
        return int(amount) + 0.99
    if rounding == "up49":
        return int(amount) + (0.49 if amount - int(amount) <= 0.49 else 0.99)
    return round(amount, 2)


def label_price(label):
    """A sheet's price label ("4,50 €", "4.50", "VB 12") read as an amount, if it is one."""
    match = re.search(r"(\d+(?:[.,]\d{1,2})?)", str(label or ""))
    return round(float(match.group(1).replace(",", ".")), 2) if match else None


def draft_price(card, preset, label=None):
    own = label_price(label)
    if own:
        return own
    if card["price"] is not None:
        return max(preset["price_min"], round_price(card["price"] * preset["price_factor"] / 100, preset["price_rounding"]))
    return preset["price_fallback"]


def card_condition(connection, uid, variant_id, preset):
    if preset["condition"] != "collection":
        return preset["condition"]
    row = connection.execute(
        "SELECT condition FROM collection_entries WHERE user_id=? AND variant_id=? AND is_graded=0 AND quantity>0 ORDER BY quantity DESC LIMIT 1",
        (uid, variant_id),
    ).fetchone()
    return COLLECTION_CONDITIONS.get(row["condition"] if row else "Near Mint", "400010")


def character_name(name):
    """The character a card shows: "Elsa - Snow Queen" -> "Elsa", "Ember (PL8)" -> "Ember"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(name).split(" - ")[0]).strip()


def card_values(card, condition, quantity, preset):
    finish = "" if card["finish"] in ("Normal", "standard", "normal") else card["finish"]
    edition = str(jload(card["variant_attributes"], {}).get("editionLabel") or "")
    foil = sheet_render.is_holo(card["finish"], card["variant_code"], card["rarity"], card["game_id"], card["is_parallel"])
    # What sets a print apart beyond its finish: so far the first print run (VCard's 1st Edition).
    features = "1st Edition" if "1st edition" in f'{card["finish"]} {edition}'.lower() else ""
    return {
        "name": card["canonical_name"], "set_name": card["set_name"], "set_code": card["set_code"], "number": card["collector_number"],
        "rarity": card["rarity"], "finish": finish, "language": card["language"], "language_name": LANGUAGE_NAMES.get(card["language"], card["language"]),
        "game": preset["game_label"] or card["game_name"], "game_short": card["game_short_name"], "condition": CARD_CONDITIONS.get(condition, ""),
        "quantity": quantity, "character": character_name(card["canonical_name"]), "manufacturer": preset["manufacturer"],
        "edition": edition, "features": features, "surface": preset["surface_foil"] if foil else preset["surface_normal"],
        "year": str(card["release_date"] or "")[:4], "graded": "Nein",
    }


def is_html(text):
    return bool(HTML_TAG.search(str(text or "")))


def description_html(text, title=""):
    """What goes to eBay: a description written in HTML as it is, plain text with its line breaks."""
    if is_html(text):
        return text
    return "<br>".join(xml_escape(line) for line in str(text or "").splitlines()) or xml_escape(title)


def build_draft(connection, uid, card, preset, quantity, label=None, sheet_id=None, currency="EUR"):
    condition = card_condition(connection, uid, card["variant_id"], preset)
    values = card_values(card, condition, quantity, preset)
    title = re.sub(r"\s+", " ", fill(preset["title_template"], values)).strip()[:TITLE_LIMIT].strip()
    # In an HTML description the card's own text must not turn into markup.
    template = preset["description_template"]
    escaped = {key: html.escape(str(value), quote=False) for key, value in values.items()} if is_html(template) else values
    description = fill(template, escaped).strip()[:DESCRIPTION_LIMIT]
    aspects = []
    for line in preset["aspects"].splitlines():
        name, separator, value = line.partition(":")
        value = re.sub(r"\s+", " ", fill(value, values)).strip()[:65]
        if separator and name.strip() and value:
            aspects.append([name.strip()[:65], value])
    stamp = now_iso()
    return {
        "user_id": uid, "game_id": card["game_id"], "variant_id": card["variant_id"], "sheet_id": sheet_id, "title": title,
        "description": description, "price": draft_price(card, preset, label), "currency": currency, "quantity": max(1, min(999, quantity)),
        "condition": condition, "aspects": json.dumps(aspects, ensure_ascii=False), "created_at": stamp, "updated_at": stamp,
    }


def draft_rows(connection, uid, where="", args=()):
    rows = connection.execute(
        f"""SELECT d.*,i.canonical_name,p.collector_number,p.language,p.rarity,s.code set_code,s.name set_name,v.finish,v.game_id variant_game,
              {latest_price_sql('v')} market_price,
              COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=d.user_id AND c.variant_id=d.variant_id),0) owned
            FROM ebay_drafts d LEFT JOIN variants v ON v.id=d.variant_id LEFT JOIN printings p ON p.id=v.printing_id
              LEFT JOIN card_identities i ON i.id=p.identity_id LEFT JOIN sets s ON s.id=p.set_id
            WHERE d.user_id=? {where} ORDER BY d.status='published',d.updated_at DESC,d.id DESC""", (uid, *args),
    ).fetchall()
    domain = marketplace(ebay_config(connection))["domain"]
    result = []
    for row in rows:
        item = dict(row)
        item["aspects"] = jload(item["aspects"], [])
        item["item_url"] = f'https://{domain}/itm/{item["item_id"]}' if item["item_id"] else None
        result.append(item)
    return result


@app.route("/api/ebay/drafts", methods=["GET", "POST"])
@login_required
def ebay_drafts():
    uid = user_id()
    if request.method == "GET":
        game_id = request.args.get("game_id")
        return jsonify({"drafts": draft_rows(db(), uid, "AND d.game_id=?" if game_id else "", (game_id,) if game_id else ())})
    payload = request.get_json(force=True) or {}
    presets = {}
    currency = marketplace(ebay_config(db()))["currency"]
    # What to make drafts of: every card of a sheet (with its count and price label), or a list of
    # cards (from the collection, with the preset's count).
    wanted = []
    if payload.get("sheet_id"):
        sheet = db().execute("SELECT * FROM trade_sheets WHERE id=? AND user_id=?", (payload["sheet_id"], uid)).fetchone()
        if not sheet:
            return jsonify({"error": "Sheet nicht gefunden."}), 404
        only = set(payload.get("variant_ids") or [])
        for entry in db().execute("SELECT variant_id,quantity,label FROM trade_sheet_cards WHERE sheet_id=?", (sheet["id"],)):
            if not only or entry["variant_id"] in only:
                wanted.append((entry["variant_id"], entry["quantity"], entry["label"], sheet["id"]))
    else:
        for variant_id in [value for value in (payload.get("variant_ids") or []) if isinstance(value, str)]:
            wanted.append((variant_id, None, None, None))
    if not wanted:
        return jsonify({"error": "Keine Karten ausgewählt."}), 400
    if len(wanted) > DRAFT_BATCH_LIMIT:
        return jsonify({"error": f"Höchstens {DRAFT_BATCH_LIMIT} Entwürfe auf einmal."}), 400
    db().execute("BEGIN IMMEDIATE")
    created, skipped = [], 0
    for variant_id, quantity, label, sheet_id in wanted:
        card = db().execute(f"{CARD_SQL} WHERE v.id=?", (variant_id,)).fetchone()
        # A card that already has an open draft gets no second one.
        if not card or db().execute("SELECT 1 FROM ebay_drafts WHERE user_id=? AND variant_id=? AND status!='published'", (uid, variant_id)).fetchone():
            skipped += 1
            continue
        owned = 0
        if quantity is None:
            owned = db().execute("SELECT COALESCE(SUM(quantity),0) FROM collection_entries WHERE user_id=? AND variant_id=?", (uid, variant_id)).fetchone()[0]
        preset = presets.get(card["game_id"]) or presets.setdefault(card["game_id"], load_preset(db(), uid, card["game_id"]))
        if quantity is None:
            quantity = owned if preset["quantity"] == "owned" and owned else 1
        draft = build_draft(db(), uid, card, preset, quantity, label, sheet_id, currency)
        cursor = db().execute(f"INSERT INTO ebay_drafts({','.join(draft)}) VALUES({','.join('?' * len(draft))})", tuple(draft.values()))
        created.append(cursor.lastrowid)
    db().commit()
    return jsonify({"created": len(created), "skipped": skipped, "ids": created}), 201


def own_draft(draft_id):
    return db().execute("SELECT * FROM ebay_drafts WHERE id=? AND user_id=?", (draft_id, user_id())).fetchone()


@app.route("/api/ebay/drafts/<int:draft_id>", methods=["PATCH", "DELETE"])
@login_required
def ebay_draft(draft_id):
    draft = own_draft(draft_id)
    if not draft:
        return jsonify({"error": "Entwurf nicht gefunden."}), 404
    if request.method == "DELETE":
        db().execute("DELETE FROM ebay_drafts WHERE id=?", (draft_id,))
        db().commit()
        return jsonify({"deleted": True})
    if draft["status"] == "published":
        return jsonify({"error": "Ein eingestelltes Angebot wird bei eBay geändert, nicht hier."}), 400
    payload = request.get_json(force=True) or {}
    changes = {}
    if "title" in payload:
        changes["title"] = re.sub(r"\s+", " ", str(payload["title"] or "")).strip()[:TITLE_LIMIT]
    if "description" in payload:
        changes["description"] = str(payload["description"] or "").strip()[:DESCRIPTION_LIMIT]
    if "price" in payload:
        try:
            amount = round(float(str(payload["price"]).replace(",", ".")), 2) if payload["price"] not in (None, "") else None
        except ValueError:
            return jsonify({"error": "Der Preis muss eine Zahl sein."}), 400
        if amount is not None and not 0 < amount <= 1_000_000:
            return jsonify({"error": "Der Preis muss größer als 0 sein."}), 400
        changes["price"] = amount
    if "quantity" in payload:
        try:
            changes["quantity"] = max(1, min(999, int(payload["quantity"])))
        except (TypeError, ValueError):
            return jsonify({"error": "Die Menge muss eine Zahl sein."}), 400
    if payload.get("condition") in CARD_CONDITIONS:
        changes["condition"] = payload["condition"]
    if changes:
        changes.update(updated_at=now_iso(), error="", status="draft")
        db().execute(f"UPDATE ebay_drafts SET {','.join(f'{key}=?' for key in changes)} WHERE id=?", (*changes.values(), draft_id))
        db().commit()
    return jsonify(draft_rows(db(), user_id(), "AND d.id=?", (draft_id,))[0])


def item_xml(connection, uid, draft, preset, picture_url=None):
    """The <Item> of AddFixedPriceItem / VerifyAddFixedPriceItem for a draft."""
    config = ebay_config(connection)
    site = marketplace(config)
    problems = []
    if not draft["title"]:
        problems.append("Titel fehlt")
    if not draft["price"]:
        problems.append("Preis fehlt")
    missing = [label for key, label in (("fulfillment_policy_id", "Versand"), ("payment_policy_id", "Zahlung"), ("return_policy_id", "Rücknahme")) if not preset[key]]
    if missing:
        problems.append(f"Richtlinie für {', '.join(missing)} in der Angebotsvorlage wählen")
    if not preset["postal_code"]:
        problems.append("Postleitzahl in der Angebotsvorlage eintragen")
    if problems:
        raise EbayError("Noch nicht bereit: " + "; ".join(problems) + ".")
    description = description_html(draft["description"], draft["title"])
    aspects = "".join(f"<NameValueList><Name>{xml_escape(name)}</Name><Value>{xml_escape(value)}</Value></NameValueList>"
                      for name, value in jload(draft["aspects"], []))
    sku = f'DL-{draft["variant_id"]}'
    return f"""<Item>
<Title>{xml_escape(draft["title"])}</Title>
<Description><![CDATA[{description.replace("]]>", "]]&gt;")}]]></Description>
<PrimaryCategory><CategoryID>{xml_escape(preset["category_id"])}</CategoryID></PrimaryCategory>
<CategoryMappingAllowed>true</CategoryMappingAllowed>
<StartPrice currencyID="{site["currency"]}">{draft["price"]:.2f}</StartPrice>
<ConditionID>{UNGRADED_CONDITION_ID}</ConditionID>
<ConditionDescriptors><ConditionDescriptor><Name>{CARD_CONDITION_DESCRIPTOR}</Name><Value>{xml_escape(draft["condition"])}</Value></ConditionDescriptor></ConditionDescriptors>
<Country>{site["country"]}</Country><Currency>{site["currency"]}</Currency><Site>{site["site"]}</Site>
<PostalCode>{xml_escape(preset["postal_code"])}</PostalCode>{f'<Location>{xml_escape(preset["location"])}</Location>' if preset["location"] else ''}
<ListingDuration>GTC</ListingDuration><ListingType>FixedPriceItem</ListingType>
<Quantity>{int(draft["quantity"])}</Quantity>
{f'<SKU>{xml_escape(sku)}</SKU>' if len(sku) <= 50 else ''}
{f'<PictureDetails><PictureURL>{xml_escape(picture_url)}</PictureURL></PictureDetails>' if picture_url else ''}
{f'<ItemSpecifics>{aspects}</ItemSpecifics>' if aspects else ''}
{'<BestOfferDetails><BestOfferEnabled>true</BestOfferEnabled></BestOfferDetails>' if preset["best_offer"] else ''}
<SellerProfiles>
<SellerShippingProfile><ShippingProfileID>{xml_escape(preset["fulfillment_policy_id"])}</ShippingProfileID></SellerShippingProfile>
<SellerReturnProfile><ReturnProfileID>{xml_escape(preset["return_policy_id"])}</ReturnProfileID></SellerReturnProfile>
<SellerPaymentProfile><PaymentProfileID>{xml_escape(preset["payment_policy_id"])}</PaymentProfileID></SellerPaymentProfile>
</SellerProfiles>
</Item>"""


def listing_fees(root):
    total = 0.0
    for fee in root.findall("Fees/Fee"):
        if text(fee, "Name") == "ListingFee":
            return number(fee, "Fee") or 0.0
        total += number(fee, "Fee") or 0.0
    return round(total, 2)


def upload_card_picture(connection, uid, variant_id):
    """The card's image on eBay's picture servers: listings need a public HTTPS image, and this
    server is usually not one."""
    card = connection.execute(f"{CARD_SQL} WHERE v.id=?", (variant_id,)).fetchone()
    cached = card_image(dict(card), variant_id) if card else None
    if not cached:
        raise EbayError("Für diese Karte gibt es kein Kartenbild, das hochgeladen werden könnte.")
    path, content_type, _ = cached
    payload = path.read_bytes()
    if content_type not in ("image/jpeg", "image/png"):
        from PIL import Image
        with Image.open(io.BytesIO(payload)) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=92)
        payload, content_type = buffer.getvalue(), "image/jpeg"
    root, _ = trading_call(connection, uid, "UploadSiteHostedPictures",
                           f"<PictureName>{xml_escape(variant_id[:80])}</PictureName><PictureSet>Supersize</PictureSet>",
                           image=("card.jpg" if content_type == "image/jpeg" else "card.png", payload, content_type))
    url = text(root, "SiteHostedPictureDetails/FullURL")
    if not url:
        raise EbayError("eBay hat das Kartenbild nicht angenommen.", 502)
    return url


@app.post("/api/ebay/drafts/<int:draft_id>/verify")
@login_required
def verify_ebay_draft(draft_id):
    """eBay checks the listing without putting it online and names its fees."""
    draft = own_draft(draft_id)
    if not draft:
        return jsonify({"error": "Entwurf nicht gefunden."}), 404
    preset = load_preset(db(), user_id(), draft["game_id"])
    try:
        root, warnings = trading_call(db(), user_id(), "VerifyAddFixedPriceItem", item_xml(db(), user_id(), draft, preset))
    except EbayError as error:
        db().execute("UPDATE ebay_drafts SET error=? WHERE id=?", (str(error), draft_id))
        db().commit()
        raise
    fees = listing_fees(root)
    db().execute("UPDATE ebay_drafts SET error='',fees=? WHERE id=?", (fees, draft_id))
    db().commit()
    return jsonify({"ok": True, "fees": fees, "warnings": warnings})


@app.post("/api/ebay/drafts/<int:draft_id>/publish")
@login_required
def publish_ebay_draft(draft_id):
    uid = user_id()
    draft = own_draft(draft_id)
    if not draft:
        return jsonify({"error": "Entwurf nicht gefunden."}), 404
    if draft["status"] == "published":
        return jsonify({"error": "Dieser Entwurf ist schon eingestellt."}), 400
    preset = load_preset(db(), uid, draft["game_id"])
    try:
        item_xml(db(), uid, draft, preset)   # cheap checks first: no picture upload for a draft that cannot go out
        picture = upload_card_picture(db(), uid, draft["variant_id"])
        root, warnings = trading_call(db(), uid, "AddFixedPriceItem", item_xml(db(), uid, draft, preset, picture))
    except EbayError as error:
        db().execute("UPDATE ebay_drafts SET status='failed',error=?,updated_at=? WHERE id=?", (str(error), now_iso(), draft_id))
        db().commit()
        raise
    item_id = text(root, "ItemID")
    stamp = now_iso()
    config = ebay_config(db())
    db().execute("UPDATE ebay_drafts SET status='published',item_id=?,error='',fees=?,published_at=?,updated_at=? WHERE id=?",
                 (item_id, listing_fees(root), stamp, stamp, draft_id))
    db().execute(
        """INSERT INTO ebay_listings(user_id,item_id,variant_id,title,sku,price,currency,quantity,status,url,image_url,started_at,synced_at)
           VALUES(?,?,?,?,?,?,?,?,'active',?,?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET variant_id=excluded.variant_id""",
        (uid, item_id, draft["variant_id"], draft["title"], f'DL-{draft["variant_id"]}'[:50], draft["price"], marketplace(config)["currency"],
         draft["quantity"], f'https://{marketplace(config)["domain"]}/itm/{item_id}', picture, stamp, stamp),
    )
    db().commit()
    return jsonify({"published": True, "item_id": item_id, "url": f'https://{marketplace(config)["domain"]}/itm/{item_id}', "warnings": warnings})


# ---- The account's own listings -----------------------------------------------------------------

def selling_list(name, extra=""):
    pages = "".join(f"<{list_name}><Include>false</Include></{list_name}>" for list_name in ("ActiveList", "SoldList", "UnsoldList") if list_name != name)
    return f"<{name}><Include>true</Include>{extra}<Pagination><EntriesPerPage>200</EntriesPerPage><PageNumber>{{page}}</PageNumber></Pagination></{name}>{pages}<DetailLevel>ReturnAll</DetailLevel>"


def item_link(connection, uid, item_id, sku):
    """The card a listing sells: from the draft it was published from, else from its SKU."""
    row = connection.execute("SELECT variant_id FROM ebay_drafts WHERE user_id=? AND item_id=?", (uid, item_id)).fetchone()
    if row:
        return row[0]
    if sku.startswith("DL-") and connection.execute("SELECT 1 FROM variants WHERE id=?", (sku[3:],)).fetchone():
        return sku[3:]
    return None


def sync_listings(connection, uid):
    """Brings ebay_listings and ebay_sales up to what eBay reports for the account."""
    stamp = now_iso()
    seen_active, counts = set(), {"active": 0, "sold": 0, "unsold": 0}

    def pages(name, extra=""):
        for page in range(1, SYNC_PAGES + 1):
            root, _ = trading_call(connection, uid, "GetMyeBaySelling", selling_list(name, extra).replace("{page}", str(page)))
            section = root.find(name)
            yield section
            if section is None or page >= int(text(section, "PaginationResult/TotalNumberOfPages", "1") or 1):
                break

    def upsert(item, status):
        item_id = text(item, "ItemID")
        if not item_id:
            return None
        sku = text(item, "SKU")
        price = number(item, "SellingStatus/CurrentPrice") or number(item, "BuyItNowPrice") or number(item, "StartPrice")
        currency_element = item.find("SellingStatus/CurrentPrice")
        currency = currency_element.get("currencyID", "") if currency_element is not None else ""
        quantity = int(number(item, "Quantity") or 0)
        sold = int(number(item, "SellingStatus/QuantitySold") or 0)
        connection.execute(
            """INSERT INTO ebay_listings(user_id,item_id,variant_id,title,sku,price,currency,quantity,quantity_sold,status,url,image_url,watch_count,started_at,ended_at,synced_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET
               variant_id=COALESCE(ebay_listings.variant_id,excluded.variant_id),title=excluded.title,sku=excluded.sku,
               price=COALESCE(excluded.price,ebay_listings.price),currency=CASE WHEN excluded.currency!='' THEN excluded.currency ELSE ebay_listings.currency END,
               quantity=CASE WHEN excluded.quantity>0 THEN excluded.quantity ELSE ebay_listings.quantity END,
               quantity_sold=MAX(ebay_listings.quantity_sold,excluded.quantity_sold),status=excluded.status,
               url=CASE WHEN excluded.url!='' THEN excluded.url ELSE ebay_listings.url END,
               image_url=CASE WHEN excluded.image_url!='' THEN excluded.image_url ELSE ebay_listings.image_url END,
               watch_count=excluded.watch_count,started_at=CASE WHEN excluded.started_at!='' THEN excluded.started_at ELSE ebay_listings.started_at END,
               ended_at=excluded.ended_at,synced_at=excluded.synced_at""",
            (uid, item_id, item_link(connection, uid, item_id, sku), text(item, "Title"), sku, price, currency, quantity, sold, status,
             text(item, "ListingDetails/ViewItemURL"), text(item, "PictureDetails/GalleryURL") or text(item, "PictureDetails/PictureURL"),
             int(number(item, "WatchCount") or 0), text(item, "ListingDetails/StartTime"),
             text(item, "ListingDetails/EndTime") if status != "active" else "", stamp),
        )
        return item_id

    for section in pages("ActiveList"):
        for item in section.findall("ItemArray/Item") if section is not None else []:
            if upsert(item, "active"):
                seen_active.add(text(item, "ItemID"))
                counts["active"] += 1
    for section in pages("UnsoldList", "<DurationInDays>60</DurationInDays>"):
        for item in section.findall("ItemArray/Item") if section is not None else []:
            if text(item, "ItemID") not in seen_active and upsert(item, "unsold"):
                counts["unsold"] += 1
    for section in pages("SoldList", "<DurationInDays>60</DurationInDays>"):
        for transaction in section.iter("Transaction") if section is not None else []:
            item = transaction.find("Item")
            item_id = text(item, "ItemID")
            if not item_id:
                continue
            if item_id not in seen_active:
                upsert(item, "sold")
            # (An Element without children is falsy: "or" cannot pick the first one found.)
            candidates = (transaction.find("TotalTransactionPrice"), transaction.find("TotalPrice"), item.find("SellingStatus/CurrentPrice"))
            price_element = next((element for element in candidates if element is not None), None)
            price = float(price_element.text) if price_element is not None and price_element.text else None
            cursor = connection.execute(
                """INSERT OR IGNORE INTO ebay_sales(user_id,item_id,transaction_id,title,sku,quantity,price,currency,buyer,sold_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (uid, item_id, text(transaction, "TransactionID") or text(transaction, "OrderLineItemID") or text(transaction, "CreatedDate"),
                 text(item, "Title"), text(item, "SKU"), int(number(transaction, "QuantityPurchased") or 1), price,
                 price_element.get("currencyID", "") if price_element is not None else "", text(transaction, "Buyer/UserID"), text(transaction, "CreatedDate")),
            )
            counts["sold"] += cursor.rowcount
    # What was active and is not any more ended without eBay saying how (outside the 60 days).
    placeholders = ",".join("?" * len(seen_active))
    connection.execute(
        f"UPDATE ebay_listings SET status='ended',synced_at=? WHERE user_id=? AND status='active'" + (f" AND item_id NOT IN ({placeholders})" if seen_active else ""),
        (stamp, uid, *seen_active),
    )
    connection.execute("UPDATE ebay_accounts SET listings_synced_at=?,last_error='' WHERE user_id=?", (stamp, uid))
    connection.commit()
    return counts


@app.post("/api/ebay/listings/sync")
@login_required
def sync_ebay_listings():
    try:
        counts = sync_listings(db(), user_id())
    except EbayError as error:
        db().execute("UPDATE ebay_accounts SET last_error=? WHERE user_id=?", (str(error), user_id()))
        db().commit()
        raise
    return jsonify({"synced": True, **counts})


@app.get("/api/ebay/listings")
@login_required
def ebay_listings():
    uid, game_id = user_id(), request.args.get("game_id")
    rows = db().execute(
        f"""SELECT l.*,i.canonical_name,p.collector_number,s.code set_code,v.finish,v.game_id,{latest_price_sql('v')} market_price
            FROM ebay_listings l LEFT JOIN variants v ON v.id=l.variant_id LEFT JOIN printings p ON p.id=v.printing_id
              LEFT JOIN card_identities i ON i.id=p.identity_id LEFT JOIN sets s ON s.id=p.set_id
            WHERE l.user_id=? AND (l.variant_id IS NULL OR v.game_id=? OR ? IS NULL)
            ORDER BY CASE l.status WHEN 'active' THEN 0 WHEN 'sold' THEN 1 ELSE 2 END,l.started_at DESC""", (uid, game_id, game_id),
    ).fetchall()
    sales = db().execute(
        """SELECT sa.*,l.variant_id FROM ebay_sales sa LEFT JOIN ebay_listings l ON l.user_id=sa.user_id AND l.item_id=sa.item_id
           WHERE sa.user_id=? ORDER BY sa.sold_at DESC LIMIT 50""", (uid,),
    ).fetchall()
    return jsonify({"listings": [dict(row) for row in rows], "sales": [dict(row) for row in sales], "status": status_payload(db(), uid)})


@app.patch("/api/ebay/listings/<item_id>")
@login_required
def link_ebay_listing(item_id):
    """Links one of the account's listings to the card it sells (or unlinks it with null)."""
    variant_id = (request.get_json(force=True) or {}).get("variant_id")
    if variant_id and not db().execute("SELECT 1 FROM variants WHERE id=?", (variant_id,)).fetchone():
        return jsonify({"error": "Diese Karte gibt es im Katalog nicht."}), 404
    cursor = db().execute("UPDATE ebay_listings SET variant_id=? WHERE user_id=? AND item_id=?", (variant_id or None, user_id(), item_id))
    if not cursor.rowcount:
        return jsonify({"error": "Angebot nicht gefunden."}), 404
    db().commit()
    return jsonify({"linked": bool(variant_id)})


# ---- Listings followed as a card's price --------------------------------------------------------

def parse_item_id(value):
    value = str(value or "").strip()
    match = PLAIN_ITEM_ID.match(value) or ITEM_URL.search(value)
    return match.group(1) if match else None


def fetch_item(connection, item_id):
    """One public listing through the Browse API: (fields to store, status)."""
    config = require_config(connection)
    response, payload = rest_get(connection, f'{hosts(config)["api"]}/buy/browse/v1/item/get_item_by_legacy_id',
                                 app_token(connection), config, {"legacy_item_id": item_id})
    if response.status_code == 404:
        return {"status": "ended", "error": ""}
    if response.status_code != 200:
        return {"status": "error", "error": rest_error(payload, f"HTTP {response.status_code}")[:300]}
    price = payload.get("price") or {}
    shipping = ((payload.get("shippingOptions") or [{}])[0] or {}).get("shippingCost") or {}
    availability = ((payload.get("estimatedAvailabilities") or [{}])[0] or {}).get("estimatedAvailabilityStatus")
    ended = availability == "OUT_OF_STOCK" or (payload.get("itemEndDate") and is_past(payload["itemEndDate"]))
    try:
        amount = round(float(price.get("value")), 2) if price.get("value") is not None else None
        shipping_amount = round(float(shipping.get("value")), 2) if shipping.get("value") is not None else None
    except (TypeError, ValueError):
        amount, shipping_amount = None, None
    return {
        "status": "ended" if ended else "active", "error": "", "title": str(payload.get("title") or "")[:200],
        "price": amount, "currency": str(price.get("currency") or ""), "shipping": shipping_amount,
        "url": str(payload.get("itemWebUrl") or ""), "image_url": str((payload.get("image") or {}).get("imageUrl") or ""),
        "end_date": str(payload.get("itemEndDate") or ""),
    }


def refresh_tracked(connection, row):
    try:
        fields = fetch_item(connection, row["item_id"])
    except EbayError as error:
        fields = {"status": "error", "error": str(error)[:300]}
    fields["checked_at"] = now_iso()
    connection.execute(f"UPDATE ebay_tracked_items SET {','.join(f'{key}=?' for key in fields)} WHERE id=?", (*fields.values(), row["id"]))


def store_metric(connection, variant_id, metric, amount, stamp):
    latest = connection.execute(
        "SELECT id,amount,observed_at FROM price_observations WHERE variant_id=? AND provider_id=? AND metric=? ORDER BY observed_at DESC,id DESC LIMIT 1",
        (variant_id, PRICE_PROVIDER, metric),
    ).fetchone()
    if latest and latest[2] == stamp:
        connection.execute("UPDATE price_observations SET amount=? WHERE id=?", (amount, latest[0]))
    elif not latest or latest[1] != amount:
        connection.execute(
            "INSERT INTO price_observations(variant_id,provider_id,metric,amount,currency,observed_at) VALUES(?,?,?,?,'EUR',?)",
            (variant_id, PRICE_PROVIDER, metric, amount, stamp),
        )


def update_card_price(connection, variant_id):
    """The card's "ebay" price from its followed listings: the median of the active ones in EUR
    (item price, without shipping) and the lowest. With no active listing left the last price
    stays as it was; with no listing left at all the price source goes away."""
    rows = connection.execute("SELECT * FROM ebay_tracked_items WHERE variant_id=?", (variant_id,)).fetchall()
    if not rows:
        connection.execute("DELETE FROM price_observations WHERE variant_id=? AND provider_id=?", (variant_id, PRICE_PROVIDER))
        connection.execute("DELETE FROM marketplace_products WHERE variant_id=? AND provider_id=?", (variant_id, PRICE_PROVIDER))
        return
    active = sorted((row for row in rows if row["status"] == "active" and row["price"] and row["currency"] == "EUR"), key=lambda row: row["price"])
    if not active:
        return
    game_id = connection.execute("SELECT game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not game_id:
        return
    stamp = now_iso()
    cheapest = active[0]
    connection.execute(
        """INSERT INTO marketplace_products(provider_id,external_product_id,variant_id,game_id,source_url,match_method,matched_at,attributes)
           VALUES(?,?,?,?,?,'manual',?,?) ON CONFLICT(provider_id,variant_id) DO UPDATE SET
           external_product_id=excluded.external_product_id,source_url=excluded.source_url,attributes=excluded.attributes""",
        (PRICE_PROVIDER, cheapest["item_id"], variant_id, game_id[0], cheapest["url"], stamp, json.dumps({"listings": len(active)})),
    )
    store_metric(connection, variant_id, "trend", round(statistics.median(row["price"] for row in active), 2), stamp)
    store_metric(connection, variant_id, "low", cheapest["price"], stamp)


def tracked_payload(variant_id):
    rows = db().execute("SELECT * FROM ebay_tracked_items WHERE variant_id=? ORDER BY status!='active',price IS NULL,price,id", (variant_id,)).fetchall()
    return {"items": [dict(row) for row in rows], "can_track": is_configured(ebay_config(db()))}


@app.route("/api/variants/<variant_id>/ebay-items", methods=["GET", "POST"])
@login_required
def variant_ebay_items(variant_id):
    if not db().execute("SELECT 1 FROM variants WHERE id=?", (variant_id,)).fetchone():
        return jsonify({"error": "variant not found"}), 404
    if request.method == "GET":
        return jsonify(tracked_payload(variant_id))
    require_config(db())
    item_id = parse_item_id((request.get_json(force=True) or {}).get("url"))
    if not item_id:
        return jsonify({"error": "Das ist kein Link zu einem eBay-Angebot (…/itm/123456789012) und keine Artikelnummer."}), 400
    if db().execute("SELECT COUNT(*) FROM ebay_tracked_items WHERE variant_id=?", (variant_id,)).fetchone()[0] >= TRACKED_PER_CARD:
        return jsonify({"error": f"Pro Karte werden höchstens {TRACKED_PER_CARD} Angebote beobachtet."}), 400
    if db().execute("SELECT 1 FROM ebay_tracked_items WHERE variant_id=? AND item_id=?", (variant_id, item_id)).fetchone():
        return jsonify({"error": "Dieses Angebot wird für diese Karte schon beobachtet."}), 409
    config = ebay_config(db())
    cursor = db().execute(
        "INSERT INTO ebay_tracked_items(variant_id,item_id,url,added_by,created_at) VALUES(?,?,?,?,?)",
        (variant_id, item_id, f'https://{marketplace(config)["domain"]}/itm/{item_id}', user_id(), now_iso()),
    )
    db().commit()
    refresh_tracked(db(), {"id": cursor.lastrowid, "item_id": item_id})
    update_card_price(db(), variant_id)
    db().commit()
    return jsonify(tracked_payload(variant_id)), 201


@app.delete("/api/variants/<variant_id>/ebay-items/<item_id>")
@login_required
def remove_variant_ebay_item(variant_id, item_id):
    cursor = db().execute("DELETE FROM ebay_tracked_items WHERE variant_id=? AND item_id=?", (variant_id, item_id))
    if not cursor.rowcount:
        return jsonify({"error": "Angebot nicht gefunden."}), 404
    update_card_price(db(), variant_id)
    db().commit()
    return jsonify(tracked_payload(variant_id))


@app.post("/api/variants/<variant_id>/ebay-items/refresh")
@login_required
def refresh_variant_ebay_items(variant_id):
    require_config(db())
    for row in db().execute("SELECT id,item_id FROM ebay_tracked_items WHERE variant_id=?", (variant_id,)).fetchall():
        refresh_tracked(db(), row)
    update_card_price(db(), variant_id)
    db().commit()
    return jsonify(tracked_payload(variant_id))


# ---- Background job (ebay_sync.py) --------------------------------------------------------------

def run_due(connection, tracked_limit=60):
    """What is due: followed listings not read for TRACKED_REFRESH_HOURS (ended ones once a day,
    in case eBay reports them differently later), then every connected account's listings when
    they were last read LISTING_SYNC_MINUTES ago."""
    summary = {"tracked": 0, "accounts": 0, "errors": []}
    config = ebay_config(connection)
    if not is_configured(config):
        return summary
    fresh = (datetime.now(timezone.utc) - timedelta(hours=TRACKED_REFRESH_HOURS)).replace(microsecond=0).isoformat()
    day = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    due = connection.execute(
        """SELECT id,item_id,variant_id FROM ebay_tracked_items
           WHERE checked_at IS NULL OR (status!='ended' AND checked_at<?) OR (status='ended' AND checked_at<? AND created_at>?)
           ORDER BY checked_at IS NOT NULL,checked_at LIMIT ?""",
        (fresh, day, (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(), tracked_limit),
    ).fetchall()
    for row in due:
        refresh_tracked(connection, {"id": row[0], "item_id": row[1]})
        summary["tracked"] += 1
    for variant_id in {row[2] for row in due}:
        update_card_price(connection, variant_id)
    connection.commit()
    if not config["ru_name"]:
        return summary
    stale = (datetime.now(timezone.utc) - timedelta(minutes=LISTING_SYNC_MINUTES)).replace(microsecond=0).isoformat()
    for (uid,) in connection.execute(
        "SELECT user_id FROM ebay_accounts WHERE refresh_token!='' AND (listings_synced_at IS NULL OR listings_synced_at<?)", (stale,),
    ).fetchall():
        try:
            sync_listings(connection, uid)
            summary["accounts"] += 1
        except EbayError as error:
            connection.execute("UPDATE ebay_accounts SET last_error=?,listings_synced_at=? WHERE user_id=?", (str(error), now_iso(), uid))
            connection.commit()
            summary["errors"].append(f"user {uid}: {error}")
    return summary
