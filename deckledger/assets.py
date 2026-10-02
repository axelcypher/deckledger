"""Logos, set visuals, filter icons, card backs and the shared foil textures."""

import math
import re
import xml.sax.saxutils as xml_escape
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from flask import Response, send_file

from .config import CARD_BACK_UPLOAD_DIR, IMAGE_CACHE, PUBLIC_DIR, PUBLIC_IMAGE_EXTENSIONS, PUBLIC_OP_ICON_DIR, PUBLIC_SET_DIR, ROOT
from .games import GAME_LOGOS
from .web import app, db, login_required


LORCANA_PRODUCT_PATHS = {
    "1": "the-first-chapter", "2": "rise-of-the-floodborn", "3": "into-the-inklands",
    "4": "ursulas-return", "5": "shimmering-skies", "6": "azurite-sea",
    "7": "archazias-island", "8": "reign-of-jafar", "9": "fabled", "10": "whispers",
    "11": "winterspell", "12": "wilds-unknown", "13": "attack-of-the-vine",
    "14": "hyperia-city", "15": "into-the-inkdark", "Q1": "deep-trouble",
    "Q2": "palace-heist", "Q3": "great-hunny-rescue",
}


@app.get("/game-logo/<game_id>")
@login_required
def game_logo(game_id):
    """Cache the official TCG wordmarks used by catalogue and game tiles."""
    source_url = GAME_LOGOS.get(game_id)
    if not source_url:
        return Response(status=404)
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    data_path = IMAGE_CACHE / f"brand-{game_id}.img"
    mime_path = IMAGE_CACHE / f"brand-{game_id}.mime"
    try:
        if data_path.exists() and mime_path.exists():
            payload, content_type = data_path.read_bytes(), mime_path.read_text().strip()
        else:
            response = urlopen(Request(source_url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "image/svg+xml,image/png,image/*"}), timeout=15)
            payload, content_type = response.read(1_000_000), response.headers.get_content_type()
            if not content_type.startswith("image/") or len(payload) < 100:
                return Response(status=502)
            data_path.write_bytes(payload)
            mime_path.write_text(content_type)
        return Response(payload, mimetype=content_type, headers={"Cache-Control": "public, max-age=604800", "X-Logo-Source": "official-provider-cache"})
    except Exception as error:
        app.logger.warning("Game logo provider failed for %s: %s", game_id, error)
        return Response(status=502)


def provider_html(url):
    request = Request(url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "text/html"})
    return urlopen(request, timeout=18).read(2_000_000).decode("utf-8", "ignore")


