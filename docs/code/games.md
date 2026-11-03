# Games

`deckledger/games/` is the per-game rules registry: each supported trading card game describes itself once as a frozen `Game` dataclass, and the rest of the application asks `game(game_id)` instead of branching on a game id.

## Public interface

### `__init__.py` - registry and lookups

- `GAMES: dict[str, Game]` - the shipped games keyed by id: `lorcana`, `one-piece`, `hololive`, `vcard`.
- `game(game_id) -> Game` - the module's `Game`, else `GENERIC` (`Game()` with plain defaults) for any id without a module, such as a game an admin created. Never raises.
- `DECK_RULESETS: dict[str, Game]` - shipped games keyed by `Game.ruleset` (`lorcana-standard`, `one-piece-standard`, `hololive-standard`, `vcard-standard`). Deck validation and zone suggestion look the ruleset up by the `games.deck_ruleset` column, so an admin-created game can be pointed at a shipped ruleset. Formats (zones, targets) still come from `game(game_id)`.
- `playset_size(game_id)`, `rarity_rank(game_id, rarity)` - an unknown label ranks `RARITY_FALLBACK_RANK` (900), i.e. last.
- `rarity_filter_ranks(game_id, keys)` - icon-filter keys to ranks; unknown keys are ignored.
- `cost_matches(game_id, card_cost, selected_costs)` - with `cost_filter_cap` set, the cap value means "this or more"; non-digit values are ignored.
- `zone_for_card_type(deck_ruleset, card_type)` - zone id from the ruleset's `zone_for_card_type`, default `"main"`.
- `matches_catalog_filters(game_id, rarity, attributes, selected_rarities, selected_costs, selected_colors, inkwell)` - in-memory counterpart of the catalogue filters for collection and watchlist rows. Colors split on `-` or `/`; `inkwell` is the string `"true"`/`"false"` (anything else = no filter); a card without a numeric cost fails a cost filter.
- `rarity_case_sql(game_id, column)` - SQL `CASE` for `ORDER BY` that mirrors `rarity_rank`; without a rarity ladder it is `<column> COLLATE NOCASE`.

### `base.py` - the `Game` description

`Game` is `@dataclass(frozen=True)`; every field has a default, so a module sets only what differs.

| Group | Fields | Notes |
| --- | --- | --- |
| Identity | `id`, `module_id`, `name`, `short_name`, `module_version`, `languages`, `accent`, `symbol`, `icon`, `logo_url` | The first seven are seeded into the `games` table (schema.py). `icon` names `static/tcg-icons/<icon>.svg`. |
| Collecting | `playset_size` (4), `rarity_order`, `rarity_filter_keys`, `premium_ranks`, `cost_filter_cap`, `tile_editions`, `is_foil`, `main_set_types` | `premium_ranks`: printings listed separately, outside Base%. `tile_editions`: (id, label, regular finish, foil finish, foil button label) per print run. `is_foil(variant, game)` decides Foil% (default: finish is not `Normal`). `main_set_types`: lower-case `set_type` values counted as "the main game" on the dashboard. |
| Decks | `formats`, `ruleset`, `zone_for_card_type`, `validate_deck`, `copy_limits`, `playable_types`, `unplayable_message`, `excluded_deck_set_types`, `deck_language`, `deck_catalog_across_printings`, `auto_fill_zone` | `formats`: dicts with `id`, `name`, `description`, `zones` (`[{id, name, target}]`), `rules_url`. `validate_deck(deck, cards, counts) -> (errors, warnings)`: lists of German messages; `counts` is copies per zone. `auto_fill_zone`: (zone, size, variant id) the builder fills open slots with. |
| Sources | `provider`, `price_method`, `price_overrides`, `cardmarket_game_id`, `image_bleed`, `image_placeholders`, `official_foil_layers`, `remote_image_url`, `remote_set_visual`, `bundled_set_logo`, `market_links` | `provider` = (minimum sets, minimum cards, timeout s) seeds `catalog_providers`; the price fields seed `games` columns where NULL. `image_placeholders` = (size, SHA-256) of stand-in images that count as missing. Hooks: `remote_image_url(row)` and `remote_set_visual(set_row)` return a URL or None; `bundled_set_logo(set_row, public_dir)` returns a path or None; `market_links(variant, attributes, mapping, search_term)` returns `price_source`, `price_url`, `image_source`, `image_source_url`. |

Methods: `rarity_rank(rarity)`; `copy_limit(card_type)` (by type, else `playset_size`); `format(format_id)` (that format, else the first, else `{}`); `deck_card_error(card, adding)` (reason text or None: an excluded set type always, an unplayable card type only when adding); `client_rules()` (`playset_size`, `copy_limits`, `symbol`, `icon`, `tile_editions` - sent to the frontend with each game in `/api/bootstrap`).

Helpers: `fetch_html(url)` (GET with a DeckLedger User-Agent, 18 s timeout, first 2 MB), `generic_market_links` and `any_other_finish` (the defaults).

Who reads what:

