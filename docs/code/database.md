# Database and application core

`config.py`, `web.py`, `schema.py`, `migrations/` and the entry point `app.py`: where state lives, the one Flask app every other module registers routes on, the per-request database connection with its request guards and login decorators, and bringing the SQLite database to the current schema and seed data at start-up.

## Public interface

### app.py

- `gunicorn app:app` in the container (docker-entrypoint.sh: 2 workers, 4 threads, port 8080); `python app.py` starts Flask's debug server on 0.0.0.0:8080. It only re-exports `deckledger.app`.
- Importing `deckledger` - or any submodule such as `deckledger.config`, since Python runs the package `__init__.py` first - imports every route module and then calls `schema.init_database()`. The background scripts (`catalog_sync.py`, `ebay_sync.py`, `post_watch.py`) rely on this to get a migrated database. docker-entrypoint.sh runs `python -c "import app"` before starting gunicorn so migrations finish before anything is served.
- Import order in `deckledger/__init__.py`: `config, games, web, schema` first, then the feature modules.

### config.py

Imports nothing from the package; safe to import from anywhere.

- `ROOT` - repository root (templates/, static/, providers/, sync scripts).
- `DB_PATH` - env `DATABASE_PATH`, default `<ROOT>/deckledger.db` (Dockerfile sets `/data/deckledger.db`). The data directory is the directory of this file; every writable directory below is a sibling of the database file, so one volume holds all state.

| Constant | Directory next to the database |
| --- | --- |
| `IMAGE_CACHE` | `card-images/` |
| `IMAGE_SOURCE_CACHE` | `card-image-sources/` |
| `IMAGE_THUMB_CACHE` | `card-thumbnails/` |
| `IMAGE_LOCK_CACHE` | `card-image-locks/` |
| `IMAGE_FOIL_MASK_CACHE` | `card-foil-masks/` |
| `IMAGE_TRIM_CACHE` | `card-images-trimmed/` |
| `CARD_BACK_UPLOAD_DIR` | `card-backs/` |
| `SHEET_BACKGROUND_DIR` | `sheet-backgrounds/` |
| `SHEET_RENDER_CACHE` | `sheet-renders/` |
| `EBAY_PHOTO_DIR` | `ebay-photos/` |

- `PUBLIC_DIR` - env `PUBLIC_DIR`, default `<ROOT>/public` (read-only mount in compose.yaml); `PUBLIC_SET_DIR` (`sets/`), `PUBLIC_OP_ICON_DIR` (`icons/one-piece/`), `PUBLIC_IMAGE_EXTENSIONS`.
- `OAUTH_CONFIG_PATH` - env, default `/config/oauth.json`; `OAUTH_PROVIDER_KEY = "generic"` (the value stored in `users.oauth_provider`); `OAUTH_CONFIG_DEFAULTS` (see auth.md).
- `now_iso()` - current UTC time, whole seconds, ISO 8601 with offset. The format of every `*_at` column.
- `jload(value, default=None)` - `json.loads` that returns `default` for `None` or invalid JSON instead of raising.

### web.py

