"""Card images: fetching, caching, trimming, thumbnails and foil masks."""

import fcntl
import hashlib
import os
import re
import xml.sax.saxutils as xml_escape
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import Response, jsonify, request, send_file, url_for

import ravensburger_foil
import sheet_render

from .config import IMAGE_CACHE, IMAGE_FOIL_MASK_CACHE, IMAGE_LOCK_CACHE, IMAGE_SOURCE_CACHE, IMAGE_THUMB_CACHE, IMAGE_TRIM_CACHE, jload
from .games import game as game_rules
from .web import app, db, login_required


def remote_image_url(row):
    """Resolve a provider-owned card image without encoding provider rules in core data."""
    attributes = jload(row["variant_attributes"], {}) if "variant_attributes" in row.keys() else {}
    if attributes.get("imageUrl"):
        return attributes["imageUrl"]
    resolve = game_rules(row["game_id"]).remote_image_url
    return resolve(row) if resolve else None


def sniff_image_type(payload):
    if payload[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if payload[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP": return "image/webp"
    if payload[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    return None


def cached_real_image(row, variant_id):
    """Return a local image path, deduplicated by provider URL when possible."""
    for directory in (IMAGE_CACHE, IMAGE_SOURCE_CACHE, IMAGE_LOCK_CACHE):
        directory.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", variant_id)
    legacy_data = IMAGE_CACHE / f"{safe_id}.img"
    legacy_mime = IMAGE_CACHE / f"{safe_id}.mime"
    attributes = jload(row["variant_attributes"], {}) if "variant_attributes" in row.keys() else {}
    if legacy_data.exists() and legacy_mime.exists() and not attributes.get("imageUrl"):
        return legacy_data, legacy_mime.read_text().strip(), f"variant-{safe_id}"
    url = remote_image_url(row)
    if not url:
        if legacy_data.exists() and legacy_mime.exists():
            return legacy_data, legacy_mime.read_text().strip(), f"variant-{safe_id}"
        return None

    # A provider may name stand-in images for a print whose own scan is not published yet (VCard
    # uploads a new set's First Edition scans before the Unlimited ones). Anything already cached
    # wins before the network is touched, so a missing primary is requested once, not per view.
    candidates = [url, *(attributes.get("imageFallbackUrls") or [])]
    for candidate in candidates:
        source_key = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        data_path, mime_path = IMAGE_SOURCE_CACHE / f"{source_key}.img", IMAGE_SOURCE_CACHE / f"{source_key}.mime"
        if data_path.exists() and mime_path.exists():
            return data_path, mime_path.read_text().strip(), source_key
    for candidate in candidates[:-1]:
        try:
            downloaded = download_real_image(candidate, legacy_data, legacy_mime)
        except OSError:
            # urllib's HTTPError/URLError are OSErrors: the next stand-in gets its turn.
            downloaded = None
        if downloaded:
            return downloaded
    return download_real_image(candidates[-1], legacy_data, legacy_mime)


def download_real_image(url, legacy_data, legacy_mime):
    source_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    data_path = IMAGE_SOURCE_CACHE / f"{source_key}.img"
    mime_path = IMAGE_SOURCE_CACHE / f"{source_key}.mime"
    if data_path.exists() and mime_path.exists():
        return data_path, mime_path.read_text().strip(), source_key

    lock_path = IMAGE_LOCK_CACHE / f"{source_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if data_path.exists() and mime_path.exists():
            return data_path, mime_path.read_text().strip(), source_key
        if legacy_data.exists() and legacy_mime.exists():
            # A hard link seeds the URL cache without duplicating 200–400 KB per
            # language/finish variant already present in the old cache.
            try:
                os.link(legacy_data, data_path)
            except FileExistsError:
                pass
            mime_path.write_text(legacy_mime.read_text().strip())
            return data_path, mime_path.read_text().strip(), source_key
        response = urlopen(
            Request(url, headers={
                "User-Agent": "DeckLedger/0.1",
                "Accept": "image/avif,image/webp,image/*",
                # Some CDNs (e.g. Cardmarket's product-image bucket) 403 hotlink-style
                # requests without a same-site Referer.
                "Referer": f"https://{urlparse(url).netloc}/",
            }),
            timeout=15,
        )
        payload = response.read(5_000_000)
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/"):
            # A handful of CDNs (again, Cardmarket) serve a bogus/missing Content-Type
            # instead of the real one -- fall back to sniffing the file's magic bytes.
            content_type = sniff_image_type(payload) or content_type
        if not content_type.startswith("image/") or len(payload) < 1000:
            return None
        data_path.write_bytes(payload)
        mime_path.write_text(content_type)
        return data_path, content_type, source_key


def cached_trimmed_image(source_path: Path, cache_key: str, bleed) -> tuple[Path, str] | None:
    """The image without its print bleed, made once. None for an image that has none to cut --
    remembered with a marker file, so it is not opened again on every request."""
    from PIL import Image

    IMAGE_TRIM_CACHE.mkdir(parents=True, exist_ok=True)
    IMAGE_LOCK_CACHE.mkdir(parents=True, exist_ok=True)
    targets = {"image/png": IMAGE_TRIM_CACHE / f"{cache_key}.png", "image/jpeg": IMAGE_TRIM_CACHE / f"{cache_key}.jpg"}
    untrimmed = IMAGE_TRIM_CACHE / f"{cache_key}.keep"

    def existing():
        return next(((path, mime) for mime, path in targets.items() if path.exists()), None)

    if existing() or untrimmed.exists():
        return existing()
    lock_path = IMAGE_LOCK_CACHE / f"trim-{cache_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if existing() or untrimmed.exists():
            return existing()
        try:
            with Image.open(source_path) as source:
                trimmed = sheet_render.trim_bleed(source, bleed)
                if trimmed is source:
                    untrimmed.touch()
                    return None
                # A photo stays a JPEG; everything else is kept lossless.
                mime = "image/jpeg" if source.format == "JPEG" else "image/png"
                temporary = targets[mime].with_suffix(".tmp")
                if mime == "image/jpeg":
                    trimmed.convert("RGB").save(temporary, format="JPEG", quality=95, subsampling=0)
                else:
                    trimmed.save(temporary, format="PNG", optimize=True)
                os.replace(temporary, targets[mime])
            return targets[mime], mime
        except Exception as error:
            app.logger.warning("Trimming the bleed failed for %s: %s", source_path, error)
            return None


def card_image(row, variant_id):
    """cached_real_image() as every view should show it: cut to the card where the provider
    publishes its print files. The cache key changes with it, so thumbnails and foil masks made
    from the untrimmed image are not reused."""
    real_image = cached_real_image(row, variant_id)
    bleed = game_rules(row["game_id"]).image_bleed
    if not real_image or not bleed:
        return real_image
    trimmed = cached_trimmed_image(real_image[0], real_image[2], bleed)
    return (trimmed[0], trimmed[1], f"{real_image[2]}-trim") if trimmed else real_image


def cached_thumbnail(source_path: Path, cache_key: str) -> Path | None:
    """Create a compact list thumbnail once; full artwork stays untouched."""
    from PIL import Image, ImageOps

    IMAGE_THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    IMAGE_LOCK_CACHE.mkdir(parents=True, exist_ok=True)
    thumb_path = IMAGE_THUMB_CACHE / f"{cache_key}-360.webp"
    if thumb_path.exists():
        return thumb_path
    lock_path = IMAGE_LOCK_CACHE / f"thumb-{cache_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if thumb_path.exists():
            return thumb_path
        try:
            with Image.open(source_path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((360, 504), Image.Resampling.LANCZOS)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.save(thumb_path, format="WEBP", quality=78, method=4)
            return thumb_path
        except Exception as error:
            app.logger.warning("Thumbnail generation failed for %s: %s", source_path, error)
            return None


def cached_foil_mask(source_path: Path, cache_key: str) -> Path | None:
    """Luminance map derived from the card's OWN artwork, used as a CSS mask-image for the
    mobile foil/prismatic/aurora effect: dark/black regions of THIS card get alpha 0 (no
    shimmer), everything else gets FULL alpha (full shimmer) -- instead of the effect washing
    uniformly over the whole card rectangle (borders, text boxes, dark backgrounds included)
    the way the older unmasked version did. Generated once per card image and cached, same
    lock-file pattern as cached_thumbnail above.
    Filename carries a version suffix (-v2) so this curve fix invalidates every mask already
    cached under the old, straight-linear version below instead of silently keeping the old
    (bad) ones around under the same cache key."""
    from PIL import Image, ImageFilter, ImageOps

    IMAGE_FOIL_MASK_CACHE.mkdir(parents=True, exist_ok=True)
    IMAGE_LOCK_CACHE.mkdir(parents=True, exist_ok=True)
    mask_path = IMAGE_FOIL_MASK_CACHE / f"{cache_key}-mask-v2.webp"
    if mask_path.exists():
        return mask_path
    lock_path = IMAGE_LOCK_CACHE / f"foilmask-{cache_key}.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if mask_path.exists():
            return mask_path
        try:
            with Image.open(source_path) as source:
                gray = ImageOps.exif_transpose(source).convert("L")
                # Downscale before blurring -- the mask only needs to be smooth, not pixel-
                # sharp, and this keeps generation fast even on large source images.
                gray.thumbnail((300, 420), Image.Resampling.LANCZOS)
                gray = gray.filter(ImageFilter.GaussianBlur(radius=3))
                # Bug fix (feedback): a straight 1:1 luminance->alpha map meant the effect
                # visibly faded across most of the card, not just genuinely black regions --
                # any midtone pixel got a proportionally dimmed mask value, which read as the
                # foil card "losing intensity" almost everywhere instead of just over its
                # actual black areas. Steep threshold curve instead: only pixels close to true
                # black (<=18/255) get excluded; everything from a fairly dim 60/255 upward
                # gets FULL, undiminished alpha. The 18-60 band is just a short ramp so the
                # mask edge isn't a hard jagged cutoff, not a general brightness response.
                gray = gray.point(lambda l: 0 if l <= 18 else (255 if l >= 60 else int((l - 18) * 255 / 42)))
                mask = Image.new("RGBA", gray.size, (255, 255, 255, 0))
                mask.putalpha(gray)
                mask.save(mask_path, format="WEBP", quality=80, method=4)
            return mask_path
        except Exception as error:
            app.logger.warning("Foil mask generation failed for %s: %s", source_path, error)
            return None


def foil_mask_fallback() -> Path:
    """Cards with no real cached artwork (generated-placeholder-SVG fallback) have nothing to
    derive a luminance map from. mask-image that fails to load makes its whole element
    invisible per spec -- rather than 404 and silently kill the foil effect, fail open with a
    flat, fully-opaque mask (no exclusion anywhere), matching the old unmasked behavior for
    just that edge case."""
    IMAGE_FOIL_MASK_CACHE.mkdir(parents=True, exist_ok=True)
    path = IMAGE_FOIL_MASK_CACHE / "_fallback.webp"
    if not path.exists():
        from PIL import Image
        Image.new("RGB", (2, 2), (255, 255, 255)).save(path, format="WEBP", quality=90)
    return path


def image_file_response(path: Path, content_type: str, source: str):
    response = send_file(path, mimetype=content_type, conditional=True, etag=True, max_age=31_536_000)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    response.headers["X-Image-Source"] = source
    return response


@app.get("/art/<variant_id>.svg")
@login_required
def card_art(variant_id):
    row = db().execute("""SELECT i.canonical_name,p.id printing_id,p.collector_number,p.rarity,p.language,
      v.variant_code,v.game_id,v.finish,v.attributes variant_attributes,g.accent,s.code set_code,s.accent set_accent
      FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
      JOIN games g ON g.id=v.game_id JOIN sets s ON s.id=p.set_id WHERE v.id=?""", (variant_id,)).fetchone()
    if not row: return Response(status=404)
    try:
        real_image = card_image(row, variant_id)
        if real_image:
            image_path, content_type, cache_key = real_image
            if request.args.get("size") == "thumb":
                thumbnail = cached_thumbnail(image_path, cache_key)
                if thumbnail:
                    return image_file_response(thumbnail, "image/webp", "local-thumbnail-cache")
            return image_file_response(image_path, content_type, "local-provider-cache")
    except Exception as error:
        app.logger.warning("Card image provider failed for %s: %s", variant_id, error)
    title = xml_escape.escape(row["canonical_name"]); number=xml_escape.escape(row["collector_number"]); rarity=xml_escape.escape(row["rarity"]); finish=xml_escape.escape(row["finish"])
    seed=sum(ord(c) for c in variant_id); x=35+seed%160; y=70+(seed*7)%170
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="540" height="756" viewBox="0 0 540 756">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{row['accent']}"/><stop offset="1" stop-color="{row['set_accent']}"/></linearGradient><radialGradient id="r"><stop stop-color="#fff" stop-opacity=".7"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient><filter id="blur"><feGaussianBlur stdDeviation="28"/></filter></defs>
    <rect width="540" height="756" rx="28" fill="#111827"/><rect x="12" y="12" width="516" height="732" rx="22" fill="url(#g)"/>
    <path d="M12 470 C120 350 215 510 330 350 C420 226 482 286 528 180 V744 H12Z" fill="#050816" opacity=".66"/>
    <circle cx="{x}" cy="{y}" r="180" fill="url(#r)" filter="url(#blur)"/><circle cx="390" cy="250" r="120" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="3"/><circle cx="390" cy="250" r="82" fill="none" stroke="#fff" stroke-opacity=".15" stroke-width="2"/>
    <path d="M150 170 L255 85 L350 175 L436 115 L404 340 L267 430 L118 334Z" fill="#fff" opacity=".12" stroke="#fff" stroke-opacity=".35" stroke-width="3"/>
    <text x="38" y="58" font-family="Arial,sans-serif" font-size="20" font-weight="700" fill="#fff" opacity=".86">{number}</text><text x="500" y="58" text-anchor="end" font-family="Arial,sans-serif" font-size="17" fill="#fff" opacity=".8">{row['language']}</text>
    <rect x="28" y="540" width="484" height="176" rx="18" fill="#07101f" opacity=".9"/><text x="52" y="590" font-family="Arial,sans-serif" font-size="29" font-weight="800" fill="#fff">{title[:29]}</text><text x="52" y="624" font-family="Arial,sans-serif" font-size="16" fill="#cbd5e1">{rarity} · {finish}</text>
    <path d="M52 657 H460" stroke="#fff" stroke-opacity=".13"/><text x="52" y="687" font-family="Arial,sans-serif" font-size="13" fill="#94a3b8">DECKLEDGER CATALOGUE EDITION</text></svg>'''
    # A stand-in for an image that could not be fetched right now, not the card's artwork:
    # browsers and the service worker must come back for the real one.
    return Response(svg,mimetype="image/svg+xml",headers={"Cache-Control":"private, max-age=3600","X-Image-Source":"placeholder"})


@app.get("/foil-mask/<variant_id>.webp")
@login_required
def foil_mask(variant_id):
    row = db().execute("""SELECT i.canonical_name,p.id printing_id,p.collector_number,p.rarity,p.language,
      v.variant_code,v.game_id,v.finish,v.attributes variant_attributes,g.accent,s.code set_code,s.accent set_accent
      FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
      JOIN games g ON g.id=v.game_id JOIN sets s ON s.id=p.set_id WHERE v.id=?""", (variant_id,)).fetchone()
    if not row: return Response(status=404)
    try:
        real_image = card_image(row, variant_id)
        if real_image:
            image_path, _content_type, cache_key = real_image
            mask_path = cached_foil_mask(image_path, cache_key)
            if mask_path:
                return image_file_response(mask_path, "image/webp", "local-foil-mask-cache")
    except Exception as error:
        app.logger.warning("Foil mask lookup failed for %s: %s", variant_id, error)
    return image_file_response(foil_mask_fallback(), "image/webp", "foil-mask-fallback")


def _official_foil_variant(variant_id):
    """Shared lookup for the two Ravensburger-foil routes below: our own variant row -> the
    matching Ravensburger sub-variant dict (see ravensburger_foil.py's own docstring for the
    matching key), or None for anything that isn't a Lorcana card or has no official data.
    Never raises -- both routes treat "no data" as a normal 404, not a 500."""
    row = db().execute("""SELECT v.finish, v.game_id, v.attributes variant_attributes, p.language
      FROM variants v JOIN printings p ON p.id=v.printing_id WHERE v.id=?""", (variant_id,)).fetchone()
    if not row or not game_rules(row["game_id"]).official_foil_layers:
        return None
    attributes = jload(row["variant_attributes"], {})
    try:
        return ravensburger_foil.find_official_variant(attributes.get("imageUrl"), row["language"], row["finish"])
    except Exception as error:
        app.logger.warning("Ravensburger foil lookup failed for %s: %s", variant_id, error)
        return None


@app.get("/api/foil-layer-meta/<variant_id>")
@login_required
def foil_layer_meta(variant_id):
    """Metadata for the OFFICIAL Ravensburger foil layers (see ravensburger_foil.py) -- the
    actual mask pixels are served separately (foil_layer_mask_asset below, one request per
    mask so the browser can cache each independently); this just tells the frontend which of
    up to three masks exist for this variant plus the type/color parameters that will drive
    the per-foil_type effect presets in a later step. "available:false" (never a 4xx/5xx) is
    the normal, expected response for anything outside Lorcana or without official data --
    callers fall back to the existing generic foil effect unconditionally in that case."""
    official = _official_foil_variant(variant_id)
    # "available" specifically means "there's a base foil mask to render" -- a matched-but-plain
    # Regular sub-variant (e.g. the Normal finish of a card that also has a Foiled printing) is
    # a real, correct match with nothing wrong about it, but has no foil_mask_url and thus
    # nothing for the frontend to actually layer -- treating that as available would just push
    # the "is there really anything here" check onto every caller instead of answering it once.
    if not official or not official.get("foil_mask_url"):
        return jsonify({"available": False})
    return jsonify({
        "available": True,
        "foil_type": official.get("foil_type"),
        "foil_top_layer": official.get("foil_top_layer"),
        "hot_foil_color": official.get("hot_foil_color"),
        "second_hot_foil_color": official.get("second_hot_foil_color"),
        "mask_url": url_for("foil_layer_mask_asset", variant_id=variant_id, kind="base") if official.get("foil_mask_url") else None,
        "top_layer_mask_url": url_for("foil_layer_mask_asset", variant_id=variant_id, kind="top") if official.get("foil_top_layer_mask_url") else None,
        "second_top_layer_mask_url": url_for("foil_layer_mask_asset", variant_id=variant_id, kind="top2") if official.get("second_foil_top_layer_mask_url") else None,
    })


@app.get("/foil-layer-mask/<variant_id>/<kind>.jpg")
@login_required
def foil_layer_mask_asset(variant_id, kind):
    """Proxies + disk-caches one of Ravensburger's own mask images (never the frontend hitting
    ravensburger.com directly -- no CORS dependency, same "external asset needs our own route"
    rule as glass-plate.svg/foil-base-reflection.png elsewhere in this file, just fetched
    on-demand instead of hand-placed in public/). kind is "base"/"top"/"top2", matching
    ravensburger_foil.MASK_KIND_FIELDS."""
    if kind not in ravensburger_foil.MASK_KIND_FIELDS:
        return Response(status=404)
    official = _official_foil_variant(variant_id)
    if not official:
        return Response(status=404)
    source_url = official.get(ravensburger_foil.MASK_KIND_FIELDS[kind])
    if not source_url:
        return Response(status=404)
    path = ravensburger_foil.cached_asset(source_url)
    if not path:
        return Response(status=404)
    return image_file_response(path, "image/jpeg", "ravensburger-foil-asset-cache")
