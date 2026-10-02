"""Named watchlists per game."""

import re
import sqlite3

from flask import Response, jsonify, request

from .config import jload, now_iso
from .games import matches_catalog_filters
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
from .catalog import query_matches_row


@app.post("/api/watchlist")
@login_required
def toggle_watchlist():
    payload = request.get_json(force=True)
    variant_id = payload.get("variant_id")
    list_id = payload.get("list_id")
    if not list_id:
        variant = db().execute("SELECT game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
        if not variant: return jsonify({"error":"variant not found"}), 404
        target = db().execute("SELECT id FROM named_watchlists WHERE user_id=? AND game_id=? ORDER BY is_default DESC,id LIMIT 1", (user_id(),variant["game_id"])).fetchone()
        list_id = target["id"] if target else None
    owned_list = db().execute("SELECT id,game_id FROM named_watchlists WHERE id=? AND user_id=?", (list_id,user_id())).fetchone()
    if not owned_list: return jsonify({"error":"watchlist not found"}), 404
    listed_variant = db().execute("SELECT game_id FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not listed_variant: return jsonify({"error":"variant not found"}), 404
    if listed_variant["game_id"] != owned_list["game_id"]: return jsonify({"error":"Diese Karte gehört zu einem anderen Spiel als die Watchlist."}), 400
    existing = db().execute("SELECT id FROM named_watchlist_entries WHERE list_id=? AND variant_id=?", (list_id,variant_id)).fetchone()
    if existing:
        db().execute("DELETE FROM named_watchlist_entries WHERE id=?", (existing["id"],)); active = False
    else:
        db().execute("INSERT INTO named_watchlist_entries(list_id,variant_id,quantity,created_at) VALUES(?,?,?,?)", (list_id,variant_id,1,now_iso())); active = True
    db().commit()
    return jsonify({"active": active,"list_id":list_id})


@app.patch("/api/watchlists/<int:list_id>/entries/<variant_id>")
@login_required
def update_watchlist_entry(list_id, variant_id):
    """Sets the desired copy count for one watchlist entry (distinct from the owned quantity
    shown elsewhere -- this is "wie viele Exemplare ich mir wünsche", not what's in the
    collection). Clamped to 1..99, same ballpark as a Lorcana playset cap elsewhere in the app;
    there's no real-world reason to want more."""
    owned_list = db().execute("SELECT id FROM named_watchlists WHERE id=? AND user_id=?", (list_id,user_id())).fetchone()
    if not owned_list: return jsonify({"error":"watchlist not found"}), 404
    payload = request.get_json(force=True)
    try: quantity = max(1, min(99, int(payload.get("quantity", 1))))
    except (TypeError, ValueError): return jsonify({"error":"quantity must be a number"}), 400
    cur = db().execute("UPDATE named_watchlist_entries SET quantity=? WHERE list_id=? AND variant_id=?", (quantity,list_id,variant_id))
    if not cur.rowcount: return jsonify({"error":"entry not found"}), 404
    db().commit()
    return jsonify({"saved": True, "quantity": quantity})


@app.post("/api/watchlists/<int:list_id>/entries/move")
@login_required
def move_watchlist_entries(list_id):
    """Bulk-moves selected variants from `list_id` to `target_list_id` (both must belong to the
    current user and the same game -- the UI only ever offers same-game lists, this is just the
    server-side guarantee). A variant already present on the target list is left as-is there
    (its existing desired quantity wins) and simply removed from the source list, rather than
    guessing how to merge two different desired counts."""
    source = db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?", (list_id,user_id())).fetchone()
    if not source: return jsonify({"error":"watchlist not found"}), 404
    payload = request.get_json(force=True)
    target_list_id = payload.get("target_list_id")
    variant_ids = [v for v in (payload.get("variant_ids") or []) if v]
    target = db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?", (target_list_id,user_id())).fetchone()
    if not target: return jsonify({"error":"target watchlist not found"}), 404
    if target["game_id"] != source["game_id"]: return jsonify({"error":"Zielliste gehört zu einem anderen Spiel."}), 400
    if not variant_ids: return jsonify({"error":"no variants selected"}), 400
    moved = 0
    for variant_id in variant_ids:
        entry = db().execute("SELECT quantity FROM named_watchlist_entries WHERE list_id=? AND variant_id=?", (list_id,variant_id)).fetchone()
        if not entry: continue
        db().execute("INSERT OR IGNORE INTO named_watchlist_entries(list_id,variant_id,quantity,created_at) VALUES(?,?,?,?)",
                      (target_list_id,variant_id,entry["quantity"],now_iso()))
        db().execute("DELETE FROM named_watchlist_entries WHERE list_id=? AND variant_id=?", (list_id,variant_id))
        moved += 1
    db().commit()
    return jsonify({"moved": moved})


@app.post("/api/watchlists/<int:list_id>/entries/remove")
@login_required
def remove_watchlist_entries(list_id):
    """Bulk-removes selected variants from one watchlist -- the multi-select complement to the
    single-card toggle in /api/watchlist."""
    owned_list = db().execute("SELECT id FROM named_watchlists WHERE id=? AND user_id=?", (list_id,user_id())).fetchone()
    if not owned_list: return jsonify({"error":"watchlist not found"}), 404
    variant_ids = [v for v in (request.get_json(force=True).get("variant_ids") or []) if v]
    if not variant_ids: return jsonify({"error":"no variants selected"}), 400
    placeholders = ",".join("?" * len(variant_ids))
    cur = db().execute(f"DELETE FROM named_watchlist_entries WHERE list_id=? AND variant_id IN ({placeholders})", (list_id,*variant_ids))
    db().commit()
    return jsonify({"removed": cur.rowcount})


@app.route("/api/watchlists", methods=["GET","POST"])
@login_required
def watchlists():
    if request.method == "POST":
        payload=request.get_json(force=True); game_id=payload.get("game_id"); name=payload.get("name","Neue Watchlist").strip()[:80]
        if not name: return jsonify({"error":"name required"}),400
        try:
            cur=db().execute("INSERT INTO named_watchlists(user_id,game_id,name,is_default,created_at) VALUES(?,?,?,?,?)",(user_id(),game_id,name,0,now_iso()));db().commit()
        except sqlite3.IntegrityError: return jsonify({"error":"Eine Watchlist mit diesem Namen existiert bereits."}),409
        return jsonify({"id":cur.lastrowid,"name":name,"game_id":game_id,"count":0,"value":0}),201
    game_id=request.args.get("game_id")
    # "value" is the cost to actually complete the list -- price times how many copies are
    # *wanted* (nwe.quantity), not one price per distinct variant.
    rows=db().execute(f"""SELECT nw.*,COUNT(nwe.id) count,COALESCE(SUM({latest_price_sql('v')}*nwe.quantity),0) value
      FROM named_watchlists nw LEFT JOIN named_watchlist_entries nwe ON nwe.list_id=nw.id
      LEFT JOIN variants v ON v.id=nwe.variant_id WHERE nw.user_id=? AND nw.game_id=?
      GROUP BY nw.id ORDER BY nw.is_default DESC,nw.created_at""",(user_id(),game_id)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/watchlists/<int:list_id>",methods=["PATCH","DELETE"])
@login_required
def manage_watchlist(list_id):
    row=db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?",(list_id,user_id())).fetchone()
    if not row:return jsonify({"error":"watchlist not found"}),404
    if request.method=="DELETE":
        if row["is_default"]: return jsonify({"error":"Die Standardliste kann nicht gelöscht werden."}),400
        db().execute("DELETE FROM named_watchlists WHERE id=?",(list_id,));db().commit();return jsonify({"deleted":True})
    name=request.get_json(force=True).get("name","").strip()[:80]
    if not name:return jsonify({"error":"name required"}),400
    db().execute("UPDATE named_watchlists SET name=? WHERE id=?",(name,list_id));db().commit();return jsonify({"saved":True})


@app.get("/api/watchlists/<int:list_id>/cards")
@login_required
def watchlist_cards(list_id):
    owned=db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?",(list_id,user_id())).fetchone()
    if not owned:return jsonify({"error":"watchlist not found"}),404
    q=request.args.get("q","").strip(); language=request.args.get("language","all"); set_id=request.args.get("set_id",""); finish=request.args.get("finish",""); sort=request.args.get("sort","added")
    rarity=request.args.get("rarity","")
    selected_rarities=[value for value in request.args.get("rarities","").split(",") if value]
    selected_costs=[value for value in request.args.get("costs","").split(",") if value.isdigit()]
    selected_colors=[value for value in request.args.get("colors","").split(",") if value]
    inkwell=request.args.get("inkwell","")
    rows = db().execute(
        f"""SELECT v.id variant_id,v.finish,i.id identity_id,i.canonical_name,i.rules_text,p.collector_number,p.language,
            p.rarity,i.card_type,i.attributes identity_attrs,p.attributes printing_attrs,s.id set_id,s.name set_name,g.id game_id,g.short_name game_name,g.accent,{latest_price_sql('v')} price,
            COALESCE(SUM(c.quantity),0) quantity,nwe.quantity desired_quantity,nwe.created_at
            FROM named_watchlist_entries nwe JOIN variants v ON v.id=nwe.variant_id JOIN printings p ON p.id=v.printing_id
            JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
            LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=? WHERE nwe.list_id=? GROUP BY v.id""", (user_id(),list_id)
    ).fetchall()
    result=[]
    for row in rows:
        item=dict(row);item["identity_attrs"]=jload(item["identity_attrs"],{})
        if rarity and item["rarity"]!=rarity:continue
        if not matches_catalog_filters(item["game_id"],item["rarity"],item["identity_attrs"],selected_rarities,selected_costs,selected_colors,inkwell):continue
        result.append(item)
    if q: result=[r for r in result if query_matches_row(q.lower(),r)]
    if language!="all":result=[r for r in result if r["language"]==language]
    if set_id:result=[r for r in result if r["set_id"]==set_id]
    if finish:result=[r for r in result if r["finish"]==finish]
    sorters={"name":lambda r:r["canonical_name"],"number":lambda r:r["collector_number"],"price_high":lambda r:-(r["price"] or 0),"price_low":lambda r:r["price"] if r["price"] is not None else float("inf"),"added":lambda r:r["created_at"]}
    result.sort(key=sorters.get(sort,sorters["added"]),reverse=sort=="added")
    return jsonify({"list":dict(owned),"cards":result})


@app.get("/api/watchlists/<int:list_id>/export.txt")
@login_required
def export_watchlist(list_id):
    """Plain-text export of one watchlist -- same "<qty>x <name>" format as the deck missing-
    list export (export_deck_missing_list), aggregated by name and sorted alphabetically. A
    watchlist is already a want-to-buy list, so unlike the deck exports there's no separate
    "list vs. missing-only" distinction -- one format, straight from each entry's own quantity
    column."""
    watchlist=db().execute("SELECT * FROM named_watchlists WHERE id=? AND user_id=?",(list_id,user_id())).fetchone()
    if not watchlist:return jsonify({"error":"watchlist not found"}),404
    rows=db().execute(
        """SELECT i.canonical_name canonical_name,nwe.quantity quantity
           FROM named_watchlist_entries nwe JOIN variants v ON v.id=nwe.variant_id
           JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
           WHERE nwe.list_id=?""",(list_id,)
    ).fetchall()
    aggregated={}
    for row in rows:
        aggregated[row["canonical_name"]]=aggregated.get(row["canonical_name"],0)+row["quantity"]
    lines=[f"{qty}x {name}" for name,qty in sorted(aggregated.items())]
    body="\n".join(lines)+"\n" if lines else "# Diese Watchlist enthält noch keine Karten.\n"
    filename=re.sub(r"[^A-Za-z0-9-]+","-",watchlist["name"].strip()).strip("-").lower() or "watchlist"
    return Response(body,mimetype="text/plain",headers={"Content-Disposition":f"attachment; filename={filename}.txt"})


@app.get("/api/watchlist")
@login_required
def legacy_watchlist():
    game_id=request.args.get("game_id") or db().execute("SELECT id FROM games ORDER BY name LIMIT 1").fetchone()[0]
    target=db().execute("SELECT id FROM named_watchlists WHERE user_id=? AND game_id=? ORDER BY is_default DESC,id LIMIT 1",(user_id(),game_id)).fetchone()
    if not target:return jsonify([])
    response=watchlist_cards(target["id"])
    return jsonify(response.json["cards"] if hasattr(response,"json") else [])
