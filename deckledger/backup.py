"""Text import, JSON backup export and restore, and undoing an import."""

import csv
import io
import json
import re

from flask import Response, jsonify, request

import sheet_render

from .config import jload, now_iso
from .games import FORMAT_PROFILES
from .web import app, db, login_required, user_id
from .schema import SALE_LIST_NAME
from .prices import latest_price_sql
from .catalog import match_collector_number, split_set_prefix
from .sheets import SHEET_KINDS, SHEET_SORTS


def parse_import(text, game_id, language="EN", condition="Near Mint"):
    results = []
    for line_no, original in enumerate(text.splitlines(), 1):
        line = original.strip()
        if not line: continue
        parts = [p.strip() for p in line.split(";")]
        match = re.match(r"^\s*(?:(\d+)\s*[xX]?\s+)?((?:[A-Za-z0-9-]+:)?[A-Za-z0-9-]+(?:/\d+)?)\s*(DE|EN|JP)?", parts[0], re.I)
        if not match:
            results.append({"line":line_no,"original":original,"status":"not_found","message":"Format nicht erkannt"}); continue
        qty = int(match.group(1) or (parts[1] if len(parts)>1 and parts[1].isdigit() else 1))
        set_code, number = split_set_prefix(match.group(2))
        lang = (match.group(3) or (parts[2] if len(parts)>2 and parts[2].upper() in ("DE","EN","JP") else language)).upper()
        variant_hint = parts[3].lower() if len(parts)>3 else "standard"
        cond = parts[4] if len(parts)>4 else condition
        selected, status, message, _ = match_collector_number(game_id, number, lang, set_code, variant_hint)
        if status == "not_found":
            message = None
        # collector_number alone isn't globally unique for every game -- Lorcana's restarts at 1
        # every set (card "16" alone matches ~240 different EN printings across the catalogue:
        # 98% of its collector numbers are shared by more than one printing), so a plain
        # "<qty> <number> <language>" line almost never resolves cleanly there. When the number
        # lookup can't, retry treating the WHOLE first field as a card NAME instead -- the same
        # fallback parse_deck_text() already has for pasted decklists. Only a name match that's
        # itself unambiguous (after the same language/variant filtering) upgrades the result to
        # "matched"; a genuine name collision still needs a human to pick.
        if status in ("ambiguous", "not_found"):
            name_query = parts[0]
            qty_prefix = re.match(r"^\s*\d+\s*[xX]?\s+", name_query)
            if qty_prefix: name_query = name_query[qty_prefix.end():]
            # match.group(3) only caught a trailing language code when it sat right after the
            # NUMBER-shaped first word (e.g. "16 EN") -- a real card name has other words in
            # between ("Prince Phillip - Dragonslayer EN"), so the language code is detected
            # fresh here instead of reusing that capture.
            trailing_lang = re.search(r"\s+(DE|EN|JP)\s*$", name_query, re.I)
            if trailing_lang:
                name_query = name_query[:trailing_lang.start()]
                lang = trailing_lang.group(1).upper()  # more reliable than the number-path's guess for a name line
            name_query = name_query.strip()
            name_candidates = db().execute(
                """SELECT v.id variant_id,v.variant_code,v.finish,v.game_id,p.collector_number,p.language,p.rarity,i.canonical_name,s.name set_name
                   FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id
                   WHERE v.game_id=? AND i.canonical_name=? COLLATE NOCASE AND p.language=?
                   ORDER BY s.release_date DESC,
                     CASE WHEN v.variant_code IN ('standard','normal') THEN 0 WHEN v.finish='Normal' THEN 0 ELSE 1 END""",
                (game_id, name_query, lang)
            ).fetchall() if name_query else []
            if name_candidates:
                name_exact = [dict(c) for c in name_candidates if variant_hint in (c["variant_code"].lower(), c["finish"].lower())]
                # Same default as parse_deck_text()'s name fallback: a name (now also pinned to
                # one language) resolving to several FINISHES of the same card isn't a real
                # ambiguity worth blocking on -- default to the base/Normal print (already
                # ordered first above) and just mention the alternative count.
                name_selected = name_exact[0] if name_exact else dict(name_candidates[0])
                selected, status = name_selected, "matched"
                message = f"{len(name_candidates)-1} weitere Drucke dieser Karte verfügbar" if len(name_candidates)>1 and not name_exact else None
        results.append({"line":line_no,"original":original,"quantity":qty,"number":number,"language":lang,"condition":cond,"status":status,"match":selected,"message":message,"inferred":not bool(match.group(3))})
    return results


