# Ophir Cards

Self-hosted, multi-user collection manager for trading card games: Disney Lorcana, One Piece, hololive OCG and VCard TCG. It keeps the catalogue, each account's collection, watchlists and decks, and the selling side (trade and sale sheets, deals, eBay) in one Flask app with an SQLite database, shipped as a single container image (`ghcr.io/axelcypher/deckledger`). In use.

Ophir Cards is the trading-card part of the Ophir series and was called DeckLedger until November 2026. The repository, the container image, the database file (`deckledger.db`), the Docker volume (`deckledger_data`), the browser's offline storage and the GitHub deployment variables still carry the old name, so existing installations keep their data.

## Features

- Local accounts with user and admin roles; optional sign-in through one OAuth2/OIDC provider
- Set-first catalogue for all four games with filters, sorting, language selection and zoom, imported from the publishers' and community sources and refreshed automatically
- Variant-aware card details (finishes, editions, promos linked to their base card), source links and real card images cached locally
- Collection with quantities, conditions and graded copies; an explicit edit mode; multi-select for one manual price, a watchlist, a sheet, eBay drafts or removal (with undo)
- Search over cards, sets and collector numbers; every search box takes plain text or a regular expression, ignoring case (`PL9|PL10` finds both)
- Several named watchlists per game; holding or right-clicking a heart puts the card on any of them or on a new one
- Decklists with game-defined zones, formats and official-rule validation
- Market prices with history and a source link: Cardmarket for Lorcana and One Piece, TCGplayer through TCGCSV for hololive EN, Yuyutei for hololive JP, converted to EUR at the ECB rate; a manual price for any card
- Trade and sale sheets (WTS, WTT, WTB, WTTF) downloaded as an image collage with a text list for the post; comments on linked posts and matching posts in subreddits land in an inbox
- Deals: what was sold, bought or traded, with whom and at what price, booked into the collection and kept as a history
- eBay: connect a seller account, follow listings as price sources, listing drafts with one template per game and own photos, publishing, the account's listings, sales and statistics
- Public profile: chosen games of a collection at `/u/<address>`, readable without signing in, without prices
- Text-list import with preview, matching and undo; JSON and CSV export; JSON backup and restore of collection, decks, watchlists, sheets and deals
- Installable web app that keeps working when the server is out of reach

## Requirements

- Docker with Compose - runs the container ([compose.yaml](compose.yaml))
- Python 3.13 - development and the test suite (the image is `python:3.13-slim`); Linux or macOS, because the app and the job scripts use `fcntl`
- Node.js 22 or newer - the frontend tests use Node's own test runner (CI uses Node 24)
- Chrome or Chromium - the browser smoke tests; without it they are skipped locally

## Setup

1. Clone the repository:

    ```bash
    git clone https://github.com/axelcypher/deckledger.git
    cd deckledger
    ```

2. Set a strong `SECRET_KEY` in the environment or in a `.env` file next to `compose.yaml` (Compose falls back to a fixed development key otherwise):

    ```dotenv
    SECRET_KEY=<long random string>
    ```

3. Build and start:

    ```bash
    docker compose up --build -d
    ```

4. Open <http://localhost:18081> and sign in as `admin` / `admin`. Change that password under **Admin → Benutzer** right away and create the other accounts there.

The first start creates the database in the `deckledger_data` volume and imports the catalogues in the background; the app serves while that runs. There is no synthetic seed: no demo account, cards, collections or prices. A database that still has the old `demo` / `deckledger` account gets it locked.

## Configuration

