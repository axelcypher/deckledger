"""What a user owns: changing quantities and the collection view."""

import json
import re

from flask import jsonify, request

from .config import jload, now_iso
from .games import matches_catalog_filters, rarity_rank
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
from .catalog import query_matches_row


@app.post("/api/collection")
@login_required
def update_collection():
    payload = request.get_json(force=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ungültige Anfrage."}), 400
    uid = user_id()
    # Read-modify-write: two quick clicks arrive as two requests on two threads. Without taking
    # the write lock before reading, both read the same old quantity and the second overwrites
    # the first (or both try to insert the row and one fails).
    db().execute("BEGIN IMMEDIATE")
    # An offline client cannot tell "never arrived" from "arrived, answer lost" and sends the
    # change again. With a request_id the repeat returns the first answer instead of counting twice.
    request_id = str(payload.get("request_id") or "")[:64]
    if request_id:
        applied = db().execute("SELECT response FROM applied_requests WHERE user_id=? AND request_id=?", (uid, request_id)).fetchone()
        if applied:
            return jsonify(jload(applied["response"], {}))
    variant_id = payload.get("variant_id")
    if not db().execute("SELECT 1 FROM variants WHERE id=?", (variant_id,)).fetchone():
        return jsonify({"error": "Diese Karte gibt es im Katalog nicht (mehr)."}), 404
    condition = payload.get("condition", "Near Mint")
    is_graded = 1 if payload.get("is_graded") else 0
    grade_label = (payload.get("grade_label") or "").strip() if is_graded else ""
    row_sql = "SELECT * FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=? AND is_graded=? AND grade_label=?"
    existing = db().execute(row_sql, (uid, variant_id, condition, is_graded, grade_label)).fetchone()
    try:
        delta = int(payload.get("delta", 0))
        if delta < 0 and payload.get("any_condition") and not is_graded and not (existing and existing["quantity"]):
            # The card tile shows one total across conditions but only knows "Near Mint". Its minus
            # button takes from whichever ungraded condition actually holds a copy.
            other = db().execute(
                "SELECT * FROM collection_entries WHERE user_id=? AND variant_id=? AND is_graded=0 AND quantity>0 ORDER BY quantity DESC,condition LIMIT 1",
                (uid, variant_id),
            ).fetchone()
            if other:
                existing, condition = other, other["condition"]
        before = existing["quantity"] if existing else 0
        quantity = max(0, int(payload.get("quantity", before + delta)))
        if "price_override" in payload:
            raw_override = payload["price_override"]
            price_override = float(raw_override) if raw_override not in (None, "") else None
        else:
            price_override = existing["price_override"] if existing else None
    except (TypeError, ValueError):
        return jsonify({"error": "Menge und Preis müssen Zahlen sein."}), 400
    stamp = now_iso()
    if quantity == 0:
        db().execute(
            "DELETE FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=? AND is_graded=? AND grade_label=?",
            (uid, variant_id, condition, is_graded, grade_label),
        )
    elif existing:
        db().execute(
            "UPDATE collection_entries SET quantity=?,notes=COALESCE(?,notes),price_override=?,last_added_at=? WHERE id=?",
            (quantity, payload.get("notes"), price_override, stamp if quantity > before else existing["last_added_at"], existing["id"]),
        )
    else:
        db().execute(
            "INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes,is_graded,grade_label,price_override,created_at,last_added_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, variant_id, condition, quantity, payload.get("notes"), is_graded, grade_label, price_override, stamp, stamp),
        )
    result = {"variant_id": variant_id, "condition": condition, "before": before, "quantity": quantity}
    if request_id:
        db().execute("DELETE FROM applied_requests WHERE created_at<datetime('now','-30 days')")
        db().execute(
            "INSERT INTO applied_requests(user_id,request_id,response,created_at) VALUES(?,?,?,datetime('now'))",
            (uid, request_id, json.dumps(result)),
        )
    db().commit()
    return jsonify(result)


@app.get("/api/collection/entries/<variant_id>")
@login_required
def collection_entries_for_variant(variant_id):
    rows = db().execute(
        "SELECT condition,quantity,notes,is_graded,grade_label,price_override FROM collection_entries WHERE user_id=? AND variant_id=? ORDER BY is_graded,condition",
        (user_id(), variant_id),
    ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.get("/api/collection")
@login_required
def collection_browser():
    game_id=request.args.get("game_id"); q=request.args.get("q","").strip().lower(); set_id=request.args.get("set_id","")
    language=request.args.get("language","all"); rarity=request.args.get("rarity",""); finish=request.args.get("finish","")
    mode=request.args.get("mode","all"); sort=request.args.get("sort","number")
    selected_rarities=[value for value in request.args.get("rarities","").split(",") if value]
    selected_costs=[value for value in request.args.get("costs","").split(",") if value.isdigit()]
    selected_colors=[value for value in request.args.get("colors","").split(",") if value]
    inkwell=request.args.get("inkwell","")
    rows=db().execute(f"""SELECT v.id variant_id,v.variant_code,v.finish,v.is_parallel,v.source_type,p.id printing_id,
      i.id identity_id,i.canonical_name,i.rules_text,i.card_type,i.attributes identity_attrs,p.collector_number,p.language,p.rarity,p.attributes printing_attrs,
      s.id set_id,s.name set_name,s.code set_code,s.release_date,g.accent,
      SUM(c.quantity) quantity,MAX(c.condition) condition,
      COALESCE(SUM(c.quantity*COALESCE({latest_price_sql('v')},0)),0) value,{latest_price_sql('v')} price,
      CASE WHEN EXISTS(SELECT 1 FROM named_watchlist_entries nwe JOIN named_watchlists nw ON nw.id=nwe.list_id WHERE nwe.variant_id=v.id AND nw.user_id=?) THEN 1 ELSE 0 END watchlisted
      FROM collection_entries c JOIN variants v ON v.id=c.variant_id JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
      WHERE c.user_id=? AND v.game_id=? AND c.quantity>0 GROUP BY v.id""",(user_id(),user_id(),game_id)).fetchall()
    cards=[]
    for item in rows:
        r=dict(item);r["identity_attrs"]=jload(r["identity_attrs"],{})
        if q and not query_matches_row(q,r):continue
        if set_id and r["set_id"]!=set_id:continue
        if language!="all" and r["language"]!=language:continue
        if rarity and r["rarity"]!=rarity:continue
        if not matches_catalog_filters(game_id,r["rarity"],r["identity_attrs"],selected_rarities,selected_costs,selected_colors,inkwell):continue
        if finish and r["finish"]!=finish:continue
        if mode=="duplicates" and r["quantity"]<2:continue
        if mode=="watchlisted" and not r["watchlisted"]:continue
        r.update({"variants":[dict(r)],"variant_count":1,"owned_variants":1})
        cards.append(r)
    sorters={"number":lambda c:[int(x) if x.isdigit() else x for x in re.split(r"(\d+)",c["collector_number"])],"name":lambda c:c["canonical_name"],"set":lambda c:(c["release_date"],c["collector_number"]),"rarity":lambda c:(rarity_rank(game_id,c["rarity"]),c["collector_number"]),"value":lambda c:-c["value"],"quantity":lambda c:-c["quantity"]}
    cards.sort(key=sorters.get(sort,sorters["number"]))
    sets=[dict(r) for r in db().execute("SELECT id,code,name FROM sets WHERE game_id=? ORDER BY release_date DESC",(game_id,))]
    stats={"variants":len(cards),"copies":sum(c["quantity"] for c in cards),"value":round(sum((c["value"] or 0) for c in cards),2)}
    if request.args.get("stats_only"):return jsonify({"stats":stats})
    return jsonify({"cards":cards,"sets":sets,"stats":stats})
