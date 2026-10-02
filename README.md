# DeckLedger

Self-hosted, multi-user collection manager for trading card games. This repository contains a runnable MVP based on `tcg_collection_planning.md`.

## Start

```bash
docker compose up --build -d
```

Open <http://localhost:18081> and sign in with:

- User: `demo`
- Password: `deckledger`

An administrator demo account is also available as `admin` / `admin`. Change both
passwords under **Admin → Benutzer** before more than one person uses the instance;
the demo hint on the login page disappears once the `demo` password has been changed.

## Included in the MVP

- Local accounts with user/admin roles
- Global collection dashboard for Lorcana, One Piece, hololive OCG and VCard TCG
- Set-first catalogue navigation with filters, sorting, language selection and zoom
- Variant-aware card details, source links and relationships
- Persistent collection quantities, conditions and watchlist entries
- Explicit Edit Mode and Quick Entry
- Global card, set and collector-number search
- Text-list import with preview, matching and undo
- JSON and CSV export
- SQLite persistence in the `deckledger_data` Docker volume
- Provider-backed real card images cached in the persistent volume
- Direct source links from every displayed market price
- Daily Cardmarket prices for Lorcana and unambiguous One Piece products
- Language-separated hololive prices: TCGplayer/TCGCSV for EN and Yuyutei retail for JP
- Persistent global TCG context across collection, search, watchlists and decks
- Multiple named watchlists per TCG with catalogue-style filters and sorting
- Saved decklists with module-defined zones, formats and official-rule validation
- Trade and sale sheets: pick cards from the collection, set quantities and price labels, and download the sheet as an image collage on a playmat-style background, with a ready-to-paste text list for the post; holo and other foil prints are marked in the image with a rainbow sheen and an iridescent frame, and VCard's print-file images are trimmed to the cut card
- JSON backups carry collection, decks, watchlists and sheets, and restore all four

There is no synthetic card, collection, deck, watchlist or price seed. On the first start, DeckLedger imports and validates the current EN/DE Lorcana catalogue from LorcanaJSON (including Ravensburger image URLs) plus the EN/JP official One Piece and hololive catalogues and the official VCard TCG card database. The normalized catalogue remains in SQLite and exact card images are cached locally on first display. Missing market observations remain empty and are never presented as `0.00` or estimated from fabricated data.

Promotional reprints are linked to their base gameplay identity and remain assigned to their physical promo group. To refresh from all live card sources, run:

```bash
docker compose exec deckledger python catalog_sync.py
```

The catalogue is re-imported automatically once a game's last successful import is older than `CATALOG_REFRESH_HOURS` (default 24, checked every six hours), so new sets and cards appear without a manual run. Each game imports on its own; a card that disappears from a source stays in the catalogue for as long as a collection, deck or watchlist still uses it. The provider code under `providers/` is what runs, unless an admin has replaced it with their own in the Admin UI.

Prices refresh automatically on startup and every six hours when a provider has published new daily data. A manual refresh is available in every card's market tab or through:

```bash
docker compose exec deckledger python price_sync.py
```

Price history stores changes, not days: a sync that finds the same price as last time writes nothing, and readers carry a price forward until the next change. Every calendar month that has aged out of the last three months is additionally folded into one time-weighted average per card, provider and metric. An existing day-by-day history is converted once, on the first price sync after the update.

Cardmarket product IDs are persisted separately from internal variant IDs. One Piece Western and Japanese expansions are resolved and priced separately: the Western expansion is anchored by Bandai's official release date, while the corresponding Japanese match must use a distinct Cardmarket expansion ID with the same set/number/name fingerprint. Ambiguous matches remain empty; the importer never resolves them by card name alone.

VCard TCG (Gamer Supps) is imported from the public set pages of vcardtcg.com. Every card is tracked per edition and finish (Unlimited/Limited and 1st Edition, each regular and Holo). The 1-of-1 God Rares are left out by default (`INCLUDE_NON_COLLECTIBLE` in `providers/vcard.py`). No marketplace price feed carries VCard yet, so its prices are entered by hand. Its card images are the publisher's print files; the 3 mm bleed is cut off once, for every view.

A price can be entered by hand for any card in its market tab ("Eigener Preis"). It is stored like a provider's price, with its own history, counts in every total, and takes precedence over the feeds until it is removed. Prices belong to the catalogue: there is one manual price per card, shared by all accounts.

