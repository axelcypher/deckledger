# Catalog

Serves the card catalogue to the web app (sets, cards of a set or game, card detail, global search, collector-number resolution, the shared search matcher) and fills it: `catalog_sync.py` runs each game's provider code in a subprocess, validates the result and writes it into the catalogue tables in one transaction per game.

## Public interface

### ophir_cards/catalog.py

HTTP endpoints, all `@login_required`; ownership, value and watchlist figures are those of the logged-in user.

| Method | Path | What it does |
|---|---|---|
| GET | `/api/games/<game_id>/sets` | `sets_of_game()`: the game's sets with owned/total, value and completion percentages. |
| GET | `/api/sets/<set_id>/cards` | Cards of one set: `{set, cards, stats, rarities}`; 404 `set not found`. |
| GET | `/api/games/<game_id>/cards` | The same for a whole game, grouped per set: `{game, groups: [{set, cards, stats}], stats, rarities}`; groups without a matching card are left out; 404 `game not found`. |
| GET | `/api/cards/<identity_id>` | One card identity with every variant: quantities, current price (`price`, `price_low`, `price_avg30`, `price_provider`, `price_currency`, `price_observed_at`, `price_manual`), native price and exchange rate from the price mapping, market and image links from the game module, `language_variations`; 404 `card not found`. |
| GET | `/api/search` | Ranked search over all variants: `q` (under 2 characters returns `[]`), `game_id`, `limit` (default 24, clamped to 1..80), `owned=1`/`true` (only variants the user holds). |

Query parameters of both card list endpoints: `language` (default `combined`), `mode` (`all`/`owned`/`missing`), `q`, `sort` (`number` default, `name`, `rarity`, `value`, `quantity`, `missing`), `rarity` (one exact label), `rarities`, `costs`, `colors` (comma lists), `inkwell` (`true`/`false`), `foil` (`owned`/`missing`), `finish` (`normal`/`foil`), `stats_only` (any value: only `{stats}`, plus `group_count` on the game endpoint). The game endpoint also takes `set_order` (`desc` default, `asc`).

Functions other modules import:

