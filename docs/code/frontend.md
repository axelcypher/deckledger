# Frontend

The browser side of Ophir Cards: the signed-in single-page app (`templates/index.html` plus the plain scripts in `static/js/`), its service worker and offline outbox, the login page, the public profile page, the stylesheets, and the Node-run frontend tests. There is no build step: everything under `static/` is served as it is on disk.

## Public interface

### Page shell and load order

- `templates/index.html` is the only page of the app (`GET /`, `@login_required`, rendered by `ophir_cards/pages.py`). It holds the fixed chrome (sidebar with `[data-route]` nav buttons and `#edit-panel`, top bar with the game picker `#global-game-filter` and `#global-search-open`, phone tab bar `#mobile-tabbar`), the view container `#content`, and the fixed overlays the scripts look up by id: `#search-overlay`, `#card-modal`/`#card-dialog`, `#import-modal`, `#deck-transfer-modal`, `#deck-import-modal`, `#deck-export-modal`, plus `#toast-stack`, `#back-to-top`, `#offline-indicator`. Renaming one of these ids breaks the scripts.
- Every stylesheet and script is referenced through `asset_url('<path under static/>')` (`ophir_cards/web.py`), which appends `?v=<first 12 hex chars of the file's SHA-256>`. A static answer whose `v` equals the current hash is sent `public, max-age=31536000, immutable`, and text files are gzipped. A new file only needs an `asset_url` reference; there is no version to bump. The manifest and the icons use plain `url_for` (no hash).
- Script order at the end of `<body>`: `filter-panel.js`, `stat-pill.js` (custom elements), then `js/core.js`, `finish`, `filters`, `catalog`, `offline`, `collection`, `watchlist`, `decks`, `card-modal`, `watch-menu`, `dashboard`, `settings`, `admin`, `import`, `sheets`, `inbox`, `deals`, `ebay`, `ebay-photos`, and `js/main.js` last. They are classic scripts sharing one global scope:
  - Code that runs at load time may only use what earlier files declared (`core.js` reads `#content` at load, so the scripts must stay at the end of the body). Functions may call into later files, because they run after all files have loaded.
  - All top-level `const`/`let`/`function` names share one namespace; a `const`/`let` name declared again in a later file is a SyntaxError that stops that whole file.
  - In `static/js/`, top-level side effects are limited to the `#content` lookup in `core.js`, event listeners (`core.js`, `offline.js`, `settings.js`, `watch-menu.js`) and the start-up in `main.js`; the two files before them only define custom elements.
- Addresses: the app may be served under a path (`/cards`, see the README). `core.js` reads it from `<meta name="app-base">` into `APP_BASE`. `api()` and `post()` take app paths (`/api/...`) and put `APP_BASE` in front themselves, and so do the offline outbox (it replays through them) and the offline save (`data()`). Every other address the scripts build (image `src`, link `href`, CSS `url()` in markup, direct `fetch()`, the service worker registration, `import()`) goes through `appUrl(path)` or starts with `${APP_BASE}`; an address written as a bare `/...` reaches the host's root instead of the app. Stylesheets refer to app routes relative to `static/` (`url(../glass-plate.svg)`), the foil modules relative to their own `import.meta.url`. `tests/frontend/base-path.test.mjs` serves the app under `/cards` and fails on any request outside it.
- Start-up (`main.js`): `init()` fetches `/api/bootstrap` into `state.boot`, fills the user menu and game pickers, calls `setActiveGame(settings.activeGameId || first game, false)`, `applyAppearance()`, `wireGlobalEvents()`, draws the dashboard, then handles the return parameters `?linked`, `?oauth_error` and `?ebay` (`handleEbayReturn` in `ebay.js`). Afterwards: `updateOfflineIndicator()`, `syncOfflineQueue()` when `navigator.onLine`, the inbox badge (`setInboxCount`, then `refreshInboxCount` every `INBOX_REFRESH_MS` = 180 s), and `dropForeignOfflineSave()` when the bootstrap was not a stale copy. Independently, on `load`, `<base>/service-worker.js` is registered with the scope `<base>/` and `navigator.storage.persist()` is requested.
- Stylesheet order: `app.css`, `foil-effects.css`, `mobile-theme.css`, `mobile-pages.css`, `responsive-theme.css`, `stat-pill.css`, `dark-appearance.css`, `design-system.css` (last). Google Fonts are loaded non-blocking (`media="print"` swapped on load).
- `<body class="dark mobile-modern">` is fixed; every theme rule is scoped to `body.dark.mobile-modern`. The light appearance is the extra body class `mobile-light`, set before first paint by an inline script from the `ophir_theme` cookie and later by `applyAppearance()` (which also rewrites the cookie).
- The server inlines `public/glass-plate.svg` (`glass_plate_inline`) into an SVG `<defs>` next to the mask `#mt-reflection-mask`; `alignReflectionMask()` in `card-modal.js` positions it for the card dialog's floor reflection.

