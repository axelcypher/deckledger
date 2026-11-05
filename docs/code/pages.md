# Pages and public profile

`ophir_cards/pages.py` serves the page shell, the service worker and the data the dashboard loads first, and `ophir_cards/public_profile.py` the read-only public view of a user's collection at `/u/<slug>`. Also covered: what the server renders into `templates/index.html`, `templates/login.html` and `templates/public.html` (the frontend scripts are documented elsewhere).

## Public interface

### ophir_cards/pages.py

| Method | Path | Auth | What it does |
| --- | --- | --- | --- |
| GET | `/` | `login_required` | Renders `index.html` with `glass_plate_inline`. |
| GET | `/service-worker.js` | none | `static/service-worker.js` served from the root, with `Service-Worker-Allowed: /`, ETag, `max-age=0`, gzip. |
| GET | `/api/bootstrap` | `login_required` | Everything the app needs at start (below). |
| GET | `/api/home-banner` | `login_required` | Dashboard banner slides; `?game_id=` limits it to one game. |
| GET | `/api/home-recent` | `login_required` | `?game_id=` required (else `[]`): up to 5 distinct owned cards, most recently added first, with their latest price. |

`/api/bootstrap` answers:

- `user` - `id, username, display_name, role, email`, plus `oauth_linked` and `password_set` (booleans; the hash itself never leaves).
- `games` - every enabled game, ordered by name: its `games` row (`languages` parsed) plus `copies`, `unique_cards`, `value` (2 decimals), `completion` and `main_completion` (percent; main = sets whose `set_type` is in `Game.main_set_types`), `set_count`, `deck_count`, `watch_count`, `sheet_count` and the fields of `Game.client_rules()`. The figures count the language the dashboard shows for the game: the user setting `defaultLanguages[game_id]`, else the game's first language.
- `settings` - all of the user's `user_settings`, JSON-decoded (the raw string where a value is not JSON).
- `imports` - the last 4 import operations (`id, created_at, game_id, undone_at`).
- `price_sync` - newest `price_observations.observed_at` over all cards.
- `oauth` - `enabled`, `provider_name` from `auth.resolve_oauth_config()`.
- `inbox_new` - count of the user's inbox items in state `new`.

`/api/home-banner`: modes come from the user setting `homeBanner.modes`, values `newest` and `value` (default `["newest"]`). Each enabled game x mode with owned cards gives one slide `{game_id, game_name, game_short_name, accent, mode, cards}` of up to 20 cards, one per card identity: `value` = most valuable priced cards, `newest` = newest set release first.

`glass_plate_inline_markup()` - the content between the outer `<svg>` tags of `public/glass-plate.svg` (newlines normalised), cached per file version; `""` when the file is missing.

### ophir_cards/public_profile.py

The owner's setting is a JSON object in `user_settings` under key `publicProfile`: `enabled`, `slug`, `name`, `games` (ids), `theme` (`auto`, `light`, `dark`).

| Method | Path | Auth | What it does |
| --- | --- | --- | --- |
| GET | `/api/account/public-profile` | `login_required` | The owner's profile plus its shareable `url`. |
| PUT | `/api/account/public-profile` | `login_required` | Updates any of `slug`, `name`, `games`, `theme`, `enabled`; returns the profile plus `url`. |
| GET | `/u/<slug>` | none | Renders `public.html`; 404 page when not public. |
| GET | `/api/public/<slug>` | none | `{name, slug, games: [{id, name, short_name, accent, total, owned, copies}]}`: distinct cards in the catalogue, distinct cards owned, copies owned. |
| GET | `/api/public/<slug>/games/<game_id>/sets` | none | The game's sets from `catalog.sets_of_game`, reduced to a fixed list of keys (codes, names, dates, counts, completion). |
| GET | `/api/public/<slug>/sets/<set_id>/cards` | none | `?mode=all\|owned\|missing` (default `all`): the set, its cards through `public_card`, and the stats `owned, total, missing, base, master, variant_owned, variant_total`. |
| GET | `/u/<slug>/art/<variant_id>` | none | Card art via `images.card_art`. |
| GET | `/u/<slug>/set-logo/<set_id>` | none | Set logo via `assets.set_logo`. |
| GET | `/u/<slug>/game-logo/<game_id>` | none | Game logo via `assets.game_logo`. |

Invariants:

