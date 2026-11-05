# Backup

`ophir_cards/backup.py` moves a user's data in and out: the text-list import into the collection (preview, apply, undo), the CSV and JSON export, and restoring a JSON backup (collection, decks, watchlists, sheets, deals), which can be undone as well.

## Public interface

### HTTP endpoints

All are `@login_required`; everything read or written belongs to the session user.

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/import/preview` | `{text, game_id, language, condition}` (defaults `lorcana`, `EN`, `Near Mint`) -> one result per non-empty line. Writes nothing. |
| POST | `/api/import/apply` | Same input plus `strategy`: `replace` sets the quantity, anything else adds to it. Writes the matched lines into ungraded collection rows and records an `import_operations` row. Returns `{operation_id, applied}`. |
| POST | `/api/import/json/preview` | A backup payload -> one result row per collection entry, plus one summary row per deck, watchlist, sheet and deal (`kind`, how many of its cards were found, or that its game does not exist here). Writes nothing. |
| POST | `/api/import/json/apply` | A backup payload plus `strategy` (`add` default, or `replace`) -> restores everything (see below) and records one `import_operations` row (`source_text` `json-backup`). Returns counts per part and `operation_id`. |
| POST | `/api/import/<operation_id>/undo` | Reverts one text or backup import, once. 404 when the operation is unknown, another user's or already undone. |
| GET | `/api/export.json` | Full backup as download `ophir-cards-collection.json`. |
| GET | `/api/export.<other>` | Any other suffix: the collection rows as CSV, download `ophir-cards-collection.csv`. |

### Text import

- One card per line, fields separated by `;`. First field: optional quantity (`3` or `3x`), card code `[SET:]NUMBER[/TOTAL]`, optional language `DE`, `EN` or `JP`. Further fields in order: quantity, language, variant hint (finish or variant code, default `standard`), condition. Values in the first field win over the later fields; missing ones fall back to the request's language and condition.
- The number goes through `catalog.match_collector_number(game, number, language, set, variant_hint)`. When that is ambiguous or finds nothing, the whole first field (quantity and a trailing language removed) is tried as an exact card name (case-insensitive) in that language: a hit is always `matched`, on the variant hint's print, else the base/Normal print of the newest set, with a note how many other prints exist.
- Result per line: `line`, `original`, `quantity`, `number`, `language`, `condition`, `status` (`matched`, `ambiguous`, `not_found`), `match` (variant row), `message`, `inferred` (language not given right after the code). Only `matched` lines are applied.

### JSON backup (`format_version` 2)

- `collection`: every `collection_entries` row with quantity > 0, one line per variant + condition + grading: `variant_id`, game name, set code and name, collector number, canonical name, language, finish, condition, quantity, notes, `is_graded`, `grade_label`, `price_override`, `created_at`, `last_added_at`, and the current `unit_price` (informational, not restored).
- `decks`: name, game, `format_id`, notes, `cover_variant_id`, timestamps, cards with zone and quantity.
- `watchlists`: named watchlists with `is_default` and entries (quantity, `created_at`).
- `trade_sheets`: name, game, kind, subtitle, background, sort, layout, timestamps, cards with quantity and label.
- `deals`: header fields, status and its timestamps, cards (side, `variant_id`, quantity, unit price, kept name and detail, `booked`, plus the catalogue fields while the card is still known), events.
- Nothing else: no settings (eBay template, Reddit name, subreddits), no eBay data, no linked posts or inbox, no sheet search or eBay-sync switches, no uploaded files.
- The CSV holds only the collection rows.

### Matching a backup row (`parse_json_backup`)

1. `variant_id` is trusted when that variant exists and its language and finish equal the row's (or the row has neither).
2. Else a strict match on game name, set code or set name, collector number (case-insensitive), language, finish and canonical name: one hit = `matched`, several = `ambiguous`. This step needs language, finish, game, set code and name; the number may be empty.
3. No hit at all: the same without the name; exactly one hit = `matched` with a note about the different name, several = `ambiguous`, none = `not_found`.

### Restore rules

Games are matched by name; items of an unknown game are skipped.

- Collection: per (variant, condition, graded, grade label), the quantity is added (or, with `replace`, set). Notes and price override only overwrite when the backup has them. A row that did not exist keeps the backup's `created_at` and `last_added_at`.
- Decks, by game + name: a missing deck is created; an existing one is skipped unless `replace`, which replaces all its cards. The backup's format is used when the game has it, else the game's first; zones the format does not know become `main`. The cover is restored only when it is among the restored cards.
- Watchlists, by game + name: created when missing; entries added; an existing entry's quantity changes only with `replace`.
- Sheets, by game + name: like decks. Kind, sort, layout and background are validated (a background must be a built-in one, else `midnight`).
- Pre-sheet backups (watchlists still carry an `is_sale_list` key): the old sale list - the flagged list, or a non-default list named "Verkaufsliste" - is restored as a WTS sheet, not as a watchlist.
- Deals: created as the record they are, whatever the strategy, unless the user already has a deal with the same partner and `created_at`. Nothing is booked; status, timestamps, `booked` counts and events are taken over. A card this catalogue does not know keeps the backup's id and name.

### Undo (`import_operations.changes`)

- `changes` is a JSON list. Collection steps: `{variant_id, condition, is_graded, grade_label, before, after, notes_before, price_override_before, last_added_at_before}` (text imports record fewer keys). Item steps have a `kind`: `deck_created`, `deck_replaced`, `watchlist_created`, `watchlist_entry_added`, `watchlist_entry_updated`, `sheet_created`, `sheet_replaced`, `deal_created`.
- Undo sets every touched collection row back to its `before` quantity (deletes it when `before` was 0) and restores notes, price override and `last_added_at` where recorded; replaced decks and sheets get their old cards (and deck cover) back; created decks, sheets, deals and non-default watchlists are deleted. Every statement is scoped to the user. Then `undone_at` is set.
- Undo writes absolute values: changes made to the same rows after the import are overwritten.
- `catalog_sync.py` rewrites variant ids inside stored `changes` when it remaps variants, so older operations stay undoable.

## Dependencies

- `ophir_cards.catalog` - `match_collector_number`, `split_set_prefix` for the text import.
- `ophir_cards.games` - deck formats and zones.
- `ophir_cards.sheets` - `SHEET_KINDS`, `SHEET_SORTS`.
- `ophir_cards.deals` - `DEAL_STATES`, `SIDES`.
- `ophir_cards.prices` - `latest_price_sql` for the exported `unit_price`.
- `sheet_render` (repo root) - `BACKGROUNDS`, `parse_layout`.
- `ophir_cards.web`, `ophir_cards.config`.
- SQLite tables `collection_entries`, `decks`, `deck_cards`, `named_watchlists`, `named_watchlist_entries`, `trade_sheets`, `trade_sheet_cards`, `deals`, `deal_cards`, `deal_events`, `import_operations`, and the catalogue tables for matching.

## Decisions and assumptions

- The export has one line per stored collection row, the key `collection_entries` is unique on, so a backup restores the identical collection; `variant_id` pins the print and the descriptive columns let a restore re-match should an id change (`export_collection` docstring).
- Canonical names and set codes drift between catalogue snapshots (Lorcana's set codes changed from numbers to letter codes), so matching falls back to set name and to a name-agnostic match, but only when the strict match finds nothing: an ambiguous strict match still asks a human (`parse_json_backup` docstring).
- In the text import a plain number is often ambiguous (Lorcana restarts at 1 every set), hence the fallback that reads the line as a card name (comment in `parse_import`).
- Decks and sheets of the same name are never merged card by card (`restore_backup_decks` docstring).
- Deals come back without booking, because the collection of the same backup already is what those deals made it (`restore_backup_deals` docstring).
- A backup made before trade sheets existed restores its sale list as the WTS sheet it has since become (see `migrations/m0002_sale_lists_to_sheets.py`).
- One apply is one operation: undo is all or nothing per operation.