### What each script owns

- `core.js` - shared `state`, small helpers, the API client, debounced settings, toasts, navigation (`routeTo`), active game, overlays, search, appearance.
- `finish.js` - how a finish looks: `artUrl`, `foilMaskUrl`, `finishPresentation` (finish tier class), `finishThumb`, official Lorcana foil layers (`/api/foil-layer-meta`).
- `filters.js` - filter bars and controls per game (Lorcana, One Piece, hololive, VCard), sort and zoom controls, debounced search inputs (`bindBrowserFilters`).
- `catalog.js` - a game's sets, a set's cards, all cards of a game, the card tile (`cardTile`), the progressive tile grid (`mountTileFeed`), tile event wiring (`bindCardEvents`), `gameRules`/`playsetSize`, `refreshCurrentView`.
- `offline.js` - the IndexedDB outbox and its replay, the offline indicator, saving everything for offline use.
- `collection.js` - quantity changes (optimistic patching, per-variant ordering, offline queueing), the collection view, conditions and gradings, price history, collection bulk selection.
- `watchlist.js` - watchlists view, selection and bulk actions, desired quantities.
- `decks.js` - deck builder: overview, catalogue panel, zones, copy limits (`deckCopyLimit`, `deckZoneMaximum`), image preview, add popup.
- `card-modal.js` - the card dialog (`openCard`, `renderCardModal`), its tabs (`card`, `collection`, `market`), variant switching, reflection mask.
- `watch-menu.js` - long-press/right-click list menu on every heart (`.watchlist-action`) via document-level listeners; a short tap keeps toggling.
- `dashboard.js` - dashboard totals, the highlights reel (`/api/home-banner`, falls back to the all-games banner on a network error), recent additions.
- `settings.js` - user settings and account, the shared stackable dialog `openSettingsModal`/`closeSettingsModal`, Reddit settings, the `Offline verfügbar machen` card, public profile settings.
- `admin.js` - admin view: users, games, catalogue providers, OAuth, price sources, manual cards, card backs (eBay admin comes from `renderAdminEbay` in `ebay.js`).
- `import.js` - import/export dialog state and previews (applying an import is wired in `main.js`).
- `sheets.js` - trade/sale sheets and the `sheets` route entry `renderSheets`, which dispatches to `renderSales` (`ebay.js`) or `renderDeal` (`deals.js`).
- `inbox.js` - inbox of new comments and matching posts (read by the server's watcher), posts linked to a sheet, searched communities, the inbox badge.
- `deals.js` - deals (sold, bought, traded) on the sales tab, the deal editor, deals from inbox entries, a card's deal record.
- `ebay.js` - eBay account and listing preset, tracked listings in the price tab, drafts, the sales tab (`renderSales`), eBay admin.
- `ebay-photos.js` - own photos of a card for eBay drafts, crop and rotate editor (upload is a `FormData` POST to `/api/ebay/photos`).
- `main.js` - wiring of the fixed controls (nav, game picker, edit switch, search, overlays, import/deck-import buttons, card tilt and lazy WebGL foil hover) and start-up.

### Shared globals other scripts rely on

From `core.js`:

- `state` - the one mutable app state (route, active game, boot data, filters per view, modal card and variant, selections, zoom, ...). `state.boot` is the `/api/bootstrap` answer `{user, games, settings, imports, price_sync, oauth, inbox_new}`; it is fetched at start and fetched again (replacing the whole object) after changes that move its figures: an applied import, collection bulk actions, some card dialog and eBay tracking actions. Settings saved through `/api/settings` are not reloaded; code that saves one also patches `state.boot.settings` itself where needed.
- `$`, `$$`, `content` (`#content`), `escapeHtml`, `money`, `price` (null gives "Kein Preis verfügbar"), `currencyMoney`, `priceSymbolFirst`, `nativePrice`, `date`, `releaseDate`, `variantName`, `initials`.
- `api(url, options)` / `post(url, data)` - JSON client. Error modes: `fetch` throwing becomes `Error` with `isNetworkError`; HTTP 401 navigates to `/login` and throws with `isAuthError`; a non-JSON body throws `Serverfehler (<status>)`; any other non-2xx throws `data.error`. Each answer's `X-Ophir-Stale` header updates the global `serverUnreachable`; a change calls `updateOfflineIndicator()`, and the change back to fresh calls `syncOfflineQueue()`. File uploads (`admin.js`, `sheets.js`, `ebay-photos.js`) use `fetch` with `FormData` directly and get none of this.
- `saveSettingSoon(key, value)` - saves a setting 400 ms after the last change per key; pending ones go out in one `keepalive` POST on `pagehide` or when the page becomes hidden (`flushPendingSettings`).
- `toast(message, actionLabel?, action?, key?)` - 5 s toast; a `key` replaces the previous toast with that key.
- `routeTo(route, data)`, `renderGuard()`, `setNav(route)` - see Views and routing.
- `setActiveGame(gameId, persist=true)` - switches game, resets per-game view state and, on a real change or the first call, the filters to the game's defaults; on a change it refreshes the watchlist badge; persists `activeGameId` unless `persist` is false.
- `setEditMode(enabled, announce)` / `toggleEditMode()` - body class `editing`, switch states.
- `openOverlay(id)` / `closeOverlay(id)` - show/hide a fixed overlay and lock page scroll on both `html` and `body`; closing `card-modal` clears `state.modalCard`.
- `mountFilterPanel(key, selectors, label)` and `statPill(items, {className, columns, mobileColumns})` - produce the two custom elements below.
- `reRenderPreservingFocus(selector, renderFn)` - redraw after typing; only the latest call per selector restores focus and selection.
- `defaultCollectionFilters(gameId)`, `defaultWatchFilters(gameId)`, `collectionUrl(gameId, filters)`, `watchlistCardsUrl(listId, filters)`, `homeBannerUrl(gameId)` - the views must build these URLs with these helpers: the offline save stores exactly these URLs, and the service worker matches saved answers by full URL.
- `searchMatcher(query)` (same rule as `ophir_cards/search.py`), `doSearch(q)` (late answers are dropped and the older request aborted), `refreshWatchCount()`, `applyAppearance()`.

From other files: `openCard(identityId, variantId, refresh)`, `changeQuantity(variantId, delta, quick)`, `changeCollectionEntry(variantId, opts)`, `cardTile`, `mountTileFeed`, `bindCardEvents`, `refreshCurrentView`, `gameRules`, `playsetSize`, `artUrl`, `foilMaskUrl`, `finishPresentation`, `finishThumb`, `openSettingsModal({id, eyebrow, title, intro, body, stack})` / `closeSettingsModal()`. `openSettingsModal` inserts `eyebrow`, `title`, `intro` and `body` as HTML, so callers escape; `stack: true` opens over an open dialog instead of replacing it.

### Views and routing

- Routing is in memory only: `routeTo(route, data)` dispatches `dashboard`, `game` (game id), `game-cards` (game id), `set` (set id), `collection`, `watchlist`, `decks`, `sheets`, `settings`, `admin` to their `render*` function. The route is not put in the URL, so a reload always opens the dashboard. `body[data-route]` is set for CSS.
- `routeTo` first invalidates every pending render (`renderGeneration++`, `deckRenderToken++`), closes deck popups, clears selections of other views, disconnects the tile feed and clears `state.statsUrl`.
- Contract for a view: an async render takes `const stale=renderGuard()` first and returns after each `await` if `stale()` (the deck builder uses its own `deckRenderToken`); it draws by replacing `#content`, with a `.page-loader` placeholder while loading. The smoke harness's `page.route()` depends on both.
- Changing the game keeps the open view; a single `set` gives way to the new game's `game` view; `settings` and `admin` are not redrawn (unsaved input stays).
- `refreshCurrentView()` redraws `set`, `game-cards`, `collection`, `watchlist`, `decks` with `preserve=true`.

### Per-game rules from the server

- Each entry of `state.boot.games` is the `games` row (with `languages` parsed), the user's figures for it, and `client_rules()` from `ophir_cards/games/base.py`: `playset_size`, `copy_limits` (by card type), `symbol`, `icon`, `tile_editions` (`[{id, label, regular, foil, foil_label}]`).
- Read through `gameRules(gameId)` (`catalog.js`, `{}` for an unknown game): `playsetSize()` (default 4), `deckCopyLimit()` (`copy_limits[card_type]`, else `playset_size`, else 4), the tile edition switch (shown only with two or more editions; the choice is `state.tileEditions`, saved as setting `tileEditions`), and `icon` for `/static/tcg-icons/<icon>.svg` in the picker.
- A game's default language is `settings.defaultLanguages[gameId]`, else its `languages[0]`.

### Server endpoints used

All paths below are relative to the app's base path (empty at the root of the host).

- Shell and session: `GET /`, `/login` (form POST to itself), `POST /logout` (plain forms), `/oauth/login`, `GET /service-worker.js`.
- Data, all JSON under `/api/`: `bootstrap`, `settings`, `search`, `games/<id>/{sets,cards,formats}`, `sets/<id>/cards`, `cards/<identity>`, `collection` (GET with filters, `&stats_only=1` for the numbers; POST for changes), `collection/{entries,remove,restore}`, `watchlist`, `watchlists...`, `decks...`, `deckbuilder/catalog`, `trade-sheets...`, `sheet-posts`, `communities`, `inbox...`, `deals...`, `ebay/...`, `import/...`, `export.json`/`export.csv` (plain links), `home-banner`, `home-recent`, `variants/...` (manual prices), `prices/sync`, `foil-layer-meta/<variant>`, `account/...`, `admin/...`, and `public/<slug>/...` for the public page.
- Images: `/art/<variant>.svg?v=6[&size=thumb]`, `/foil-mask/<variant>.webp?v=2`, `/set-logo/`, `/card-back/`, `/game-logo/`, `/lorcana-filter-icon/`, `/op-filter-icon/`.
- Writes must come from the same origin (`reject_cross_site_writes` in `ophir_cards/web.py`); no token is sent.

### Service worker contract (`static/service-worker.js`)

- Served at `<base>/service-worker.js` by `ophir_cards/pages.py` (scope `<base>/`, not behind login, `Service-Worker-Allowed: <base>/`, `max-age=0`). The worker takes the base path from `self.registration.scope` and matches every path below against it. `skipWaiting()` on install, `clients.claim()` on activate. Registration only works in a secure context (HTTPS or localhost).
- There is no `postMessage` channel. The contract with the page is cache names and response headers:
  - Caches `ophir-cards-shell-v2`, `ophir-cards-api-v2`, `ophir-cards-images-v2` (from `CACHE_VERSION = 'v2'`) and `deckledger-offline-v1` (written only by the page, only read here). Activate deletes this app's other shell/API/image caches (`ophir-cards-*`, and the older `ophir-*` and `deckledger-*` names) and leaves every other cache alone: other Ophir apps may share the origin. Bump `CACHE_VERSION` only when the strategy itself changes.
  - `X-Ophir-Stale: 1` on every answer served from a cache because the server did not answer.
  - `X-Image-Source: placeholder` (set by the server on stand-in images) means "do not keep".
- Only same-origin GETs are intercepted, in this order:
  - Images (path contains `/art/`, `/set-logo/`, `/card-back/`, `/lorcana-filter-icon/`, `/op-filter-icon/` or `/icons/`): cache first (image cache, then the offline save), network on a miss, kept unless a placeholder, trimmed to the newest 6000 every 250 stores. While the server is marked unreachable a miss is not fetched. A missing full-size `/art/` image falls back to its `size=thumb` copy.
  - Other `/static/` files: with `?v=` the cached copy is final; without, the cached copy is served and refreshed in the background. Storing a file deletes other query variants of the same path.
  - `/api/`: network first with a 15 s limit; navigations: network first with a 3 s limit. Successful answers are kept (API cache trimmed to the newest 150). The limit only applies when a kept or saved copy exists; without one the request simply waits for the network. On timeout, network error or 502/503/504 the request is aborted, the server is marked unreachable for 30 s (later requests then wait 1.5 s), and the kept copy is returned marked stale. Other error statuses pass through and do not replace the kept copy.
  - Everything else (writes, other origins such as Google Fonts, `/health`, `/foil-mask/`) is left to the browser.
- Only `<base>/static/manifest.json` is precached; `<base>/` is cached on the first signed-in visit.

### Offline state in the browser (`static/js/offline.js`)

- Outbox: IndexedDB `deckledger-offline` (version 1), store `outbox` (auto-increment `id`), entries `{payload, createdAt, userId}`. Only pure-delta `POST /api/collection` payloads are queued (`changeQuantity`, and `changeCollectionEntry` without `quantity`/`price_override`), when `navigator.onLine` is false or `api()` threw `isNetworkError`. Each carries a `request_id`; the server applies an id once, so a replay after a lost answer does not count twice. Absolute writes are refused offline with a toast.
- Quantity changes are optimistic: `patchLocalQuantity` patches tiles, `state.cards` and the open dialog at once; `inQuantityOrder(variantId, task)` sends changes to one variant strictly in click order (different variants in parallel); a refused change is rolled back; `scheduleRefresh()` (900 ms debounce, waits while changes are in flight) fetches only the stat numbers when the view set `state.statsUrl`, else redraws the view.
- `syncOfflineQueue()` runs on the `online` event, after start-up when `navigator.onLine`, and when `api()` sees the stale flag clear; one run at a time. It replays in order, skips entries of other users (kept for them), stops at a network or auth error (the rest stays queued), and drops an entry on any other error with a toast. After a run with entries it redraws the view and reopens the card dialog.
- `updateOfflineIndicator()` shows "Offline", "Server nicht erreichbar · gespeicherter Stand" or "Wird synchronisiert" with the count of this user's queued changes.
- Saving for offline use (`saveForOffline({fullImages, onProgress})`, started from the `Offline verfügbar machen` card in Settings): fetches into `deckledger-offline-v1` the bootstrap, then per game sets, formats, decks, trade sheets, recent additions, the banner, the collection and every watchlist's cards with the default filters, then `/api/cards/<identity>` and the thumbnail (optionally also the full image) of every owned or watched card, 6 at a time. Stale answers and placeholder images are refused; a card file that fails is counted as skipped. Entries not fetched this time are deleted. The marker `/offline-save/info` holds `{userId, savedAt, cards, files, skipped, fullImages}` (`offlineSaveInfo()`). One save at a time; `cancelOfflineSave()` aborts it. This cache is never trimmed and survives `CACHE_VERSION` bumps.
- Per account: `dropForeignOfflineSave()` deletes the saved cache when its `userId` is not the signed-in user. The login page deletes every `ophir-cards-api-*` cache (and the older `ophir-api-*`/`deckledger-api-*`) on load. Shell and image caches are not cleared per account.

### Custom elements, other pages, manifest

- `<ophir-filter-panel filter-key label open>` (`static/filter-panel.js`) - moves its children into a collapsible surface (`contentElement`) with a toggle button and dispatches a bubbling `ophir-filter-toggle` with `detail {key, open}`; `main.js` keeps the open state in `state.mobileFiltersOpen` across redraws. Created via `mountFilterPanel`.
- `<ophir-stat-pill columns mobile-columns>` (`static/stat-pill.js`) - children with `data-stat` become list items; column counts are clamped to 1-8 and passed to CSS as `--stat-pill-columns`/`--stat-pill-mobile-columns`. Created via `statPill()`.
- `templates/login.html` - no app scripts and only `login.css`; an inline script sets `html[data-theme]` from the `ophir_theme` cookie before paint; the SSO link shows when `oauth_enabled`; on load it deletes the API caches (see above).
- `templates/public.html` + `static/public.js` + `public.css` - the shared profile at `/u/<slug>` without sign-in. `public.js` is standalone (it reads `<meta name="app-base">` itself): hash routes `#<game>` and `#<game>/<set>`, data from `/api/public/<slug>`, `/api/public/<slug>/games/<game>/sets`, `/api/public/<slug>/sets/<set>/cards` (each URL fetched once per page load; a failed one is fetched again on the next render), images under `/u/<slug>/art|set-logo|game-logo/`, no prices. The owner's appearance comes from the server (`theme`; `auto` follows the visitor's system). It does not register the service worker, and it reuses names of the app scripts (`renderGame`, `renderSet`, `cardTile`, `releaseDate`), so it must never be loaded together with them.
- `static/manifest.json` - PWA manifest: `start_url` and `scope` `../` and the icons relative to the manifest's own address, so they follow the app's base path, `display: standalone`, theme colour `#090909`, 192 and 512 px icons as `any` and `maskable`.
- `static/lorcana-foil/` - ES modules for the WebGL Lorcana foil (covered in its own doc). Entry point `FoilInteractionController.js`, exporting `foilInteractionController`; `main.js` imports it lazily and, if that fails or WebGL does not attach, the CSS foil stays.

