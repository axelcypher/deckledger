# DeckLedger

Self-hosted, multi-user collection manager for trading card games. This repository contains a runnable MVP based on `tcg_collection_planning.md`.

## Start

```bash
docker compose up --build -d
```

Open <http://localhost:18081> and sign in with:

- User: `demo`
- Password: `deckledger`

An administrator demo account is also available as `admin` / `admin`.

## Included in the MVP

- Local accounts with user/admin roles
- Global collection dashboard for Lorcana, One Piece and hololive OCG
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

There is no synthetic card, collection, deck, watchlist or price seed. On the first start, DeckLedger imports and validates the current EN/DE Lorcana catalogue from LorcanaJSON (including Ravensburger image URLs) plus the EN/JP official One Piece and hololive catalogues. The normalized catalogue remains in SQLite and exact card images are cached locally on first display. Missing market observations remain empty and are never presented as `0.00` or estimated from fabricated data.

Promotional reprints are linked to their base gameplay identity and remain assigned to their physical promo group. To refresh from all live card sources, run:

```bash
docker compose exec deckledger python catalog_sync.py
```

Prices refresh automatically on startup and every six hours when a provider has published new daily data. A manual refresh is available in every card's market tab or through:

```bash
docker compose exec deckledger python price_sync.py
```

Cardmarket product IDs are persisted separately from internal variant IDs. One Piece Western and Japanese expansions are resolved and priced separately: the Western expansion is anchored by Bandai's official release date, while the corresponding Japanese match must use a distinct Cardmarket expansion ID with the same set/number/name fingerprint. Ambiguous matches remain empty; the importer never resolves them by card name alone.

hololive mappings are language-locked: EN variants use TCGplayer's daily USD export through TCGCSV; JP variants use Yuyutei's JPY retail listings. Original quotes and the daily ECB exchange rate are retained, while EUR conversions are used for collection totals. Ambiguous set/number/rarity matches remain empty.

Set visuals placed in `public/sets` always override provider-fetched images and
generated wordmarks. Prefer the internal set ID as filename, for example
`one-piece-op-01.webp`; AVIF, WebP, PNG, JPEG, and SVG are supported. The public
folder is mounted read-only into the container, so adding an asset needs no
image rebuild.

## Account settings

Every signed-in user can update their own display name, username, email
address and password from **Settings → Kontodaten / Passwort**. Changing a
password always requires the current one, except for an account that has no
local password yet (e.g. one created through SSO auto-provisioning below) —
that account can set its first password directly.

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
  account's email and that account isn't linked to anything yet, it's linked
  automatically on first login.
- `auto_provision`: additionally, an unmatched identity gets a brand-new
  local account (`user` role, never `admin`) created automatically.

Running behind a TLS-terminating reverse proxy (Traefik, Nginx, Caddy, …)?
Most OAuth/OIDC providers require an `https://` redirect URI. Set
`TRUST_PROXY_HEADERS=true` so DeckLedger honors the proxy's
`X-Forwarded-Proto`/`X-Forwarded-Host` headers when building that URL — only
enable this when the proxy is the sole way to reach the container, since it
otherwise lets a direct client spoof those headers.

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
