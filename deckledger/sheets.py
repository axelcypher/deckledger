"""Trade / sale sheets: storage, sorting and handing cards to the renderer."""

import io
import re
from pathlib import Path

from flask import Response, jsonify, request

import sheet_render

from .config import SHEET_BACKGROUND_DIR, now_iso
from .games import RARITY_FALLBACK_RANK, game as game_rules, rarity_rank
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
from .images import card_image
from .catalog import natural_code_key


# ---- Trade / sale sheets ---------------------------------------------------------------------
# A sheet is a named selection of cards (with a quantity and an optional short label each) that
# is rendered as one or more images for a "want to sell" / "want to trade" post. Layout and
# drawing live in sheet_render.py; this part is storage, sorting and handing over card images.
# A sheet shows either what is offered -- for sale (WTS), for trade (WTT) or both -- or what is
# looked for -- to buy (WTB), to trade for (WTTF) or both. The two sides are separate sheets:
# one picture cannot say which of its cards are on offer and which are wanted.
SHEET_KINDS = ("WTS", "WTT", "WTS/WTT", "WTB", "WTTF", "WTB/WTTF")
WANTED_SHEET_KINDS = ("WTB", "WTTF", "WTB/WTTF")
SHEET_SORTS = ("number", "rarity")
# How many copies of a variant (v.id) the user's reserved deals have spoken for (deckledger/deals.py).
RESERVED_SQL = """(SELECT COALESCE(SUM(rc.quantity),0) FROM deal_cards rc JOIN deals rd ON rd.id=rc.deal_id
  WHERE rd.user_id=? AND rd.status='reserved' AND rc.side='give' AND rc.variant_id=v.id)"""
SHEET_CARD_LIMIT = 400


def scout_communities(names):
    """A list of community names as it is stored on a sheet: lower case, comma separated."""
    names = names if isinstance(names, list) else []
    return ",".join(sorted({str(name).lower() for name in names if re.fullmatch(r"[A-Za-z0-9_]{2,21}", str(name))}))[:400]


def own_sheet(sheet_id):
    return db().execute("SELECT * FROM trade_sheets WHERE id=? AND user_id=?", (sheet_id, user_id())).fetchone()


def sheet_cards(sheet):
    rows = [dict(row) for row in db().execute(
        f"""SELECT e.variant_id,e.quantity,e.label,v.finish,v.variant_code,v.is_parallel,v.game_id,v.attributes variant_attributes,
              i.id identity_id,i.canonical_name,p.collector_number,p.language,p.rarity,
              s.code set_code,s.name set_name,s.release_date,{latest_price_sql('v')} price,
              COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=v.id),0) owned,
              {RESERVED_SQL} reserved
            FROM trade_sheet_cards e JOIN variants v ON v.id=e.variant_id JOIN printings p ON p.id=v.printing_id
              JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
            WHERE e.sheet_id=?""", (sheet["user_id"], sheet["user_id"], sheet["id"])
    )]
    for row in rows:
        # Reserved is what a sheet of offers shows; no more copies than it lists can be.
        row["reserved"] = 0 if sheet["kind"] in WANTED_SHEET_KINDS else min(row["reserved"], row["quantity"])

    def by_number(row):
        return (row["release_date"] or "", natural_code_key(row["set_code"]), natural_code_key(row["collector_number"]),
                rarity_rank(row["game_id"], row["rarity"]), row["finish"], row["variant_id"])

    def by_rarity(row):
        rank = rarity_rank(row["game_id"], row["rarity"])
        # Rarest first; rarities without a known rank (promos, tokens) go after the ranked ones.
        return (rank == RARITY_FALLBACK_RANK, -rank, *by_number(row))

    rows.sort(key=by_rarity if sheet["sort"] == "rarity" else by_number)
    return rows