### Stylesheets

- `app.css` - base: reset, the original `:root` tokens, layout and components of every view (shell, tiles, dialogs, deck builder, sheets, inbox, heart menu, bulk bar), reduced-motion and per-tile performance rules. The theme files override its tokens.
- `foil-effects.css` - the finish tier classes (`finish-normal|foil|prismatic|aurora` from `finishPresentation()`), the per-card mask `--foil-mask`, the `.foil-fx-a/b/c` layers and the tilt/shine toolkit (`.card-tilt-zone`). Must load after `app.css`: one override wins by source order only.
- `mobile-theme.css` - phone theme (inside `@media (max-width:760px)`): tokens, bottom tab bar, the card detail stage and reflection.
- `mobile-pages.css` - phone layout of the app chrome and `#content` pages; outside the media query it neutralises phone-only wrappers (`.mobile-filter-shell` is `display:contents`, phone buttons hidden).
- `responsive-theme.css` - the same theme for tablet and desktop (`min-width:761px`).
- `stat-pill.css` - the `ophir-stat-pill` component.
- `dark-appearance.css` - dark-only extras (`:not(.mobile-light)`): filled buttons deep green with a gold hairline, active states gold. Changes no size or position and never applies to the light appearance or the card stage.
- `design-system.css` - loaded last: `--ds-*` tokens per appearance (they also re-point `--bg`, `--surface`, `--surface-2`, `--line`, `--mp-*`), surface steps, fields and secondary buttons, empty areas, selected chips, the one checkbox, section headings, the page head (stat pill, then the tile row), one column of at most 1560 px, `scrollbar-gutter: stable`.
- `login.css` - the login page on its own, in the app's palette; light or dark from `html[data-theme]`, else the system.
- `public.css` - the public profile; reuses the app's tile and set card classes (loaded after the theme files, without `foil-effects.css` and `stat-pill.css`).
- Rule for shared components (comments in `design-system.css`): building blocks used on several pages (surfaces, controls, checkbox, chips, section headings, the tile row shared by the dashboard and "Verkauf & Tausch") are styled once in `design-system.css`, so every page and both appearances change together. Sizes and positions of page layouts stay in the theme files.

