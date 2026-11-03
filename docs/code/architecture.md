# Architecture

DeckLedger is one Flask application with one SQLite database, plus a few standalone job scripts that run next to it in the same container. This page is the map; each area has its own doc in this folder.

## Processes

```
browser (templates/index.html + static/js, service worker)
   |  JSON under /api/, images, static files
   v
gunicorn app:app (2 workers x 4 threads, port 8080)
   |  deckledger package: one Flask app, routes from every module
   v
SQLite  <DATA>/deckledger.db (WAL)  +  cache folders next to it
   ^
   |  background loops started by docker-entrypoint.sh
catalog_sync.py --if-needed       at start, then every 6 h (with --max-age-hours $CATALOG_REFRESH_HOURS)
  -> catalog_provider_harness.py   one subprocess per provider, runs its fetch_catalog()
price_sync.py --if-needed         at start, then every 6 h
post_watch.py                     every 60 s, at most one Reddit feed per run
ebay_sync.py                      every 600 s, followed listings and connected accounts when due
```

- `docker-entrypoint.sh` first runs `python -c "import app"`, so the migrations finish before gunicorn serves anything. Importing the `deckledger` package anywhere (web workers, `catalog_sync.py`, `post_watch.py`, `ebay_sync.py`) migrates and seeds the database (`schema.init_database`). `price_sync.py` imports nothing from the package.
- The job scripts open their own SQLite connections and take a file lock next to the database (`price-sync.lock`, `post-watch.lock`, `ebay-sync.lock`), so runs never overlap. Several of them can also be started from the web: `POST /api/prices/sync`, `POST /api/admin/providers/<id>/run`, the "Jetzt pruefen" and eBay sync buttons.
- Every connection uses `busy_timeout` 10 s; web requests and jobs share the one writer of the WAL database.

## Package layout

`app.py` is only the entry point. `deckledger/__init__.py` imports `config, games, web, schema` first, then the feature modules, then runs `schema.init_database()`. `config` imports nothing from the package and every feature module builds on `web` (`app`, `db`, the decorators). Feature modules import each other where they need to, not strictly in the listed order: `sheets` imports `ebay` and `admin` imports `ebay_photos` at module level, `ebay` imports `deals` inside a function.

| Area | Source | Doc |
| --- | --- | --- |
| Core: paths, the Flask app, request guards, schema and migrations | `app.py`, `deckledger/config.py`, `web.py`, `schema.py`, `migrations/` | [database.md](database.md) |
| Per-game rules | `deckledger/games/` | [games.md](games.md) |
| Catalogue, search, catalogue import, provider plumbing | `deckledger/catalog.py`, `search.py`, `catalog_sync.py`, `catalog_provider_*.py` | [catalog.md](catalog.md) |
| Catalogue sources per game | `providers/` | [providers.md](providers.md) |
| Prices, manual prices, price import | `deckledger/prices.py`, `price_sync.py` | [prices.md](prices.md) |
| Card images, foil masks, logos, set visuals, card backs | `deckledger/images.py`, `assets.py`, `ravensburger_foil.py`, `static/lorcana-foil/` | [images.md](images.md) |
| Collection and watchlists | `deckledger/collection.py`, `watchlists.py` | [collection.md](collection.md) |
| Decks | `deckledger/decks.py` | [decks.md](decks.md) |
| Trade and sale sheets, sheet images | `deckledger/sheets.py`, `sheet_render.py` | [sheets.md](sheets.md) |
| Deals | `deckledger/deals.py` | [deals.md](deals.md) |
| Linked posts, inbox, subreddit search | `deckledger/watcher.py`, `post_watch.py` | [watcher.md](watcher.md) |
| eBay | `deckledger/ebay.py`, `ebay_photos.py`, `ebay_sync.py` | [ebay.md](ebay.md) |
| Export, backup, restore, text import | `deckledger/backup.py` | [backup.md](backup.md) |
| Sign-in, SSO, own account, admin API | `deckledger/auth.py`, `oauth_client.py`, `account.py`, `admin.py` | [auth.md](auth.md) |
| Page shell, dashboard data, public profile | `deckledger/pages.py`, `public_profile.py`, `templates/` | [pages.md](pages.md) |
| Browser frontend, service worker, frontend tests | `templates/index.html`, `static/` | [frontend.md](frontend.md) |

