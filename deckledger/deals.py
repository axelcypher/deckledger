"""Deals: selling, buying and trading cards, and the record of it.

A sheet is the shop window; a deal is the business done -- with anybody, anywhere: the partner
and the platform are free text, a link is optional. A deal has two sides, what goes out ("give")
and what comes in ("get"), each with cards at a price and, beside them, money and shipping.

Its status is what moves the collection:
  open       nothing is booked.
  reserved   the cards going out are spoken for; sheets show them as reserved.
  done       the cards going out leave the collection and the sheets that offer them.
             The cards coming in are on their way: they enter the collection (and leave the
             sheets of wanted cards) only when their arrival is confirmed -- `receive`.
  cancelled  before 'done' nothing was booked; after it, the booking is undone.
A deal that is done is the record and is no longer edited.
"""

from flask import jsonify, request

from .config import now_iso
from . import search
from .web import app, db, login_required, user_id
from .sheets import RESERVED_SQL, WANTED_SHEET_KINDS

DEAL_STATES = ("open", "reserved", "done", "cancelled")
SIDES = ("give", "get")
EDITABLE = ("open", "reserved")
# status now -> statuses it may change to
TRANSITIONS = {"open": ("reserved", "done", "cancelled"), "reserved": ("open", "done", "cancelled"), "done": ("cancelled",), "cancelled": ("open",)}
DEAL_CARD_LIMIT = 200
MAX_AMOUNT = 1_000_000


def own_deal(deal_id):
    return db().execute("SELECT * FROM deals WHERE id=? AND user_id=?", (deal_id, user_id())).fetchone()


def amount(value, default=0.0):
    """A sum of money from a request: a number from 0 up, rounded to cents. Raises ValueError."""
    if value in (None, ""):
        return default
    number = round(float(str(value).replace(",", ".")), 2)
    if not 0 <= number <= MAX_AMOUNT:
        raise ValueError("amount out of range")
    return number


def owned_quantity(uid, variant_id):
    return db().execute("SELECT COALESCE(SUM(quantity),0) FROM collection_entries WHERE user_id=? AND variant_id=?", (uid, variant_id)).fetchone()[0]


def take_from_collection(uid, variant_id, quantity):
    """Removes up to `quantity` copies, ungraded ones first. Returns how many it found."""
    taken = 0
    rows = db().execute(
        "SELECT * FROM collection_entries WHERE user_id=? AND variant_id=? AND quantity>0 ORDER BY is_graded,quantity DESC,id", (uid, variant_id),
    ).fetchall()
    for row in rows:
        if quantity <= 0:
            break
        part = min(quantity, row["quantity"])
        if part == row["quantity"]:
            db().execute("DELETE FROM collection_entries WHERE id=?", (row["id"],))
        else:
            db().execute("UPDATE collection_entries SET quantity=quantity-? WHERE id=?", (part, row["id"]))
        taken += part
        quantity -= part
    return taken


def add_to_collection(uid, variant_id, quantity, condition="Near Mint", is_graded=0, grade_label=""):
    stamp = now_iso()
    db().execute(
        """INSERT INTO collection_entries(user_id,variant_id,condition,quantity,is_graded,grade_label,created_at,last_added_at) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id,variant_id,condition,is_graded,grade_label) DO UPDATE SET quantity=quantity+excluded.quantity,last_added_at=excluded.last_added_at""",
        (uid, variant_id, condition, quantity, is_graded, grade_label, stamp, stamp),
    )


def lower_on_sheets(uid, variant_id, quantity, wanted):
    """The same stock can stand on several sheets; a copy that is gone (or, for wanted cards,
    has arrived) is one less on each of them."""
    marks = ",".join("?" for _ in WANTED_SHEET_KINDS)
    sheets = f"SELECT id FROM trade_sheets WHERE user_id=? AND kind {'IN' if wanted else 'NOT IN'} ({marks})"
    db().execute(f"UPDATE trade_sheet_cards SET quantity=quantity-? WHERE variant_id=? AND sheet_id IN ({sheets})", (quantity, variant_id, uid, *WANTED_SHEET_KINDS))
    db().execute(f"DELETE FROM trade_sheet_cards WHERE variant_id=? AND quantity<=0 AND sheet_id IN ({sheets})", (variant_id, uid, *WANTED_SHEET_KINDS))