### Frontend tests (`tests/frontend/`)

- Run all: `node --test "tests/frontend/*.test.mjs"` (README and CI; Node 22 or newer, no packages). Unit tests alone: `node --test "tests/frontend/unit.test.mjs"`. CI runs them with Node 24 after `python -m pytest`, with `OPHIR_PYTHON: python`.
- `scripts.mjs` - `loadScripts(names, extra)` runs files from `static/` in one `node:vm` context, in page order, with a stub `document`/`window`; `run(expression)` evaluates in it and copies plain data out (top-level `const`/`let` are read by name, not as globals).
- `unit.test.mjs` - logic without a page: finish classification against `tests/fixtures/finish_cases.json` (the same cases `tests/test_trade_sheets.py` checks for `sheet_render.py`), image and mask URLs, escaping and prices, `searchMatcher`, set grouping and ordering, per-game rules (`playsetSize`, `deckCopyLimit`, `deckZoneMaximum`), tile quantity buttons and the edition switch, default filters and the URLs the offline save uses, per-variant change ordering, debounced settings and the `pagehide` flush, late search answers, the per-game watchlist badge, the banner's offline fallback, `renderGuard`.
- `service-worker.test.mjs` - the service worker with fake caches, a scripted network and hand-fired timers: network first with a marked fallback, timeouts (15 s, then 1.5 s), gateway errors, error answers not replacing kept copies, the offline save as fallback, cache limits, image caching and placeholders, hashed vs unhashed static files, the 3 s navigation limit, what is not intercepted, cache cleanup on activate.
- `harness.mjs` - `startServer()` starts `serve.py` on a free port and waits for `/health`; `startBrowser({width, height, mobile})` starts headless Chrome with a throwaway profile and drives it over the DevTools protocol (`evaluate`, `waitFor`, `goto`, `login`, `route`, `motion`, `offline`, `close`). `page.problems` collects uncaught exceptions, `console.error` and HTTP 5xx answers.
- `serve.py` - the app for the browser tests: imports `tests/conftest.py` (throwaway `DATABASE_PATH`, accounts `demo`/`deckledger` and `admin`/`admin`), writes the sample catalogue for `vcard`, `lorcana`, `one-piece`, replaces `ophir_cards.watcher.fetch_feed` with canned Reddit feeds, and runs Flask on `127.0.0.1:<port>`.
- `smoke.test.mjs` - the real app in Chrome: every view for every game, the page standing still behind a dialog, the edit switch in the collapsed sidebar, view keeping on game change, views left while loading, quick add clicks, the VCard edition switch, card dialog and manual price, appearances, watchlist, deck, sheet, the highlights reel (motion, hover pause, reduced motion), inbox and Reddit settings, a deal, search, the offline queue, offline save read back without the server, dropping another account's save, phone layout, admin, eBay drafts dialog, the public profile, admin API refused for a normal user. Tests assert that `page.problems` is empty; only the offline tests and the refused admin call discard the errors they cause on purpose.
- Environment: `CHROME_BIN` overrides the Chrome lookup; `OPHIR_PYTHON` is the interpreter that runs `serve.py` (default `python` on Windows, `python3` elsewhere); without Chrome the smoke tests are skipped unless `CI` is set, in which case they run and fail. The harness passes `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUTF8=1` to the server.

