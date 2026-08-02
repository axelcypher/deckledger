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

## Operations

```bash
docker compose ps
docker compose logs -f
docker compose stop
docker compose down
```

`docker compose down` keeps the named data volume. Use a strong `SECRET_KEY` environment variable before exposing the application outside a local test environment.