def touch_sheets(uid, variant_ids):
    """A sheet's pictures are cached by its updated_at; a reservation changes what they show."""
    for variant_id in variant_ids:
        db().execute("UPDATE trade_sheets SET updated_at=? WHERE user_id=? AND id IN (SELECT sheet_id FROM trade_sheet_cards WHERE variant_id=?)", (now_iso(), uid, variant_id))


def log_event(deal_id, kind, detail=""):
    db().execute("INSERT INTO deal_events(deal_id,at,kind,detail) VALUES(?,?,?,?)", (deal_id, now_iso(), kind, detail))


def deal_cards(deal):
    """The deal's cards with what the catalogue and the collection say about them now. A card
    the catalogue no longer has is still listed, from what the deal kept."""
    uid = deal["user_id"]
    rows = db().execute(
        f"""SELECT dc.*,v.id known,v.finish,v.variant_code,v.is_parallel,v.game_id,i.id identity_id,p.rarity,p.collector_number,s.code set_code,
              COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=dc.variant_id),0) owned,
              {RESERVED_SQL.replace('v.id', 'dc.variant_id')} reserved
            FROM deal_cards dc LEFT JOIN variants v ON v.id=dc.variant_id LEFT JOIN printings p ON p.id=v.printing_id
              LEFT JOIN card_identities i ON i.id=p.identity_id LEFT JOIN sets s ON s.id=p.set_id
            WHERE dc.deal_id=? ORDER BY dc.side,dc.id""", (uid, uid, deal["id"]),
    ).fetchall()
    cards = []
    for row in rows:
        card = dict(row)
        card["known"] = bool(card["known"])
        # Reserved by other deals: this deal's own reservation is not competition for itself.
        if deal["status"] == "reserved" and card["side"] == "give":
            card["reserved"] -= card["quantity"]
        cards.append(card)
    return cards


def deal_kind(cards):
    gives, gets = any(card["side"] == "give" for card in cards), any(card["side"] == "get" for card in cards)
    return "trade" if gives and gets else "sale" if gives else "purchase" if gets else ""


def deal_payload(deal, with_events=True):
    cards = deal_cards(deal)
    payload = dict(deal) | {
        "cards": cards, "kind": deal_kind(cards),
        # Cards coming in that have not been confirmed as arrived.
        "in_transit": deal["status"] == "done" and not deal["received_at"] and any(card["side"] == "get" for card in cards),
        "value_give": round(sum((card["unit_price"] or 0) * card["quantity"] for card in cards if card["side"] == "give"), 2),
        "value_get": round(sum((card["unit_price"] or 0) * card["quantity"] for card in cards if card["side"] == "get"), 2),
    }
    if with_events:
        payload["events"] = [dict(row) for row in db().execute("SELECT at,kind,detail FROM deal_events WHERE deal_id=? ORDER BY id", (deal["id"],))]
    return payload


def card_snapshot(variant_id, game_id):
    """(name, detail) of a card as the deal keeps it, or None when the catalogue has no such card
    in this game."""
    row = db().execute(
        """SELECT i.canonical_name,s.code,p.collector_number,p.language,v.finish FROM variants v JOIN printings p ON p.id=v.printing_id
           JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id WHERE v.id=? AND v.game_id=?""", (variant_id, game_id),
    ).fetchone()
    if not row:
        return None
    return row["canonical_name"], " · ".join(str(part) for part in (row["code"], row["collector_number"], row["language"], row["finish"]) if part)


