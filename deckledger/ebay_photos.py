"""Own photos of a card for its eBay listings (deckledger/ebay.py): eBay wants pictures of the
item, and the catalogue image is only the fallback.

Photos belong to a user's card (variant), not to a draft, so they outlast a draft that is deleted
and made again and serve every listing of that card. Each keeps the uploaded original; the picture
listed is cut from it on the server by the crop the user chose (fractions of the upright,
rotated original), so the crop can be changed any time without uploading again.

On eBay a photo is uploaded once (UploadSiteHostedPictures) and its URL reused while eBay keeps
it: a picture no listing uses expires after 30 days, so an older upload is made again.

The pictures a listing already has on eBay can be fetched as photos of its card, to crop, sort or
replace them and put them back on that listing.
"""
import hashlib
import io
import json
import re
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from flask import jsonify, request, send_file

from .config import EBAY_PHOTO_DIR, jload, now_iso
from .web import app, db, login_required, user_id

PHOTOS_PER_CARD = 24             # eBay's limit of pictures per listing
UPLOAD_LIMIT = 25 * 1024 * 1024
ORIGINAL_MAX_SIDE = 4000
LISTED_MAX_SIDE = 1600
LISTED_MIN_SIDE = 500            # eBay rejects pictures whose longer side is shorter
EPS_REUSE = timedelta(days=25)
FULL_CROP = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "rotate": 0}


class PhotoError(ValueError):
    pass


def photo_file(photo_id, original=False):
    return EBAY_PHOTO_DIR / f'{int(photo_id)}{"-original" if original else ""}.jpg'


def parse_crop(raw):
    """The crop as the editor sends it, clamped to the picture; anything unreadable is the whole picture."""
    if isinstance(raw, str):
        raw = jload(raw, {}) if raw.strip() else {}
    if not isinstance(raw, dict):
        raw = {}
    crop = dict(FULL_CROP)
    try:
        crop["rotate"] = int(raw.get("rotate", 0)) % 360 // 90 * 90
        for key in ("x", "y", "w", "h"):
            if key in raw:
                crop[key] = min(1.0, max(0.0, float(raw[key])))
    except (TypeError, ValueError):
        return dict(FULL_CROP)
    crop["w"] = max(0.02, min(crop["w"], 1 - crop["x"]))
    crop["h"] = max(0.02, min(crop["h"], 1 - crop["y"]))
    return crop


def render_listed(photo_id, crop):
    """Cuts the listed picture out of the original. Raises PhotoError if it would be too small for eBay."""
    from PIL import Image

    with Image.open(photo_file(photo_id, original=True)) as original:
        image = original.convert("RGB")
    if crop["rotate"]:
        image = image.rotate(-crop["rotate"], expand=True)   # PIL turns counter-clockwise
    width, height = image.size
    box = (round(crop["x"] * width), round(crop["y"] * height),
           round((crop["x"] + crop["w"]) * width), round((crop["y"] + crop["h"]) * height))
    image = image.crop(box)
    if max(image.size) < LISTED_MIN_SIDE:
        raise PhotoError(f"Der Ausschnitt ist zu klein: eBay verlangt mindestens {LISTED_MIN_SIDE} Pixel an der längeren Seite "
                         f"(hier {max(image.size)}). Wähle einen größeren Ausschnitt oder ein schärferes Foto.")
    image.thumbnail((LISTED_MAX_SIDE, LISTED_MAX_SIDE), Image.LANCZOS)
    image.save(photo_file(photo_id), format="JPEG", quality=90)


