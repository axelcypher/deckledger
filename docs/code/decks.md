# Decks

Deck builder backend: per-user decklists with zones and a format, the builder's paginated card catalogue, validation against the game's deck rules, owned/missing copies against the collection, and text import and export.

## Public interface

All routes `@login_required`; decks are limited to the signed-in user (404 `deck not found`).

| Method | Path | What it does |
|---|---|---|
| GET | `/api/games/<game_id>/formats` | The game's `formats` from its game module. |
| GET | `/api/deckbuilder/catalog?game_id=` | One base variant per printing (per identity and language for games with `deck_catalog_across_printings`), only playable cards. Filters: `q`, `set_id`, `language` (default `Game.deck_language`), `type`, `types`, `color`, `colors`, `costs`, `attributes`, `rarity`, `rarities`, `kinds` (hololive card kinds), `bloomLevels`, `inkwell`. Sort `number` (default), `name`, `cost`, `rarity`. `limit` 24..20000 (default 72), `offset`. Answers `cards` (with `owned`, `price`, parsed `attributes`, `suggested_zone`), `sets`, `types`, `colors`, `rarities`, `pagination {offset, limit, total, has_more}`. |
| GET | `/api/decks?game_id=` | The user's decks of a game, last changed first, with counts per zone (`main`, `leader`, `don`, `oshi`, `cheer`), `total_count`, `cover_variant_id` (the stored cover, else the first leader/oshi card, then main, then any other zone) and the market summary. |
| POST | `/api/decks` | Create `{game_id, name, format_id}`; format defaults to the game's first format, else `standard`; name max 100 chars, default `Neues Deck`. 201 `{id}`. |
| GET | `/api/decks/<id>` | `{deck, cards, validation, summary, default_don}`. |
| PATCH | `/api/decks/<id>` | `name` (max 100), `format_id`, `notes`, `cover_variant_id` (must be a card in the deck, else 400; empty clears). Answers `{saved, cover_variant_id, validation}`. |
| DELETE | `/api/decks/<id>` | Delete the deck (its `deck_cards` cascade). |
| POST | `/api/decks/<id>/cards` | `{variant_id, delta \| quantity, zone}`. `zone` missing or `auto` -> the ruleset's zone for the card type. 400 when the card belongs to another game, when `Game.deck_card_error` refuses it (only checked when adding), or when the zone is not in the deck's format. Quantity 0 removes the row and clears the cover if it was that card. Answers `{quantity, before, zone, validation}`. |
| POST | `/api/decks/<id>/import/preview` | `{text}` -> one result per line (see `parse_deck_text`). |
| POST | `/api/decks/<id>/import/apply` | `{text, strategy}`; `strategy: "replace"` empties the deck first, otherwise quantities are added to existing rows. Lines whose zone is not in the format are skipped. Answers `{applied, matched, skipped_zone, total}`. |
| GET | `/api/decks/<id>/export/list.txt` | `# <deck name>`, then a `# <Zone>` section per zone with `<qty>x <name>` lines. |
| GET | `/api/decks/<id>/export/missing.txt` | Missing copies as `<qty>x <name>`, summed by card name, sorted alphabetically (file name `<deck>-fehlend.txt`). |

Python functions:

- `deck_validation(deck_id) -> {valid, errors, warnings, counts, rules_url, profile} | None` - for the signed-in user's deck.
- `deck_cards_with_ownership(deck_id)` - deck rows with catalogue data, `collection_quantity`, `owned_quantity`, `missing_quantity`.
- `deck_market_summaries(deck_ids, uid) -> {deck_id: summary}` - `deck_value`, `deck_unpriced_copies`, `required_copies`, `owned_copies`, `missing_copies`, `complete_entries`, `missing_entries`, `missing_cost`, `missing_unpriced_copies`.
- `parse_deck_text(text, game_id)` - per non-comment line `{line, original, quantity, status (matched | ambiguous | not_found), message, alt_printings, match, zone}`.
- `game_deck_ruleset(game_id)` - the `games.deck_ruleset` value.

### How the per-game rules are used

Two sources, looked up separately:

- `game(game_id)` (the game module by id, `deckledger/games`): `formats` (each `{id, name, description, zones: [{id, name, target}], rules_url}`), `deck_language`, `deck_catalog_across_printings`, `excluded_deck_set_types` and `playable_types` (filter the builder catalogue and drive `deck_card_error`), `cost_filter_cap`, `auto_fill_zone`, the rarity order. A game an admin created has no module and gets the defaults: no formats, so only zone `main`.
- `DECK_RULESETS[games.deck_ruleset]` (a database column an admin can point at any existing ruleset): `validate_deck(deck, cards, counts) -> (errors, warnings)` and, through `zone_for_card_type(ruleset, card_type)`, the zone a card type goes to (`main` when unmapped).

Validation takes the profile from `Game.format(format_id)` (falls back to the game's first format), counts copies per profile zone, then lets the ruleset's `validate_deck` decide; without a ruleset each zone's `target` must be met exactly. Messages are German strings. `Game.auto_fill_zone` (One Piece: the DON!! deck filled to 10 with a standard card) is returned as `default_don` in the deck detail, never stored.

## Dependencies

- `deckledger/games` - `game()`, `DECK_RULESETS`, `zone_for_card_type`, `rarity_rank`, `rarity_filter_ranks`, `rarity_case_sql`.
- `deckledger/catalog.py` - `match_collector_number()`, `split_set_prefix()` for the import.
- `deckledger/search.py` - `card_score_sql()` for the builder search.
- `deckledger/prices.py` - `latest_price_sql()`.
- `deckledger/web.py` - `app`, `db`, `login_required`, `user_id`.
- Tables: `decks`, `deck_cards`; read: `games.deck_ruleset`, `collection_entries`, the catalogue and price tables.

## Decisions and assumptions

- The builder lists one base printing per card instead of every variant; alternate art, foil and reprints stay in the card detail (code comment). The base variant is the `standard`/`normal` one, else a non-parallel one; across printings, ties go to the earliest release.
- The builder's total comes from a window count on the page query; a second count query runs only for an offset past the last match.
- Owned copies of a variant are shared out over the deck rows that need it (in zone, then number order), so the same printing in two rows is not counted as owned twice.
- The list export is name-based, not collector-number-based: numbers repeat across sets (Lorcana restarts at 1 in every set) and would not round-trip; `#` lines are comments to the importer, so an export pastes back in cleanly (docstring).
- The missing list is meant for Cardmarket's wantlist box, which matches products by English name only, hence names summed across rows and no zones (docstring).
- Import tries a collector-number code first (`[SET:]number [DE|EN|JP]`, language default EN, via `match_collector_number`), then the exact card name (case-insensitive), picking the newest release, then EN, then the Normal finish; `alt_printings` says how many other matches there were. Lines starting with `#` or `//` are skipped; a missing quantity counts as 1.
- `POST /api/decks/<id>/cards` starts with `BEGIN IMMEDIATE` (read-modify-write: quick clicks must not overwrite each other); an error answer leaves nothing changed.
- Import apply does not call `deck_card_error`; with the current games the same cards (Lorcana quest cards, unplayable VCard rarities) show up as validation errors instead.