- PUT validation: `slug` is lower-cased and must be 3-32 characters of `a-z0-9-` with no hyphen at either end, and unique among all profiles (400 otherwise); `name` is cut to 60 characters; `games` keeps only existing game ids, de-duplicated in the given order; an unknown `theme` is ignored; `enabled` needs a slug and at least one game (400).
- A profile counts as public only when it is enabled and lists at least one game. Off, unknown, or a game/set/variant outside the shared games all give the same 404 (JSON on `/api/public/...`, an empty 404 for images).
- For cards only the fields in `public_card` leave the server: `identity_id, canonical_name, collector_number, rarity, language, variant_id, finish, quantity, languages`, and per variant `variant_id, finish, language, quantity, rarity, collector_number`. No prices, values, conditions or watchlists.
- The name shown is the profile's `name`, else the account's display name.
- `url` is `PUBLIC_PROFILE_URL` (env) or the current request's host, plus `/u/<slug>`.
- The image routes call the login-guarded views' `__wrapped__`, so those views must carry `@login_required` directly (it uses `functools.wraps`). `images` and `assets` are imported inside the route functions.

### Templates (server-rendered parts only)

| Template | Rendered by | What the server fills in |
| --- | --- | --- |
| `index.html` | `/` | `url_for` for manifest and icons, `asset_url(...)` for every stylesheet and script (fixed order, `js/core.js` first, `js/main.js` last), and `glass_plate_inline` (marked `safe`) inside a hidden `<svg><defs>`. All data comes later from `/api/bootstrap`. |
| `login.html` | `/login` | `error` (message or None), `oauth_enabled`, `oauth_provider_name`; the form posts `username`/`password` to `/login`; the SSO button links to `url_for('oauth_login')` when enabled. |
| `public.html` | `/u/<slug>` | `slug` (as `data-slug` on `#content`, read by `static/public.js`), `name` (title), `missing` (404 state; `public.js` not loaded), `theme` (body class and `data-theme`; `auto` follows the visitor's `prefers-color-scheme`, also live). Carries a `robots` meta tag `noindex, nofollow`. |

Client-side bits in the templates: `index.html` and `login.html` pick light/dark from the `ophir_theme` cookie before first paint; `login.html` deletes service-worker caches whose names start with `ophir-api-`.

## Dependencies

- `ophir_cards.web` - `app`, `db`, `login_required`, `user_id`, `compress_file`, `derived_from_file`; `asset_url` as Jinja global
- `ophir_cards.config` - `PUBLIC_DIR`, `jload`
- `ophir_cards.games` - `game(...)`: `main_set_types`, `client_rules()`
- `ophir_cards.prices` - `latest_price_sql`, `latest_prices`
- `ophir_cards.auth` - `resolve_oauth_config`
- `ophir_cards.catalog` - `card_rows`, `serialize_card_rows`, `sets_of_game`
- `ophir_cards.images`, `ophir_cards.assets` - image views reused by the public routes
- Files: `public/glass-plate.svg`, `static/service-worker.js`
- External: Google Fonts (the templates load fonts.googleapis.com without blocking rendering)

## Decisions and assumptions

- The glass-plate SVG is inlined into the page because Chromium paints nothing for a cross-document `<use href="/glass-plate.svg#glass_side">` once that file is also used by a CSS mask; only a same-document id works. The markup is cut out by string slicing, not XML parsing, so ids and clip-path references stay byte-for-byte intact. `public/glass-plate.svg` is the one canonical copy.
- The service worker is served at `/` for a whole-app scope and is not login-guarded, since browsers fetch it independently of the app's sign-in.
- Collection figures for the bootstrap are computed in one pass over the collection for all games; banner and recent cards look up prices only for the cards shown.
- Everything public goes through `public_profile.py`, so what leaves the server is decided in one place (`public_card`); a profile that is off and one that does not exist look identical (commit 49661ec).
- The profile setting is only written through `PUT /api/account/public-profile` (`/api/settings` skips `publicProfile`), which keeps slugs checked and unique.
- `PUBLIC_PROFILE_URL` exists because the app itself may run under a private address.
- The visitor theme is rendered into the page so it shows from the first paint (commit 3d84d80).
- Web fonts load non-blocking so the page does not hang without internet access.
- The login page clears the service worker's API caches so a previous user's cached answers do not outlive their session.
