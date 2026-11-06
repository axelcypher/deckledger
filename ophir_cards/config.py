"""Paths, constants and the two helpers everything else leans on. Imports nothing from the package."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


# The repository root: templates, static files, providers and the sync scripts live there.
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("DATABASE_PATH", str(ROOT / "deckledger.db"))
IMAGE_CACHE = Path(os.path.dirname(DB_PATH)) / "card-images"
IMAGE_SOURCE_CACHE = Path(os.path.dirname(DB_PATH)) / "card-image-sources"
IMAGE_THUMB_CACHE = Path(os.path.dirname(DB_PATH)) / "card-thumbnails"
IMAGE_LOCK_CACHE = Path(os.path.dirname(DB_PATH)) / "card-image-locks"
IMAGE_FOIL_MASK_CACHE = Path(os.path.dirname(DB_PATH)) / "card-foil-masks"
IMAGE_TRIM_CACHE = Path(os.path.dirname(DB_PATH)) / "card-images-trimmed"
PUBLIC_DIR = Path(os.environ.get("PUBLIC_DIR", ROOT / "public"))
PUBLIC_SET_DIR = PUBLIC_DIR / "sets"
PUBLIC_OP_ICON_DIR = PUBLIC_DIR / "icons" / "one-piece"
PUBLIC_IMAGE_EXTENSIONS = (".avif", ".webp", ".png", ".jpg", ".jpeg", ".svg")
# Optional SSO config file mounted into the container (docker-entrypoint.sh has no live mount
# for the rest of the app, but operators can mount just this one file read-only). When present
# it is the sole source of truth for OAuth settings and the Admin UI renders it read-only; when
# absent, OAuth settings live in the app_settings table and are fully editable from Admin.
OAUTH_CONFIG_PATH = os.environ.get("OAUTH_CONFIG_PATH", "/config/oauth.json")
# Shared sign-in with the other Ophir apps on one host (ophir_auth.py, docs/code/shared-auth.md):
# standalone (default), master (signs people in for all of them under AUTH_PATH) or slave (takes
# the master's sign-in). Read through the module (config.AUTH_MODE) so tests can switch it.
AUTH_MODE = os.environ.get("OPHIR_AUTH_MODE", "standalone").strip().lower() or "standalone"
# Where the master serves sign-in on the shared host, and where it sends people after a sign-in
# there that did not say where to go (OPHIR_AUTH_HOME, e.g. /cards/).
AUTH_PATH = "/" + os.environ.get("OPHIR_AUTH_PATH", "/auth").strip().strip("/")
AUTH_HOME = os.environ.get("OPHIR_AUTH_HOME", "/").strip() or "/"
# Fixed since there's only ever one configured provider (see plan: single generic OAuth2/OIDC
# client, not a multi-provider table) -- kept as a named constant purely so users.oauth_provider
# reads as "which identity system", not a magic string repeated at every call site.
OAUTH_PROVIDER_KEY = "generic"
# The SSO settings an environment can supply (e.g. from a Vault-rendered secrets file), by variable.
OAUTH_ENVIRONMENT = {"client_id": "OAUTH_CLIENT_ID", "client_secret": "OAUTH_CLIENT_SECRET", "discovery_url": "OAUTH_DISCOVERY_URL"}
OAUTH_CONFIG_DEFAULTS = {
    "enabled": False, "provider_name": "SSO", "client_id": "", "client_secret": "",
    "discovery_url": "", "authorize_url": "", "token_url": "", "userinfo_url": "",
    "scopes": "openid email profile", "username_claim": "preferred_username",
    "email_claim": "email", "subject_claim": "sub", "account_matching": "auto_provision",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def jload(value, default=None):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


CARD_BACK_UPLOAD_DIR = Path(os.path.dirname(DB_PATH)) / "card-backs"
SHEET_BACKGROUND_DIR = Path(os.path.dirname(DB_PATH)) / "sheet-backgrounds"
SHEET_RENDER_CACHE = Path(os.path.dirname(DB_PATH)) / "sheet-renders"
EBAY_PHOTO_DIR = Path(os.path.dirname(DB_PATH)) / "ebay-photos"