- `app` - the single Flask app (subclass `DeckLedger`, `root_path=ROOT`, so `templates/` and `static/` are the repository's). Every route module does `from .web import app`.
- `db()` - one `sqlite3` connection per app context (`g.db`), closed at teardown. `sqlite3.Row` rows, `PRAGMA foreign_keys = ON`, `busy_timeout` 10 s, and the SQL function `search_score` (from `deckledger.search`). Nothing auto-commits: a writing view calls `db().commit()` itself.
- `login_required` - the session's `user_id` must name an existing `users` row (checked on every request; a stale session is cleared). Failure: `/api/...` gets 401 `{"error": "authentication required"}`, other paths redirect to `/login`.
- `admin_required` - `login_required` plus `users.role == 'admin'`. Failure: `/api/...` gets 403 JSON, other paths redirect to `/`.
- `user_id()` - `int(session["user_id"])`; only valid inside a guarded view.
- `asset_url(filename)` (Jinja global) - static URL plus `?v=<first 12 hex of the file's SHA-256>`. A static response whose `v` equals the current hash is sent `public, max-age=31536000, immutable`.
- `derived_from_file(path, cache, derive)` - `derive(bytes)` of a shipped file, memoised in `cache` per (mtime, size).
- `compress_file(response, path)` - gzips a `send_file` answer (text types only, files from 1 KiB) for clients that accept gzip; compressed once per file version, ETag suffixed `-gzip`, `Accept-Ranges` dropped. `accepts_gzip()` honours q-values.
- Global hooks:
  - `before_request` `reject_cross_site_writes` - any method other than GET/HEAD/OPTIONS is refused with 403 JSON unless `Sec-Fetch-Site` is `same-origin` or `none`, or, without that header, `Origin` is absent or matches the request host. This is the app's only CSRF protection.
  - `after_request` `compress_answer` - gzip (level 5) for 200 answers of type JSON or HTML from 2 KiB.
  - `errorhandler(sqlite3.IntegrityError)` - rolls back; `/api/...` gets 400 JSON, anything else re-raises.
- JSON answers go through `OrjsonProvider` (sorted keys). It falls back to Flask's own provider for debug pretty-printing, `sort_keys` off, or values orjson cannot write. Visible differences: non-ASCII as UTF-8, NaN as `null`, number-like keys sorted as text.
- Settings: `SECRET_KEY` from env (fallback: a fixed development key); `TRUST_PROXY_HEADERS` in (`1`, `true`, `yes`) wraps the app in `ProxyFix(x_for=1, x_proto=1, x_host=1)`; `SESSION_COOKIE_SAMESITE = "Lax"`. Sessions are Flask's signed cookies; there is no server-side session store.

| Method | Path | Auth | What it does |
| --- | --- | --- | --- |
| GET | `/health` | none | `{"status": "ok", "database": <file name>, "schema_version", "schema_expected"}`; polled by the Dockerfile HEALTHCHECK |

### schema.py

- `init_database()` - runs once per process at package import. Creates the data directory, opens its own connection (`busy_timeout` 10 s), sets `journal_mode = WAL`, calls `migrations.migrate(connection, DB_PATH)`, then runs `seed_database` and `ensure_user_lists` in one `BEGIN IMMEDIATE` transaction and commits. A failing migration raises and the process does not start.
- `seed_database(connection)` - with an empty `users` table: creates the first admin account (username `admin`, a fixed default password the README says to change at once) and inserts every game of `games.GAMES`. Otherwise `INSERT OR IGNORE` of the games only. Both paths then run:
  - `seed_default_providers` - a `catalog_providers` row (`kind='custom_code'`) per game with `Game.provider`, its code read from `providers/<game id with - as _>.py` and `provider_version = digest(code)`. A row with `customized=0` follows the shipped file (code and version are replaced when they differ, which makes the next `catalog_sync --if-needed` re-import); a row with `customized=1` is never touched.
  - `seed_default_deck_rulesets`, `seed_default_price_methods` (also `game_price_overrides`, `INSERT OR IGNORE`), `seed_default_cardmarket_game_ids` - set the `games` columns only where they are NULL, so admin edits survive restarts.
- `ensure_user_lists(connection, user_id=None)` - idempotent; gives every account (or the one given) a default watchlist named `Merkliste` (`is_default=1`) per game. Call it right after creating an account at runtime (admin create and SSO auto-provisioning do). Does not commit.
- `default_provider_code(game_id)` - text of the shipped provider file; raises if it does not exist.
- `GAME_COLUMNS` - the `games` columns the seed writes.

### migrations/

- `MIGRATIONS` - ordered list of modules `mNNNN_<what>.py`; a migration's version is its 1-based position. Each module has `NAME` and `apply(connection, context)`.
- `Context(connection, now)` - `now` is one timestamp for the whole migration; `run_script(sql)` runs several statements inside the migration's transaction (`sqlite3.executescript` would commit first) and raises `ValueError` on an incomplete trailing statement.
- `migrate(connection, database_path=None, migrations=None, log=print)` - applies every missing version in order and returns the list applied. Each migration runs in its own `BEGIN IMMEDIATE` transaction together with its `schema_migrations` row; on any exception it rolls back and re-raises. Applied versions are re-read after taking the write lock, so gunicorn workers starting together apply each migration once. Raises `RuntimeError` when the database has a version this code does not know (written by a newer image). Before migrating a database that already has a `users` table, it copies it with the SQLite backup API to `<db file>.before-migration-<first pending version, 4 digits>` next to it and keeps only the newest 2 copies (`BACKUPS_KEPT`).
- `status(connection)` - (highest applied version, `len(MIGRATIONS)`); used by `/health`.
- `applied_versions(connection)` - creates `schema_migrations(version, name, applied_at)` if missing.

Adding a migration: a new file with the next number, listed in `MIGRATIONS`; never edit a released one. Tests (tests/test_migrations.py) check that the numbers have no gaps, that every file is listed, and that no `CREATE TABLE/INDEX`, `ALTER TABLE` or `DROP` appears in `deckledger/*.py`, `deckledger/games/`, `catalog_sync.py` or `price_sync.py` (TEMP tables excepted).

| # | Name | What it does |
| --- | --- | --- |
| 0001 | baseline | The schema as of the switch to numbered migrations (users, games, catalogue tables sets/card_identities/printings/variants, price_observations, marketplace_products, collection_entries, watchlists, decks, import_operations, user_settings, app_settings, catalog_metadata, catalog_providers, applied_requests, trade_sheets, game_price_overrides, indexes). First upgrades a pre-migration database of any age (adds missing columns, rebuilds collection_entries for the graded UNIQUE key with a row-count check), moves the first release's single watchlist into default lists, runs ANALYZE if there are no statistics. Frozen. |
| 0002 | sale lists become trade sheets | Turns "Verkaufsliste" watchlists into WTS trade sheets and drops `named_watchlists.is_sale_list` and `named_watchlist_entries.source`. |
| 0003 | uploaded sheet backgrounds | Table `sheet_backgrounds` (files in `sheet-backgrounds/`). |
| 0004 | drop the classic theme setting | Deletes the `mobileTheme` user setting. |
| 0005 | sheet posts and inbox | Tables `sheet_posts` and `inbox_items`. |
| 0006 | communities to search, finds in the inbox | Tables `watch_communities`, `community_feeds`; `trade_sheets.scout`, `scout_communities`; rebuilds `inbox_items` with `sheet_id` and a nullable `post_id`. |
| 0007 | deals | Tables `deals`, `deal_cards`, `deal_events`. |
| 0008 | one inbox entry per comment | Collapses duplicate comment inbox items to one per user and comment. |
| 0009 | ebay | Tables `ebay_accounts`, `ebay_listings`, `ebay_sales`, `ebay_drafts`, `ebay_tracked_items`. |
| 0010 | lock the demo account | Blanks the password hash of a `demo` account that still has the published demo password. |
| 0011 | ebay stats and sheets | `ebay_accounts.scopes`, listing traffic columns, `trade_sheets.ebay_sync`, `trade_sheet_cards.ebay_item`. |
| 0012 | ebay listing shipping | `ebay_listings.shipping_cost`. |
| 0013 | ebay photos | Table `ebay_photos` (files in `ebay-photos/`). |
| 0014 | ebay sale deals | `ebay_sales.deal_id`. |
| 0015 | price observed_at index | Index on `price_observations(observed_at)`. |
| 0016 | lookup indexes | Indexes on `ebay_listings(user_id, variant_id)`, `card_identities(canonical_name NOCASE)`, `deal_events(deal_id)`, `inbox_items(post_id)`, `applied_requests(created_at)`. |

## Dependencies

- Flask / Werkzeug - the app, sessions, `ProxyFix`, password hashing for the seeded admin
- orjson - JSON encoding of answers
- sqlite3 (stdlib) - the database: one file at `DATABASE_PATH`, WAL mode
- gunicorn - production server (docker-entrypoint.sh)
- `deckledger.search` - `search_score` SQL function registered on every connection
- `deckledger.games` - `GAMES`, the registry the seed reads
- `catalog_provider_contract` (repository root) - `digest()` for provider versions
- `providers/*.py` - shipped provider code, copied into `catalog_providers.code` at seed time

## Decisions and assumptions

- The schema is only what the migrations produce; each runs exactly once, so a migration may rename, drop and rewrite without checking state. Seeds (games, providers, default lists) are not migrations and run at every start (commit f34275e).
- A database migrated by a newer image is refused rather than run against an unknown schema; an older image must not open it.
- WAL is set once in `init_database` (it is stored in the file) instead of per request, which cost every connection a lock round-trip.
- `busy_timeout` 10 s on every connection: web requests race the background sync scripts and other gunicorn threads, and would otherwise fail with "database is locked".
- The seed runs under `BEGIN IMMEDIATE` because gunicorn workers can import the app concurrently on a fresh volume; this keeps two first admins from being created.
- `login_required` re-reads the `users` row on every request so an account an admin deleted stops working at once, not when its cookie expires.
- Cross-site writes are rejected via Fetch Metadata / `Origin` instead of CSRF tokens because the API reads JSON regardless of Content-Type; clients sending neither header (scripts, tools) are not treated as cross-site browsers.
- `TRUST_PROXY_HEADERS` is opt-in: it is needed behind a TLS-terminating proxy so `url_for(_external=True)` builds `https://` OAuth redirect URIs, but direct clients could spoof `X-Forwarded-*`.
- Static URLs carry a content hash and are cached as immutable because hand-bumped `?v=N` values were forgotten and left stale files in browsers and the service worker.
- Answers are gzipped in the app: a game's catalogue is about 28 MB of JSON; orjson writes it in about 60 ms instead of over 200 ms.
- Game rows are seeded with `INSERT OR IGNORE`: a later change to a game module's name, short name, languages, accent or version does not reach an existing database, and `price_method`, `deck_ruleset` and `cardmarket_game_id` only fill NULLs. A shipped game deleted in the admin UI is inserted again at the next start.
- In the deal and eBay tables `variant_id` (and `ebay_sales.deal_id`) are deliberately no foreign keys: that history must outlive catalogue entries and deleted deals (migration docstrings 0007, 0009, 0013, 0014).