def sheet_text(sheet, cards):
    """The list that goes into the post next to the images, as Reddit-flavoured Markdown."""
    lines = [f'**[{sheet["kind"]}] {sheet["name"]}**', ""]
    # The language is only worth a mention where a game is printed in more than one.
    name_language = len(game_rules(sheet["game_id"]).languages) != 1
    for card in cards:
        details = [f'{card["set_code"]} {card["collector_number"]}']
        if name_language:
            details.append(card["language"])
        if card["finish"] not in ("Normal", "standard"):
            details.append(card["finish"])
        price = card["label"] or (f'{card["price"]:.2f} €'.replace(".", ",") if card["price"] is not None and "WTS" in sheet["kind"].split("/") else "")
        reserved = "" if not card["reserved"] else " – reserved" if card["reserved"] >= card["quantity"] else f' – {card["reserved"]} reserved'
        lines.append(f'* {card["quantity"]}x **{card["canonical_name"]}** ({", ".join(details)})' + (f" – {price}" if price else "") + reserved)
    return "\n".join(lines) + "\n"


def sheet_payload(sheet):
    cards = sheet_cards(sheet)
    pages = [{"columns": columns, "rows": rows, "count": count} for columns, rows, count in sheet_render.paginate(len(cards), sheet["layout"])]
    for card in cards:
        card.pop("variant_attributes", None)
    return {"sheet": dict(sheet), "cards": cards, "pages": pages, "text": sheet_text(sheet, cards)}


@app.get("/api/trade-sheets/options")
@login_required
def trade_sheet_options():
    return jsonify({
        "kinds": SHEET_KINDS,
        "backgrounds": [{"id": key, "label": value[0], "top": value[1], "bottom": value[2], "accent": value[3], "glow": value[4]} for key, value in sheet_render.BACKGROUNDS.items()] + [
            {"id": f'custom-{row["id"]}', "label": row["name"], "accent": row["accent"], "custom": True}
            for row in db().execute("SELECT id,name,accent FROM sheet_backgrounds WHERE user_id=? ORDER BY id", (user_id(),))
        ],
        "layouts": ["auto"] + [f"{columns}x{rows}" for columns, rows in sheet_render.LAYOUTS if columns * rows > 1],
    })