| Setting | Where | Description | Default | Required |
| --- | --- | --- | --- | --- |
| `SECRET_KEY` | env | Signs the session cookies | a fixed development key | yes, outside a local test |
| `DATABASE_PATH` | env | SQLite file; every cache (card images, thumbnails, sheet renders, uploaded backgrounds, card backs, eBay photos) lives in folders next to it | `deckledger.db` in the repo root; `/data/deckledger.db` in the image | no |
| `PUBLIC_DIR` | env | Folder with set visuals, card backs and foil assets | `public/` in the repo root | no |
| `TRUST_PROXY_HEADERS` | env | `true`, `1` or `yes`: honour `X-Forwarded-For` / `-Proto` / `-Host` / `-Prefix` | off | behind a TLS-terminating proxy |
| `OAUTH_CONFIG_PATH` | env | SSO config file that replaces the Admin UI settings | `/config/oauth.json` | no |
| `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_DISCOVERY_URL` | env | SSO client credentials and discovery URL; with `OAUTH_CLIENT_ID` set they win over the Admin UI and SSO is on | empty | no |
| `PUBLIC_PROFILE_URL` | env | Base URL public profile links are built on, with the path when the app is served under one (`https://ophir.example.org/cards`) | the address the settings page was opened on | no |
| `CATALOG_REFRESH_HOURS` | env | Age of a game's last successful import that triggers a new one | `24` | no |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_RU_NAME` | env or **Admin → eBay** | Keys of the eBay application; when `EBAY_CLIENT_ID` is set, the environment wins and the Admin card is read-only | empty | for eBay |
| `EBAY_ENVIRONMENT` | env | `production` or `sandbox` | `production` | no |
| `EBAY_MARKETPLACE` | env | `EBAY_DE`, `EBAY_AT`, `EBAY_FR`, `EBAY_IT`, `EBAY_ES`, `EBAY_GB` or `EBAY_US` | `EBAY_DE` | no |
| `EBAY_DELETION_ENDPOINT` | env | Full URL of the account-deletion endpoint when the public host differs from the admin page's host | `https://<host>/ebay/account-deletion` | no |
| `EBAY_VERIFICATION_TOKEN` | env | Replaces the generated verification token for account-deletion notices | generated | no |
| SSO settings | **Admin → Single Sign-On** or `oauth.json` | See below | off | no |

### Serving under a path