@app.post("/api/import/preview")
@login_required
def import_preview():
    p = request.get_json(force=True)
    return jsonify(parse_import(p.get("text",""),p.get("game_id","lorcana"),p.get("language","EN"),p.get("condition","Near Mint")))


JSON_BACKUP_MATCH_COLUMNS = """v.id variant_id,v.finish,v.game_id,p.collector_number,p.language,i.canonical_name,s.name set_name"""
JSON_BACKUP_MATCH_FROM = """FROM variants v JOIN printings p ON p.id=v.printing_id JOIN card_identities i ON i.id=p.identity_id
               JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id"""


def parse_json_backup(entries):
    """Match rows from a DeckLedger /api/export.json payload back onto current catalog variants.

    collector_number can legitimately be an empty string (e.g. One Piece DON!! tokens), so
    only language/finish/game/set_code are required. The primary match also pins canonical_name
    because misprints and alternate-art tokens can otherwise share (game,set_code,number,
    language,finish) across genuinely different variants — without it, an ambiguous match would
    silently apply to the wrong physical card.

    canonical_name is free text pulled straight from each provider's upstream source, though, and
    is by far the field most likely to drift between two catalogues synced at different times (a
    spelling fix, a punctuation/dash change upstream, ...) -- unlike game/set_code/finish, which
    are small, stable, effectively-structural vocabularies. A backup made against an older or
    newer catalogue snapshot than the one it's being restored into would otherwise show every
    affected card as "not found" even though the exact same physical printing is right there.
    So: only fall back to a name-agnostic re-match when the strict one finds nothing at all
    (0 candidates) -- an ambiguous strict match (>1) still means "can't tell which one, ask a
    human", never "guess by ignoring the name".

    set_code turned out to be less stable than assumed: Lorcana's own `sets.code` changed from
    LorcanaJSON's raw numeric strings ("1", "2", ...) to community letter abbreviations ("TFC",
    "ROF", ...) -- an older backup taken before that change still has the numeric code, which no
    longer matches ANY current set, so every card from that set (in practice: everything from the
    first affected set onward, since a JSON backup is sorted by release date) came back "not
    found". set_name (the actual set title, e.g. "The First Chapter") wasn't touched by that
    rename and isn't expected to be as volatile as a short code -- matching on EITHER makes this
    resilient to that change and to the same kind of code-scheme change in the future.
    """
    results = []
    for idx, entry in enumerate(entries, 1):
        number = str(entry.get("collector_number") or "").strip()
        lang = str(entry.get("language") or "").strip().upper()
        finish = str(entry.get("finish") or "").strip()
        game_name = str(entry.get("game") or "").strip()
        set_code = str(entry.get("set_code") or "").strip()
        set_name_field = str(entry.get("set_name") or "").strip()
        name = str(entry.get("canonical_name") or "").strip()
        label = name or number or f"Zeile {idx}"
        # Backups since format_version 2 carry the exact variant id. It is only trusted while the
        # print it points at still looks like the exported one; otherwise the descriptive match
        # below decides, exactly as for an older backup without ids.
        pinned = db().execute(
            f"SELECT {JSON_BACKUP_MATCH_COLUMNS} {JSON_BACKUP_MATCH_FROM} WHERE v.id=?", (str(entry.get("variant_id") or ""),)
        ).fetchone()
        if pinned and (lang, finish) not in ((pinned["language"], pinned["finish"]), ("", "")):
            pinned = None
        if not pinned and not (lang and finish and game_name and set_code and name):
            results.append({"line":idx,"original":label,"number":number,"language":lang,"status":"not_found","message":"Unvollständiger Eintrag"})
            continue
        candidates = [pinned] if pinned else db().execute(
            f"""SELECT {JSON_BACKUP_MATCH_COLUMNS} {JSON_BACKUP_MATCH_FROM}
               WHERE g.name=? AND (s.code=? OR s.name=?) AND UPPER(p.collector_number)=UPPER(?) AND p.language=? AND v.finish=? AND i.canonical_name=?""",
            (game_name, set_code, set_name_field, number, lang, finish, name)
        ).fetchall()
        selected, message = None, None
        if len(candidates) == 1:
            selected = dict(candidates[0])
        elif len(candidates) > 1:
            message = "Mehrdeutig – mehrere Varianten passen, manuell prüfen"
        else:
            loose = db().execute(
                f"""SELECT {JSON_BACKUP_MATCH_COLUMNS} {JSON_BACKUP_MATCH_FROM}
                   WHERE g.name=? AND (s.code=? OR s.name=?) AND UPPER(p.collector_number)=UPPER(?) AND p.language=? AND v.finish=?""",
                (game_name, set_code, set_name_field, number, lang, finish)
            ).fetchall()
            if len(loose) == 1:
                selected = dict(loose[0])
                message = f"Name im Katalog abweichend („{selected['canonical_name']}“ statt „{name}“ im Backup) – anhand Set/Nummer/Sprache/Finish zugeordnet."
            elif len(loose) > 1:
                message = "Mehrdeutig – mehrere Varianten teilen sich Set/Nummer/Sprache/Finish, der Name aus dem Backup passt auf keine davon"
            else:
                message = "Karte nicht im Katalog gefunden"
        status = "matched" if selected else ("ambiguous" if candidates or message.startswith("Mehrdeutig") else "not_found")
        is_graded = 1 if entry.get("is_graded") else 0
        try:
            price_override = float(entry["price_override"]) if entry.get("price_override") not in (None, "") else None
        except (TypeError, ValueError):
            price_override = None
        results.append({
            "line": idx, "original": label, "number": number, "language": selected["language"] if selected else lang,
            "quantity": int(entry.get("quantity") or 0),
            "condition": entry.get("condition") or "Near Mint",
            "notes": entry.get("notes"),
            "is_graded": is_graded,
            "grade_label": str(entry.get("grade_label") or "").strip() if is_graded else "",
            "price_override": price_override,
            "created_at": str(entry.get("created_at") or ""),
            "last_added_at": str(entry.get("last_added_at") or ""),
            "status": status,
            "message": None if selected and not message else message,
            "match": selected,
        })
    return results