hololive mappings are language-locked: EN variants use TCGplayer's daily USD export through TCGCSV; JP variants use Yuyutei's JPY retail listings. Original quotes and the daily ECB exchange rate are retained, while EUR conversions are used for collection totals. Ambiguous set/number/rarity matches remain empty.

Set visuals placed in `public/sets` always override provider-fetched images and
generated wordmarks. Prefer the internal set ID as filename, for example
`one-piece-op-01.webp`; AVIF, WebP, PNG, JPEG, and SVG are supported. The public
folder is mounted read-only into the container, so adding an asset needs no
image rebuild.

The fixed "Verkaufsliste" that older versions kept among the watchlists has been
replaced by the trade sheets. On the first start after the update every such list
that holds cards becomes a WTS sheet of the same name with the same cards and
quantities; a backup that still contains one restores it as a sheet as well. What
that list collected automatically is now the "Über Playset" source when adding cards
to a sheet.

## Offline use

The app is installable and keeps working when the server cannot be reached: the page and what was looked at recently come from the service worker's cache (marked as a saved copy), and quantity changes are queued on the device and sent once the server answers again.

What was "looked at recently" is limited and gets trimmed. **Settings → Offline verfügbar machen** fetches a whole account on purpose: the collection and watchlists of every game, the deck and sheet overviews, and for every owned or watched card its details and thumbnail, optionally the full-size images. That copy lives in a cache of its own that is never trimmed, belongs to the account that saved it (another account signing in on the same browser removes it), and is refreshed with the same button.

## Account settings

Every signed-in user can update their own display name, username, email
address and password from **Settings → Kontodaten / Passwort**. Changing a
password always requires the current one, except for an account that has no
local password yet (e.g. one created through SSO auto-provisioning below) —
that account can set its first password directly.

## User management

Admins manage accounts under **Admin → Benutzer**: create accounts, change
name, username, email and role, reset a password, detach an SSO identity, and
delete an account together with its collection, decks, lists and sheets. The
last remaining admin can be neither demoted nor deleted, and nobody deletes
their own account there. A deleted account's open sessions stop working
immediately.

## OAuth / SSO login

DeckLedger supports logging in through one external OAuth2/OIDC identity
provider (Google, Authentik, Keycloak, Authelia, Okta, or any other
OIDC-compatible IdP), in addition to local username/password accounts. It can
be configured two ways:

- **Web UI**: sign in as an admin and open **Admin → Single Sign-On (OAuth)**.
  Settings are stored in the database and take effect immediately, no restart
  needed.
- **Config file mounted into the container**: create a JSON file (see
  `oauth.json.example` in this repository for the shape) and mount it
  read-only at `/config/oauth.json`:

  ```yaml
  services:
    deckledger:
      volumes:
        - deckledger_data:/data
        - ./public:/app/public:ro
        - ./oauth.json:/config/oauth.json:ro
  ```

  **The config file always wins when it's present.** In that case, the Admin
  UI shows every SSO field read-only with a note pointing at the file — edit
  the file and restart the container (`docker compose restart deckledger`) to
  change anything. The mount path can be moved with the `OAUTH_CONFIG_PATH`
  environment variable; without a file at that path, settings come from the
  database and the Admin UI is fully editable.

Only `client_id`, `client_secret`, and either `discovery_url` (OIDC) or all
three of `authorize_url`/`token_url`/`userinfo_url` (plain OAuth2) are
required; the rest have sane defaults. `account_matching` controls how a
first-time login from an identity DeckLedger hasn't seen before is resolved,
each level including the ones before it:

- `manual` (safest): the identity must already be linked. A user links their
  own account themselves from **Settings → Single Sign-On** while signed in
  with a password.
- `email`: additionally, if the provider's email matches an existing local
  account's email (admin accounts included) and that account isn't linked to
  anything yet, it's linked automatically on first login. The provider has to
  report the address as verified.
- `auto_provision`: additionally, an identity whose email belongs to no
  account gets a brand-new local account (`user` role, never `admin`) created
  automatically. An unverified address that matches an existing account is
  turned away instead of getting a second account next to it.

Running behind a TLS-terminating reverse proxy (Traefik, Nginx, Caddy, …)?
Most OAuth/OIDC providers require an `https://` redirect URI. Set
`TRUST_PROXY_HEADERS=true` so DeckLedger honors the proxy's
`X-Forwarded-Proto`/`X-Forwarded-Host` headers when building that URL — only
enable this when the proxy is the sole way to reach the container, since it
otherwise lets a direct client spoof those headers.

## Code layout

