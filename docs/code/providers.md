# Providers

The catalogue providers DeckLedger ships with, one per game: each turns the publisher's or a community source into the catalog dict defined in `catalog_provider_contract.py`. They are seeded into `catalog_providers` and run by `catalog_sync.py` in a subprocess (see catalog.md).

## Public interface

Every provider exposes one function, `fetch_catalog() -> dict`, returning `{sets, identities, printings, variants, sources}`. A failed download raises `RuntimeError` (the contract's `fetch` tries three times), and so do the provider-specific completeness checks below; a raise fails that game's import and leaves its stored catalogue untouched. Validation minimums and timeouts come from `Game.provider` in `deckledger/games/<game>.py`:

| File | Game id | Min. sets | Min. cards | Timeout |
|---|---|---|---|---|
| `providers/lorcana.py` | `lorcana` | 15 | 2500 | 300 s |
| `providers/one_piece.py` | `one-piece` | 20 | 1000 | 600 s |
| `providers/hololive.py` | `hololive` | 10 | 500 | 600 s |
| `providers/vcard.py` | `vcard` | 4 | 1200 | 300 s |

### providers/lorcana.py

- Reads `https://lorcanajson.org/files/current/en/allCards.json` and `.../de/allCards.json`.
- Sets: one per LorcanaJSON set code, id `lorcana-<code>`; English wins over German. `code` is the display abbreviation from `LORCANA_SET_ABBREVIATIONS` ("1" -> "TFC" ... "13" -> "AOTV"), `classifications` the formats the set is allowed in. Cards with a `promoGrouping` go to a set of that group (`LORCANA_PROMO_GROUPS` gives name and type, unknown groups become "Promo Group <code>", no release date). `printed_card_count` = EN printings in the set.
- Identities: `lorcana-card-<baseId or id>`, so promos and reprints share their base card's identity; name, rules text and gameplay attributes (color, cost, inkwell, strength, willpower, lore, subtypes, allowedInFormats) come from the English card.
- Printings: `lorcana-print-<card id>-<en|de>`, collector number and rarity label as printed in that language; attributes carry `localizedName`, `localizedRulesText`, artists, promo fields and LorcanaJSON's `externalLinks`.
- Variants: one per `foilTypes` entry (`None` -> code `normal`, finish `Normal`; otherwise the foil type as finish, `is_parallel=1`). Attributes: `imageUrl` (LorcanaJSON's full image), `priceUrl` (Cardmarket link, else TCGplayer, else CardTrader) and `priceSource`. `price_sync.py` matches Lorcana prices through that Cardmarket URL.
- German TFC misprints (`LORCANA_DE_TFC_MISPRINTS`, cards #21 and #2): the DE printing gets two extra variants (`normal-<suffix>`, `silver-<suffix>`, `source_type='physical-errata-printing'`) with a Cardmarket photo, the Cardmarket product URL as `priceUrl`, `editionLabel`, `errata` and `isMisprint`.

### providers/one_piece.py

- Card lists: `https://en.onepiece-cardgame.com/cardlist/` and `https://www.onepiece-cardgame.com/cardlist/` (JP); every product in the series dropdown is fetched as `?series=<n>` (8 threads).
- Set code: the code in the label's trailing square (or Japanese lenticular) brackets; promo labels give `PROMO`, anything else `SERIES-<last 3 digits>`. Set id `one-piece-<code>`, shared by EN and JP (EN record wins). Names lose the product category prefix and the code (booster titles are title-cased); `PROMO` -> "Promotion Cards", `SERIES-801` -> "Other Product Cards". `release_date` = latest Western date from `https://en.onepiece-cardgame.com/products/` (all result pages). `printed_card_count` = distinct identities per set.
- Identities: `one-piece-card-<collector number>`, language-independent, English preferred. Printings: `one-piece-print-<set code>-<number>-<en|jp>`, so a reprint in another product is another printing with the same number. Variants come from the card's modal id: no suffix -> `standard` / `Normal`, otherwise `Parallel <suffix>` with `is_parallel=1`.
- Combined Western products (`ONE_PIECE_COMBINED_RELEASES`: OP14-EB04, OP15-EB04): their EN printings move to OP-14/OP-15, or to EB-04 for `EB04-` numbers, with `canonicalSetCode`, `releaseProductCode`, `releaseProductName`, `releaseProductReleaseDate` on the printing; the combined sets themselves are dropped and OP-14, OP-15, EB-04 get fixed names (`ONE_PIECE_CANONICAL_SETS`). A missing canonical set raises.
- DON!! cards (absent from the official card list):
  - EN: TCGJSON's `one-piece.full.json.gz` asset of the latest release of GitHub repo `HanClinto/tcgjson`, resolved through `https://api.github.com/repos/HanClinto/tcgjson/releases/latest`. Products with rarity `DON!!` and at least one image become `one-piece-print-don-<productId>-en` with an empty collector number (`unprintedCollectorNumber`), one variant per `foilings` entry, in the set matching the TCGJSON set abbreviation if the catalogue has it, else the fallback set `one-piece-don` ("DON!! Cards").
  - JP: Bandai's PDF `https://asia-en.onepiece-cardgame.com/pdf/don-cardlist.pdf`, read with pypdf. Each embedded image is one DON!! card (`one-piece-print-don-jp-<NNN>`, from the image object number), its set from the product code printed below it. The image is written as JPEG to `<dirname(DATABASE_PATH)>/card-images/<variant id>.img` plus `.mime`; the variant has no `imageUrl`.
  - Fewer than 350 DON!! variants in total, or fewer than 200 JP images, raises.
- Knob: env `DATABASE_PATH` (default `/data/deckledger.db`) only to locate that image cache.

### providers/hololive.py

- Reads `https://en.hololive-official-cardgame.com/cardlist/` and `https://hololive-official-cardgame.com/cardlist/` (JP). Every product tile (`/cardlist/cardsearch/?expansion=<code>`) gives a set `hololive-<code>` with name, category (set type and classification) and release date; duplicate tiles per language and code collapse, EN and JP share the set id (EN wins).
- Per product: `<tile url>&view=text`, then `/cardlist/cardsearch_ex?expansion=<code>&view=text&page=N` from page 2 until a 404, an empty page or a page without new variants (at most page 79); 6 threads.
- Identities: `hololive-card-<collector number>`, language-independent, English preferred; card type, color, tags, HP, bloom level and skill texts parsed from the EN or JP labels. Printings: `hololive-print-<physical set>-<number>-<en|jp>`. Variants: one per rarity and image, `<printing>-<rarity>-<digest of image URL>`, `finish` = rarity (from the card's Rarity field, else the image file name suffix), `is_parallel` unless the rarity is C, U, R, RR, S or OSR.
- `sele...` expansions are tournament card pools on the official site, not products: they create no set, their cards go to the product named by the collector-number prefix (hBP01-024 -> hBP01) and are flagged `virtualPoolSource`, and the real product's printing wins when both exist. `catalog_sync.py` moves user data off old `sele` variants (see catalog.md).
- `printed_card_count` = distinct identities per set.

### providers/vcard.py

- Reads `https://www.vcardtcg.com/cards`. Set slugs: `KNOWN_SET_SLUGS` (rising-stars, awakened-worlds, divine-chaos, fractured-paradox) plus every `path:"/cards/<slug>"` in the `/app/cards/page-*.js` bundle the index page loads; if discovery fails only the known slugs are loaded. `api.vcardtcg.com` is deliberately not used.
- Per set, `https://www.vcardtcg.com/cards/<slug>`: the card list is parsed from the Next.js flight payload (`setConfig`, `initialCardsData`). Unrevealed and placeholder cards are skipped. A known slug that fails to load or has no cards raises; a discovered slug that fails or has no cards yet is skipped.
- Set `vcard-<slug>`, code = official set number, `set_type` "Booster Set", accent from the set theme; `printed_card_count` is left empty and filled by `catalog_sync.py` (EN printings per set).
- Identities and printings are per set: `vcard-card-<set number>-<card key>`, `vcard-print-<set number>-<card key>-en`, all EN. The official `cardType` becomes the rarity label (Mascot, Support, World, Uncommon, Rare, Ultra Rare, Secret Rare, Paradox, God Rare, Box Topper, Promo); the gameplay `card_type` is taken from `CARD_KINDS`, else from the same-named base card of the set, else `VT` for cards with a power level, else `ALTERNATE_FALLBACK`. VT cards with a power level are named `<name> (PL<n>)`. Attributes `color` = element and `cost` = power level, so the shared filters work.
- Collector numbers: digits zero-padded to 3; box toppers `BT-<n>` when the set's run-together numbers decode cleanly; missing numbers `GR-`/`P-`/`BT-`/`X-` plus the card key.
- Variants: every edition with an image (unlimited, first edition) times every finish (regular, holographic); the first in that order gets `variant_code='normal'`; finish labels such as `Normal`, `Holo`, `1st Edition`, `1st Edition Holo`; `imageFallbackUrls` lists the other editions' scans.
- Knob: `INCLUDE_NON_COLLECTIBLE = False` skips editions the official site flags `collectible: false` (God Rares); a card left without editions is dropped.

## Dependencies

- `catalog_provider_contract.py` - `fetch`, `fetch_bytes`, `clean`, `slug`, `digest`, `empty_catalog`, `put_identity`, `merge`.
- pypdf (`requirements.txt`, `pypdf[image]==6.6.2`) - One Piece's JP DON!! PDF and its images.
- External sources - lorcanajson.org, en.onepiece-cardgame.com, www.onepiece-cardgame.com, asia-en.onepiece-cardgame.com, api.github.com (TCGJSON release asset), en.hololive-official-cardgame.com, hololive-official-cardgame.com, www.vcardtcg.com.
- `deckledger/schema.py` - seeds each file as provider code: game `<id>` reads `providers/<id with - replaced by _>.py`.

## Decisions and assumptions

- Self-contained on purpose: only the standard library, the contract and pypdf, because provider code runs in its own process without the app; that is also why `one_piece.py` reads `DATABASE_PATH` itself instead of importing the app's path.
- The database copy of the code is what runs; edits here reach an installation on its next start unless an admin replaced the code (`customized=1`).
- Ids must stay stable: collections, decks and prices hang off them. Lorcana keeps `lorcana-<number>` set ids even though the displayed code became an abbreviation; One Piece's combined-product split only changes `set_id`, never printing or variant ids.
- Identities are language-independent (Lorcana via `baseId`, One Piece and hololive via the collector number) and take name and text from English; localized names and texts live on the printing.
- Lorcana's set abbreviations 10 to 13 follow the community first-letter convention; "AOTV" was chosen over "VINE" because usage is split.
- The German TFC misprints are real, separately sold printings (Cardmarket lists them as own products), so they are variants of their own rather than notes.
- EN DON!! came from the OPTCG API before; TCGJSON links each DON!! product to a real set id instead of guessing it from the name (commit 5d56a54). JP DON!! still comes from Bandai's PDF because no other source has JP DON!! data and user data references those variants.
- Completeness floors (DON!! counts, VCard's known sets) make a shrunken source fail the import instead of silently dropping cards.
- God Rares are 1-of-1 cards; importing them would make 100% set completion impossible, hence `INCLUDE_NON_COLLECTIBLE = False`.