@app.post("/api/import/json/preview")
@login_required
def import_json_preview():
    p = request.get_json(force=True)
    rows = parse_json_backup(p.get("collection") or [])
    games = {row["name"] for row in db().execute("SELECT name FROM games")}
    # One summary line per deck and watchlist in the backup, in the same shape as a card row.
    for kind, label, items, cards_key in (
        ("deck", "Deck", p.get("decks") or [], "cards"), ("watchlist", "Watchlist", backup_watchlists(p), "entries"),
        ("sheet", "Sheet", backup_sheets(p), "cards"),
    ):
        for item in items:
            entries = item.get(cards_key) or []
            if not entries:
                continue
            found = sum(1 for row in parse_json_backup(entries) if row["status"] == "matched")
            known_game = item.get("game") in games
            rows.append({
                "line": len(rows) + 1, "kind": kind, "original": f'{label}: {item.get("name") or "ohne Namen"}',
                "status": "matched" if found and known_game else "not_found",
                "message": f'{item.get("game")} · {found} von {len(entries)} Karten gefunden' if known_game else f'Spiel „{item.get("game")}“ gibt es hier nicht',
            })
    return jsonify(rows)


def is_legacy_sale_list(item):
    """A backup from before the trade sheets marks every watchlist with is_sale_list; newer ones
    no longer have the key. In such a backup the sale list is the flagged list or, where the
    account had its own list of that name instead, that one -- see migrate_sale_lists()."""
    return "is_sale_list" in item and bool(item["is_sale_list"] or (not item.get("is_default") and item.get("name") == SALE_LIST_NAME))


def backup_watchlists(payload):
    return [item for item in payload.get("watchlists") or [] if not is_legacy_sale_list(item)]


def backup_sheets(payload):
    """The sheets of a backup, including an older backup's sale list, which is read as the WTS
    sheet it has since become."""
    legacy = [
        {"name": item.get("name"), "game": item.get("game"), "kind": "WTS", "cards": item.get("entries") or []}
        for item in payload.get("watchlists") or [] if is_legacy_sale_list(item)
    ]
    return list(payload.get("trade_sheets") or []) + legacy