def store_original(payload, photo_id):
    """The upload as the original: upright (EXIF turned into pixels, as a browser shows it), RGB, JPEG."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception:  # not an image, truncated, a format Pillow cannot read, a decompression bomb
        raise PhotoError("Die Datei ist kein Bild, das sich lesen lässt (JPEG, PNG oder WebP).")
    image.thumbnail((ORIGINAL_MAX_SIDE, ORIGINAL_MAX_SIDE), Image.LANCZOS)
    image.save(photo_file(photo_id, original=True), format="JPEG", quality=92)


def photo_payload(row):
    # Changes with every new crop, also twice in one second, so the browser fetches the new picture.
    version = hashlib.sha1(f'{row["crop"]}|{row["updated_at"] or row["created_at"]}'.encode()).hexdigest()[:10]
    return {"id": row["id"], "variant_id": row["variant_id"], "position": row["position"], "crop": parse_crop(row["crop"]),
            "url": f'/api/ebay/photos/{row["id"]}/image?v={version}', "original_url": f'/api/ebay/photos/{row["id"]}/original'}


def card_photos(connection, uid, variant_ids):
    """{variant_id: [photo, ...]} in their order, for the drafts of these cards."""
    variant_ids = list(dict.fromkeys(variant_ids))
    result = {variant_id: [] for variant_id in variant_ids}
    for start in range(0, len(variant_ids), 500):
        chunk = variant_ids[start:start + 500]
        rows = connection.execute(f"SELECT * FROM ebay_photos WHERE user_id=? AND variant_id IN ({','.join('?' * len(chunk))}) ORDER BY position,id",
                                  (uid, *chunk)).fetchall()
        for row in rows:
            result[row["variant_id"]].append(photo_payload(row))
    return result


def own_photo(photo_id):
    return db().execute("SELECT * FROM ebay_photos WHERE id=? AND user_id=?", (photo_id, user_id())).fetchone()


def listing_pictures(connection, uid, variant_id, upload_card_picture):
    """The eBay picture URLs for a listing of this card: the user's photos in their order, else
    the catalogue image."""
    from .ebay import EbayError, text, trading_call
    from xml.sax.saxutils import escape as xml_escape

    rows = connection.execute("SELECT * FROM ebay_photos WHERE user_id=? AND variant_id=? ORDER BY position,id", (uid, variant_id)).fetchall()
    if not rows:
        return [upload_card_picture(connection, uid, variant_id)]
    urls, fresh_after = [], datetime.now(timezone.utc) - EPS_REUSE
    for row in rows:
        uploaded = row["eps_uploaded_at"]
        if row["eps_url"] and uploaded and datetime.fromisoformat(uploaded) > fresh_after:
            urls.append(row["eps_url"])
            continue
        path = photo_file(row["id"])
        if not path.is_file():
            raise EbayError("Ein Foto dieser Karte fehlt auf dem Server. Lade es bitte neu hoch.")
        root, _ = trading_call(connection, uid, "UploadSiteHostedPictures",
                               f'<PictureName>{xml_escape(f"{variant_id}-{row["id"]}"[:80])}</PictureName><PictureSet>Supersize</PictureSet>',
                               image=(f'photo-{row["id"]}.jpg', path.read_bytes(), "image/jpeg"))
        url = text(root, "SiteHostedPictureDetails/FullURL")
        if not url:
            raise EbayError("eBay hat ein Foto nicht angenommen.", 502)
        connection.execute("UPDATE ebay_photos SET eps_url=?,eps_uploaded_at=? WHERE id=?", (url, now_iso(), row["id"]))
        connection.commit()
        urls.append(url)
    return urls


def add_photo(connection, uid, variant_id, payload, crop=None, eps_url=""):
    """Stores one photo of the card. Raises PhotoError for anything eBay would not take."""
    if connection.execute("SELECT COUNT(*) FROM ebay_photos WHERE user_id=? AND variant_id=?", (uid, variant_id)).fetchone()[0] >= PHOTOS_PER_CARD:
        raise PhotoError(f"eBay nimmt höchstens {PHOTOS_PER_CARD} Fotos je Angebot.")
    crop = crop or dict(FULL_CROP)
    EBAY_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()
    position = connection.execute("SELECT COALESCE(MAX(position),-1)+1 FROM ebay_photos WHERE user_id=? AND variant_id=?", (uid, variant_id)).fetchone()[0]
    photo_id = connection.execute(
        "INSERT INTO ebay_photos(user_id,variant_id,position,crop,eps_url,eps_uploaded_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid, variant_id, position, json.dumps(crop), eps_url, stamp if eps_url else "", stamp, stamp)).lastrowid
    try:
        store_original(payload, photo_id)
        render_listed(photo_id, crop)
    except PhotoError:
        connection.rollback()
        photo_file(photo_id).unlink(missing_ok=True)
        photo_file(photo_id, original=True).unlink(missing_ok=True)
        raise
    connection.commit()
    return photo_id


def largest_picture(url):
    """eBay serves a picture in several sizes; its address names one. The largest is 1600 pixels."""
    url = re.sub(r"/s-l\d+\.", "/s-l1600.", url)
    return re.sub(r"\$_\d+\.", "$_57.", url)


def is_ebay_picture(url):
    parts = urlparse(url)
    return parts.scheme == "https" and (parts.hostname or "").endswith(".ebayimg.com")


def remove_user_photos(uid):
    """Rows and files of an account that is being deleted."""
    for row in db().execute("SELECT id FROM ebay_photos WHERE user_id=?", (uid,)).fetchall():
        photo_file(row["id"]).unlink(missing_ok=True)
        photo_file(row["id"], original=True).unlink(missing_ok=True)
    db().execute("DELETE FROM ebay_photos WHERE user_id=?", (uid,))


@app.get("/api/ebay/photos")
@login_required
def list_ebay_photos():
    variant_id = request.args.get("variant_id", "")
    return jsonify(card_photos(db(), user_id(), [variant_id])[variant_id])


@app.post("/api/ebay/photos")
@login_required
def upload_ebay_photo():
    uid, variant_id = user_id(), str(request.form.get("variant_id") or "")
    file = request.files.get("file")
    if not file or not variant_id:
        return jsonify({"error": "Keine Datei übermittelt."}), 400
    if not db().execute("SELECT 1 FROM variants WHERE id=?", (variant_id,)).fetchone():
        return jsonify({"error": "Diese Karte gibt es im Katalog nicht."}), 404
    count = db().execute("SELECT COUNT(*) FROM ebay_photos WHERE user_id=? AND variant_id=?", (uid, variant_id)).fetchone()[0]
    if count >= PHOTOS_PER_CARD:
        return jsonify({"error": f"eBay nimmt höchstens {PHOTOS_PER_CARD} Fotos je Angebot."}), 400
    payload = file.stream.read(UPLOAD_LIMIT + 1)
    if len(payload) > UPLOAD_LIMIT:
        return jsonify({"error": "Das Foto ist größer als 25 MB."}), 400
    try:
        photo_id = add_photo(db(), uid, variant_id, payload, parse_crop(request.form.get("crop")))
    except PhotoError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(photo_payload(own_photo(photo_id))), 201


@app.post("/api/ebay/listings/<item_id>/photos/import")
@login_required
def import_listing_pictures(item_id):
    """The pictures the listing has on eBay become photos of its card, in their order, after the
    ones there are. A picture fetched before is not fetched again. Each keeps its eBay address, so
    putting it back unchanged needs no new upload."""
    from .ebay import EbayError, http, text, trading_call

    uid = user_id()
    listing = db().execute("SELECT * FROM ebay_listings WHERE user_id=? AND item_id=?", (uid, item_id)).fetchone()
    if not listing:
        return jsonify({"error": "Angebot nicht gefunden."}), 404
    if not listing["variant_id"]:
        return jsonify({"error": "Ordne dem Angebot zuerst eine Karte zu: Fotos gehören zu einer Karte."}), 400
    if not item_id.isdigit():
        return jsonify({"error": "Angebot nicht gefunden."}), 404
    try:
        root, _ = trading_call(db(), uid, "GetItem", f"<ItemID>{item_id}</ItemID>")
    except EbayError as error:
        return jsonify({"error": str(error)}), error.status
    urls = [url for url in (element.text or "" for element in root.findall("Item/PictureDetails/PictureURL")) if is_ebay_picture(url)]
    known = {row["eps_url"] for row in db().execute("SELECT eps_url FROM ebay_photos WHERE user_id=? AND variant_id=?", (uid, listing["variant_id"]))}
    added, failed = 0, []
    for url in urls:
        if url in known:
            continue
        try:
            response = http("GET", largest_picture(url), timeout=30)
            if response.status_code != 200 or len(response.content) > UPLOAD_LIMIT:
                raise PhotoError(f"HTTP {response.status_code}")
            add_photo(db(), uid, listing["variant_id"], response.content, eps_url=url)
            added += 1
        except PhotoError as error:
            if "höchstens" in str(error):
                failed.append(str(error))
                break
            failed.append(f"Ein Bild ließ sich nicht laden ({error}).")
        except Exception as error:  # the picture server unreachable
            failed.append(f"Ein Bild ließ sich nicht laden ({error.__class__.__name__}).")
    return jsonify({"found": len(urls), "added": added, "errors": failed,
                    "photos": card_photos(db(), uid, [listing["variant_id"]])[listing["variant_id"]]})


@app.patch("/api/ebay/photos/<int:photo_id>")
@login_required
def crop_ebay_photo(photo_id):
    row = own_photo(photo_id)
    if not row:
        return jsonify({"error": "Foto nicht gefunden."}), 404
    crop = parse_crop((request.get_json(force=True) or {}).get("crop"))
    try:
        render_listed(photo_id, crop)
    except PhotoError as error:
        return jsonify({"error": str(error)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Das Original dieses Fotos fehlt. Lade es bitte neu hoch."}), 404
    db().execute("UPDATE ebay_photos SET crop=?,eps_url='',eps_uploaded_at='',updated_at=? WHERE id=?", (json.dumps(crop), now_iso(), photo_id))
    db().commit()
    return jsonify(photo_payload(own_photo(photo_id)))


@app.post("/api/ebay/photos/order")
@login_required
def order_ebay_photos():
    payload = request.get_json(force=True) or {}
    uid, variant_id = user_id(), str(payload.get("variant_id") or "")
    ids = [int(value) for value in payload.get("ids") or [] if str(value).isdigit()]
    known = [row["id"] for row in db().execute("SELECT id FROM ebay_photos WHERE user_id=? AND variant_id=? ORDER BY position,id", (uid, variant_id))]
    order = [photo_id for photo_id in ids if photo_id in known] + [photo_id for photo_id in known if photo_id not in ids]
    for position, photo_id in enumerate(order):
        db().execute("UPDATE ebay_photos SET position=? WHERE id=?", (position, photo_id))
    db().commit()
    return jsonify(card_photos(db(), uid, [variant_id])[variant_id])


@app.delete("/api/ebay/photos/<int:photo_id>")
@login_required
def delete_ebay_photo(photo_id):
    if not own_photo(photo_id):
        return jsonify({"error": "Foto nicht gefunden."}), 404
    db().execute("DELETE FROM ebay_photos WHERE id=?", (photo_id,))
    db().commit()
    photo_file(photo_id).unlink(missing_ok=True)
    photo_file(photo_id, original=True).unlink(missing_ok=True)
    return jsonify({"deleted": True})


@app.get("/api/ebay/photos/<int:photo_id>/<kind>")
@login_required
def ebay_photo_image(photo_id, kind):
    if kind not in ("image", "original") or not own_photo(photo_id):
        return jsonify({"error": "Foto nicht gefunden."}), 404
    path = photo_file(photo_id, original=kind == "original")
    if not path.is_file():
        return jsonify({"error": "Foto nicht gefunden."}), 404
    response = send_file(path, mimetype="image/jpeg", max_age=0)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable" if kind == "image" and request.args.get("v") else "private, no-cache"
    return response
