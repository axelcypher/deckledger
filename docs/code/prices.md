# Prices

Reads the current price and price history of a card variant, stores prices entered by hand, and imports market prices: `price_sync.py` downloads the price feeds, maps feed products to catalogue variants once per run, and stores every price in EUR as change-only history.

## Public interface

### ophir_cards/prices.py

SQL fragments other modules embed (catalog, collection, decks, sheets, watchlists, pages, backup, ebay):

- `latest_price_sql(alias="v", metric="trend")` - correlated subquery for the current amount of variant `<alias>.id`. Only providers with a `marketplace_products` row for the variant count. Priority: `manual`, then `cardmarket`, then any other provider by newest `observed_at`, then provider id. Uses index `idx_prices_variant_metric`.
- `latest_price_meta_sql(alias="v", field="provider_id")` - another column (`provider_id`, `currency`, `observed_at`) of that same trend observation.
- `latest_prices(variant_ids)` -> `{variant_id: amount or None}`, for views that choose a few cards first.
- `MANUAL_PRICE_PROVIDER = "manual"`.

HTTP endpoints, all `@login_required`:

| Method | Path | What it does |
|---|---|---|
| POST | `/api/prices/sync` | Runs `price_sync.py` without `--if-needed` (180 s timeout). Returns `{counts, synced_at}` from `catalog_metadata`; 504 on timeout, 502 if the script failed. |
| PUT | `/api/variants/<variant_id>/manual-price` | Body `{amount}`; sets the card's manual price. `{saved, amount}`; 400 invalid amount, 404 unknown variant. |
| DELETE | `/api/variants/<variant_id>/manual-price` | Removes the manual price and all its history rows. `{removed: true}`. |
| POST | `/api/variants/manual-prices` | `{variant_ids, amount}` or `{variant_ids, remove: true}` for up to 2000 ids (`MANUAL_PRICE_BULK_LIMIT`); unknown ids are skipped. `{changed, amount}`. |
| GET | `/api/variants/<variant_id>/price-history` | `metric` = `trend` (default) / `low` / `avg30`, `days` = 7..3650 (default 180). `{variant_id, metric, points: [{date, amount, currency}]}`. |

- Manual amount: a number, a comma accepted as decimal point, rounded to 2 places, `0 < amount <= 1_000_000` (`MANUAL_PRICE_LIMIT`). Stored like a feed price: a `marketplace_products` row (`provider_id='manual'`, `external_product_id` = variant id, `match_method='manual'`) plus a change-only `trend` observation in EUR. A second save within the same second updates that row instead of adding one.
- Price history: uses only the provider of the card's current price; rebuilds a stepped daily series from the stored changes (the last change before the window opens it, each price holds until the day before the next change) and extends the last price to the provider's last successful sync (`price_sync_checked`), at most today. Days are cut from the `observed_at` text, not `date()`.

### price_sync.py

`python price_sync.py [--if-needed] [--dry-run]`