def restore_backup_decks(decks, strategy, changes):
    """A deck from the backup is created when none of that name exists for the game. An existing
    one is left alone unless the import runs with "replace" -- two decks of the same name are
    never merged card by card."""
    uid, stamp, summary = user_id(), now_iso(), {"decks_restored": 0, "decks_skipped": 0}
    games = {row["name"]: row["id"] for row in db().execute("SELECT id,name FROM games")}
    for deck in decks:
        game_id, name = games.get(deck.get("game")), str(deck.get("name") or "").strip()[:100]
        if not game_id or not name:
            continue
        profiles = FORMAT_PROFILES.get(game_id, [])
        profile = next((item for item in profiles if item["id"] == deck.get("format_id")), profiles[0] if profiles else None)
        zones = {zone["id"] for zone in (profile or {}).get("zones", [])} or {"main"}
        entries = deck.get("cards") or []
        cards = {}
        for entry, row in zip(entries, parse_json_backup(entries)):
            if row["status"] == "matched" and row["quantity"] > 0:
                key = (row["match"]["variant_id"], entry.get("zone") if entry.get("zone") in zones else "main")
                cards[key] = cards.get(key, 0) + row["quantity"]
        existing = db().execute("SELECT id,cover_variant_id FROM decks WHERE user_id=? AND game_id=? AND name=?", (uid, game_id, name)).fetchone()
        if existing and strategy != "replace":
            summary["decks_skipped"] += 1
            continue
        if existing:
            deck_id = existing["id"]
            before = [dict(row) for row in db().execute("SELECT variant_id,zone,quantity FROM deck_cards WHERE deck_id=?", (deck_id,))]
            changes.append({"kind": "deck_replaced", "deck_id": deck_id, "cards_before": before, "cover_before": existing["cover_variant_id"]})
            db().execute("UPDATE decks SET cover_variant_id=NULL,updated_at=? WHERE id=?", (stamp, deck_id))
            db().execute("DELETE FROM deck_cards WHERE deck_id=?", (deck_id,))
        else:
            deck_id = db().execute(
                "INSERT INTO decks(user_id,game_id,name,format_id,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (uid, game_id, name, profile["id"] if profile else "standard", deck.get("notes") or "", deck.get("created_at") or stamp, deck.get("updated_at") or stamp),
            ).lastrowid
            changes.append({"kind": "deck_created", "deck_id": deck_id})
        db().executemany("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)", [(deck_id, variant_id, zone, quantity) for (variant_id, zone), quantity in cards.items()])
        if any(variant_id == deck.get("cover_variant_id") for variant_id, _ in cards):
            db().execute("UPDATE decks SET cover_variant_id=? WHERE id=?", (deck["cover_variant_id"], deck_id))
        summary["decks_restored"] += 1
    return summary


def restore_backup_watchlists(watchlists, strategy, changes):
    """Entries are added to the list of the same name (created if missing). A card already on
    the list keeps its wanted quantity unless the import runs with "replace"."""
    uid, stamp, summary = user_id(), now_iso(), {"watchlist_entries_restored": 0}
    games = {row["name"]: row["id"] for row in db().execute("SELECT id,name FROM games")}
    for watchlist in watchlists:
        game_id, name = games.get(watchlist.get("game")), str(watchlist.get("name") or "").strip()[:80]
        entries = watchlist.get("entries") or []
        if not game_id or not name or not entries:
            continue
        target = db().execute("SELECT id FROM named_watchlists WHERE user_id=? AND game_id=? AND name=?", (uid, game_id, name)).fetchone()
        if target:
            list_id = target["id"]
        else:
            list_id = db().execute("INSERT INTO named_watchlists(user_id,game_id,name,is_default,created_at) VALUES(?,?,?,0,?)", (uid, game_id, name, stamp)).lastrowid
            changes.append({"kind": "watchlist_created", "list_id": list_id})
        for entry, row in zip(entries, parse_json_backup(entries)):
            if row["status"] != "matched":
                continue
            variant_id, quantity = row["match"]["variant_id"], max(1, min(99, row["quantity"] or 1))
            existing = db().execute("SELECT id,quantity FROM named_watchlist_entries WHERE list_id=? AND variant_id=?", (list_id, variant_id)).fetchone()
            if existing and (strategy != "replace" or existing["quantity"] == quantity):
                continue
            if existing:
                changes.append({"kind": "watchlist_entry_updated", "entry_id": existing["id"], "quantity_before": existing["quantity"]})
                db().execute("UPDATE named_watchlist_entries SET quantity=? WHERE id=?", (quantity, existing["id"]))
            else:
                entry_id = db().execute(
                    "INSERT INTO named_watchlist_entries(list_id,variant_id,quantity,created_at) VALUES(?,?,?,?)",
                    (list_id, variant_id, quantity, entry.get("created_at") or stamp),
                ).lastrowid
                changes.append({"kind": "watchlist_entry_added", "entry_id": entry_id})
            summary["watchlist_entries_restored"] += 1
    return summary