- `sets_of_game(game_id, uid)` - also used by `public_profile.py`. Sets without a single printing that has a variant are left out. Sorted by latest release date (product release dates of the printings, else the set's own date), then natural set code, newest first.
- `card_rows(set_id, uid, stats_only=False)`, `game_card_rows(game_id, uid, stats_only=False)` - one row per variant with quantity, watchlist mark and current price. `serialize_card_rows(raw, language, mode, query, sort, game_id, ...)` turns them into `(cards, stats)`: one card per identity (premium printings as extra cards, see below). Used by `public_profile.py`.
- `query_matches_row(query, row)` - the search match for rows already loaded in Python (`collection.py`, `watchlists.py`). Needs `canonical_name`/`collector_number`; `set_id` and `printing_attrs` (raw JSON) add set and localized fields. Needs a request context (set labels are cached on `flask.g`).
- `match_collector_number(game_id, number, language, set_code=None, variant_hint=None)` -> `(variant_row, status, message, candidate_count)` with status `matched`/`ambiguous`/`not_found` and a German message. Used by `backup.py` (collection import) and `decks.py` (deck list import). The number is compared case-insensitively within game and language; `set_code` restricts to that set; several printings without a set resolve to the set the number's prefix names ("OP01-016" -> OP-01), otherwise the answer is ambiguous. Within one printing it picks the variant whose code or finish equals `variant_hint`, else the base variant (standard/normal code, Normal finish, non-parallel first).
- `split_set_prefix("TFC:16")` -> `("TFC", "16")`; a plain number -> `(None, number)`.
- `natural_code_key(value)` (`sheets.py`), `compare_set_release(a, b, direction)`.

### ophir_cards/search.py

How every search box matches. A query is split into words; every word must occur in one of the card's fields (name and localized name, number, set name, set code, finish, rarity, language, rules text and localized rules text), any order. Inside a field, case, accents, spaces and punctuation are ignored. A word of 4+ letters without digits may be one edit off (two from 8 letters on) a field word, two adjacent words run together, or their start; rules text never matches fuzzily. A query containing `| ^ $ \ [ ] * + ? { }` is matched instead as typed or as a regular expression (case-insensitive) in any one field; queries over 200 characters (`QUERY_MAX`) or invalid patterns match literally only.

- `score(text, *fields)` - registered as SQL function `search_score(query, text1, weight1, ...)` on every request connection (`ophir_cards/web.py`, `db()`); 0 means no match. Weights: `NAME` 4, `NUMBER` 3, `SET` 2, `DETAIL` 1, `TEXT` 0.4; a fuzzy hit counts `FUZZY` (0.6) of the weight; the whole query equal to a name adds 10, a name starting with it adds 5.
- `card_score_sql(parameter="?")` - that call over the aliases `i` (card_identities), `p` (printings), `v` (variants), `s` (sets). Used by the global search and `decks.py`.
- `matches(text, fields)`, `card_fields(row, set_name, set_code, attrs)`, `query(text)` (cached `Query`), `fold(text)`. `deals.py` scores its own fields with the weight constants.
- `title_score(title, card)` - the reverse direction for eBay listing titles (`ebay.py`): every word of the card name must be in the title (a letter off allowed), number, set, set code, finish and rarity add to the score; 0 if the name is missing.

### catalog_sync.py

`python catalog_sync.py [--if-needed [--max-age-hours H]] [--provider ID]`

- No flag: every enabled row of `catalog_providers`, one after another.
- `--if-needed`: only providers that are due: never imported at their current code (`last_synced_version != provider_version`). With `--max-age-hours H` (float) also those whose last run failed or never happened, or whose last run is H hours old or more. Prints `Alle Kataloge sind aktuell.` and exits 0 when none is due. `--max-age-hours` has no effect without `--if-needed`.
- `--provider ID`: exactly that provider, ignoring staleness and the `enabled` flag; exit 1 if the id is unknown.
- Exit code 1 if any provider failed, else 0.

Callers: `docker-entrypoint.sh` (in the background at start with `--if-needed`, then every 6 hours with `--if-needed --max-age-hours ${CATALOG_REFRESH_HOURS:-24}`) and `POST /api/admin/providers/<id>/run` (`--provider`, timeout = the provider's `timeout_seconds` + 30 s). Importing the script imports `ophir_cards`, so migrations and provider seeding run first.

Per provider: run its code -> `fill_missing_printed_card_counts` -> `validate` -> `write_database` -> `mark_provider_result` (success: `last_synced_version := provider_version`, `last_status='ok'`, `last_summary` `{sets, cards}`; failure: `last_status='error'`, `last_error`).

- `validate(catalog, {game_id: (minimum_sets, minimum_cards)})` raises `RuntimeError` if the game has fewer sets or identities than the provider row's minimums, a variant points at a missing printing, or a printing at a missing set.
- `write_database(catalog, fetched_game_ids)` - one `BEGIN IMMEDIATE` transaction (busy timeout 10 s): upserts `sets`, `card_identities`, `printings`, `variants` by id; runs `migrate_hololive_virtual_pools`; prunes rows of the fetched games that were not delivered, except `source_type='manual-override'` rows and any variant still referenced by `collection_entries`, `deck_cards`, `named_watchlist_entries`, `watchlist_entries`, `trade_sheet_cards`, `deal_cards`, `ebay_tracked_items`, unpublished `ebay_drafts` or `decks.cover_variant_id` (plus the printing, identity and set it hangs off); deletes `marketplace_products` and `price_observations` of pruned variants; writes `catalog_metadata` keys `catalog_format`, `catalog_synced_at`, `catalog_sources`, `catalog_counts`. Rolls back on any error; runs `ANALYZE` afterwards with errors ignored.
- `migrate_hololive_virtual_pools(connection)` - moves user references (collection, both watchlists, decks, price observations, variant ids inside `import_operations.changes`) from variants in hololive `sele...` pseudo sets to the physical variant with the same number and language (prefers same finish and artwork, then artwork, then finish; must be unique). A referenced variant that cannot be mapped unambiguously aborts the whole write.

### Provider plumbing

A provider is a row in `catalog_providers`: `id`, `game_id`, `label`, `kind` (only `'custom_code'`, enforced by a CHECK), `code` (Python source), `minimum_sets`, `minimum_cards`, `timeout_seconds` (default 300), `provider_version` (`digest(code)`, first 12 hex chars of SHA-1), `last_synced_version`, `last_run_at`, `last_status`, `last_summary`, `last_error`, `enabled`, `customized`.

- `catalog_provider_contract.py` - what provider code may import (stdlib only, no app): `empty_catalog()`, `fetch(url, attempts=3, headers=None)` (text, User-Agent `OphirCards/1.0 (+local collection manager)`, 40 s timeout, three attempts with 1.5/3/4.5 s pauses, then `RuntimeError("Quelle nicht erreichbar: <url>: <error>")`), `fetch_bytes` (same, 90 s), `clean(html)`, `slug(value)`, `digest(value)`, `put_identity(catalog, record, prefer=False)` (keeps the first record unless `prefer`), `merge(target, source, preferred_language="EN")`, `fill_missing_printed_card_counts(catalog)` (EN printings per set where a set has no count).
- The catalog contract: a dict with exactly `sets`, `identities`, `printings`, `variants`, `sources`, each keyed by record id. Fields written to the database: set `id, game_id, code, name, set_type, release_date, printed_card_count, classifications` (list) `, accent`; identity `id, game_id, canonical_name, rules_text, card_type, attributes` (dict); printing `id, identity_id, game_id, set_id, collector_number, language, rarity, attributes`; variant `id, printing_id, game_id, variant_code, finish, artwork_id, is_parallel` (0/1) `, source_type, attributes`; `sources` maps a name to the URL read. Other keys are ignored on write. `merge` lets a set or identity from the preferred language (`_source_language` on sets, `attributes.sourceLanguage` on identities) replace one from another language, keeps the first printing unless it came from a virtual pool (`attributes.virtualPoolSource`) and the new one did not, and lets later variants and sources overwrite earlier ones.
- `catalog_provider_registry.py` - `load_enabled_providers(conn)`, `get_provider(conn, id)`, `provider_already_current(row)`, `dispatch_provider(row)` (`RuntimeError` for any kind but `custom_code`), `mark_provider_result(conn, id, ok=, now=, summary=, error=)` (caller commits).
- `catalog_provider_runner.py` - `run_custom_code(code, timeout_seconds) -> dict`: writes the code to a temp dir and runs `<python> catalog_provider_harness.py <code-file> <output-file>` with the repo root as working directory. Raises `RuntimeError` on timeout, when no output file was written (with the last 2000 characters of stderr/stdout), or when the harness reports an error (its traceback).
- `catalog_provider_harness.py` - `exec`s the code, calls its `fetch_catalog()`, which must return a dict, and writes `{"ok": true, "catalog": ...}` or `{"ok": false, "error": <traceback>}` as JSON to the output file.

Which code runs: always the `code` column. `ophir_cards/schema.py` (`seed_default_providers`, at every import of the app) inserts a row per game with `Game.provider = (minimum sets, minimum cards, timeout)`, reading `providers/<game_id with - as _>.py`, and on later starts overwrites `code` and `provider_version` with the shipped file whenever they differ and `customized=0`. `PATCH /api/admin/providers/<id>` with `code` sets `customized=1`, or back to 0 when the code equals the shipped file, which makes the row follow `providers/*.py` again. Providers created through the admin API start with `customized=1`. A changed `provider_version` makes the provider due for the next `--if-needed` run.

Admin provider routes (`ophir_cards/admin.py`, all `@admin_required`): `GET /api/admin/providers` (list), `POST /api/admin/providers` (create; game must exist, code non-empty, 409 on duplicate id), `PATCH /api/admin/providers/<id>` (code, label, minimums, timeout, enabled), `DELETE /api/admin/providers/<id>`, `POST /api/admin/providers/<id>/run` (runs `catalog_sync.py --provider`, returns status, summary, error and on failure the log tail; 504 on timeout).

## Dependencies

- `ophir_cards/web.py` - Flask app, `db()`, `login_required`, `user_id`; registers `search_score`.
- `ophir_cards/games` - per-game rules: rarity ranks, playset size, premium ranks, cost filter cap, `is_foil`, `market_links`, `provider` tuple.
- `ophir_cards/prices.py` - `latest_price_sql`, `latest_price_meta_sql`, `MANUAL_PRICE_PROVIDER`.
- `ophir_cards/assets.py` - `set_visual_version` for set logos.
- `ophir_cards/config.py` - `DB_PATH`, `ROOT`, `jload`.
- `ophir_cards/schema.py` - seeds and refreshes `catalog_providers` rows from `providers/*.py`.
- `providers/*.py` - the shipped provider code (see providers.md).
- SQLite tables `games`, `sets`, `card_identities`, `printings`, `variants`, `catalog_providers`, `catalog_metadata`, plus the user tables the prune step protects.

## Decisions and assumptions

- Every provider, the shipped ones included, is plain Python run in a subprocess with only a wall-clock timeout: trusted-admin model, no network or filesystem sandbox. It replaced a declarative field-mapping interpreter and hardcoded builtin importers that turned out too complex (commit ccb02c3).
- The entrypoint is `fetch_catalog`, not `fetch`, so provider code can import the contract's `fetch` helper. The result travels through a file, never stdout; a provider's own output is captured and dropped unless the run fails without a result.
- Each game is fetched, validated and written on its own, so one unreachable source does not block the others, and nothing touches the database before a game's whole download has passed validation (commit 2c0f296).
- Ids are stable and an import only adds, updates and prunes unreferenced rows; anything user data points at survives even if the source stops listing it. An earlier branch that emptied all tables when a marker row was missing was removed because it could erase a real collection.
- `--if-needed` alone (the start-up run) only re-runs providers whose code changed: a provider that failed after once succeeding at the same version waits for the scheduled run with `--max-age-hours`. The schedule exists because new sets only arrive by fetching again.
- `ANALYZE` after each import keeps the planner's statistics current; stale statistics once made import-matching queries fall back to full scans.
- Set statistics are a few grouped queries per game, and `stats_only` prices owned variants only, because per-set and per-variant price lookups made the overview take seconds.
- Epic/Enchanted/Iconic printings (`Game.premium_ranks`) are listed as cards of their own but stay out of Base% and Playset%, and the `foil` ownership filter does not apply to them.
- `finish=foil` and the `foil` filter look for a variant whose finish is `Silver` (Lorcana's foil). Other games have no such variant, so for them `finish=foil` changes nothing and `foil=owned` filters out every non-premium card. The set overview's `foil_completion` equals `master_completion` (owned variants / all variants), unlike the card endpoints' Foil%, which uses `Game.is_foil`.
- Lorcana's numbered sets keep the id `lorcana-<number>` while their `code` holds the community abbreviation; the set overview puts the number back in front ("9 FAB") by reading it from the id.
- The card detail dates an unchanged price by the provider's last successful sync (`catalog_metadata.price_sync_checked`), because unchanged prices write no new row.
- `match_collector_number` never picks "whichever row came first": shared numbers (Lorcana and VCard restart per set, One Piece reprints keep their number) need a set or the number's own prefix.
- Search caches folded fields and fuzzy results (`lru_cache`) because every search scores every card's fields; rules text is excluded from fuzzy matching since long texts have a near miss for almost any word.