def remote_set_visual(set_row):
    """Resolve a set-specific visual from each game's official provider."""
    if set_row["game_id"] == "lorcana":
        path = LORCANA_PRODUCT_PATHS.get(set_row["code"])
        if not path:
            return None
        page_url = f"https://www.disneylorcana.com/en-US/product/{path}"
        page = provider_html(page_url)
        images = re.findall(r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"', page, re.I | re.S)
        header = next((src for src, alt in images if "header" in alt.lower() or "logo" in alt.lower()), None)
        return urljoin(page_url, header) if header else None

    if set_row["game_id"] == "hololive":
        for base in ("https://en.hololive-official-cardgame.com", "https://hololive-official-cardgame.com"):
            page_url = f"{base}/cardlist/"
            page = provider_html(page_url)
            pattern = rf'<a class="anchor" href="(/cardlist/cardsearch/\?expansion={re.escape(set_row["code"])})">(.*?)</a>'
            product = re.search(pattern, page, re.I | re.S)
            if product:
                image = re.search(r'<img[^>]+src="([^"]+)"', product.group(2), re.I)
                if image:
                    return urljoin(base, image.group(1))
        return None

    if set_row["game_id"] == "vcard":
        # The set id is "vcard-<official slug>"; each official set page embeds its own logo path.
        page_url = f"https://www.vcardtcg.com/cards/{set_row['id'].removeprefix('vcard-')}"
        logo = re.search(r'\\"logo\\":\{\\"main\\":\\"(.*?)\\"', provider_html(page_url))
        return urljoin(page_url, quote(logo.group(1), safe=":/%")) if logo else None

    if set_row["game_id"] != "one-piece":
        # Games without an official visual source of their own get the generated wordmark.
        return None

    code = re.sub(r"[^a-z0-9]", "", set_row["code"].lower())
    category = "decks" if set_row["code"].startswith("ST-") else "boosters"
    filenames = [code]
    starter_number = re.fullmatch(r"st(\d{2})", code)
    if starter_number:
        number = int(starter_number.group(1))
        for first, last in ((1, 4), (8, 9), (15, 20), (23, 28), (31, 36)):
            if first <= number <= last:
                filenames.append(f"st{first:02d}-{last:02d}")
    for base in ("https://en.onepiece-cardgame.com", "https://www.onepiece-cardgame.com"):
        for filename in filenames:
            page_url = f"{base}/products/{category}/{filename}.php"
            try:
                page = provider_html(page_url)
            except Exception:
                continue
            images = re.findall(r'<img[^>]+(?:src|data-src)="([^"]+)"', page, re.I)
            hero = next((src for src in images if "/images/products/" in src and re.search(r"/mv(?:_|\.)", src, re.I)), None)
            if hero:
                return urljoin(page_url, hero)
    return None


def set_wordmark(set_row):
    name = xml_escape.escape(set_row["name"])
    code = xml_escape.escape(set_row["code"])
    words = name.split()
    midpoint = max(1, math.ceil(len(words) / 2))
    first, second = " ".join(words[:midpoint]), " ".join(words[midpoint:])
    second_line = f'<text x="400" y="132" text-anchor="middle" class="name">{second}</text>' if second else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="220" viewBox="0 0 800 220">
      <style>.name{{font:700 40px Arial,sans-serif;fill:#eef3f9;letter-spacing:-1px}}.code{{font:700 16px Arial,sans-serif;fill:#9cabbc;letter-spacing:4px}}</style>
      <path d="M170 176h460" stroke="#64758a" stroke-opacity=".42"/><text x="400" y="86" text-anchor="middle" class="name">{first}</text>{second_line}<text x="400" y="202" text-anchor="middle" class="code">{code}</text>
    </svg>'''


def public_set_visual(set_row) -> Path | None:
    """Return a server-provided set visual before consulting any fallback."""
    # Lorcana's numbered-set logos live alongside its other icon assets now
    # (set{code}-logo.png), not the shared per-game PUBLIC_SET_DIR -- checked first, falling
    # through to the generic lookup below for anything not covered there (promo sets, or any
    # numbered set that hasn't been added to that folder).
    if set_row["game_id"] == "lorcana" and set_row["code"].isdigit():
        candidate = PUBLIC_DIR / "icons" / "lorcana" / f"set{int(set_row['code']):02d}-logo.png"
        if candidate.is_file():
            return candidate
    stems = []
    for value in (set_row["id"], set_row["code"]):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip("-.")
        for candidate in (safe, safe.lower()):
            if candidate and candidate not in stems:
                stems.append(candidate)
    for stem in stems:
        for extension in PUBLIC_IMAGE_EXTENSIONS:
            candidate = PUBLIC_SET_DIR / f"{stem}{extension}"
            if candidate.is_file():
                return candidate
    return None


def set_visual_version(set_row) -> str:
    """Cache-bust set visuals whenever a public override changes in place."""
    visual = public_set_visual(set_row)
    if not visual:
        return "provider-v1"
    stat = visual.stat()
    return f"public-{stat.st_mtime_ns:x}-{stat.st_size:x}"


@app.get("/set-logo/<set_id>")
@login_required
def set_logo(set_id):
    set_row = db().execute("SELECT id,game_id,code,name FROM sets WHERE id=?", (set_id,)).fetchone()
    if not set_row:
        return Response(status=404)
    public_visual = public_set_visual(set_row)
    if public_visual:
        response = send_file(public_visual, conditional=True, etag=True, max_age=0)
        # Public assets may be replaced in place. Browsers retain them locally
        # but revalidate cheaply, so a newly supplied file wins immediately.
        response.headers["Cache-Control"] = "public, no-cache"
        response.headers["X-Logo-Source"] = "public-set-asset"
        return response
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", set_id)
    data_path = IMAGE_CACHE / f"set-{safe_id}.img"
    mime_path = IMAGE_CACHE / f"set-{safe_id}.mime"
    try:
        if data_path.exists() and mime_path.exists():
            payload, content_type = data_path.read_bytes(), mime_path.read_text().strip()
        else:
            source_url = remote_set_visual(set_row)
            if not source_url:
                raise RuntimeError("no official set visual published")
            response = urlopen(Request(source_url, headers={"User-Agent": "DeckLedger/1.0", "Accept": "image/avif,image/webp,image/*"}), timeout=18)
            payload, content_type = response.read(4_000_000), response.headers.get_content_type()
            if not content_type.startswith("image/") or len(payload) < 500:
                raise RuntimeError("invalid set visual response")
            data_path.write_bytes(payload)
            mime_path.write_text(content_type)
        return Response(payload, mimetype=content_type, headers={"Cache-Control":"public, max-age=604800", "X-Logo-Source":"official-set-provider-cache"})
    except Exception as error:
        app.logger.info("Set visual fallback for %s: %s", set_id, error)
        return Response(set_wordmark(set_row), mimetype="image/svg+xml", headers={"Cache-Control":"public, max-age=86400", "X-Logo-Source":"set-wordmark-fallback"})


@app.get("/op-filter-icon/<name>")
def op_filter_icon(name):
    if not re.fullmatch(r"(?:cost-(?:10|[1-9])\.png|(?:attribute-(?:slash|strike|special|ranged|wisdom)|color)\.svg)", name):
        return Response(status=404)
    path = PUBLIC_OP_ICON_DIR / name
    if not path.is_file():
        return Response(status=404)
    mimetype = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return send_file(path, mimetype=mimetype, conditional=True, etag=True, max_age=0)


# Explicit filenames rather than a pattern -- icon assets are hand-supplied (some svg, some
# png, no consistent rule between them), so there's nothing regular to match against.
LORCANA_FILTER_ICON_FILES = {
    "amber.svg", "amethyst.svg", "emerald.svg", "ruby.svg", "sapphire.svg", "steel.svg",
    "cost.png", "inkable.png", "uninkable.png",
    "common.svg", "uncommon.svg", "rare.svg", "super_rare.svg", "legendary.svg",
    "enchanted.png", "epic.png", "iconic.png", "promo.png",
}


@app.get("/lorcana-filter-icon/<name>")
def lorcana_filter_icon(name):
    if name not in LORCANA_FILTER_ICON_FILES:
        return Response(status=404)
    mimetype = "image/svg+xml" if name.endswith(".svg") else "image/png"
    path = PUBLIC_DIR / "icons" / "lorcana" / name
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype=mimetype, conditional=True, etag=True, max_age=0)


def card_back_placeholder(game_row):
    name = xml_escape.escape(game_row["short_name"] or game_row["name"])
    accent = game_row["accent"] or "#6366f1"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="560" viewBox="0 0 400 560">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{accent}" stop-opacity=".9"/><stop offset="1" stop-color="#11151f"/></linearGradient></defs>
      <rect width="400" height="560" rx="22" fill="url(#g)"/>
      <rect x="18" y="18" width="364" height="524" rx="14" fill="none" stroke="#ffffff" stroke-opacity=".28" stroke-width="3"/>
      <text x="200" y="290" text-anchor="middle" font-family="Arial,sans-serif" font-weight="700" font-size="34" fill="#ffffff">{name}</text>
    </svg>'''


@app.get("/card-back/<game_id>")
def card_back(game_id):
    if not re.fullmatch(r"[a-z0-9-]+", game_id):
        return Response(status=404)
    uploaded = CARD_BACK_UPLOAD_DIR / f"{game_id}.jpg"
    if uploaded.is_file():
        return send_file(uploaded, mimetype="image/jpeg", conditional=True, etag=True, max_age=0)
    # An admin's upload wins, then the public folder (a mount in most deployments), then the
    # back that ships with the app.
    for path in (PUBLIC_DIR / f"{game_id}-back.jpg", Path(app.static_folder) / "assets" / game_id / f"{game_id}-back.jpg"):
        if path.is_file():
            return send_file(path, mimetype="image/jpeg", conditional=True, etag=True, max_age=604800)
    game_row = db().execute("SELECT short_name, name, accent FROM games WHERE id=?", (game_id,)).fetchone()
    if not game_row:
        return Response(status=404)
    return Response(card_back_placeholder(game_row), mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/glass-plate.svg")
def glass_plate_asset():
    path = PUBLIC_DIR / "glass-plate.svg"
    if not path.is_file():
        return Response(status=404)
    # conditional+etag (not a fixed max_age) so edits to the file on disk -- this is meant to
    # be hand-tuned in place -- are picked up on the next request via a 304 revalidation
    # instead of needing a manually-bumped ?v= query param each time.
    return send_file(path, mimetype="image/svg+xml", conditional=True, etag=True, max_age=0)


@app.get("/foil-base-reflection.png")
def foil_base_reflection_asset():
    path = PUBLIC_DIR / "foil_base_reflection.png"
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype="image/png", conditional=True, etag=True, max_age=0)


# ---- Lorcana WebGL foil renderer: shared effect textures + material presets ----
# The 26 textures (public/assets/lorcana/foil/*.png) were extracted from the Disney Lorcana TCG
# Companion app's own Unity build (assets/bin/Data/data.unity3d, via UnityPy) -- these are the
# SAME shared, non-card-specific effect textures the official app itself uses (rainbow
# gradients, noise/pattern textures, shine/varnish maps), not generated stand-ins. See
# lorcana_foil_original_presets.json's own "source" field for exactly which app build they came
# from. conditional+etag (not a fixed max_age), matching glass-plate.svg above -- these are
# meant to be hand-verified/replaceable in place, not versioned via a ?v= query param.
LORCANA_FOIL_ASSET_DIR = PUBLIC_DIR / "assets" / "lorcana" / "foil"


@app.get("/assets/lorcana/foil/<name>.png")
def lorcana_foil_texture_asset(name):
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "", name)
    path = LORCANA_FOIL_ASSET_DIR / f"{safe_name}.png"
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype="image/png", conditional=True, etag=True, max_age=0)


@app.get("/assets/lorcana/foil-presets.json")
def lorcana_foil_presets_asset():
    # Lives at the project root (where it was dropped), not duplicated into public/ -- served
    # directly from there via its own route, same "one canonical file, still gets a route"
    # pattern as glass-plate.svg's inlining elsewhere in this file.
    path = ROOT / "lorcana_foil_original_presets.json"
    if not path.is_file():
        return Response(status=404)
    return send_file(path, mimetype="application/json", conditional=True, etag=True, max_age=0)