def restore_backup_sheets(sheets, strategy, changes):
    """Like decks: a sheet is created when none of that name exists for the game, and an existing
    one keeps its cards unless the import runs with "replace"."""
    uid, stamp, summary = user_id(), now_iso(), {"sheets_restored": 0, "sheets_skipped": 0}
    games = {row["name"]: row["id"] for row in db().execute("SELECT id,name FROM games")}
    for sheet in sheets:
        game_id, name = games.get(sheet.get("game")), str(sheet.get("name") or "").strip()[:80]
        entries = sheet.get("cards") or []
        cards = {}
        for entry, row in zip(entries, parse_json_backup(entries)):
            if row["status"] == "matched" and row["quantity"] > 0:
                cards[row["match"]["variant_id"]] = (min(99, row["quantity"]), str(entry.get("label") or "").strip()[:24])
        if not game_id or not name or not cards:
            continue
        existing = db().execute("SELECT id FROM trade_sheets WHERE user_id=? AND game_id=? AND name=?", (uid, game_id, name)).fetchone()
        if existing and strategy != "replace":
            summary["sheets_skipped"] += 1
            continue
        if existing:
            sheet_id = existing["id"]
            before = [dict(row) for row in db().execute("SELECT variant_id,quantity,label FROM trade_sheet_cards WHERE sheet_id=? ORDER BY id", (sheet_id,))]
            changes.append({"kind": "sheet_replaced", "sheet_id": sheet_id, "cards_before": before})
            db().execute("DELETE FROM trade_sheet_cards WHERE sheet_id=?", (sheet_id,))
            db().execute("UPDATE trade_sheets SET updated_at=? WHERE id=?", (stamp, sheet_id))
        else:
            layout = sheet.get("layout")
            sheet_id = db().execute(
                "INSERT INTO trade_sheets(user_id,game_id,name,kind,subtitle,background,sort,layout,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    uid, game_id, name, sheet.get("kind") if sheet.get("kind") in SHEET_KINDS else "WTS",
                    str(sheet.get("subtitle") or "").strip()[:80],
                    sheet.get("background") if sheet.get("background") in sheet_render.BACKGROUNDS else "midnight",
                    sheet.get("sort") if sheet.get("sort") in SHEET_SORTS else "number",
                    layout if isinstance(layout, str) and sheet_render.parse_layout(layout) else "auto",
                    sheet.get("created_at") or stamp, stamp,
                ),
            ).lastrowid
            changes.append({"kind": "sheet_created", "sheet_id": sheet_id})
        db().executemany(
            "INSERT INTO trade_sheet_cards(sheet_id,variant_id,quantity,label) VALUES(?,?,?,?)",
            [(sheet_id, variant_id, quantity, label) for variant_id, (quantity, label) in cards.items()],
        )
        summary["sheets_restored"] += 1
    return summary