Behind a reverse proxy the app can also run under a path, for example `ophir.example.org/cards` next to other apps of the Ophir series. The proxy strips the path and passes it on as `X-Forwarded-Prefix` (Traefik's `stripPrefix` middleware does both), and `TRUST_PROXY_HEADERS` must be on. The app then builds every address under that path: links, redirects, scripts, images, the service worker's scope and the web app manifest. Requests without the header are served at the root, so one container can answer on a host of its own and under a path at the same time.

- Each host is its own origin: a sign-in, the offline copy and queued changes are per address.
- The session cookie is `ophir_cards_session`, limited to the app's path and signed with a salt of its own, so apps on the same host never read each other's sessions, even with the same `SECRET_KEY`.
- The SSO redirect URI and the eBay callback contain the path too (`https://ophir.example.org/cards/oauth/callback`, `.../cards/ebay/callback`); register them with the provider for every address you sign in on.

### Single sign-on (OAuth2 / OIDC)

Ophir Cards can sign users in through one external OAuth2/OIDC identity provider (Google, Authentik, Keycloak, Authelia, Okta or any other OIDC-compatible one), next to local username and password accounts. It is configured one of three ways:

- **Admin → Single Sign-On**: stored in the database, effective immediately.
- **Environment variables** `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_DISCOVERY_URL` (for example from a secrets file): once `OAUTH_CLIENT_ID` is set they replace the client credentials and discovery URL of the Admin UI and switch SSO on. Those fields are then read-only there; the button label, manual endpoints, claims and account matching stay editable.
- **A config file**: shape as in [oauth.json.example](oauth.json.example), mounted read-only at `/config/oauth.json` (or `OAUTH_CONFIG_PATH`):

    ```yaml
    services:
      ophir_cards:
        volumes:
          - deckledger_data:/data
          - ./public:/app/public:ro
          - ./oauth.json:/config/oauth.json:ro
    ```

    The file always wins when it is present: the Admin UI then shows every field read-only. Change the file and run `docker compose restart ophir_cards`.

Required are `client_id`, `client_secret` and either `discovery_url` (OIDC) or all three of `authorize_url`, `token_url` and `userinfo_url` (plain OAuth2). `account_matching` decides what happens on the first login of an identity Ophir Cards has not seen, each level including the ones before it:

- `manual` (safest): the identity must already be linked; a user links their own account under **Einstellungen → Konto** while signed in with a password.
- `email`: an identity whose email matches an unlinked local account (admins included) is linked to it, unless the provider reports the address as unverified (`email_verified: false`).
- `auto_provision` (default): an identity whose email belongs to no account gets a new `user` account (never `admin`). An unverified address that matches an existing account is turned away.

Most providers require an `https://` redirect URI. Behind a TLS-terminating reverse proxy set `TRUST_PROXY_HEADERS=true`, and only when the proxy is the sole way to reach the container: otherwise a direct client can spoof those headers.

### eBay

eBay needs an application from the eBay Developer Program. Enter its client id, client secret and RuName under **Admin → eBay** (or set the `EBAY_*` variables). The RuName's accept and decline URL is `https://<host>/ebay/callback`. Client id and secret alone are enough to follow listings as prices; connecting accounts and publishing need the RuName too. Drafts stay in Ophir Cards until they are published, because eBay's API cannot create Seller Hub drafts. Listing statistics need the scope `sell.analytics.readonly`; accounts connected before it was requested reconnect once.

eBay unlocks production keys only once the application listens for deleted eBay accounts. Under **Application Keys → Notifications → Marketplace Account Deletion** enter the endpoint URL and verification token shown under **Admin → eBay**. The endpoint is `https://<public host>/ebay/account-deletion`; it needs no login and must be reachable by eBay over HTTPS, so exposing just that path is enough. If the public host differs from the one the admin page is opened on, set `EBAY_DELETION_ENDPOINT`.

### Set visuals

Files in `public/sets/` override provider-fetched set images and generated wordmarks. Name them after the internal set id (`one-piece-op-01.webp`, `lorcana-1.png`) or the set code; AVIF, WebP, PNG, JPEG and SVG work. `compose.yaml` mounts `public/` read-only, so adding a file needs no image rebuild (see [public/sets/README.md](public/sets/README.md)).

## Usage

### Background jobs

The container's [docker-entrypoint.sh](docker-entrypoint.sh) runs the migrations, then starts gunicorn and these loops:

| Job | When | What |
| --- | --- | --- |
| `catalog_sync.py --if-needed` | at start, then every six hours with `--max-age-hours $CATALOG_REFRESH_HOURS` | imports a game whose provider code has not been imported yet; the six-hourly run also retries failed imports and re-imports those older than `CATALOG_REFRESH_HOURS` |
| `price_sync.py --if-needed` | at start, then every six hours | downloads the price feeds and stores prices only when a feed has published new data |
| `post_watch.py` | every minute | reads one Reddit feed: a linked post's comments or a subreddit's new posts |
| `ebay_sync.py` | every ten minutes | re-reads followed eBay listings and connected accounts' listings and sales when due |

A failing job leaves the last stored catalogue and prices in use. Catalogue and prices can also be refreshed by hand:

```bash
docker compose exec ophir_cards python catalog_sync.py
docker compose exec ophir_cards python price_sync.py
```

The catalogues come from LorcanaJSON (Lorcana EN/DE, with Ravensburger image URLs), the official One Piece and hololive card lists (EN/JP) and the official VCard TCG set pages; VCard's 1-of-1 God Rares are left out (`INCLUDE_NON_COLLECTIBLE` in `providers/vcard.py`). The code under `providers/` is what runs, unless an admin has replaced it under **Admin → Katalog-Provider**. A card that disappears from a source stays in the catalogue for as long as a collection, deck or watchlist still uses it. Missing market prices stay empty; they are never shown as `0.00` or estimated. A manual price ("Eigener Preis" in a card's market tab) is one per card, shared by all accounts, and wins over the feeds until it is removed. VCard has no price feed, so its prices are entered by hand.

### Verkauf & Tausch

The page has two tabs: **Verkäufe** (the eBay listings, the inbox and the deals) and **Sheets**.

- **Sheets** are the shop window: pick cards from the collection (or, for wanted cards, from the watchlists or the whole catalogue), set quantities and price labels, choose a drawn playmat or an uploaded picture as background, and download the collage with a text list for the post.
- **Posts and the inbox**: paste the link of a post into the sheet editor ("Posts zu diesem Sheet") and its new comments land in the inbox ("Eingang"). The gear on the Sheets tab opens the Reddit settings: your Reddit name (your own posts and answers are skipped) and, per game, the subreddits whose new posts are matched against your sheets ("Fremde Posts" in the sheet editor narrows that per sheet). Reddit is read through its public feeds, without an account or API key, and nothing is posted. Reddit allows about one anonymous request a minute per public IP address, which is why `post_watch.py` reads one feed per run.
- **Deals** ("Vorgänge") record what was given and got, with partner, platform, money and shipping. Their status books the collection: **Offen** books nothing, **Reserviert** marks the cards on every sheet that offers them, **Abgeschlossen** takes the given cards out and, once **Empfang bestätigen** is clicked, the received cards in; cancelling a finished deal takes its booking back. eBay sales are booked as finished deals.