def set_card(deal, entry):
    """Adds, changes or (quantity 0) removes one card of a deal. Returns an error text or None."""
    side, variant_id = entry.get("side"), entry.get("variant_id")
    if side not in SIDES:
        return "Unbekannte Seite."
    existing = db().execute("SELECT * FROM deal_cards WHERE deal_id=? AND side=? AND variant_id=?", (deal["id"], side, variant_id)).fetchone()
    try:
        before = existing["quantity"] if existing else 0
        quantity = max(0, min(99, int(entry.get("quantity", before + int(entry.get("delta", 0))))))
        unit_price = amount(entry["unit_price"], None) if "unit_price" in entry else (existing["unit_price"] if existing else None)
    except (TypeError, ValueError):
        return "Menge und Preis müssen Zahlen sein."
    if quantity == 0:
        db().execute("DELETE FROM deal_cards WHERE deal_id=? AND side=? AND variant_id=?", (deal["id"], side, variant_id))
    elif existing:
        db().execute("UPDATE deal_cards SET quantity=?,unit_price=? WHERE id=?", (quantity, unit_price, existing["id"]))
    else:
        snapshot = card_snapshot(variant_id, deal["game_id"])
        if not snapshot:
            return "Diese Karte gehört nicht zu diesem Spiel oder existiert nicht (mehr)."
        if db().execute("SELECT COUNT(*) FROM deal_cards WHERE deal_id=?", (deal["id"],)).fetchone()[0] >= DEAL_CARD_LIMIT:
            return f"Ein Vorgang fasst höchstens {DEAL_CARD_LIMIT} verschiedene Karten."
        db().execute("INSERT INTO deal_cards(deal_id,side,variant_id,quantity,unit_price,name,detail) VALUES(?,?,?,?,?,?,?)",
                     (deal["id"], side, variant_id, quantity, unit_price, *snapshot))
    return None


def header_values(payload, deal=None):
    """The fields of a deal a request may set. Raises ValueError for a sum that is no number."""
    current = dict(deal) if deal else {"partner": "", "platform": "", "url": "", "note": "", "money_in": 0, "money_out": 0, "shipping": 0}
    url = str(payload.get("url", current["url"]) or "").strip()[:500]
    return {
        "partner": str(payload.get("partner", current["partner"]) or "").strip()[:80],
        "platform": str(payload.get("platform", current["platform"]) or "").strip()[:40],
        # Only an address a browser may open as a link is kept as one.
        "url": url if url.startswith(("https://", "http://")) else "",
        "note": str(payload.get("note", current["note"]) or "").strip()[:2000],
        "money_in": amount(payload["money_in"]) if "money_in" in payload else current["money_in"],
        "money_out": amount(payload["money_out"]) if "money_out" in payload else current["money_out"],
        "shipping": amount(payload["shipping"]) if "shipping" in payload else current["shipping"],
    }


# ---- what a change of status books ----------------------------------------------------------------

def book_done(deal):
    """The cards going out leave the collection and the sheets that offer them."""
    short = []
    for card in db().execute("SELECT * FROM deal_cards WHERE deal_id=? AND side='give' ORDER BY id", (deal["id"],)).fetchall():
        taken = take_from_collection(deal["user_id"], card["variant_id"], card["quantity"])
        db().execute("UPDATE deal_cards SET booked=? WHERE id=?", (taken, card["id"]))
        lower_on_sheets(deal["user_id"], card["variant_id"], card["quantity"], wanted=False)
        if taken < card["quantity"]:
            short.append(f'{card["name"]}: {taken} von {card["quantity"]}')
    return "Nicht in der Sammlung: " + "; ".join(short) if short else ""


def book_received(deal):
    """The cards coming in enter the collection and leave the sheets that ask for them."""
    for card in db().execute("SELECT * FROM deal_cards WHERE deal_id=? AND side='get'", (deal["id"],)).fetchall():
        add_to_collection(deal["user_id"], card["variant_id"], card["quantity"])
        db().execute("UPDATE deal_cards SET booked=? WHERE id=?", (card["quantity"], card["id"]))
        lower_on_sheets(deal["user_id"], card["variant_id"], card["quantity"], wanted=True)


def unbook(deal):
    """Undoes what a finished deal booked in the collection. Sheets are left as they are: what
    stands on them is the user's to decide once the cards are back."""
    for card in db().execute("SELECT * FROM deal_cards WHERE deal_id=? AND booked>0", (deal["id"],)).fetchall():
        if card["side"] == "give":
            add_to_collection(deal["user_id"], card["variant_id"], card["booked"])
        else:
            take_from_collection(deal["user_id"], card["variant_id"], card["booked"])
    db().execute("UPDATE deal_cards SET booked=0 WHERE deal_id=?", (deal["id"],))


# ---- API ------------------------------------------------------------------------------------------