def undo_restored_item(change):
    """Reverts one deck/watchlist/sheet step of a backup import; every statement is scoped to the user."""
    uid, kind = user_id(), change["kind"]
    own_sheet_row = "id=? AND user_id=?"
    if kind == "sheet_created":
        db().execute(f"DELETE FROM trade_sheets WHERE {own_sheet_row}", (change["sheet_id"], uid))
    elif kind == "sheet_replaced" and db().execute(f"SELECT 1 FROM trade_sheets WHERE {own_sheet_row}", (change["sheet_id"], uid)).fetchone():
        db().execute("DELETE FROM trade_sheet_cards WHERE sheet_id=?", (change["sheet_id"],))
        db().executemany(
            "INSERT INTO trade_sheet_cards(sheet_id,variant_id,quantity,label) VALUES(?,?,?,?)",
            [(change["sheet_id"], card["variant_id"], card["quantity"], card["label"]) for card in change["cards_before"]],
        )
        db().execute("UPDATE trade_sheets SET updated_at=? WHERE id=?", (now_iso(), change["sheet_id"]))
    own_deck = "id=? AND user_id=?"
    own_entry = "id=? AND list_id IN (SELECT id FROM named_watchlists WHERE user_id=?)"
    if kind == "deck_created":
        db().execute(f"DELETE FROM decks WHERE {own_deck}", (change["deck_id"], uid))
    elif kind == "deck_replaced" and db().execute(f"SELECT 1 FROM decks WHERE {own_deck}", (change["deck_id"], uid)).fetchone():
        db().execute("UPDATE decks SET cover_variant_id=NULL WHERE id=?", (change["deck_id"],))
        db().execute("DELETE FROM deck_cards WHERE deck_id=?", (change["deck_id"],))
        db().executemany("INSERT INTO deck_cards(deck_id,variant_id,zone,quantity) VALUES(?,?,?,?)", [(change["deck_id"], card["variant_id"], card["zone"], card["quantity"]) for card in change["cards_before"]])
        db().execute("UPDATE decks SET cover_variant_id=? WHERE id=?", (change["cover_before"], change["deck_id"]))
    elif kind == "watchlist_created":
        db().execute("DELETE FROM named_watchlists WHERE id=? AND user_id=? AND is_default=0", (change["list_id"], uid))
    elif kind == "watchlist_entry_added":
        db().execute(f"DELETE FROM named_watchlist_entries WHERE {own_entry}", (change["entry_id"], uid))
    elif kind == "watchlist_entry_updated":
        db().execute(f"UPDATE named_watchlist_entries SET quantity=? WHERE {own_entry}", (change["quantity_before"], change["entry_id"], uid))


@app.post("/api/import/json/apply")
@login_required
def import_json_apply():
    p = request.get_json(force=True)
    rows = parse_json_backup(p.get("collection") or [])
    strategy = p.get("strategy", "add")
    changes = []
    import_stamp = now_iso()
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        vid, cond, qty, notes = row["match"]["variant_id"], row["condition"], row["quantity"], row.get("notes")
        graded, grade, override = row["is_graded"], row["grade_label"], row["price_override"]
        old = db().execute("SELECT quantity,notes,price_override,last_added_at FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=? AND is_graded=? AND grade_label=?", (user_id(),vid,cond,graded,grade)).fetchone()
        before, notes_before, override_before = (old["quantity"], old["notes"], old["price_override"]) if old else (0, None, None)
        after = qty if strategy == "replace" else before + qty
        last_added_at = import_stamp if after > before else (old["last_added_at"] if old else import_stamp)
        if not old and row["last_added_at"]:
            # Restoring into an empty slot keeps the backup's own timestamps, so "zuletzt
            # hinzugefügt" still reflects when the cards were collected, not when they were restored.
            last_added_at = row["last_added_at"]
        db().execute(
            """INSERT INTO collection_entries(user_id,variant_id,condition,quantity,notes,is_graded,grade_label,price_override,created_at,last_added_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,variant_id,condition,is_graded,grade_label) DO UPDATE SET
                 quantity=excluded.quantity,notes=COALESCE(excluded.notes,collection_entries.notes),
                 price_override=COALESCE(excluded.price_override,collection_entries.price_override),last_added_at=excluded.last_added_at""",
            (user_id(), vid, cond, after, notes, graded, grade, override, row["created_at"] or import_stamp, last_added_at)
        )
        changes.append({"variant_id":vid,"condition":cond,"is_graded":graded,"grade_label":grade,"before":before,"after":after,"notes_before":notes_before,"price_override_before":override_before,"last_added_at_before":old["last_added_at"] if old else None})
    applied = len(changes)
    summary = {
        **restore_backup_decks(p.get("decks") or [], strategy, changes), **restore_backup_watchlists(backup_watchlists(p), strategy, changes),
        **restore_backup_sheets(backup_sheets(p), strategy, changes),
    }
    games = sorted({row["match"]["game_id"] for row in rows if row.get("match")})
    cur = db().execute(
        "INSERT INTO import_operations(user_id,created_at,game_id,source_text,changes) VALUES(?,?,?,?,?)",
        (user_id(), now_iso(), ",".join(games) or "backup", "json-backup", json.dumps(changes))
    )
    db().commit()
    return jsonify({"operation_id":cur.lastrowid,"applied":applied,"matched":sum(1 for r in rows if r["status"]=="matched"),"total":len(rows),**summary})