## Dependencies

- `ophir_cards/pages.py`, `ophir_cards/web.py` - page shell, `asset_url`, service worker route, `/api/bootstrap`, static caching and compression, same-origin write guard.
- JSON API under `/api/` (the `ophir_cards` modules) - all data and changes.
- `ophir_cards/games` - per-game rules sent with each game (`client_rules()`).
- Image routes (`ophir_cards/images.py`, `ophir_cards/assets.py`) - card art, foil masks, logos; placeholders marked `X-Image-Source: placeholder`.
- `ophir_cards/public_profile.py` - data and images of the public page.
- Google Fonts - optional web fonts; the stylesheets fall back to system fonts.
- Browser APIs - Service Worker and Cache Storage, IndexedDB, `navigator.storage`, custom elements, IntersectionObserver (without it the tile feed renders everything at once), WebGL (optional, via `static/lorcana-foil/`).
- Tests - Node 22+ (`node:test`, `node:vm`, global `fetch`/`WebSocket`), Chrome or Chromium, Python with `requirements-dev.txt` and `tests/conftest.py`.

## Decisions and assumptions

- No build step: plain scripts sharing one global scope, loaded in a fixed order, each with its own content-hashed URL so a changed file is the only one fetched again (commit 15bb142 split the former 3,300-line `static/app.js` this way).
- Content-hash URLs replaced hand-bumped `?v=N`, because a forgotten bump left a stale stylesheet or script in use; unhashed static files are refreshed in the background (commit da3853a).
- The page shell and API answers always prefer the network; a cached copy is shown only when the server does not answer within 3 s (shell) or 15 s (API) or a proxy reports the app down, and it is always marked stale so old data is never shown as current (commit da3853a).
- Only delta collection changes are queued offline, because they replay safely in any order across devices; absolute writes (set to X, price overrides) are blocked offline instead of risking an overwrite.
- The outbox and the offline save live in the browser, not in an account: both record the user id, other users' queued changes are never sent, and another account's saved data is deleted after a fresh bootstrap.
- `/` is not precached: an install while signed out would store the login page under the app's URL.
- The service worker's own caches only hold what was looked at and drop the oldest entries; a whole collection is available offline only through the explicit save, which has its own never-trimmed cache.
- Views do not appear in the URL; every reload starts on the dashboard.
- A render that is overtaken by navigation must not draw over the newer view (`renderGuard`, `deckRenderToken`).
- Large grids are built progressively (`TILE_CHUNK` 120 tiles, the next chunk once the end is within 2400 px below the viewport) from data already in memory. After a quantity change in a view with a stat summary (set, all cards, collection: `state.statsUrl`) only the numbers are refetched and the grid is not rebuilt, because a rebuild replaced the buttons and lost clicks; watchlist and deck builder still redraw.
- Not everything game-specific comes from the server: the scripts still branch on game ids (`lorcana`, `vcard`, `one-piece`, `hololive`) for filter bars, Lorcana finish labels and badges, and VCard chip order.
- Web fonts load non-blocking so the page does not hang without internet.
- The tests run the real files (vm context for logic, headless Chrome for the app), and an unexpected console error, uncaught exception or 5xx answer fails a smoke test.