@app.route("/api/deals", methods=["GET", "POST"])
@login_required
def deals():
    if request.method == "POST":
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Ungültige Anfrage."}), 400
        if not db().execute("SELECT 1 FROM games WHERE id=?", (payload.get("game_id"),)).fetchone():
            return jsonify({"error": "Spiel nicht gefunden"}), 404
        try:
            values = header_values(payload)
        except (TypeError, ValueError):
            return jsonify({"error": "Beträge müssen Zahlen ab 0 sein."}), 400
        stamp = now_iso()
        cursor = db().execute(
            f"INSERT INTO deals(user_id,game_id,{','.join(values)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in values)},?,?)",
            (user_id(), payload["game_id"], *values.values(), stamp, stamp),
        )
        deal = own_deal(cursor.lastrowid)
        for entry in payload.get("cards") if isinstance(payload.get("cards"), list) else []:
            error = set_card(deal, entry) if isinstance(entry, dict) else "Ungültige Karte."
            if error:
                db().rollback()
                return jsonify({"error": error}), 400
        log_event(deal["id"], "created")
        db().commit()
        return jsonify(deal_payload(deal)), 201

    clauses, values = ["d.user_id=?"], [user_id()]
    if request.args.get("game_id"):
        clauses.append("d.game_id=?")
        values.append(request.args["game_id"])
    rows = db().execute(f"SELECT d.* FROM deals d WHERE {' AND '.join(clauses)} ORDER BY COALESCE(d.completed_at,d.updated_at) DESC,d.id DESC", values).fetchall()
    found = [deal_payload(row, with_events=False) for row in rows]
    status, query = request.args.get("status", ""), request.args.get("q", "").strip()
    if status == "transit":
        found = [deal for deal in found if deal["in_transit"]]
    elif status in DEAL_STATES:
        found = [deal for deal in found if deal["status"] == status]
    if query:
        found = [deal for deal in found if search.matches(query, [(deal["partner"], search.NAME), (deal["platform"], search.DETAIL), (deal["note"], search.TEXT),
                                                                  *((card["name"], search.NAME) for card in deal["cards"])])]
    done = [deal for deal in found if deal["status"] == "done"]
    totals = {
        "count": len(found), "done": len(done),
        "money_in": round(sum(deal["money_in"] for deal in done), 2),
        "money_out": round(sum(deal["money_out"] for deal in done), 2),
        "shipping": round(sum(deal["shipping"] for deal in done), 2),
    }
    totals["balance"] = round(totals["money_in"] - totals["money_out"] - totals["shipping"], 2)
    return jsonify({"deals": found, "totals": totals})