| Consumer | Uses |
| --- | --- |
| `schema.py` | identity fields, `provider`, `ruleset`, `price_method`, `price_overrides`, `cardmarket_game_id` |
| `admin.py` | `GAMES[...].provider` (is a provider shipped) |
| `catalog.py` | `playset_size`, rarity ranks and filter keys, `premium_ranks`, `cost_filter_cap`, `is_foil`, `market_links` |
| `decks.py` | `formats`/`format`, `DECK_RULESETS` (`validate_deck`), `zone_for_card_type`, `deck_language`, `deck_catalog_across_printings`, `auto_fill_zone`, `deck_card_error`, `cost_filter_cap`, `rarity_case_sql` |
| `images.py` | `remote_image_url`, `image_placeholders`, `image_bleed`, `official_foil_layers` |
| `assets.py` | `logo_url`, `remote_set_visual`, `bundled_set_logo` |
| `pages.py` | `main_set_types`, `client_rules()` |
| `collection.py`, `watchlists.py` | `matches_catalog_filters` |
| `sheets.py` | `rarity_rank`, `RARITY_FALLBACK_RANK`, `languages` |
| `backup.py` | `formats` |

### The four game modules

Each module exports `GAME`.

| Module | id, languages | What is specific |
| --- | --- | --- |
| `lorcana.py` | `lorcana`, DE/EN | English and German rarity labels share ranks 0-8; Epic/Enchanted/Iconic are premium ranks and do not count as foil; cost filter cap 7; one tile edition Normal/Silver. Formats core, infinity, coconut (60-card deck). Validation: at least 60 cards, at most 4 per name, at most 2 ink colors (coconut: 3, only the coconut character may repeat), quest-set cards refused, core legality from the card's `legality` attribute. Provider (15, 2500, 300); price method `cardmarket`, Cardmarket game id 19; `official_foil_layers`. Images from the Lorcast search API, set visuals scraped from disneylorcana.com product pages (`PRODUCT_PATHS`), bundled logos `public/icons/lorcana/setNN-logo.png` for numeric set codes. |
| `one_piece.py` | `one-piece`, EN/JP | Format standard: leader 1, main 50, don 10; `Leader` -> leader, `DON!!` -> don. Validation: exactly 1 leader, 50 main cards, at most 10 DON!!, at most 4 per collector number, zone/type checks, a warning for cards off the leader's color. Deck builder opens in EN and lists one printing per number; open DON!! slots are filled with a standard DON!! variant. Provider (20, 1000, 600); `cardmarket`, game id 18. Image URLs built from the official card list (`_p1` for parallel/manga); set visuals scraped from product pages. |
| `hololive.py` | `hololive`, EN/JP | Format standard: oshi 1, main 50, cheer 20, zones from card type (EN and JP labels). Validation mirrors that, at most 4 per number in the main deck. Provider (10, 500, 600); price method `tcgcsv`, JP override `yuyutei`. Images scraped from the official card search (EN falls back to the JP catalogue), set visuals from the official card list page; market links go to Yuyutei for JP. |
| `vcard.py` | `vcard`, EN | Playset 3, Mascot limited to 2; only VT, Mascot, Support, World are playable. Two tile editions (Limited/Unlimited, 1st Edition), each plain and Holo; foil = finish contains `Holo`. Validation: 50 cards, copies counted per (name, type, cost), at most 2 elements (Neutral excluded), at least one PL8 VT. Provider (4, 1200, 300); no price method. `image_bleed = sheet_render.PRINT_BLEED`, one known placeholder image; no `remote_image_url`; market links are an eBay search. |

Adding a game: a module here, listed in the tuple that builds `GAMES`, a provider at `providers/<id with - as _>.py` and an icon `static/tcg-icons/<icon>.svg`. tests/test_games.py checks that every shipped game is complete (formats, ruleset, validator, rarity ladder, provider, logo, main set types, provider file, icon).

## Dependencies

- `deckledger.config` - `jload` (validators of lorcana, one_piece, vcard)
- `sheet_render` (repository root) - `PRINT_BLEED` for VCard
- urllib (stdlib) - the hooks' HTTP requests
- External sites the hooks read: api.lorcast.com, disneylorcana.com, en./www.onepiece-cardgame.com, en./hololive-official-cardgame.com, vcardtcg.com; `market_links` only builds URLs (Cardmarket, TCGplayer, Yuyutei, eBay).

## Decisions and assumptions

- One module per game holds all of its rules; no module outside `games/` compares a game id (commit 4833e12). tests/test_games.py fails on `game_id == "..."`-style comparisons in `deckledger/*.py`.
- A game without a module gets `Game()` defaults: four-copy playsets, no formats, no deck rules, no remote sources.
- Ranking always uses the module's `rarity_order`; `rarity_case_sql` inlines those labels as SQL literals, which is safe because they are code constants, never user input.
- Lorcana maps the English and the official German rarity terms to the same rank because printings carry the label in their own language.
- VCard has no price method on purpose: no marketplace feed lists it, so prices are entered by hand (an admin can assign one later).
- Lorcana images come from Lorcast's API image URIs; CDN URLs are deliberately not constructed.
- The `remote_*` hooks make blocking HTTP requests (12-18 s timeouts) when an image or set visual is requested and may raise on network errors; only One Piece's set-visual hook skips pages that fail.
