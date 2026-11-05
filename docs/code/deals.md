# Deals

`ophir_cards/deals.py` keeps a user's deals ("Vorgaenge"): sales, purchases and trades with any partner on any platform, and books them into the collection and the user's trade sheets as the deal's status changes.

## Public interface

### Model

- `deals`: one deal per row, owned by one user and tied to one game. Header fields: `partner` (free text, 80 chars), `platform` (40), `url` (500; kept only when it starts with `http://` or `https://`, else stored as `''`), `note` (2000), `money_in`, `money_out`, `shipping` (0 to 1,000,000, comma accepted as decimal separator, rounded to cents). Timestamps `reserved_at`, `completed_at`, `received_at`, `cancelled_at`.
- `deal_cards`: the two sides, `give` (going out) and `get` (coming in); unique per (deal, side, variant), quantity 1-99, optional `unit_price`. At most 200 distinct cards per deal (`DEAL_CARD_LIMIT`). A new card must exist in the deal's game; its `name` and `detail` (set, number, language, finish) are copied in at that moment. `booked` = how many copies this deal actually took out of or put into the collection.
- `deal_events`: history, `kind` one of `created`, `reserved`, `open`, `done`, `received`, `cancelled`, with an optional German `detail`.
- Derived in the payload: `kind` = `trade` (both sides), `sale` (give only), `purchase` (get only); `in_transit` = done, not received, has get cards; `value_give` / `value_get` = sum of unit price x quantity.

### Statuses and what they book

| Status (UI label) | May change to | Effect in collection and sheets |
| --- | --- | --- |
| `open` (Offen) | reserved, done, cancelled | Nothing is booked. |
| `reserved` (Reserviert) | open, done, cancelled | Nothing moves. The give cards count as reserved through `sheets.RESERVED_SQL`; offer sheets show them as reserved. Needs at least one give card. |
| `done` (Abgeschlossen) | cancelled | `book_done`: each give card is taken from the collection (up to its quantity, ungraded rows first, then the larger rows), the number found goes into `booked`, and offer sheets (every kind except WTB, WTTF, WTB/WTTF) are lowered by the full quantity, entries at 0 deleted. Missing copies are no error: the event detail lists them ("Nicht in der Sammlung: ..."). Get cards are not booked yet. |
| `cancelled` (Abgebrochen) | open | From `done`: `unbook` reverses every `booked` count (give copies added back, get copies taken out again) and resets `booked` to 0; sheets are not touched. `received_at` is cleared. From open/reserved nothing was booked. |

- `reserved` and `done` require at least one card (400 otherwise).
- Changing to `open` clears all four status timestamps.
- Arrival (`receive`): only for a `done` deal without `received_at` that has get cards. `book_received` adds every get card to the collection, sets `booked` to its quantity and lowers wanted sheets (WTB, WTTF, WTB/WTTF) by it.
- Copies added to the collection (receive, or cancelling a sale) always land in condition "Near Mint", ungraded.
- Every status change, every receive and every give-card change of a reserved deal bumps `trade_sheets.updated_at` of the sheets holding those cards (`touch_sheets`), because sheet images are cached by it.

### Editing rules

- Header and cards can change only while the deal is `open` or `reserved` (`EDITABLE`). A `done` or `cancelled` deal accepts only `note` in a PATCH; the cards endpoint answers 409.
- Only `open` and `cancelled` deals can be deleted (409 otherwise); cards and events go with them (ON DELETE CASCADE).

### HTTP endpoints

All are `@login_required` and only see the session user's deals. Errors are `{"error": "<German text>"}`.

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/deals` | List, newest first by `COALESCE(completed_at, updated_at)`. Filters: `game_id`, `status` (a state, or `transit` for in-transit deals), `q` (forgiving search over partner, platform, note and card names). Returns `{deals, totals}`; deals without events. `totals` sums money only over `done` deals; `balance` = in - out - shipping. |
| POST | `/api/deals` | Create from `{game_id, partner, platform, url, note, money_in, money_out, shipping, cards: [...]}`. 404 unknown game, 400 bad amount or card (nothing is stored then). 201 with the deal payload. |
| GET / PATCH / DELETE | `/api/deals/<id>` | Read with events; change header fields; delete (see rules). |
| POST | `/api/deals/<id>/cards` | One card: `{side, variant_id, quantity | delta, unit_price}`; quantity 0 removes it. |
| POST | `/api/deals/<id>/status` | `{status}`; 409 when the transition is not allowed. |
| POST | `/api/deals/<id>/receive` | Confirms that the get cards arrived (see above). 409 when nothing is waiting, 400 without get cards. |
| GET | `/api/variants/<variant_id>/deals` | Up to 100 reserved or done deal rows that include this card, newest first, each with `in_transit`. |

The deal payload is the `deals` row plus `cards` (each with catalogue fields, `owned`, `known` = still in the catalogue, and `reserved` = copies reserved by other deals: a reserved deal's own reservation is subtracted), `kind`, `in_transit`, `value_give`, `value_get` and, for single deals, `events`.

### Python functions used by other modules

- `book_done(deal)`, `set_card(deal, entry)`, `log_event(deal_id, kind, detail="")`, `touch_sheets(uid, variant_ids)`: used by `ebay.deals_from_sales` to book eBay sales as done deals. They work on `web.db()` and never commit; a caller without a request has to put its connection into `g.db` first (`ebay.on_connection`). `set_card` returns an error text or None; `book_done` returns the shortfall text.
- `DEAL_STATES`, `SIDES`: used by `backup` to validate restored deals.
- The card, status and receive endpoints open `BEGIN IMMEDIATE` before reading, so concurrent changes to one deal are serialized.

## Dependencies

- `ophir_cards.web` - Flask app, `db()`, `login_required`, `user_id`.
- `ophir_cards.sheets` - `RESERVED_SQL` (reserved copies per variant), `WANTED_SHEET_KINDS`.
- `ophir_cards.search` - `matches()` for the `q` filter.
- `ophir_cards.config` - `now_iso`.
- SQLite tables `deals`, `deal_cards`, `deal_events` (migration `m0007_deals`), `collection_entries`, `trade_sheets`, `trade_sheet_cards`, and the catalogue tables for card snapshots.

## Decisions and assumptions

- A sheet is the shop window, a deal the business done; partner and platform are free text and a link is optional, so a deal works with anybody anywhere (module docstring, commit 5cf79a0).
- Incoming cards enter the collection only when their arrival is confirmed, not when the deal is done (module docstring).
- A done deal is the record and is not edited any more, except for its note (module docstring, PATCH comment).
- `deal_cards.variant_id` is deliberately no foreign key and the card's name and detail are copied, so the history outlives catalogue changes; such cards come back with `known: false` (m0007 docstring).
- Cancelling a done deal undoes the collection booking but leaves sheets alone: what stands on them is the user's decision once the cards are back (`unbook` docstring).
- Selling cards that were never entered in the collection is allowed; the deal books what it finds and records the shortfall (`book_done`, README).
- Original condition and grading of booked copies are not remembered: copies coming back are added as Near Mint, ungraded (`add_to_collection` defaults).
- eBay sales become done deals automatically (see `ebay.md`); `ebay_sales.deal_id` remembers the deal, so a deal the user deletes does not come back with the next sync (m0014 docstring).
- Backups restore deals as a record without booking anything again (see `backup.md`).
