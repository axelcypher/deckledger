# Collection

What each user owns (copies per variant, condition and grading) and the user's named watchlists per game. `ophir_cards/collection.py` changes and lists owned copies, including the multi-select remove/undo; `ophir_cards/watchlists.py` manages the lists, their entries and the bulk edits on them.

## Public interface

### ophir_cards/collection.py

All routes `@login_required` and limited to the signed-in user's rows.

| Method | Path | What it does |
|---|---|---|
| POST | `/api/collection` | Change one row: `{variant_id, delta \| quantity, condition, is_graded, grade_label, price_override, notes, any_condition, request_id}`. Answers `{variant_id, condition, before, quantity}`. |
| POST | `/api/collection/remove` | Multi-select: `{variant_ids}` removes every condition and graded copy of those cards. Answers `{removed, copies, entries}`; `entries` are the removed rows. |
| POST | `/api/collection/restore` | Undo of remove: `{entries}` as remove returned them; quantities are added onto rows that exist again. Answers `{restored}`. |
| GET | `/api/collection/entries/<variant_id>` | The user's rows of one variant (condition, quantity, notes, grading, price override). |
| GET | `/api/collection?game_id=` | Owned cards of one game, one per variant, with `quantity`, `price`, `value`, `watchlisted`, plus `sets` and `stats` `{variants, copies, value}`. Filters: `q`, `set_id`, `language`, `rarity`, `finish`, `rarities`, `costs`, `colors`, `inkwell`, `mode` (`all`, `duplicates`, `watchlisted`). Sort: `number` (default), `name`, `set`, `rarity`, `value`, `quantity`. `stats_only=1` answers only the stats. |

Rules of `POST /api/collection`:

- A row is identified by (user, variant, `condition`, `is_graded`, `grade_label`), unique in `collection_entries`. `condition` defaults to `"Near Mint"` and is not validated; `grade_label` is kept only for graded copies.
- `quantity` wins over `delta`; the result is clamped at 0, and 0 deletes the row.
- `any_condition` with a negative delta on an ungraded card whose named condition holds no copy takes from the ungraded condition with the most copies (the card tile shows one total but only knows "Near Mint").
- `price_override` sent -> set (null or empty clears it); not sent -> kept. `notes` is only overwritten when sent. `last_added_at` moves only when the quantity grows.
- `request_id` (max 64 chars): a repeat with the same id returns the first answer instead of counting again (table `applied_requests`, rows older than 30 days are pruned on each such request).
- Errors: 400 for a non-object body or a non-numeric quantity/price, 404 for an unknown variant.

The other bulk actions of the collection multi-select live elsewhere: add to a watchlist (`/api/watchlists/<id>/entries/add`, below), add to a sheet (`/api/trade-sheets/<id>/cards` with `at_least`, see sheets.md), manual prices (`/api/variants/manual-prices` in `ophir_cards/prices.py`), eBay drafts (`ophir_cards/ebay.py`).

### ophir_cards/watchlists.py

All routes `@login_required`. A list belongs to one user and one game; every route checks that the list is the user's (404 `watchlist not found`), and entries must be variants of the list's game.

| Method | Path | What it does |
|---|---|---|
| POST | `/api/watchlist` | Heart: `{variant_id, list_id?, active?}`. Without `list_id` the user's default list of the card's game. Without a boolean `active` it toggles. Answers `{active, list_id, watchlisted}`. |
| GET | `/api/watchlists/membership?variant_id=` | The lists of the card's game with `count` and `contains` (the heart's long-press menu). |
| GET | `/api/watchlists?game_id=` | The user's lists of a game with `count` and `value` (price x wanted copies), default list first. |
| POST | `/api/watchlists` | Create `{game_id, name, variant_id?}`; with `variant_id` the card starts on it and `game_id` may be omitted. 409 when the name exists; 201. |
| PATCH | `/api/watchlists/<id>` | Rename `{name}` (max 80 chars). |
| DELETE | `/api/watchlists/<id>` | Delete; the default list cannot be deleted (400). |
| GET | `/api/watchlists/<id>/cards` | Entries with catalogue data, owned `quantity` and `desired_quantity`. Filters: `q`, `set_id`, `language`, `rarity`, `finish`, `rarities`, `costs`, `colors`, `inkwell`; sort `added` (default, newest first), `name`, `number`, `price_high`, `price_low`. |
| POST | `/api/watchlists/<id>/entries/add` | Bulk add `{variant_ids}`; only variants of the list's game; entries already there stay unchanged. Answers `{added}`. |
| PATCH | `/api/watchlists/<id>/entries/<variant_id>` | Wanted copies `{quantity}`, clamped to 1..99. |
| POST | `/api/watchlists/<id>/entries/move` | Bulk move `{variant_ids, target_list_id}` to another list of the same game (400 otherwise). Answers `{moved}`. |
| POST | `/api/watchlists/<id>/entries/remove` | Bulk remove `{variant_ids}`. Answers `{removed}`. |
| GET | `/api/watchlists/<id>/export.txt` | Plain text, `<qty>x <name>` per line, summed by card name and sorted alphabetically. |
| GET | `/api/watchlist?game_id=` | Legacy: the cards of the default list (first game by name when `game_id` is missing). |

`variant_watchlisted(variant_id)` - whether the card is on any of the signed-in user's lists (what a heart shows).

## Dependencies

- `ophir_cards/web.py` - `app`, `db`, `login_required`, `user_id`.
- `ophir_cards/prices.py` - `latest_price_sql()` for prices and values.
- `ophir_cards/catalog.py` - `query_matches_row()` for the text search.
- `ophir_cards/games` - `matches_catalog_filters()`, `rarity_rank()`.
- `ophir_cards/schema.py` - `ensure_user_lists()` creates the default list ("Merkliste", `is_default=1`) for every user and game, at start-up and right after an account is created.
- Tables: `collection_entries`, `applied_requests`, `named_watchlists`, `named_watchlist_entries`; read: the catalogue and price tables.

## Decisions and assumptions

- The read-modify-write routes (`POST /api/collection`, remove, restore, watchlist move) start with `BEGIN IMMEDIATE`, because two quick clicks arrive on two threads and would otherwise read the same old quantity (code comment). An error answer after that leaves nothing changed: the open transaction is dropped when the request's connection closes.
- Collection and watchlist rows are small, user-specific result sets, so they are loaded and then filtered in Python with the same `matches_catalog_filters()` the catalogue uses, instead of duplicating game-specific SQL (docstring in `ophir_cards/games`). The text search runs last because it is the most expensive test.
- A collection card is one variant; the answer still wraps it as `variants: [itself]`, `variant_count: 1` so the frontend can treat it like a catalogue card.
- A watchlist entry's `quantity` is how many copies are wanted, not how many are owned.
- Moving entries: a card already on the target list keeps its wanted quantity there instead of merging two counts (docstring). Copy and delete run as one transaction.
- The heart menu sends an explicit `active`, so a second click that crosses an earlier one in flight cannot undo it (code comment).
- Other writers: `ophir_cards/backup.py` (import and undo) changes both tables; `ophir_cards/deals.py` and `catalog_sync.py` change `collection_entries`; `ophir_cards/admin.py` deletes watchlist entries when an account or game is removed.
