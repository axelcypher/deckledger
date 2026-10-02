"""Deck builder: decks, their cards, validation, text import and export."""

import re

from flask import Response, jsonify, request

from .config import jload, now_iso
from .games import DECK_RULESETS, FORMAT_PROFILES, LORCANA_RARITY_KEYS, VCARD_PLAYABLE_TYPES, rarity_case_sql, rarity_rank, zone_for_card_type
from .web import app, db, login_required, user_id
from .prices import latest_price_sql
from .catalog import match_collector_number, split_set_prefix


def game_deck_ruleset(game_id):
    row = db().execute("SELECT deck_ruleset FROM games WHERE id=?", (game_id,)).fetchone()
    return row["deck_ruleset"] if row else None


@app.get("/api/games/<game_id>/formats")
@login_required
def game_formats(game_id):
    return jsonify(FORMAT_PROFILES.get(game_id,[]))




@app.get("/api/deckbuilder/catalog")
@login_required
def deck_catalog():
    game_id = request.args.get("game_id")
    q = request.args.get("q", "").strip().lower()
    set_id = request.args.get("set_id", "")
    language = request.args.get("language", "EN" if game_id == "one-piece" else "all")
    card_type = request.args.get("type", "")
    color = request.args.get("color", "")
    selected_types = [value for value in request.args.get("types", "").split(",") if value]
    selected_colors = [value for value in request.args.get("colors", "").split(",") if value]
    selected_costs = [value for value in request.args.get("costs", "").split(",") if value.isdigit()]
    selected_attributes = [value for value in request.args.get("attributes", "").split(",") if value]
    selected_rarities = [value for value in request.args.get("rarities", "").split(",") if value]
    rarity = request.args.get("rarity", "")
    selected_kinds = [value for value in request.args.get("kinds", "").split(",") if value]
    selected_bloom_levels = [value for value in request.args.get("bloomLevels", "").split(",") if value]
    inkwell = request.args.get("inkwell", "")
    sort = request.args.get("sort", "number")
    limit = min(20_000, max(24, request.args.get("limit", 72, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))

    # The deck catalogue shows one base printing per gameplay identity and
    # language. Alternate art, foil and reprint variants stay available in the
    # card detail view, but do not flood the builder browser.
    if game_id == "one-piece":
        base_variant_filter = (
            "v.id=(SELECT v2.id FROM variants v2 JOIN printings p2 ON p2.id=v2.printing_id "
            "JOIN sets s2 ON s2.id=p2.set_id WHERE p2.identity_id=i.id AND p2.language=p.language "
            "ORDER BY CASE WHEN v2.variant_code IN ('standard','normal') THEN 0 "
            "WHEN v2.is_parallel=0 THEN 1 ELSE 2 END,COALESCE(s2.release_date,'9999-12-31'),p2.id,v2.id LIMIT 1)"
        )
    else:
        base_variant_filter = (
            "v.id=(SELECT v2.id FROM variants v2 WHERE v2.printing_id=p.id "
            "ORDER BY CASE WHEN v2.variant_code IN ('standard','normal') THEN 0 "
            "WHEN v2.is_parallel=0 THEN 1 ELSE 2 END,v2.id LIMIT 1)"
        )
    filters = ["v.game_id=?", base_variant_filter]
    values = [game_id]
    if game_id == "lorcana":
        filters.append("lower(s.set_type)<>'quest'")
    if game_id == "vcard":
        # Box Toppers, Promos and God Rares are collectibles without a gameplay role.
        filters.append(f"i.card_type IN ({','.join('?' for _ in VCARD_PLAYABLE_TYPES)})")
        values.extend(VCARD_PLAYABLE_TYPES)
    if q:
        # Matches the English name, the localized (DE/JP/...) name and rules text for this
        # printing, and the English rules text -- a search box that only understood the English
        # canonical_name was useless while actually browsing German- or Japanese-language cards.
        filters.append(
            "(lower(i.canonical_name) LIKE ? OR lower(p.collector_number) LIKE ? OR lower(i.rules_text) LIKE ?"
            " OR lower(json_extract(p.attributes,'$.localizedName')) LIKE ? OR lower(json_extract(p.attributes,'$.localizedRulesText')) LIKE ?)"
        )
        values.extend((f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
    if set_id:
        filters.append("p.set_id=?")
        values.append(set_id)
    if language != "all":
        filters.append("p.language=?")
        values.append(language)
    if card_type:
        filters.append("i.card_type=?")
        values.append(card_type)
    if color:
        filters.append("json_extract(i.attributes,'$.color')=?")
        values.append(color)
    if selected_types:
        filters.append(f"i.card_type IN ({','.join('?' for _ in selected_types)})")
        values.extend(selected_types)
    if rarity:
        filters.append("p.rarity=?")
        values.append(rarity)
    if selected_rarities:
        selected_ranks = [LORCANA_RARITY_KEYS[key] for key in selected_rarities if key in LORCANA_RARITY_KEYS]
        if selected_ranks:
            filters.append(f"{rarity_case_sql(game_id, 'p.rarity')} IN ({','.join('?' for _ in selected_ranks)})")
            values.extend(selected_ranks)
    if selected_colors:
        filters.append("("+" OR ".join(
            "instr('-'||replace(COALESCE(json_extract(i.attributes,'$.color'),''),'/','-')||'-', '-'||?||'-')>0"
            for _ in selected_colors
        )+")")
        values.extend(selected_colors)
    if selected_costs:
        cost_expr = "CAST(json_extract(i.attributes,'$.cost') AS INTEGER)"
        cost_clauses = []
        for value in selected_costs:
            if value == "7" and game_id == "lorcana":
                cost_clauses.append(f"{cost_expr}>=7")
            else:
                cost_clauses.append(f"{cost_expr}=?")
                values.append(value)
        filters.append("(" + " OR ".join(cost_clauses) + ")")
    if selected_attributes:
        filters.append("("+" OR ".join(
            "instr('/'||COALESCE(json_extract(i.attributes,'$.attribute'),'')||'/', '/'||?||'/')>0"
            for _ in selected_attributes
        )+")")
        values.extend(selected_attributes)
    if inkwell in {"true", "false"}:
        filters.append("CAST(json_extract(i.attributes,'$.inkwell') AS INTEGER)=?")
        values.append(1 if inkwell == "true" else 0)
    if selected_bloom_levels:
        filters.append(f"json_extract(i.attributes,'$.bloomLevel') IN ({','.join('?' for _ in selected_bloom_levels)})")
        values.extend(selected_bloom_levels)
    if selected_kinds:
        kind_clauses = []
        for kind in selected_kinds:
            if kind == "oshi":
                kind_clauses.append("i.card_type IN ('Oshi','Oshi holomem','推しホロメン')")
            elif kind == "holomem":
                kind_clauses.append("(lower(i.card_type)='holomem' OR i.card_type='ホロメン')")
            elif kind == "buzz":
                kind_clauses.append("(lower(i.card_type)='buzz holomem' OR i.card_type='Buzzホロメン')")
            elif kind == "support":
                kind_clauses.append("(lower(i.card_type) LIKE 'support%' OR i.card_type LIKE 'サポート%')")
            elif kind == "cheer":
                kind_clauses.append("i.card_type IN ('Cheer','エール')")
        if kind_clauses:
            filters.append("(" + " OR ".join(kind_clauses) + ")")
    where = " AND ".join(filters)
    order_by = {
        "number": "p.collector_number COLLATE NOCASE,i.canonical_name COLLATE NOCASE",
        "name": "i.canonical_name COLLATE NOCASE,p.collector_number COLLATE NOCASE",
        "cost": "CAST(COALESCE(json_extract(i.attributes,'$.cost'),0) AS REAL),i.canonical_name COLLATE NOCASE",
        "rarity": f"{rarity_case_sql(game_id, 'p.rarity')},p.collector_number COLLATE NOCASE",
    }.get(sort, "p.collector_number COLLATE NOCASE,i.canonical_name COLLATE NOCASE")
    joins = """FROM variants v JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id"""
    total = db().execute(f"SELECT COUNT(*) {joins} WHERE {where}", values).fetchone()[0]
    rows = db().execute(
        f"""SELECT v.id variant_id,v.finish,v.is_parallel,p.id printing_id,i.id identity_id,
          i.canonical_name,i.card_type,i.attributes identity_attrs,p.collector_number,p.language,
          p.rarity,p.set_id,s.name set_name,s.code set_code,{latest_price_sql('v')} price,
          COALESCE(SUM(c.quantity),0) owned
          {joins} LEFT JOIN collection_entries c ON c.variant_id=v.id AND c.user_id=?
          WHERE {where} GROUP BY v.id ORDER BY {order_by} LIMIT ? OFFSET ?""",
        [user_id(), *values, limit, offset],
    ).fetchall()
    deck_ruleset = game_deck_ruleset(game_id)
    result = []
    for item in rows:
        card = dict(item)
        card["attributes"] = jload(card.pop("identity_attrs"), {})
        card["suggested_zone"] = zone_for_card_type(deck_ruleset, card["card_type"])
        result.append(card)
    sets = [dict(row) for row in db().execute(
        "SELECT id,code,name FROM sets WHERE game_id=? ORDER BY release_date DESC", (game_id,)
    )]
    types = [row[0] for row in db().execute(
        "SELECT DISTINCT card_type FROM card_identities WHERE game_id=? AND card_type<>'' ORDER BY card_type", (game_id,)
    )]
    colors = [row[0] for row in db().execute(
        """SELECT DISTINCT json_extract(attributes,'$.color') color FROM card_identities
           WHERE game_id=? AND color IS NOT NULL AND color<>'' ORDER BY color""", (game_id,)
    )]
    rarities = sorted({row[0] for row in db().execute(
        "SELECT DISTINCT rarity FROM printings WHERE game_id=? AND rarity<>''", (game_id,)
    )}, key=lambda value: (rarity_rank(game_id, value), value))
    return jsonify({
        "cards": result, "sets": sets, "types": types, "colors": colors, "rarities": rarities,
        "pagination": {"offset": offset, "limit": limit, "total": total, "has_more": offset + len(result) < total},
    })


@app.route("/api/decks",methods=["GET","POST"])
@login_required
def decks():
    if request.method=="POST":
        p=request.get_json(force=True);game_id=p.get("game_id");profiles=FORMAT_PROFILES.get(game_id,[]);format_id=p.get("format_id") or (profiles[0]["id"] if profiles else "standard");stamp=now_iso()
        cur=db().execute("INSERT INTO decks(user_id,game_id,name,format_id,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(user_id(),game_id,p.get("name","Neues Deck").strip()[:100] or "Neues Deck",format_id,"",stamp,stamp));db().commit()
        return jsonify({"id":cur.lastrowid}),201
    game_id=request.args.get("game_id")
    rows=db().execute("""SELECT d.*,COALESCE(SUM(CASE WHEN dc.zone='main' THEN dc.quantity ELSE 0 END),0) main_count,
      COALESCE(SUM(CASE WHEN dc.zone='leader' THEN dc.quantity ELSE 0 END),0) leader_count,
      COALESCE(SUM(CASE WHEN dc.zone='don' THEN dc.quantity ELSE 0 END),0) don_count,
      COALESCE(SUM(CASE WHEN dc.zone='oshi' THEN dc.quantity ELSE 0 END),0) oshi_count,
      COALESCE(SUM(CASE WHEN dc.zone='cheer' THEN dc.quantity ELSE 0 END),0) cheer_count,
      COALESCE(SUM(dc.quantity),0) total_count,
      COALESCE(d.cover_variant_id,(SELECT dc2.variant_id FROM deck_cards dc2 WHERE dc2.deck_id=d.id
       ORDER BY CASE dc2.zone WHEN 'leader' THEN 0 WHEN 'oshi' THEN 0 WHEN 'main' THEN 1 ELSE 2 END,dc2.id LIMIT 1)) effective_cover_variant_id
      FROM decks d LEFT JOIN deck_cards dc ON dc.deck_id=d.id
      WHERE d.user_id=? AND d.game_id=? GROUP BY d.id ORDER BY d.updated_at DESC""",(user_id(),game_id)).fetchall()
    result=[dict(r) for r in rows]
    for row in result:row["cover_variant_id"]=row.pop("effective_cover_variant_id")
    summaries=deck_market_summaries([row["id"] for row in result],user_id())
    for row in result:row.update(summaries.get(row["id"],empty_deck_market_summary()))
    return jsonify(result)


def empty_deck_market_summary():
    return {
        "deck_value":0.0,"deck_unpriced_copies":0,"required_copies":0,"owned_copies":0,
        "missing_copies":0,"complete_entries":0,"missing_entries":0,"missing_cost":0.0,
        "missing_unpriced_copies":0,
    }


def deck_market_summaries(deck_ids,uid):
    """Calculate deck value and collection coverage once per physical variant."""
    if not deck_ids:return {}
    placeholders=",".join("?" for _ in deck_ids)
    rows=db().execute(
        f"""SELECT dc.deck_id,dc.variant_id,SUM(dc.quantity) required,{latest_price_sql('v')} price,
          COALESCE((SELECT SUM(c.quantity) FROM collection_entries c
                    WHERE c.user_id=? AND c.variant_id=dc.variant_id),0) collection_quantity
          FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id
          WHERE dc.deck_id IN ({placeholders}) GROUP BY dc.deck_id,dc.variant_id""",
        [uid,*deck_ids],
    ).fetchall()
    summaries={deck_id:empty_deck_market_summary() for deck_id in deck_ids}
    for row in rows:
        summary=summaries[row["deck_id"]]
        required=int(row["required"] or 0);available=int(row["collection_quantity"] or 0)
        owned=min(required,available);missing=max(0,required-owned);price=row["price"]
        summary["required_copies"]+=required;summary["owned_copies"]+=owned;summary["missing_copies"]+=missing
        summary["complete_entries"]+=int(missing==0);summary["missing_entries"]+=int(missing>0)
        if price is None:
            summary["deck_unpriced_copies"]+=required;summary["missing_unpriced_copies"]+=missing
        else:
            summary["deck_value"]+=required*price;summary["missing_cost"]+=missing*price
    for summary in summaries.values():
        summary["deck_value"]=round(summary["deck_value"],2);summary["missing_cost"]=round(summary["missing_cost"],2)
    return summaries


def deck_validation(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return None
    cards=[dict(r) for r in db().execute("""SELECT dc.*,i.canonical_name,i.attributes,p.collector_number,p.language,p.rarity,i.card_type,s.set_type
      FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id JOIN printings p ON p.id=v.printing_id
      JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id WHERE dc.deck_id=?""",(deck_id,))]
    game=deck["game_id"]
    profile=next((p for p in FORMAT_PROFILES.get(game,[]) if p["id"]==deck["format_id"]),FORMAT_PROFILES.get(game,[{}])[0])
    zone_ids=[z["id"] for z in profile.get("zones",[])] or ["main"]
    counts={zone:sum(c["quantity"] for c in cards if c["zone"]==zone) for zone in zone_ids}
    ruleset=DECK_RULESETS.get(game_deck_ruleset(game))
    if ruleset:
        errors,warnings=ruleset["validate"](deck,cards,counts)
    else:
        # No bespoke ruleset assigned: fall back to checking each zone's target
        # count from the format profile, without any game-specific extra rules.
        errors,warnings=[],[]
        for zone in profile.get("zones",[]):
            target=zone.get("target")
            if target and counts.get(zone["id"],0)!=target:
                errors.append(f'{zone["name"]}: {counts.get(zone["id"],0)}/{target} Karten.')
    return {"valid":not errors,"errors":errors,"warnings":warnings,"counts":counts,"rules_url":profile.get("rules_url"),"profile":profile}


def default_one_piece_don(quantity):
    if quantity<=0:return None
    row=db().execute(
        f"""SELECT v.id variant_id,v.finish,v.is_parallel,i.id identity_id,i.canonical_name,i.card_type,
          p.collector_number,p.language,p.rarity,p.set_id,s.code set_code,{latest_price_sql('v')} price
          FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
          JOIN sets s ON s.id=p.set_id WHERE v.id='one-piece-print-don-008-en-standard'"""
    ).fetchone()
    if not row:return None
    card=dict(row);card.update({
        "zone":"don","quantity":quantity,"collection_quantity":quantity,"owned_quantity":quantity,
        "missing_quantity":0,"auto_filled":True,
    })
    return card


@app.route("/api/decks/<int:deck_id>",methods=["GET","PATCH","DELETE"])
@login_required
def deck_detail_api(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    if request.method=="DELETE":db().execute("DELETE FROM decks WHERE id=?",(deck_id,));db().commit();return jsonify({"deleted":True})
    if request.method=="PATCH":
        p=request.get_json(force=True);cover_variant_id=deck["cover_variant_id"]
        if "cover_variant_id" in p:
            requested_cover=p.get("cover_variant_id") or None
            if requested_cover and not db().execute("SELECT 1 FROM deck_cards WHERE deck_id=? AND variant_id=? AND quantity>0",(deck_id,requested_cover)).fetchone():
                return jsonify({"error":"Als Cover kann nur eine Karte aus diesem Deck gewählt werden."}),400
            cover_variant_id=requested_cover
        db().execute("UPDATE decks SET name=?,format_id=?,notes=?,cover_variant_id=?,updated_at=? WHERE id=?",(p.get("name",deck["name"])[:100],p.get("format_id",deck["format_id"]),p.get("notes",deck["notes"] or ""),cover_variant_id,now_iso(),deck_id));db().commit();return jsonify({"saved":True,"cover_variant_id":cover_variant_id,"validation":deck_validation(deck_id)})
    cards=deck_cards_with_ownership(deck_id)
    summary=deck_market_summaries([deck_id],user_id()).get(deck_id,empty_deck_market_summary())
    explicit_don=sum(card["quantity"] for card in cards if card["zone"]=="don")
    default_don=default_one_piece_don(max(0,10-explicit_don)) if deck["game_id"]=="one-piece" else None
    return jsonify({"deck":dict(deck),"cards":cards,"validation":deck_validation(deck_id),"summary":summary,"default_don":default_don})


def deck_cards_with_ownership(deck_id):
    """Every deck_cards row for this deck, joined with catalog data and split into
    owned_quantity/missing_quantity against the current collection. Shared by the deck detail
    API and both export routes below so the "pool a variant's owned copies across whichever deck
    rows need it first" logic -- e.g. the same foil printing appearing in two different deck
    rows -- only lives in one place."""
    cards=[dict(r) for r in db().execute(f"""SELECT dc.*,v.finish,i.id identity_id,i.canonical_name,i.card_type,p.collector_number,p.language,p.rarity,
      s.code set_code,{latest_price_sql('v')} price,
      COALESCE((SELECT SUM(c.quantity) FROM collection_entries c WHERE c.user_id=? AND c.variant_id=v.id),0) collection_quantity
      FROM deck_cards dc JOIN variants v ON v.id=dc.variant_id
      JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
      WHERE dc.deck_id=? ORDER BY dc.zone,p.collector_number""",(user_id(),deck_id))]
    remaining_owned={}
    for card in cards:remaining_owned[card["variant_id"]]=int(card["collection_quantity"] or 0)
    for card in cards:
        available=remaining_owned[card["variant_id"]];owned=min(card["quantity"],available)
        card["owned_quantity"]=owned;card["missing_quantity"]=max(0,card["quantity"]-owned)
        remaining_owned[card["variant_id"]]=max(0,available-owned)
    return cards


def deck_zone_names(game_id, format_id):
    profile=next((p for p in FORMAT_PROFILES.get(game_id,[]) if p["id"]==format_id),FORMAT_PROFILES.get(game_id,[{}])[0])
    return {z["id"]:z["name"] for z in profile.get("zones",[])}


@app.get("/api/decks/<int:deck_id>/export/list.txt")
@login_required
def export_deck_list(deck_id):
    """General decklist export -- plain text, one card per line as "<qty>x <canonical_name>",
    grouped under a "# <Zone>" comment per zone. `#`-prefixed lines are comments to our own
    importer (parse_deck_text), so the zone headers are inert if pasted back in -- this is the
    exact same name-matching branch that already handles pasted decklists from OTHER
    deckbuilders, so it's also the most portable format to hand to a human or another tool.
    Deliberately name-based, NOT collector-number-based: collector_number is only unique WITHIN
    a set for this game (Lorcana's own numbering restarts at 1 every set -- confirmed against
    the real data: card number "1" alone matches 24 different EN printings across sets), so a
    bare "<number> <language>" line round-trips ambiguously ("31 Karten teilen sich diese
    Nummer") for exactly the games/cards this export needs to work reliably for. One Piece and
    hololive's collector_number happens to already embed a set-ish prefix (e.g. "OP01-016") and
    would have round-tripped fine either way, but using name everywhere keeps this one format
    correct for all three games instead of special-casing per game_id."""
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    cards=deck_cards_with_ownership(deck_id)
    zone_names=deck_zone_names(deck["game_id"],deck["format_id"])
    zones={}
    for card in cards:
        zones.setdefault(card["zone"],[]).append(card)
    lines=[f"# {deck['name']}"]
    for zone_id,zone_cards in zones.items():
        lines.append(f"\n# {zone_names.get(zone_id,zone_id.capitalize())}")
        for card in zone_cards:
            lines.append(f"{card['quantity']}x {card['canonical_name']}")
    filename=re.sub(r"[^A-Za-z0-9-]+","-",deck["name"].strip()).strip("-").lower() or "deck"
    return Response("\n".join(lines)+"\n",mimetype="text/plain",headers={"Content-Disposition":f"attachment; filename={filename}.txt"})


@app.get("/api/decks/<int:deck_id>/export/missing.txt")
@login_required
def export_deck_missing_list(deck_id):
    """Missing-copies list for pasting into Cardmarket's own bulk "Wantlist"/quick-add box,
    which matches products by NAME only (no set/collector-number field in that particular
    entry form) -- one line per card as "<qty>x <name>", English canonical_name (Cardmarket's
    own product listings are named in English regardless of which print you end up buying).
    Aggregated across every deck row missing that card (e.g. the same card needed in two
    different finishes both count toward the one Cardmarket product) and sorted alphabetically,
    not by zone -- a shopping list, not a decklist."""
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    cards=deck_cards_with_ownership(deck_id)
    missing={}
    for card in cards:
        if card["missing_quantity"]>0:
            missing[card["canonical_name"]]=missing.get(card["canonical_name"],0)+card["missing_quantity"]
    lines=[f"{qty}x {name}" for name,qty in sorted(missing.items())]
    body="\n".join(lines)+"\n" if lines else "# Keine fehlenden Karten in diesem Deck.\n"
    filename=re.sub(r"[^A-Za-z0-9-]+","-",deck["name"].strip()).strip("-").lower() or "deck"
    return Response(body,mimetype="text/plain",headers={"Content-Disposition":f"attachment; filename={filename}-fehlend.txt"})


def parse_deck_text(text, game_id):
    """Match a pasted decklist (from this app or another deckbuilder) onto catalog variants.

    Accepts two line styles, tried in order: a collector-number code (the same syntax the
    collection importer uses, e.g. "4 OP01-016 EN") and, when that yields no candidate, a bare
    card name (what most external deckbuilder exports use, e.g. "4 Belle - Strange and Beautiful").
    Name matches can span multiple printings/finishes of the same card; the most recently
    released printing is picked automatically and the alternative count is surfaced so the result
    is inspectable in the preview rather than silently guessed.
    """
    deck_ruleset = game_deck_ruleset(game_id)
    results = []
    for line_no, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line or line.startswith("//") or line.startswith("#"): continue
        qty_match = re.match(r"^\s*(\d+)\s*[xX]?\s+(.+)$", line)
        qty = int(qty_match.group(1)) if qty_match else 1
        rest = qty_match.group(2).strip() if qty_match else line
        selected, status, message, alt_count = None, "not_found", "Karte nicht im Katalog gefunden", 0
        number_match = re.match(r"^((?:[A-Za-z0-9-]+:)?[A-Za-z0-9-]+(?:/\d+)?)\s*(DE|EN|JP)?\s*$", rest, re.I)
        number_status = "not_found"
        if number_match:
            set_code, number = split_set_prefix(number_match.group(1))
            lang = (number_match.group(2) or "EN").upper()
            selected, number_status, message, _ = match_collector_number(game_id, number, lang, set_code)
        if number_status == "ambiguous":
            selected, status = None, "ambiguous"
        elif number_status == "not_found":
            message = "Karte nicht im Katalog gefunden"
            name_rows = db().execute(
                """SELECT v.id variant_id,v.finish,i.canonical_name,i.card_type,p.collector_number,p.language,s.name set_name
                   FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
                   JOIN sets s ON s.id=p.set_id
                   WHERE v.game_id=? AND i.canonical_name=? COLLATE NOCASE
                   ORDER BY s.release_date DESC, CASE WHEN p.language='EN' THEN 0 ELSE 1 END, CASE WHEN v.finish='Normal' THEN 0 ELSE 1 END""",
                (game_id, rest)
            ).fetchall()
            if name_rows:
                selected = dict(name_rows[0])
                alt_count = max(0, len(name_rows) - 1)
        if selected:
            status, message = "matched", None
        results.append({
            "line": line_no, "original": original.strip(),
            "quantity": qty,
            "status": status,
            "message": message,
            "alt_printings": alt_count,
            "match": selected,
            "zone": zone_for_card_type(deck_ruleset, selected["card_type"]) if selected else None,
        })
    return results


@app.post("/api/decks/<int:deck_id>/import/preview")
@login_required
def deck_import_preview(deck_id):
    deck = db().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id())).fetchone()
    if not deck: return jsonify({"error": "deck not found"}), 404
    p = request.get_json(force=True)
    return jsonify(parse_deck_text(p.get("text", ""), deck["game_id"]))


@app.post("/api/decks/<int:deck_id>/import/apply")
@login_required
def deck_import_apply(deck_id):
    deck = db().execute("SELECT * FROM decks WHERE id=? AND user_id=?", (deck_id, user_id())).fetchone()
    if not deck: return jsonify({"error": "deck not found"}), 404
    p = request.get_json(force=True)
    rows = parse_deck_text(p.get("text", ""), deck["game_id"])
    profile = next((item for item in FORMAT_PROFILES.get(deck["game_id"], []) if item["id"] == deck["format_id"]), None)
    allowed_zones = {item["id"] for item in (profile or {}).get("zones", [])} or {"main"}
    if p.get("strategy") == "replace":
        db().execute("DELETE FROM deck_cards WHERE deck_id=?", (deck_id,))
    applied, skipped_zone = 0, 0
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        if row["zone"] not in allowed_zones:
            skipped_zone += 1; continue
        vid = row["match"]["variant_id"]
        existing = db().execute("SELECT * FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?", (deck_id, vid, row["zone"])).fetchone()
        quantity = (existing["quantity"] if existing else 0) + row["quantity"]
        if existing:
            db().execute("UPDATE deck_cards SET quantity=? WHERE id=?", (quantity, existing["id"]))
        else:
            db().execute("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)", (deck_id, vid, row["zone"], quantity))
        applied += 1
    db().execute("UPDATE decks SET updated_at=? WHERE id=?", (now_iso(), deck_id)); db().commit()
    return jsonify({"applied": applied, "matched": sum(1 for r in rows if r["status"] == "matched"), "skipped_zone": skipped_zone, "total": len(rows)})


@app.post("/api/decks/<int:deck_id>/cards")
@login_required
def update_deck_card(deck_id):
    deck=db().execute("SELECT * FROM decks WHERE id=? AND user_id=?",(deck_id,user_id())).fetchone()
    if not deck:return jsonify({"error":"deck not found"}),404
    p=request.get_json(force=True);variant_id=p.get("variant_id")
    db().execute("BEGIN IMMEDIATE")  # same read-modify-write as update_collection: quick clicks must not overwrite each other
    card=db().execute("""SELECT v.game_id,i.card_type,s.set_type,pr.rarity FROM variants v JOIN printings pr ON pr.id=v.printing_id
      JOIN card_identities i ON i.id=pr.identity_id JOIN sets s ON s.id=pr.set_id WHERE v.id=?""",(variant_id,)).fetchone()
    if not card or card["game_id"]!=deck["game_id"]:return jsonify({"error":"Karte gehört nicht zu diesem TCG."}),400
    if deck["game_id"]=="lorcana" and str(card["set_type"] or "").lower()=="quest":return jsonify({"error":"Quest-Karten sind nicht für Lorcana-Constructed-Decks zulässig."}),400
    if deck["game_id"]=="vcard" and card["card_type"] not in VCARD_PLAYABLE_TYPES and int(p.get("quantity",p.get("delta",0)) or 0)>0:return jsonify({"error":f'{card["rarity"]}-Karten sind Sammelkarten und nicht spielbar.'}),400
    profile=next((item for item in FORMAT_PROFILES.get(deck["game_id"],[]) if item["id"]==deck["format_id"]),None)
    allowed_zones={item["id"] for item in (profile or {}).get("zones",[])} or {"main"}
    suggested_zone=zone_for_card_type(game_deck_ruleset(deck["game_id"]),card["card_type"])
    zone=p.get("zone")
    if not zone or zone=="auto":zone=suggested_zone
    if zone not in allowed_zones:return jsonify({"error":"Diese Zone gehört nicht zum gewählten Regelprofil."}),400
    existing=db().execute("SELECT * FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?",(deck_id,variant_id,zone)).fetchone();before=existing["quantity"] if existing else 0;quantity=max(0,int(p.get("quantity",before+int(p.get("delta",0)))))
    if quantity==0:db().execute("DELETE FROM deck_cards WHERE deck_id=? AND variant_id=? AND zone=?",(deck_id,variant_id,zone))
    elif existing:db().execute("UPDATE deck_cards SET quantity=? WHERE id=?",(quantity,existing["id"]))
    else:db().execute("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)",(deck_id,variant_id,zone,quantity))
    if quantity==0 and deck["cover_variant_id"]==variant_id and not db().execute("SELECT 1 FROM deck_cards WHERE deck_id=? AND variant_id=? AND quantity>0",(deck_id,variant_id)).fetchone():
        db().execute("UPDATE decks SET cover_variant_id=NULL WHERE id=?",(deck_id,))
    db().execute("UPDATE decks SET updated_at=? WHERE id=?",(now_iso(),deck_id));db().commit()
    return jsonify({"quantity":quantity,"before":before,"zone":zone,"validation":deck_validation(deck_id)})