### Offline use

The app is installable and keeps working when the server cannot be reached: pages and what was looked at recently come from the service worker's cache (marked as a saved copy), and quantity changes are queued and sent once the server answers again. **Einstellungen → Offline verfügbar machen** saves a whole account on purpose (collection, watchlists, deck and sheet overviews, the details and thumbnails of every owned or watched card, optionally full-size images) in a cache of its own that is never trimmed and belongs to that account.

### Accounts and users

- Every user changes their own name, username, email and password under **Einstellungen → Konto**; a password change needs the current one, except for an account without a local password yet (for example one created by SSO).
- Admins manage accounts under **Admin → Benutzer**: create, rename, change role, reset a password, detach an SSO identity, delete an account with all its data. The last admin can be neither demoted nor deleted, and nobody deletes their own account there.
- **Einstellungen → Öffentliches Profil** switches on a public link to chosen games of the collection.

### Operations

```bash
docker compose ps
docker compose logs -f
docker compose stop
docker compose down
```

`docker compose down` keeps the `deckledger_data` volume; it holds the database and every cache, so it is what to back up. `GET /health` answers with the database name, `schema_version` and `schema_expected`. Before an existing database is migrated, a copy is written next to it (`deckledger.db.before-migration-NNNN`, the two newest kept); a database written by a newer version than the running image is refused at start-up.

## Development

Without Docker (Linux or macOS):

```bash
pip install -r requirements-dev.txt
python app.py
```

`python app.py` starts Flask's debug server on port 8080 with `deckledger.db` in the repo root; `python catalog_sync.py --provider <id>` imports one game's catalogue into it.

- Code layout: `app.py` is only the entry point; the application is the `ophir_cards` package, the job scripts, sheet renderer and providers sit next to it. Module docs: [docs/code/](docs/code/architecture.md).
- Schema changes: add `ophir_cards/migrations/mNNNN_<what>.py` with `NAME` and `apply(connection, context)` and list it in `MIGRATIONS`. A released migration is never edited; nothing else creates or alters tables (a test enforces it).
- Python tests: `python -m pytest` - runs against a throwaway database with a small built-in catalogue, never touches the network.
- Frontend tests: `node --test "tests/frontend/*.test.mjs"` - script logic in a bare JavaScript context, the service worker against fake caches, and smoke tests that drive the real app in headless Chrome (`CHROME_BIN` sets the browser, `OPHIR_PYTHON` the interpreter that starts the app).
- Lint / format: none configured.

## Release

[.github/workflows/container-image.yml](.github/workflows/container-image.yml) runs on every push to `main`, on `v*` tags, on pull requests and by hand. It runs both test suites, then builds the image and, except for pull requests, pushes it to `ghcr.io/axelcypher/deckledger` with the tags `main`, `latest`, `sha-<commit>` and, for a `v*` tag, the semver tags, and attests it. There is no version number in the code; images are identified by their `sha-<commit>` tag.

After a published build from `main` or a `v*` tag, the job `update-deployment-repository` writes that tag into an env file of another repository. It stays off until the repository variable `DECKLEDGER_DEPLOY_REPOSITORY` is set:

| Variable / secret | Description | Default |
| --- | --- | --- |
| `DECKLEDGER_DEPLOY_REPOSITORY` | Target repository, `owner/repository` | none (job disabled) |
| `DECKLEDGER_DEPLOY_BRANCH` | Branch to commit to | `main` |
| `DECKLEDGER_DEPLOY_ENV_FILE` | Env file in the target repository | `.env` |
| `DECKLEDGER_DEPLOY_IMAGE_KEY` | Variable that receives the tag | `DECKLEDGER_IMAGE_VERSION` |
| `DECKLEDGER_DEPLOY_TOKEN` (secret) | Fine-grained token with read/write access to the target's contents | none |

## Documentation

- Module docs for agents: [docs/code/](docs/code/architecture.md)
- Detailed docs: vault note `homelab-docs/projects/ophir_cards.md`