- No flag: download, match and store everything.
- `--if-needed`: after downloading Cardmarket, TCGCSV and the ECB rates, compare their versions (Cardmarket `createdAt` of product list and price guide per game, TCGCSV `last-updated.txt`, today's date for Yuyutei, the ECB rates) with `catalog_metadata.price_sync_versions`; if equal, return the stored counts with `skipped` and write nothing. Yuyutei is only scraped after this check.
- `--dry-run`: match and check the safety floors, print counts, versions and match audits, write nothing.
- Prints the result as JSON; any failure (download, safety floor) raises and leaves the stored prices as they were.
- One run at a time: an exclusive, blocking `fcntl.flock` on `<data dir>/price-sync.lock`.
- Callers: `docker-entrypoint.sh` (`--if-needed` at start and every 6 hours) and `POST /api/prices/sync`.

Which feed a game uses: per language of the game, `game_price_overrides(game_id, language)` else `games.price_method`; a game joins a pipeline if any of its languages resolves to it. Seeded defaults (`Game.price_method`, `Game.price_overrides`, `Game.cardmarket_game_id`; admins change `price_method` and `cardmarket_game_id` through `PATCH /api/admin/games/<game_id>`, `@admin_required`; overrides have no admin route):

| Game | Languages | Method | Feed | Metrics stored (EUR) |
|---|---|---|---|---|
| lorcana | DE, EN | `cardmarket`, game id 19 | Cardmarket product list + price guide | trend, low, avg30 (the `-foil` fields for non-Normal finishes) |
| one-piece | EN, JP | `cardmarket`, game id 18 | Cardmarket, EN and JP matched to separate expansions | trend, low, avg30 (normal fields) |
| hololive | EN | `tcgcsv` | TCGplayer prices via TCGCSV, category 87, USD | trend = marketPrice, low = lowPrice |
| hololive | JP | `yuyutei` (override) | Yuyutei sell pages, retail JPY | trend |
| vcard | EN | none | no feed; manual prices only | - |

Independently of these, `ophir_cards/ebay.py` stores an `ebay` provider price for cards with followed eBay listings. Any other game with method `cardmarket` and a `cardmarket_game_id` uses the generic matcher; a game with the method but no id is skipped. The `tcgcsv` and `yuyutei` pipelines only run for the game id `hololive`.

Feeds: Cardmarket `https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_<id>.json` and `.../priceGuide/price_guide_<id>.json`; TCGCSV `https://tcgcsv.com/tcgplayer/87/groups`, `/<groupId>/products`, `/<groupId>/prices` (groups published up to today, 3 threads, a short pause between calls) and `https://tcgcsv.com/last-updated.txt`; Yuyutei `https://yuyu-tei.jp/sell/hocg/s/<set code>` for every JP hololive set released by today or without a release date; ECB `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` (USD and JPY, fetched every run, missing rates raise).

Matching (each mapping is stored in `marketplace_products` with `match_method`, `source_url` and attributes; anything ambiguous is skipped, never guessed):

- Expansion resolution (`expansion_scores`, all Cardmarket matchers): each catalogue set or product group is a fingerprint set of product keys and is matched to the Cardmarket expansion with the best F1 of coverage and precision. Accepted only with at least min(3, group size) keys in common, coverage >= 0.45, and either a margin of >= 0.04 F1 over the runner-up or, where the group has a release date, the expansion whose dominant `dateAdded` is closest to it (within 120 days).
- Lorcana (`map_lorcana`): uses the variant's `priceUrl` only if it is a cardmarket.com `/Singles/<expansion>/<product>[-V<n>]` URL; product = (resolved expansion, normalized name), the `-V<n>` suffix picks the n-th candidate by `idProduct`. Several candidates without a suffix are taken (earliest `idProduct`) only when they all share one `idMetacard`.
- One Piece (`map_one_piece`): DON!! and unnumbered printings are skipped. Product key = the `(CODE-NNN)` suffix of the Cardmarket name plus the normalized name. EN groups are the release product (`releaseProductCode`) or the set and use its release date; JP sets are matched afterwards with the Western expansion of the same set excluded. A printing is mapped only when the candidate count equals its variant count; variants (standard first) and products (`idProduct` order) are paired in order.
- Generic (`map_generic`): name plus `(SET-NUMBER)` suffix when at least half of the game's products carry one, else name only; exactly one candidate per printing, shared by all its variants, Normal finish -> normal fields, others -> foil fields.
- hololive EN (`map_hololive_tcgplayer`) and JP (`map_hololive_yuyutei`): key = (normalized set code via `hololive_set_key`, collector number, rarity code). Exactly one catalogue variant and one feed product per key; TCGplayer also needs exactly one price row with a `marketPrice`. Yuyutei pages are cached per day in `<data dir>/price-cache/yuyutei/`; a 404 counts as "not listed", one HTTP 429 gets a 15 s cooldown and a retry, a second 429 stops all further requests for the run.

Currency: everything is stored as EUR. USD and JPY amounts are divided by the ECB rate; the native amounts, rate and rate date are kept in the mapping's attributes (`sourceCurrency`, `sourceTrend`, `sourceLow`, `eurRate`, `exchangeDate`), which the card detail shows.

Safety floors (the run raises and stores nothing): Lorcana < 5000 Cardmarket mappings; One Piece < 400 mappings in total, or < 400 for EN or for JP; hololive < 500 TCGplayer mappings; a game on the generic matcher with fewer than half of its numbered variants matched. Yuyutei instead keeps its previous mappings and prices when any set failed to load or fewer than 500 cards matched, and marks its version incomplete so the next run tries again.

Writing (one transaction, after the newest stored observations were read outside it): delete and re-insert the `marketplace_products` rows of `cardmarket` (if any game ran on it), `tcgplayer`, `yuyutei` (only on success), and delete the retired `optcgapi` mappings and observations; insert an observation only where the amount differs by 0.005 or more from the newest stored row of its series (variant, provider, metric) and is newer than it; update `catalog_metadata` keys `price_sync_checked` (newest `observed_at` seen per provider), `price_sync_versions`, `price_sync_counts`, `price_sync_last_success`, `price_sync_sources`. `observed_at` is the Cardmarket price guide's `createdAt`, TCGCSV's last-updated text, or the run time for Yuyutei.

History compaction (afterwards, own transactions; a failure is logged and does not undo the sync):

- Once per database (`catalog_metadata.price_history_format = "changes"`): delete rows that repeat the previous amount of their series, then `VACUUM` if any were removed.
- Every run: each calendar month entirely before the first day of the month that contains (today in Europe/Berlin - 92 days, `DAILY_HISTORY_DAYS`) is folded into one row per series dated `YYYY-MM-15T12:00:00+00:00` with the month's time-weighted average (starting from the price carried in from before the month). If the month's closing price differs from that average, it is written at `<next month>-01T00:00:00+00:00` unless that day already has a row. Idempotent; one commit per month.

## Dependencies

- `ophir_cards/web.py` - `app`, `db()`, `login_required` (prices.py).
- `ophir_cards/config.py` - `ROOT`, `jload`, `now_iso` (prices.py). `price_sync.py` resolves `DATABASE_PATH` itself and imports nothing from the app.
- `ophir_cards/schema.py` / `ophir_cards/games` - seed `games.price_method`, `game_price_overrides`, `games.cardmarket_game_id`.
- `providers/lorcana.py` - the variant `priceUrl` Lorcana matching depends on; `providers/one_piece.py` - `releaseProductCode`/`releaseProductReleaseDate` for One Piece EN groups.
- Cardmarket (downloads.s3.cardmarket.com) - product catalogue and daily price guide, no API key.
- TCGCSV (tcgcsv.com) - daily TCGplayer export for hololive EN.
- Yuyutei (yuyu-tei.jp) - JP hololive retail prices, scraped from HTML.
- European Central Bank (www.ecb.europa.eu) - daily USD/JPY reference rates.
- SQLite tables `price_observations`, `marketplace_products`, `catalog_metadata`, `games`, `game_price_overrides`, catalogue tables for matching.

## Decisions and assumptions

- Feed products are matched to variants once per sync and the external product id is stored; reading a price never guesses by name.
- A `price_observations` row means "from this moment on the price was X"; a price that did not change writes nothing, and readers carry the newest row forward (commit 871d109). Hence "as of" comes from `price_sync_checked`, not from the newest row's date.
- Prices belong to the catalogue, not to an account: one manual price per card, set by any logged-in user, and it wins over every feed until removed.
- A price is only shown while a `marketplace_products` row maps the variant to its provider; observations without a mapping are not read. Each sync deletes the `cardmarket` mappings of all games and re-inserts only those of the games fetched in that run.
- The current price prefers Cardmarket over other feeds and the history chart sticks to one provider, so the line never jumps between two marketplaces' price levels.
- Cardmarket lists Western and Japanese One Piece releases as separate expansions with near-identical fingerprints, so EN is resolved first with Bandai's Western release dates and JP from the remaining set-shaped candidate; prices stay separated by language.
- The generic Cardmarket matcher works at printing level (normal vs. foil fields of one product), like Lorcana; games that list alternate arts as separate products need a bespoke entry in `CARDMARKET_MATCHERS` (commit e622a5f).
- Safety floors exist so a feed format change never silently erases working mappings; a floor only applies to a game actually assigned to that method in the run.
- TCGCSV asks for non-bursty polling, hence 3 threads and a pause; Yuyutei gets 0.55 s between pages, a daily page cache and a stop after repeated throttling.
- The newest observations are read before the write transaction and months are compacted one per transaction, to keep the database write lock short for the web app.
- `--if-needed` still downloads the Cardmarket, TCGCSV and ECB files each time; it only avoids rewriting when nothing changed.
- `price_sync.py` takes "today" in Europe/Berlin (`local_today`: compaction boundary, released-by-today checks); the Yuyutei page cache and the price history endpoint use the UTC date.