@app.route("/api/deals/<int:deal_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
def deal(deal_id):
    deal = own_deal(deal_id)
    if not deal:
        return jsonify({"error": "deal not found"}), 404
    if request.method == "DELETE":
        if deal["status"] not in ("open", "cancelled"):
            return jsonify({"error": "Nur offene oder abgebrochene Vorgänge lassen sich löschen."}), 409
        db().execute("DELETE FROM deals WHERE id=?", (deal_id,))
        db().commit()
        return jsonify({"deleted": True})
    if request.method == "PATCH":
        payload = request.get_json(force=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Ungültige Anfrage."}), 400
        if deal["status"] not in EDITABLE:
            # The record of a finished deal stands; a note about it can still be added.
            payload = {"note": payload["note"]} if "note" in payload else {}
        try:
            values = header_values(payload, deal)
        except (TypeError, ValueError):
            return jsonify({"error": "Beträge müssen Zahlen ab 0 sein."}), 400
        db().execute(f"UPDATE deals SET {','.join(f'{key}=?' for key in values)},updated_at=? WHERE id=?", (*values.values(), now_iso(), deal_id))
        db().commit()
        deal = own_deal(deal_id)
    return jsonify(deal_payload(deal))


@app.post("/api/deals/<int:deal_id>/cards")
@login_required
def update_deal_cards(deal_id):
    """One card ({side, variant_id, delta | quantity, unit_price}); a quantity of 0 removes it."""
    payload = request.get_json(force=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    db().execute("BEGIN IMMEDIATE")  # read-modify-write, see update_collection
    deal = own_deal(deal_id)
    if not deal:
        return jsonify({"error": "deal not found"}), 404
    if deal["status"] not in EDITABLE:
        return jsonify({"error": "Ein abgeschlossener Vorgang wird nicht mehr geändert."}), 409
    error = set_card(deal, payload)
    if error:
        db().rollback()
        return jsonify({"error": error}), 400
    db().execute("UPDATE deals SET updated_at=? WHERE id=?", (now_iso(), deal_id))
    if deal["status"] == "reserved" and payload.get("side") == "give":
        touch_sheets(deal["user_id"], [payload.get("variant_id")])
    db().commit()
    return jsonify(deal_payload(own_deal(deal_id)))


@app.post("/api/deals/<int:deal_id>/status")
@login_required
def change_deal_status(deal_id):
    target = (request.get_json(force=True) or {}).get("status")
    db().execute("BEGIN IMMEDIATE")
    deal = own_deal(deal_id)
    if not deal:
        return jsonify({"error": "deal not found"}), 404
    if target not in TRANSITIONS.get(deal["status"], ()):
        return jsonify({"error": "Dieser Schritt ist für den Vorgang nicht möglich."}), 409
    gives = [row["variant_id"] for row in db().execute("SELECT variant_id FROM deal_cards WHERE deal_id=? AND side='give'", (deal_id,))]
    has_cards = db().execute("SELECT COUNT(*) FROM deal_cards WHERE deal_id=?", (deal_id,)).fetchone()[0]
    stamp, detail = now_iso(), ""
    if target in ("reserved", "done") and not has_cards:
        return jsonify({"error": "Der Vorgang hat noch keine Karten."}), 400
    if target == "reserved" and not gives:
        return jsonify({"error": "Reservieren lässt sich nur, was du abgibst."}), 400
    if target == "reserved":
        db().execute("UPDATE deals SET status='reserved',reserved_at=?,updated_at=? WHERE id=?", (stamp, stamp, deal_id))
    elif target == "open":
        db().execute("UPDATE deals SET status='open',reserved_at=NULL,completed_at=NULL,received_at=NULL,cancelled_at=NULL,updated_at=? WHERE id=?", (stamp, deal_id))
    elif target == "done":
        detail = book_done(deal)
        db().execute("UPDATE deals SET status='done',completed_at=?,updated_at=? WHERE id=?", (stamp, stamp, deal_id))
    elif target == "cancelled":
        if deal["status"] == "done":
            unbook(deal)
            detail = "Buchung in der Sammlung zurückgenommen"
        db().execute("UPDATE deals SET status='cancelled',cancelled_at=?,received_at=NULL,updated_at=? WHERE id=?", (stamp, stamp, deal_id))
    touch_sheets(deal["user_id"], gives)
    log_event(deal_id, target, detail)
    db().commit()
    return jsonify(deal_payload(own_deal(deal_id)))


@app.post("/api/deals/<int:deal_id>/receive")
@login_required
def receive_deal(deal_id):
    """Confirms that the cards coming in have arrived: only now are they in the collection."""
    db().execute("BEGIN IMMEDIATE")
    deal = own_deal(deal_id)
    if not deal:
        return jsonify({"error": "deal not found"}), 404
    if deal["status"] != "done" or deal["received_at"]:
        return jsonify({"error": "Für diesen Vorgang ist kein Empfang offen."}), 409
    if not db().execute("SELECT 1 FROM deal_cards WHERE deal_id=? AND side='get'", (deal_id,)).fetchone():
        return jsonify({"error": "In diesem Vorgang kommen keine Karten an."}), 400
    book_received(deal)
    stamp = now_iso()
    db().execute("UPDATE deals SET received_at=?,updated_at=? WHERE id=?", (stamp, stamp, deal_id))
    touch_sheets(deal["user_id"], [row["variant_id"] for row in db().execute("SELECT variant_id FROM deal_cards WHERE deal_id=? AND side='get'", (deal_id,))])
    log_event(deal_id, "received")
    db().commit()
    return jsonify(deal_payload(own_deal(deal_id)))


@app.get("/api/variants/<variant_id>/deals")
@login_required
def variant_deals(variant_id):
    """What became of this card in the user's deals, newest first: sold, bought, traded,
    reserved, on its way."""
    rows = db().execute(
        """SELECT d.id deal_id,d.partner,d.platform,d.status,d.completed_at,d.received_at,d.updated_at,dc.side,dc.quantity,dc.unit_price
           FROM deal_cards dc JOIN deals d ON d.id=dc.deal_id
           WHERE d.user_id=? AND dc.variant_id=? AND d.status IN ('reserved','done')
           ORDER BY COALESCE(d.completed_at,d.updated_at) DESC,d.id DESC LIMIT 100""", (user_id(), variant_id),
    ).fetchall()
    return jsonify([dict(row) | {"in_transit": row["status"] == "done" and row["side"] == "get" and not row["received_at"]} for row in rows])