## Request flow

- Every route module registers on the one `web.app`. `web.db()` gives one connection per request (`sqlite3.Row`, foreign keys on, the SQL function `search_score`); writing views commit themselves.
- `@login_required` re-reads the session's user on every request; `@admin_required` adds the admin role. `/api/...` answers 401/403 JSON, pages redirect.
- A `before_request` hook refuses cross-site writes (Fetch Metadata / `Origin`); it is the app's only CSRF protection. Sessions are Flask's signed cookies (`SECRET_KEY`).
- JSON is written with orjson; JSON, HTML and static text files are gzipped; static URLs carry a content hash (`asset_url`) and are cached for a year.
- Unauthenticated routes: `/login`, the OAuth callback, `/health`, `/ebay/account-deletion`, and the public profile (`/u/<slug>`, `/api/public/...`).

## Games

Nothing outside `deckledger/games/` branches on a game's id for its rules: modules ask `games.game(game_id)` for playset size, rarity ladder, finishes, deck formats and rules, image and logo hooks, price method and the catalogue provider. The frontend gets the rules it needs with each game in `/api/bootstrap`. Adding a game is a module in `deckledger/games/`, a provider in `providers/` and an icon (see [games.md](games.md)). Known exceptions are listed in the module docs (the `hololive`-only price pipelines in `price_sync.py`, a few branches in `static/js/catalog.js`).

## Where state lives

All state is in the directory of `DATABASE_PATH` (`/data` in the container, the `deckledger_data` volume):

- `deckledger.db` - the SQLite database. Table groups: users, settings (`user_settings`, `app_settings`), catalogue (`games`, `sets`, `card_identities`, `printings`, `variants`, `catalog_providers`, `catalog_metadata`), prices (`marketplace_products`, `price_observations`, `game_price_overrides`), collection and lists (`collection_entries`, `named_watchlists`, `named_watchlist_entries`, `decks`, `deck_cards`, `import_operations`, `applied_requests`), selling (`trade_sheets`, `trade_sheet_cards`, `sheet_backgrounds`, `sheet_posts`, `inbox_items`, `watch_communities`, `community_feeds`, `deals`, `deal_cards`, `deal_events`, `ebay_*`), and `schema_migrations`.
- `deckledger.db.before-migration-NNNN` - copies taken before a migration (two kept).
- Caches and uploads: `card-images/`, `card-image-sources/`, `card-image-locks/`, `card-images-trimmed/`, `card-thumbnails/`, `card-foil-masks/`, `ravensburger-catalog/`, `ravensburger-foil-assets/`, `price-cache/yuyutei/`, `sheet-renders/` (all rebuildable), and `card-backs/`, `sheet-backgrounds/`, `ebay-photos/` (uploads, not rebuildable).
- `PUBLIC_DIR` (`public/`, read-only in the container): set visuals, card backs and foil assets that ship with or are added to the deployment.
- In the browser: the service worker's caches, an IndexedDB outbox for queued quantity changes, and the per-account offline copy (see [frontend.md](frontend.md)).

## External services

- Catalogue: lorcanajson.org, the official One Piece and hololive card lists, vcardtcg.com, the TCGJSON release on GitHub ([providers.md](providers.md)).
- Prices: Cardmarket's public product and price-guide downloads, TCGCSV, Yuyutei, the ECB reference rates ([prices.md](prices.md)).
- Images and visuals: the card image URLs from the providers, api.lorcast.com, api.lorcana.ravensburger.com (Lorcana foil masks), the publishers' sites for logos and set visuals ([images.md](images.md)).
- Reddit's public feeds ([watcher.md](watcher.md)), eBay's REST, Trading and Media APIs ([ebay.md](ebay.md)), one OAuth2/OIDC identity provider ([auth.md](auth.md)).

## Tests

- `python -m pytest` (`tests/`): a throwaway database with a small built-in catalogue (`tests/conftest.py`), no network; eBay and Reddit are replaced by fakes. On Windows `conftest.py` stubs `fcntl`.
- `node --test "tests/frontend/*.test.mjs"`: frontend unit tests, the service worker against fake caches, and smoke tests in headless Chrome against `tests/frontend/serve.py` ([frontend.md](frontend.md)).
- CI (`.github/workflows/container-image.yml`) runs both before an image is built.
