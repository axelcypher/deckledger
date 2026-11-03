# Sheets

Trade and sale sheets: named, per-game card selections for "want to sell / trade / buy" posts, stored per account and rendered on the server as one or more collage images plus a Markdown list for the post. `deckledger/sheets.py` does storage, sorting and image lookup; `sheet_render.py` does layout and drawing.

## Public interface

### deckledger/sheets.py

Names other modules import:

- `SHEET_KINDS = ("WTS", "WTT", "WTS/WTT", "WTB", "WTTF", "WTB/WTTF")` and `WANTED_SHEET_KINDS` (the last three) - used by `backup.py`, `deals.py`, `watcher.py`. A sheet is either an offer (WTS/WTT) or a search (WTB/WTTF), never both.
- `SHEET_SORTS = ("number", "rarity")` - used by `backup.py`.
- `RESERVED_SQL` - correlated subquery: copies of `v.id` that the user's deals in status `reserved` give away (one `?` for the user id). Used by `deals.py`.
- `own_sheet(sheet_id)` - the signed-in user's `trade_sheets` row or None. Used by `watcher.py`.
- `remove_user_backgrounds(uid)` - deletes an account's uploaded backgrounds, rows and files, without committing. Used by `admin.py`.
- `sheet_cards(sheet)`, `sheet_text(sheet, cards)`, `sheet_payload(sheet)`.

The sheet payload (answer of GET/PATCH of a sheet and of card updates) is `{"sheet": row, "cards": [...], "pages": [{"columns", "rows", "count"}], "text": "<markdown>"}`. Each card carries `quantity`, `label`, `price` (current market price), `owned`, `reserved` (0 on wanted sheets, otherwise capped at the quantity) and `ebay` (the cheapest active own listing of the variant as an object, or null).

Routes, all `@login_required` and limited to the signed-in user's sheets and backgrounds (404 otherwise):

