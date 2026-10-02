"""Trade / sale sheets: storage, sorting and handing cards to the renderer."""

import io
import re

from flask import Response, jsonify, request

import sheet_render

from .config import now_iso
from .games import RARITY_FALLBACK_RANK, rarity_rank
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
from .images import card_image
from .catalog import natural_code_key


# ---- Trade / sale sheets ---------------------------------------------------------------------
# A sheet is a named selection of cards (with a quantity and an optional short label each) that
# is rendered as one or more images for a "want to sell" / "want to trade" post. Layout and
# drawing live in sheet_render.py; this part is storage, sorting and handing over card images.
SHEET_KINDS = ("WTS", "WTT")
SHEET_SORTS = ("number", "rarity")
SHEET_CARD_LIMIT = 400


def own_sheet(sheet_id):
    return db().execute("SELECT * FROM trade_sheets WHERE id=? AND user_id=?", (sheet_id, user_id())).fetchone()


def sheet_cards(sheet):
    rows = [dict(row) for row in db().execute(
        f"""SELECT e.variant_id,e.quantity,e.label,v.finish,v.variant_code,v.is_parallel,v.game_id,v.attributes variant_attributes,
              i.id identity_id,i.canonical_name,p.collector_number,p.language,p.rarity,
              s.code set_code,s.name set_name,s.release_date,{latest_price_sql('v')} price,
              COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=v.id),0) owned
            FROM trade_sheet_cards e JOIN variants v ON v.id=e.variant_id JOIN printings p ON p.id=v.printing_id
              JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
            WHERE e.sheet_id=?""", (sheet["user_id"], sheet["id"])
    )]

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
    for card in cards:
        finish = "" if card["finish"] in ("Normal", "standard") else f', {card["finish"]}'
        price = card["label"] or (f'{card["price"]:.2f} €'.replace(".", ",") if card["price"] is not None and sheet["kind"] == "WTS" else "")
        lines.append(f'* {card["quantity"]}x **{card["canonical_name"]}** ({card["set_code"]} {card["collector_number"]}, {card["language"]}{finish})' + (f" – {price}" if price else ""))
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
        "backgrounds": [{"id": key, "label": value[0], "top": value[1], "bottom": value[2], "accent": value[3], "glow": value[4]} for key, value in sheet_render.BACKGROUNDS.items()],
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
            "background": p.get("background") if p.get("background") in sheet_render.BACKGROUNDS else sheet["background"],
            "sort": p.get("sort") if p.get("sort") in SHEET_SORTS else sheet["sort"],
            "layout": p["layout"] if p.get("layout") == "auto" or sheet_render.parse_layout(p.get("layout")) else sheet["layout"],
        }
        db().execute(
            "UPDATE trade_sheets SET name=?,subtitle=?,kind=?,background=?,sort=?,layout=?,updated_at=? WHERE id=?",
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
                      "number": card["collector_number"], "quantity": card["quantity"], "label": card["label"],
                      "holo": sheet_render.is_holo(card["finish"], card["variant_code"], card["rarity"], card["game_id"], card["is_parallel"])})
    image = sheet_render.render_page(
        tiles, columns, rows, background=sheet["background"], kind=sheet["kind"], title=sheet["name"],
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


_sheet_swatches = {}


@app.get("/api/trade-sheets/backgrounds/<name>.jpg")
@login_required
def trade_sheet_background(name):
    if name not in sheet_render.BACKGROUNDS:
        return Response(status=404)
    if name not in _sheet_swatches:
        buffer = io.BytesIO()
        sheet_render.swatch(name).save(buffer, format="JPEG", quality=85)
        _sheet_swatches[name] = buffer.getvalue()
    return Response(_sheet_swatches[name], mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