@app.post("/api/import/apply")
@login_required
def import_apply():
    p = request.get_json(force=True)
    rows = parse_import(p.get("text",""),p.get("game_id","lorcana"),p.get("language","EN"),p.get("condition","Near Mint"))
    changes = []
    import_stamp = now_iso()
    for row in rows:
        if row["status"] != "matched" or not row.get("match"): continue
        vid, cond, qty = row["match"]["variant_id"], row["condition"], row["quantity"]
        old = db().execute("SELECT quantity,last_added_at FROM collection_entries WHERE user_id=? AND variant_id=? AND condition=? AND is_graded=0 AND grade_label=''", (user_id(),vid,cond)).fetchone()
        before = old["quantity"] if old else 0
        after = qty if p.get("strategy") == "replace" else before + qty
        last_added_at = import_stamp if after > before else (old["last_added_at"] if old else import_stamp)
        db().execute(
            """INSERT INTO collection_entries(user_id,variant_id,condition,quantity,is_graded,grade_label,created_at,last_added_at)
               VALUES(?,?,?,?,0,'',?,?)
               ON CONFLICT(user_id,variant_id,condition,is_graded,grade_label) DO UPDATE SET
                 quantity=excluded.quantity,last_added_at=excluded.last_added_at""",
            (user_id(),vid,cond,after,import_stamp,last_added_at),
        )
        changes.append({"variant_id":vid,"condition":cond,"before":before,"after":after,"last_added_at_before":old["last_added_at"] if old else None})
    cur = db().execute("INSERT INTO import_operations(user_id,created_at,game_id,source_text,changes) VALUES(?,?,?,?,?)", (user_id(),now_iso(),p.get("game_id"),p.get("text",""),json.dumps(changes)))
    db().commit()
    return jsonify({"operation_id":cur.lastrowid,"applied":len(changes)})


@app.post("/api/import/<int:operation_id>/undo")
@login_required
def undo_import(operation_id):
    op = db().execute("SELECT * FROM import_operations WHERE id=? AND user_id=?", (operation_id,user_id())).fetchone()
    if not op or op["undone_at"]: return jsonify({"error":"operation unavailable"}), 404
    for change in jload(op["changes"],[]):
        if change.get("kind"):
            undo_restored_item(change)
            continue
        # An import only ever touches one row of the variant: this condition and this grading.
        # Operations recorded before grading was tracked only wrote ungraded rows, hence the defaults.
        row_key = (user_id(), change["variant_id"], change["condition"], change.get("is_graded", 0), change.get("grade_label", ""))
        row_filter = "user_id=? AND variant_id=? AND condition=? AND is_graded=? AND grade_label=?"
        if change["before"] == 0:
            db().execute(f"DELETE FROM collection_entries WHERE {row_filter}", row_key)
        elif "price_override_before" in change:
            db().execute(f"UPDATE collection_entries SET quantity=?,notes=?,price_override=? WHERE {row_filter}", (change["before"],change["notes_before"],change["price_override_before"],*row_key))
        elif "notes_before" in change:
            db().execute(f"UPDATE collection_entries SET quantity=?,notes=? WHERE {row_filter}", (change["before"],change["notes_before"],*row_key))
        else:
            db().execute(f"UPDATE collection_entries SET quantity=? WHERE {row_filter}", (change["before"],*row_key))
        if change["before"] and change.get("last_added_at_before"):
            db().execute(f"UPDATE collection_entries SET last_added_at=? WHERE {row_filter}", (change["last_added_at_before"],*row_key))
    db().execute("UPDATE import_operations SET undone_at=? WHERE id=?", (now_iso(),operation_id)); db().commit()
    return jsonify({"undone":True})