| Method | Path | What it does |
|---|---|---|
| GET | `/api/trade-sheets/options` | Kinds, backgrounds (built-in plus the user's `custom-<id>`), layouts (`auto` and every `CxR` of `sheet_render.LAYOUTS` with more than one slot). |
| GET | `/api/trade-sheets?game_id=` | The user's sheets of one game with `card_count` and `copies`, last changed first. |
| POST | `/api/trade-sheets` | Create `{game_id, name, kind}`; an unknown kind becomes `WTS`, an empty name `Neues Sheet`; 404 for an unknown game; 201 `{id}`. |
| GET | `/api/trade-sheets/<id>` | The payload. |
| PATCH | `/api/trade-sheets/<id>` | Update `name`, `subtitle` (each max 80 chars), `kind`, `background`, `sort`, `layout`, `scout`, `scout_communities`, `ebay_sync`; an invalid value keeps the old one. Switching `ebay_sync` on runs `ebay.update_sheets_from_listings` at once. |
| DELETE | `/api/trade-sheets/<id>` | Delete; comments filed under its post links are first handed to another sheet of the user linked to the same post (`watcher.hand_over_comments`). |
| POST | `/api/trade-sheets/<id>/cards` | One entry `{variant_id, delta \| quantity \| at_least, label}` or `{entries: [...]}`. Quantity clamped 0..99, 0 removes the card; `at_least` never lowers an existing count (used when a selection is taken over); `label` max 24 chars, kept when not sent. Answers the payload plus `skipped`. |
| GET | `/api/trade-sheets/<id>/image/<page>.<jpg\|png>` | One rendered page. `?scale=` 0.2..1 (default 1), `?download=1` adds an attachment file name. ETag/304; `Cache-Control: private, no-cache`. |
| POST | `/api/trade-sheets/backgrounds` | Upload a picture (multipart `file`, optional `name`, max 40 chars); max 25 MB and 24 per user; 201 `{id: "custom-<id>", label, custom: true}`. |
| DELETE | `/api/trade-sheets/backgrounds/<name>` | Delete an uploaded background; sheets using it go back to `midnight`. |
| GET | `/api/trade-sheets/backgrounds/<name>.jpg` | 240x150 preview swatch of a built-in or own background. |

Card update errors: a single entry for an unknown variant or another game is a 400; in a batch such entries are skipped and counted in `skipped`. A non-numeric quantity or more than `SHEET_CARD_LIMIT` (400) distinct cards is a 400, and then nothing of the request is stored (the open transaction is dropped with the connection).

### sheet_render.py (repo root)

No Flask or database dependency (Pillow only), so it can be tested and tuned on its own.

- `render_page(cards, columns, rows, *, background, kind, title, subtitle, page=(n, total), scale, background_image=None, accent=None) -> Image` (RGBA). `cards` must already be sorted and cut to the page; each is a dict with `image_path`, `name`, `set_code`, `number`, `quantity`, `label`, `holo`, `reserved`, `ebay`. A missing or unreadable image becomes a labelled stand-in tile.
- `paginate(count, layout) -> [(columns, rows, on_page)]` and `page_slices(cards, layout)` (same, with the card lists). `"auto"`: full pages use the largest layout (8x5), the last page the smallest one that fits. A fixed `"CxR"` (up to `MAX_COLUMNS` 10 x `MAX_ROWS` 6, checked by `parse_layout`) is used for every page.
- `BACKGROUNDS` (eight drawn mats: id -> label, top, bottom, accent, glow colours), `DEFAULT_BACKGROUND = "midnight"`, `swatch(name)`.
- `prepare_photo(source, target) -> accent` - stores an upload upright, RGB, at most 3600 px, as JPEG, and returns an accent colour taken from the picture; raises for anything Pillow cannot read. `photo_background(size, path)` fills a page with it.
- `is_holo(finish, variant_code, rarity, game_id, is_parallel) -> bool` - foil detection with the same rules as `finishPresentation()` in `static/js/finish.js`; also used by `deckledger/ebay.py`.
- `trim_bleed(image, bleed)`, `PRINT_BLEED`, `CUT_CARD_RATIO` - used by `deckledger/images.py` and the VCard game module.
- `scale_for(columns)` - scale that keeps the widest layouts within 3600 px.
- `FONT_PATH` - the bundled `static/fonts/Manrope.ttf`.

## Dependencies

- `sheet_render.py` - layout and drawing.
- `deckledger/images.py` - `card_image()` for each card's (bleed-trimmed) image file.
- `deckledger/prices.py` - `latest_price_sql()`.
- `deckledger/catalog.py` - `natural_code_key()` for sorting.
- `deckledger/games` - `rarity_rank`, `RARITY_FALLBACK_RANK`, the game's `languages` for the text list.
- `deckledger/ebay.py` - `update_sheets_from_listings()`; the payload reads `ebay_listings`.
- `deckledger/watcher.py` - `hand_over_comments()` on delete (imported inside the function, since `watcher` imports this module).
- `deckledger/web.py` - `app`, `db`, `login_required`, `user_id`, `derived_from_file`.
- Tables: `trade_sheets`, `trade_sheet_cards`, `sheet_backgrounds`; read: `sheet_posts`, `deals`, `deal_cards`, `collection_entries`, `ebay_listings` and the catalogue.
- Data dir: `sheet-backgrounds/<id>.jpg`, `sheet-renders/<key>.<jpg|png>`.

## Decisions and assumptions

- Offers and searches are separate sheets because one picture cannot say which of its cards are offered and which are wanted (code comment, commit 20d9a9f). A wanted sheet never shows reserved copies.
- Drawing a page takes one to five seconds, so pages are cached on disk under a key (SHA-256) over everything drawn: a hash of `sheet_render.py` itself, the font file, each card with the mtime and size of its image file, layout, background (and its file), kind, name, subtitle, page, scale and format. The key is also the ETag. No explicit invalidation exists or is needed: any change gives a new key. The cache is shared by the workers (temp file + `os.replace`) and keeps the 200 most recently used pages, at most 200 MB.
- Sort `number`: release date, set code, collector number (natural order), rarity rank, finish, variant id. Sort `rarity`: ranked rarities rarest first, unranked ones (promos, tokens) after them.
- Text list: Reddit-flavoured Markdown, one line per card. The language is shown only for games printed in more than one language; the label is the price, and without one the current market price is used, formatted in euros with a decimal comma, but only on sheets whose kind includes WTS.
- The drawn backgrounds are an own playmat look in VCard's element colours; no publisher artwork or logo is used (module docstring). An uploaded picture is referred to as `custom-<id>`; one whose row or file is gone falls back to the default mat. Swatches are cached in process memory.
- Holo cards get a rainbow sheen and a pearly frame because a holo scan looks like its regular print, and on a sale sheet that difference is the price (code comment).
- The card update starts with `BEGIN IMMEDIATE` before reading (read-modify-write: quick clicks must not overwrite each other).
- `scout` / `scout_communities` steer the search of other people's posts in `deckledger/watcher.py`; communities are stored lower-case and comma-separated (names `[A-Za-z0-9_]{2,21}`, max 400 chars, empty = all of the game's communities).
- `ebay_sync` sheets take quantity and price label of their cards from the account's active eBay listings (only WTS and WTS/WTT sheets, see `ebay.update_sheets_from_listings`).