@app.route("/api/trade-sheets", methods=["GET", "POST"])
@login_required
def trade_sheets():
    if request.method == "POST":
        p = request.get_json(force=True)
        if not isinstance(p, dict):
            return jsonify({"error": "Ungültige Anfrage."}), 400
        if not db().execute("SELECT 1 FROM games WHERE id=?", (p.get("game_id"),)).fetchone():
            return jsonify({"error": "Spiel nicht gefunden"}), 404
        kind = p.get("kind") if p.get("kind") in SHEET_KINDS else "WTS"
        stamp = now_iso()
        cur = db().execute(
            "INSERT INTO trade_sheets(user_id,game_id,name,kind,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id(), p["game_id"], str(p.get("name") or "").strip()[:80] or "Neues Sheet", kind, stamp, stamp),
        )
        db().commit()
        return jsonify({"id": cur.lastrowid}), 201
    rows = db().execute(
        """SELECT t.*,COUNT(e.id) card_count,COALESCE(SUM(e.quantity),0) copies
           FROM trade_sheets t LEFT JOIN trade_sheet_cards e ON e.sheet_id=t.id
           WHERE t.user_id=? AND t.game_id=? GROUP BY t.id ORDER BY t.updated_at DESC""", (user_id(), request.args.get("game_id")),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/trade-sheets/<int:sheet_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
def trade_sheet(sheet_id):
    sheet = own_sheet(sheet_id)
    if not sheet:
        return jsonify({"error": "sheet not found"}), 404
    if request.method == "DELETE":
        from .watcher import hand_over_comments   # watcher imports this module
        hand_over_comments(db(), [row[0] for row in db().execute("SELECT id FROM sheet_posts WHERE sheet_id=?", (sheet_id,))])
        db().execute("DELETE FROM trade_sheets WHERE id=?", (sheet_id,))
        db().commit()
        return jsonify({"deleted": True})
    if request.method == "PATCH":
        p = request.get_json(force=True)
        if not isinstance(p, dict):
            return jsonify({"error": "Ungültige Anfrage."}), 400
        values = {
            "name": str(p.get("name", sheet["name"]) or "").strip()[:80] or sheet["name"],
            "subtitle": str(p.get("subtitle", sheet["subtitle"]) or "").strip()[:80],
            "kind": p.get("kind") if p.get("kind") in SHEET_KINDS else sheet["kind"],
            "background": p.get("background") if p.get("background") in sheet_render.BACKGROUNDS or own_background(p.get("background")) else sheet["background"],
            "sort": p.get("sort") if p.get("sort") in SHEET_SORTS else sheet["sort"],
            "layout": p["layout"] if p.get("layout") == "auto" or sheet_render.parse_layout(p.get("layout")) else sheet["layout"],
            # Other people's posts are searched for the sheet's cards (deckledger/watcher.py):
            # whether at all, and in which of the game's communities ('' = all of them).
            "scout": int(bool(p["scout"])) if "scout" in p else sheet["scout"],
            "scout_communities": scout_communities(p["scout_communities"]) if "scout_communities" in p else sheet["scout_communities"],
        }
        db().execute(
            "UPDATE trade_sheets SET name=?,subtitle=?,kind=?,background=?,sort=?,layout=?,scout=?,scout_communities=?,updated_at=? WHERE id=?",
            (*values.values(), now_iso(), sheet_id),
        )
        db().commit()
        sheet = own_sheet(sheet_id)
    return jsonify(sheet_payload(sheet))


@app.post("/api/trade-sheets/<int:sheet_id>/cards")
@login_required
def update_trade_sheet_cards(sheet_id):
    """One entry ({variant_id, delta | quantity, label}) or several ({entries: [...]}, used to take
    over a whole list at once). A quantity of 0 removes the card from the sheet."""
    p = request.get_json(force=True)
    if not isinstance(p, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    db().execute("BEGIN IMMEDIATE")  # read-modify-write, see update_collection
    sheet = own_sheet(sheet_id)
    if not sheet:
        return jsonify({"error": "sheet not found"}), 404
    entries = p["entries"] if isinstance(p.get("entries"), list) else [p]
    skipped = 0
    for entry in entries:
        variant = db().execute("SELECT game_id FROM variants WHERE id=?", (entry.get("variant_id"),)).fetchone()
        if not variant or variant["game_id"] != sheet["game_id"]:
            if len(entries) == 1:
                return jsonify({"error": "Diese Karte gehört nicht zu diesem Spiel oder existiert nicht (mehr)."}), 400
            skipped += 1
            continue
        existing = db().execute("SELECT * FROM trade_sheet_cards WHERE sheet_id=? AND variant_id=?", (sheet_id, entry["variant_id"])).fetchone()
        before = existing["quantity"] if existing else 0
        try:
            quantity = max(0, min(99, int(entry.get("quantity", before + int(entry.get("delta", 0))))))
        except (TypeError, ValueError):
            return jsonify({"error": "Die Menge muss eine Zahl sein."}), 400
        label = str(entry.get("label", existing["label"] if existing else "") or "").strip()[:24]
        if quantity == 0:
            db().execute("DELETE FROM trade_sheet_cards WHERE sheet_id=? AND variant_id=?", (sheet_id, entry["variant_id"]))
        elif existing:
            db().execute("UPDATE trade_sheet_cards SET quantity=?,label=? WHERE id=?", (quantity, label, existing["id"]))
        else:
            if db().execute("SELECT COUNT(*) FROM trade_sheet_cards WHERE sheet_id=?", (sheet_id,)).fetchone()[0] >= SHEET_CARD_LIMIT:
                return jsonify({"error": f"Ein Sheet fasst höchstens {SHEET_CARD_LIMIT} verschiedene Karten."}), 400
            db().execute("INSERT INTO trade_sheet_cards(sheet_id,variant_id,quantity,label) VALUES(?,?,?,?)", (sheet_id, entry["variant_id"], quantity, label))
    db().execute("UPDATE trade_sheets SET updated_at=? WHERE id=?", (now_iso(), sheet_id))
    db().commit()
    return jsonify({**sheet_payload(own_sheet(sheet_id)), "skipped": skipped})


@app.get("/api/trade-sheets/<int:sheet_id>/image/<int:page>.<fmt>")
@login_required
def trade_sheet_image(sheet_id, page, fmt):
    sheet = own_sheet(sheet_id)
    if not sheet or fmt not in ("jpg", "png"):
        return jsonify({"error": "sheet not found"}), 404
    cards = sheet_cards(sheet)
    pages = sheet_render.page_slices(cards, sheet["layout"])
    if not 1 <= page <= len(pages):
        return jsonify({"error": "Diese Seite gibt es nicht."}), 404
    columns, rows, on_page = pages[page - 1]
    try:
        scale = min(1.0, max(0.2, float(request.args.get("scale", 1))))
    except ValueError:
        scale = 1.0
    tiles = []
    for card in on_page:
        image_path = None
        try:
            cached = card_image(card, card["variant_id"])
            image_path = cached[0] if cached else None
        except Exception as error:  # an unreachable image source must not fail the whole sheet
            app.logger.warning("Sheet image for %s unavailable: %s", card["variant_id"], error)
        tiles.append({"image_path": image_path, "name": card["canonical_name"], "set_code": card["set_code"],
                      "number": card["collector_number"], "quantity": card["quantity"], "label": card["label"], "reserved": card["reserved"],
                      "holo": sheet_render.is_holo(card["finish"], card["variant_code"], card["rarity"], card["game_id"], card["is_parallel"])})
    image = sheet_render.render_page(
        tiles, columns, rows, **background_for(sheet), kind=sheet["kind"], title=sheet["name"],
        subtitle=sheet["subtitle"], page=(page, len(pages)), scale=scale * sheet_render.scale_for(columns),
    )
    buffer = io.BytesIO()
    if fmt == "png":
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
    else:
        image.convert("RGB").save(buffer, format="JPEG", quality=92, subsampling=0)
    response = Response(buffer.getvalue(), mimetype="image/png" if fmt == "png" else "image/jpeg")
    response.headers["Cache-Control"] = "private, no-store"
    if request.args.get("download"):
        name = re.sub(r"[^A-Za-z0-9-]+", "-", f'{sheet["kind"]}-{sheet["name"]}').strip("-").lower() or "sheet"
        suffix = f"-{page}" if len(pages) > 1 else ""
        response.headers["Content-Disposition"] = f"attachment; filename={name}{suffix}.{fmt}"
    return response


# ---- Backgrounds ------------------------------------------------------------------------------
# The drawn mats ship with the app (sheet_render.BACKGROUNDS). A user can add pictures of their
# own; a sheet names one of those as "custom-<id>".
BACKGROUND_UPLOAD_LIMIT = 25 * 1024 * 1024
BACKGROUNDS_PER_USER = 24
_sheet_swatches = {}


def own_background(name, uid=None):
    """The row of an uploaded background this account may use, or None."""
    match = re.fullmatch(r"custom-(\d+)", str(name or ""))
    if not match:
        return None
    return db().execute("SELECT * FROM sheet_backgrounds WHERE id=? AND user_id=?", (int(match.group(1)), uid or user_id())).fetchone()


def background_file(row):
    return SHEET_BACKGROUND_DIR / f'{row["id"]}.jpg'


def background_for(sheet):
    """What render_page needs for a sheet's background. A custom one that is gone (deleted, or
    its file lost) falls back to the default mat."""
    row = own_background(sheet["background"], sheet["user_id"])
    if row and background_file(row).is_file():
        return {"background_image": background_file(row), "accent": row["accent"]}
    return {"background": sheet["background"] if sheet["background"] in sheet_render.BACKGROUNDS else sheet_render.DEFAULT_BACKGROUND}


def remove_user_backgrounds(uid):
    """Rows and files of an account that is being deleted."""
    for row in db().execute("SELECT id FROM sheet_backgrounds WHERE user_id=?", (uid,)).fetchall():
        background_file(row).unlink(missing_ok=True)
    db().execute("DELETE FROM sheet_backgrounds WHERE user_id=?", (uid,))


@app.post("/api/trade-sheets/backgrounds")
@login_required
def upload_trade_sheet_background():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Keine Datei übermittelt."}), 400
    if db().execute("SELECT COUNT(*) FROM sheet_backgrounds WHERE user_id=?", (user_id(),)).fetchone()[0] >= BACKGROUNDS_PER_USER:
        return jsonify({"error": f"Es sind höchstens {BACKGROUNDS_PER_USER} eigene Hintergründe möglich. Lösche erst einen."}), 400
    payload = file.stream.read(BACKGROUND_UPLOAD_LIMIT + 1)
    if len(payload) > BACKGROUND_UPLOAD_LIMIT:
        return jsonify({"error": "Das Bild ist größer als 25 MB."}), 400
    name = str(request.form.get("name") or Path(file.filename).stem).strip()[:40] or "Eigener Hintergrund"
    SHEET_BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    background_id = db().execute(
        "INSERT INTO sheet_backgrounds(user_id,name,accent,created_at) VALUES(?,?,'',?)", (user_id(), name, now_iso()),
    ).lastrowid
    target = SHEET_BACKGROUND_DIR / f"{background_id}.jpg"
    try:
        accent = sheet_render.prepare_photo(io.BytesIO(payload), target)
    except Exception:  # not an image, truncated, a format Pillow cannot read, a decompression bomb
        db().rollback()
        target.unlink(missing_ok=True)
        return jsonify({"error": "Die Datei ist kein Bild, das sich lesen lässt (JPEG, PNG oder WebP)."}), 400
    db().execute("UPDATE sheet_backgrounds SET accent=? WHERE id=?", (accent, background_id))
    db().commit()
    return jsonify({"id": f"custom-{background_id}", "label": name, "custom": True}), 201


@app.delete("/api/trade-sheets/backgrounds/<name>")
@login_required
def delete_trade_sheet_background(name):
    row = own_background(name)
    if not row:
        return jsonify({"error": "Hintergrund nicht gefunden."}), 404
    # Sheets that used it go back to the default mat.
    db().execute("UPDATE trade_sheets SET background=?,updated_at=? WHERE user_id=? AND background=?",
                 (sheet_render.DEFAULT_BACKGROUND, now_iso(), user_id(), name))
    db().execute("DELETE FROM sheet_backgrounds WHERE id=?", (row["id"],))
    db().commit()
    background_file(row).unlink(missing_ok=True)
    _sheet_swatches.pop(name, None)
    return jsonify({"deleted": True})


@app.get("/api/trade-sheets/backgrounds/<name>.jpg")
@login_required
def trade_sheet_background(name):
    row = own_background(name)
    if not row and name not in sheet_render.BACKGROUNDS:
        return Response(status=404)
    if name not in _sheet_swatches:
        if row and not background_file(row).is_file():
            return Response(status=404)
        swatch = sheet_render.photo_background((240, 150), background_file(row)).convert("RGB") if row else sheet_render.swatch(name)
        buffer = io.BytesIO()
        swatch.save(buffer, format="JPEG", quality=85)
        _sheet_swatches[name] = buffer.getvalue()
    return Response(_sheet_swatches[name], mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