@app.get("/api/export.<fmt>")
@login_required
def export_collection(fmt):
    """Every collection row exactly as stored -- one line per variant + condition + grading, the
    same key collection_entries itself is unique on -- so a backup restores to the identical
    collection. variant_id pins the exact print; the descriptive columns next to it keep the file
    readable and let parse_json_backup() re-match a row should an id ever change upstream."""
    uid = user_id()
    rows = db().execute(
        f"""SELECT v.id variant_id,g.name game,s.code set_code,s.name set_name,p.collector_number,i.canonical_name,p.language,v.finish,
          c.condition,c.quantity,c.notes,c.is_graded,c.grade_label,c.price_override,c.created_at,c.last_added_at,
          {latest_price_sql('v')} unit_price
          FROM collection_entries c JOIN variants v ON v.id=c.variant_id JOIN printings p ON p.id=v.printing_id
          JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id
          WHERE c.user_id=? AND c.quantity>0
          ORDER BY g.name,s.release_date,p.collector_number,v.id,c.condition,c.is_graded,c.grade_label""", (uid,)
    ).fetchall()
    data = [dict(r) for r in rows]
    if fmt == "json":
        # Decks, watchlists and sheets ride along so one file holds everything a user entered by hand.
        card_columns = """v.id variant_id,g.name game,s.code set_code,s.name set_name,p.collector_number,i.canonical_name,p.language,v.finish"""
        card_joins = """JOIN variants v ON v.id=e.variant_id JOIN printings p ON p.id=v.printing_id
          JOIN card_identities i ON i.id=p.identity_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=v.game_id"""
        decks = []
        for deck in db().execute("SELECT d.*,g.name game FROM decks d JOIN games g ON g.id=d.game_id WHERE d.user_id=? ORDER BY d.id", (uid,)):
            cards = db().execute(f"SELECT {card_columns},e.zone,e.quantity FROM deck_cards e {card_joins} WHERE e.deck_id=? ORDER BY e.zone,p.collector_number,v.id", (deck["id"],))
            decks.append({
                "name": deck["name"], "game": deck["game"], "format_id": deck["format_id"], "notes": deck["notes"],
                "cover_variant_id": deck["cover_variant_id"], "created_at": deck["created_at"], "updated_at": deck["updated_at"],
                "cards": [dict(card) for card in cards],
            })
        watchlists = []
        for watchlist in db().execute("SELECT w.*,g.name game FROM named_watchlists w JOIN games g ON g.id=w.game_id WHERE w.user_id=? ORDER BY w.id", (uid,)):
            entries = db().execute(f"SELECT {card_columns},e.quantity,e.created_at FROM named_watchlist_entries e {card_joins} WHERE e.list_id=? ORDER BY e.id", (watchlist["id"],))
            watchlists.append({
                "name": watchlist["name"], "game": watchlist["game"], "is_default": watchlist["is_default"],
                "entries": [dict(entry) for entry in entries],
            })
        sheets = []
        for sheet in db().execute("SELECT t.*,g.name game FROM trade_sheets t JOIN games g ON g.id=t.game_id WHERE t.user_id=? ORDER BY t.id", (uid,)):
            cards = db().execute(f"SELECT {card_columns},e.quantity,e.label FROM trade_sheet_cards e {card_joins} WHERE e.sheet_id=? ORDER BY e.id", (sheet["id"],))
            sheets.append({key: sheet[key] for key in ("name", "game", "kind", "subtitle", "background", "sort", "layout", "created_at", "updated_at")} | {"cards": [dict(card) for card in cards]})
        payload = {"format_version": 2, "exported_at": now_iso(), "collection": data, "decks": decks, "watchlists": watchlists, "trade_sheets": sheets}
        return Response(json.dumps(payload,indent=2,ensure_ascii=False),mimetype="application/json",headers={"Content-Disposition":"attachment; filename=deckledger-collection.json"})
    out = io.StringIO(); writer = csv.DictWriter(out,fieldnames=data[0].keys() if data else ["game","set_code","collector_number","canonical_name","language","finish","condition","quantity"]); writer.writeheader(); writer.writerows(data)
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=deckledger-collection.csv"})