`app.py` is only the entry point (`gunicorn app:app`). The application is the `deckledger` package:

| Module | What it holds |
| --- | --- |
| `config`, `web`, `schema` | paths and constants; the Flask app, request guards and login decorators; database schema, migrations and seed data |
| `games/` | everything that differs per game, one module each (`lorcana`, `one_piece`, `hololive`, `vcard`) plus the `Game` description they fill in |
| `catalog`, `collection`, `watchlists`, `decks`, `sheets` | the views of the same names |
| `prices`, `images`, `assets` | price lookup and manual prices; card images, thumbnails, trimming, foil masks; logos, set visuals, card backs |
| `auth`, `account`, `admin`, `pages`, `backup` | sign-in and SSO; a user's own settings; the admin API; page shell and dashboard data; import, export and restore |

Modules import each other in one direction only (the order `deckledger/__init__.py` lists them in). No module outside `games/` branches on a game's id: it asks `games.game(game_id)` for the playset size, the rarity ladder, the deck rules, where images and prices come from. Adding a game is one module there, its provider under `providers/`, and an icon. The frontend receives the rules it needs (playset size, copy limits, icon) with each game.

The frontend has no build step. `static/js/` holds plain scripts that share one global scope and are loaded in a fixed order (`core.js` first, `main.js` last, see `templates/index.html`): `core` (state, API client, navigation), `finish`, `filters`, `catalog`, `offline`, `collection`, `watchlist`, `decks`, `card-modal`, `dashboard`, `settings`, `admin`, `import`, `sheets`, `main` (wiring and start-up). Each file gets its own content-hashed URL, so a changed file is the only one a browser fetches again.

The catalogue and price jobs (`catalog_sync.py`, `price_sync.py`), the sheet renderer (`sheet_render.py`) and the providers stay standalone scripts and modules next to the package.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The suite runs against a throwaway database with a small built-in catalogue and never touches the network. It covers the backup round trip, import undo, what a catalogue sync may and may not delete, provider seeding, the collection endpoint, the VCard provider and deck rules, price lookup, and the cross-site/session checks. The container workflow runs it before building an image.

The frontend has three kinds of tests, all run by Node's own test runner (Node 22 or newer, no packages to install):

```bash
node --test "tests/frontend/*.test.mjs"
```

- `unit.test.mjs` loads the scripts from `static/js` into a bare JavaScript context and checks the logic that needs no page: foil classification (against the same cases as the sheet renderer), set grouping and sorting, copy limits, the URLs the views request, the ordering of quantity changes.
- `service-worker.test.mjs` runs the service worker against fake caches, a scripted network and hand-fired timers: network first with a marked fallback, giving up on a server that does not answer, the offline save as a fallback, what is kept and what is trimmed.
- `smoke.test.mjs` drives the real app in a headless Chrome against the test catalogue: every view for every game, adding cards with quick clicks, a card and a manual price, a deck, a sheet, the offline queue, saving the collection for offline use and reading it back without the server, the phone layout, the admin view. Any error in the browser console fails a test. `CHROME_BIN` overrides where Chrome is looked for, `DECKLEDGER_PYTHON` which interpreter starts the app; without Chrome these tests are skipped locally.

The workflow runs all of them after the Python suite.

## Operations

```bash
docker compose ps
docker compose logs -f
docker compose stop
docker compose down
```

`docker compose down` keeps the named data volume. Use a strong `SECRET_KEY` environment variable before exposing the application outside a local test environment.

## Deployment repository update

After a successful image publication from `main` or a `v*` tag, the container
workflow can update an image variable in a tracked environment file in another
repository. The written value is only the immutable image tag without registry
or image name, for example `sha-99fc47d723e19fd5d0bfea747318416cfaec03eee`.

Configure these GitHub repository variables in DeckLedger:

- `DECKLEDGER_DEPLOY_REPOSITORY` (required): target in `owner/repository` form
- `DECKLEDGER_DEPLOY_BRANCH` (optional, default `main`)
- `DECKLEDGER_DEPLOY_ENV_FILE` (optional, default `.env`)
- `DECKLEDGER_DEPLOY_IMAGE_KEY` (optional, default `DECKLEDGER_IMAGE_VERSION`)

Add `DECKLEDGER_DEPLOY_TOKEN` as a repository secret. It must be a fine-grained
personal access token with read/write access to repository contents in the
target repository. The deployment-update job stays disabled until
`DECKLEDGER_DEPLOY_REPOSITORY` is configured.
