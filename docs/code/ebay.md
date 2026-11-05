# eBay

`ophir_cards/ebay.py`, `ophir_cards/ebay_photos.py` and the job script `ebay_sync.py` connect Ophir Cards to eBay's official APIs: a user's seller account (its listings, sales and statistics), listing drafts made from cards and published from Ophir Cards, the user's own photos for those listings, and anyone's eBay listings followed as a card's "ebay" price.

## Public interface

### ebay.py

#### Application keys

- `ebay_config(connection)`: when `EBAY_CLIENT_ID` is set, all keys come from the environment (`source: "environment"`, read-only in Admin -> eBay); otherwise from the `app_settings` row `ebay_config` (JSON) over the defaults (`source: "database"`). An unknown environment falls back to `production`, an unknown marketplace to `EBAY_DE`.
- Two levels of setup: client id + secret (`is_configured`) are enough for followed listings (application token); everything tied to a user account also needs the RuName (`require_config(connection, for_users=True)`), otherwise an `EbayError` with status 409.
- Environments: `production` (`auth.ebay.com`, `api.ebay.com`, `apiz.ebay.com`, `apim.ebay.com`) and `sandbox` (the same hosts with `.sandbox.`).
- Marketplaces `EBAY_DE`, `EBAY_AT`, `EBAY_FR`, `EBAY_IT`, `EBAY_ES`, `EBAY_GB`, `EBAY_US`; the configured one sets site id, currency, country, listing domain and language headers for every call.

#### Tokens and account connection

- Application token (client credentials, scope `https://api.ebay.com/oauth/api_scope`): cached in `app_settings` row `ebay_app_token` together with environment and client id, renewed 120 s before it runs out. Saving the keys in Admin drops it.
- User connection: OAuth authorization code. `/ebay/connect` keeps a random state in the Flask session and sends the user to eBay with `prompt=login`; `/ebay/callback` checks the state, exchanges the code, reads the username from the Identity API and stores access token, refresh token, both expiries, environment and the requested scopes in `ebay_accounts` (one row per user). Results come back as `/?ebay=connected|denied|state|failed|not_configured`.
- Requested scopes: `api_scope`, `sell.inventory`, `sell.account.readonly`, `commerce.identity.readonly` (`LEGACY_SCOPES`) plus `sell.analytics.readonly`. A row with empty `scopes` counts as the four legacy scopes.
- `user_token(connection, uid)`: the stored access token, or a new one from the refresh token for exactly the granted scopes. 409 when no account is connected or the refresh token has expired; a refused renewal is written to `ebay_accounts.last_error`.
- `trading_call(connection, uid, call, body)`: one Trading API XML call (`/ws/api.dll`, compatibility level 1349) with the user's token; returns `(root without namespaces, warnings)`; eBay errors become `EbayError`.
- `http(method, url, **kwargs)`: every outgoing request goes through it (default timeout 30 s); the tests replace it.
- `EbayError(message, status=400)`: a global Flask error handler turns it into `{"error": message}` with that status. Messages are German and shown as they are.

#### Followed listings (price source)

- `ebay_tracked_items` belong to a card (variant), not to a user (`added_by` only records who added it): at most 20 per card, unique per (variant, item). The item id is parsed from `/itm/...`, `?item=`, `itm=`, `itemId=` or a plain 9-15 digit number; a `var=` parameter makes it `<item>-<variation>`.
- Read through the Browse API (`get_item_by_legacy_id`) with the application token. 404 = ended; `OUT_OF_STOCK` or a past end date = ended; a listing with variations but no `var=` = error with a hint.
- `update_card_price(connection, variant_id)`: from the active EUR listings, the median item price (without shipping) is stored as metric `trend` and the cheapest as `low` in `price_observations` with provider `"ebay"` (`PRICE_PROVIDER`), change-only (a new row only when the amount differs; same timestamp updates in place), plus a `marketplace_products` row (match method `manual`, the cheapest listing as product). No active listing left: the last price stays. No followed listing left: the card's `ebay` price rows are deleted.

#### Listing template ("Angebotsvorlage") and drafts

- Stored per user in `user_settings` key `ebayPreset` as `{"shared": {...}, "games": {game_id: {...}}}`. Shared by all games: business policy ids, postal code, location, best offer, shipping mode (`direct` or `policies`), shipping service, costs and payer, dispatch days, returns. Everything else is per game: category (default `183454`), condition (`collection` or a fixed condition), title, description and item-specifics templates, game label, manufacturer, foil/normal surface labels, price factor (percent), minimum, rounding (`none`, `up99`, `up49`), fallback price, quantity (`one` or `owned`). Game defaults exist for lorcana, one-piece, hololive and vcard. A legacy flat preset is read as shared part and as every game's template until the game gets its own. `load_preset` always passes the stored value through `clean_preset`.
- Template placeholders: `name`, `set_name`, `set_code`, `number`, `rarity`, `finish` (empty for normal), `language`, `language_name`, `game`, `game_short`, `condition`, `quantity`, `character`, `manufacturer`, `edition`, `features` (`1st Edition`), `surface`, `year`, `graded`. Unknown placeholders vanish.
- Draft creation (`POST /api/ebay/drafts`): from a sheet (`sheet_id`, optionally only some `variant_ids`; the entry's quantity and price label are used) or from a list of `variant_ids` (quantity 1, or the owned count when the template says `owned`). At most 400 per request. Cards with an active listing that has copies left are skipped (`listed`), cards with a draft that is not published (including `failed`) too (`skipped`).
- Price: the sheet label if it reads as an amount, else market price x factor / 100, rounded, at least the minimum, else the fallback. Condition: the template's, or the most-held ungraded collection condition mapped to eBay's card condition descriptor (default `400010`, Near Mint or better).
- Title at most 80 characters ("1st Edition" becomes "1st Ed", brackets are removed); description at most 4000 (card values are HTML-escaped when the template is HTML; plain text gets `<br>` line breaks); item specifics from `Name: value` lines (65 characters each).
- Verify and publish: first the cheap checks (title, price, postal code, and the three policies or a shipping service depending on the mode), then the pictures (`listing_pictures`), then `VerifyAddFixedPriceItem` (stores the fees) or `AddFixedPriceItem`. Listings are fixed price, GTC, condition 4000 with descriptor 40001, SKU `DL-<variant_id>` (when at most 50 characters). Publishing sets the draft to `published` with `item_id` and inserts the `ebay_listings` row; a failure sets it to `failed` with the error.
- Revising an active listing that has a card (`ReviseFixedPriceItem`): chosen parts `title`, `description`, `aspects`, `shipping` (also best offer), `pictures`; price and quantity stay. Pictures are replaced only by the user's own photos, never by the catalogue image.

#### The account's listings, sales and statistics

- `sync_listings(connection, uid)`: `GetMyeBaySelling` for ActiveList, UnsoldList (60 days) and SoldList (60 days), 200 per page, at most 10 pages each, all fetched before anything is written. Upserts `ebay_listings` (status `active`, `unsold`, `sold`); a listing's card comes from the draft it was published from or its SKU `DL-<variant_id>`, and a card once linked is kept. One `ebay_sales` row per transaction (unique per user, item, transaction). Active listings eBay no longer returns become `ended`. Then, in this order: traffic report (failures are only logged), `deals_from_sales`, `update_sheets_from_listings`; commits.
- Traffic report (`sync_traffic`, only with `sell.analytics.readonly` granted): Analytics API, last 90 days, 200 listings per call; stores `view_count`, `impression_count`, `click_through_rate`, `conversion_rate`.
- `deals_from_sales(connection, uid)`: every sale whose listing has a card becomes a finished deal: partner = buyer, platform `eBay`, link = listing URL, the sold copies on the give side at total / quantity, `money_in` = total, created and completed at the sale time, booked with `deals.book_done` (copies leave the collection and offer sheets). `ebay_sales.deal_id` records the deal; `0` = the card is no longer in the catalogue, never retried. Sales of listings without a card wait until one is linked. Runs `deals.py` functions on the given connection through `on_connection`.
- `update_sheets_from_listings(connection, uid)`: sheets of kind WTS or WTS/WTT with `ebay_sync=1` take quantity (available copies, at most 99) and price label (cheapest active listing) of each card from its active listings and remember the listing in `trade_sheet_cards.ebay_item`. A card whose remembered listing sold out leaves the sheet; one whose listing ended otherwise loses the label. Bumps the sheet's `updated_at`. Does not commit. Also called by `sheets.py` when a sheet's `ebay_sync` is switched on.
- Listing numbers in `GET /api/ebay/listings`: `popularity` = views + 10 x watchers + 25 x sold + impressions / 100 (Ophir Cards' own measure), `rank` among active listings, `market_ratio` = (price + shipping) / market price x 100 for EUR listings.

#### Marketplace Account Deletion

- `GET /ebay/account-deletion?challenge_code=...` answers `{"challengeResponse": sha256(challenge + verification token + endpoint URL)}`. The token is `EBAY_VERIFICATION_TOKEN` or one generated once and kept in `app_settings` row `ebay_deletion_token`; the endpoint URL is `EBAY_DELETION_ENDPOINT` or the external URL Flask builds for the route. Both are shown to admins in `GET /api/admin/ebay`.
- `POST`: notices for usernames Ophir Cards does not know (no connected account, no buyer in `ebay_sales`) are acknowledged with 204 without checking. For a known one, `X-EBAY-SIGNATURE` is verified (ECDSA/SHA1 against eBay's public key from the Notification API, cached per process); a bad signature gets 412. Then the account's `ebay_accounts` row is deleted, the buyer name is blanked in `ebay_sales` and as partner of the deals booked from those sales; 204.

#### Background entry point

- `run_due(connection, tracked_limit=60)`: returns `{tracked, accounts, errors}`. Does nothing without client id and secret. Followed listings are due when never read, when not ended and read more than 6 h ago (`TRACKED_REFRESH_HOURS`), or when ended, read more than 24 h ago and added within the last 30 days; at most 60 per run, oldest first; prices updated, commit. Then, only with a RuName, every account with a refresh token whose listings were synced more than 30 min ago (`LISTING_SYNC_MINUTES`) or never; an `EbayError` is stored in `last_error` and `listings_synced_at` is set anyway, so the account is retried 30 min later.

#### HTTP endpoints (ebay.py)

| Method | Path | Guard | What it does |
| --- | --- | --- | --- |
| GET | `/api/ebay/status` | login | Setup and connection state (`configured`, `can_track`, `connected`, `expired`, `stats_need_reconnect`, marketplace, last sync and error, condition labels). |
| GET | `/ebay/connect` | login | Redirect to eBay's consent page. |
| GET | `/ebay/callback` | login | The RuName's accept and decline URL; stores the connection. |
| POST | `/api/ebay/disconnect` | login | Deletes the tokens; listings and sales stay as history. |
| GET / POST | `/api/admin/ebay` | admin | Read the keys (never the secret, only `client_secret_set`; plus callback URL, deletion endpoint and token, marketplaces) / save them (400 when configured by environment; a blank secret keeps the stored one). |
| GET / POST | `/ebay/account-deletion` | none | eBay's challenge and deletion notices (see above). |
| GET / PUT | `/api/ebay/preset` | login | The template of one game (`game_id` required), with defaults and the list of shared keys. |
| GET | `/api/ebay/policies` | login | The seller's business policies; `available: false` for accounts without them. |
| GET | `/api/ebay/shipping-services` | login | Domestic shipping services of the marketplace (`GeteBayDetails`), cached per process. |
| GET / POST | `/api/ebay/drafts` | login | List drafts (optional `game_id`) with photos and item URL / create drafts. |
| PATCH / DELETE | `/api/ebay/drafts/<id>` | login | Edit title, description, price, quantity, condition (not once published; an edit resets the status to `draft`) / delete the local draft. |
| POST | `/api/ebay/drafts/<id>/verify` | login | Check at eBay without listing; returns fees and warnings. |
| POST | `/api/ebay/drafts/<id>/publish` | login | List it on eBay. |
| GET | `/api/ebay/listings` | login | Listings (optional `game_id`; listings without a card always included), last 50 sales, summary numbers, status. |
| POST | `/api/ebay/listings/sync` | login | `sync_listings` now. |
| GET | `/api/ebay/listings/revisions` | login | Active listings with the title the template would give them. |
| POST | `/api/ebay/listings/<item_id>/revise` | login | Apply the template's chosen parts to a listing. |
| GET | `/api/ebay/listings/<item_id>/suggestions` | login | Up to 6 owned cards whose name is in the listing title. |
| PATCH | `/api/ebay/listings/<item_id>` | login | Link the listing to a card (`variant_id`, or null to unlink); then books waiting sales and updates sheets. |
| GET / POST | `/api/variants/<variant_id>/ebay-items` | login | Followed listings of a card / follow one by `{url}` (read at once). |
| DELETE | `/api/variants/<variant_id>/ebay-items/<item_id>` | login | Stop following; recompute the price. |
| POST | `/api/variants/<variant_id>/ebay-items/refresh` | login | Re-read all followed listings of the card. |

### ebay_photos.py

- Photos belong to a user's card (`ebay_photos`: user, variant, position, crop, `eps_url`, `eps_uploaded_at`), not to a draft, so they serve every listing of that card and survive deleted drafts. At most 24 per card (eBay's limit), upload at most 25 MB.
- Files in `<data dir>/ebay-photos/` (`config.EBAY_PHOTO_DIR`): `<id>-original.jpg` (EXIF orientation applied, RGB, longer side at most 4000 px) and `<id>.jpg`, the listed picture cut from the original by the crop (fractions `x`, `y`, `w`, `h` of the rotated original, `rotate` in 90 degree steps), longer side at most 1600 px. A cut whose longer side is below 500 px is refused (eBay's minimum).
- `listing_pictures(connection, uid, variant_id, upload_card_picture)`: the picture URLs for a listing: the user's photos in their order, else the catalogue image (`ebay.upload_card_picture`, converted to JPEG unless it is JPEG or PNG). Each photo is uploaded once via `ebay.upload_picture` (Media API `createImageFromFile` on the `apim` host, multipart field `image`, user token) and its URL reused for 25 days; changing the crop clears it. Commits after each upload.
- `card_photos(connection, uid, variant_ids)`: `{variant_id: [photo payload]}` for drafts and listings.
- `remove_user_photos(uid)`: rows and files of an account that is being deleted (called by `admin.py`; works on `db()`, does not commit).

| Method | Path | Guard | What it does |
| --- | --- | --- | --- |
| GET | `/api/ebay/photos?variant_id=` | login | The card's photos. |
| POST | `/api/ebay/photos` | login | Upload one (multipart `file`, `variant_id`, optional `crop`). |
| POST | `/api/ebay/listings/<item_id>/photos/import` | login | Fetch the listing's pictures (`GetItem`; only `https://*.ebayimg.com`, in the 1600 px size) as photos of its card, after the existing ones; pictures imported before are skipped; each keeps its eBay URL, so putting it back unchanged needs no upload. |
| PATCH | `/api/ebay/photos/<id>` | login | New crop; re-cuts the listed picture. |
| POST | `/api/ebay/photos/order` | login | `{variant_id, ids}`: new order; ids not named keep their order after the named ones. |
| DELETE | `/api/ebay/photos/<id>` | login | Delete row and files. |
| GET | `/api/ebay/photos/<id>/image` or `/original` | login | The JPEG; `image` with `?v=` is cached as immutable, otherwise no-cache. |

### ebay_sync.py

- Run by the container every 600 s (`docker-entrypoint.sh`). Takes a non-blocking `flock` on `<data dir>/ebay-sync.lock` (an overlapping run exits 0), opens its own connection (`sqlite3.Row`, busy timeout 10 s, foreign keys on) and calls `ebay.run_due`. Prints the summary only when something was read, synced or failed.

## Dependencies

- eBay OAuth (`auth.ebay.com/oauth2/authorize`) and Identity token endpoint (`api.ebay.com/identity/v1/oauth2/token`) - user and application tokens.
- eBay Trading API (`api.ebay.com/ws/api.dll`) - `GetMyeBaySelling`, `GeteBayDetails`, `VerifyAddFixedPriceItem`, `AddFixedPriceItem`, `ReviseFixedPriceItem`, `GetItem`.
- eBay REST APIs on `api.ebay.com` - Browse (followed listings), Sell Account (business policies), Sell Analytics (traffic report), Commerce Notification (public keys for deletion notices).
- eBay Commerce Identity API on `apiz.ebay.com` - username at connection time.
- eBay Media API on `apim.ebay.com` - picture uploads.
- eBay picture servers `*.ebayimg.com` - importing a listing's pictures.
- `requests` - HTTP; `cryptography` - signature check; Pillow - image conversion and cropping (both imported lazily).
- `ophir_cards.deals` - booking sales as deals; `ophir_cards.prices` (`latest_price_sql`), `ophir_cards.images` (`card_image`), `ophir_cards.search` (`title_score`), `sheet_render` (`is_holo`), `ophir_cards.web`, `ophir_cards.config`.
- SQLite tables `ebay_accounts`, `ebay_listings`, `ebay_sales`, `ebay_drafts`, `ebay_tracked_items`, `ebay_photos` (migrations `m0009`, `m0011`-`m0014`), `price_observations`, `marketplace_products`, `deals`/`deal_cards`/`deal_events`, `trade_sheets`, `trade_sheet_cards`, `user_settings`, `app_settings`.

## Decisions and assumptions

- Official APIs only; drafts stay in Ophir Cards until published because eBay's API cannot create Seller Hub drafts (module docstring).
- Pictures go up through the Media API on the `apim` host: the Trading API's `UploadSiteHostedPictures` was switched off in October 2026, and `api.ebay.com` answers 404 for the Media API path (commits b87894d, cf6cb55).
- The card image is uploaded to eBay because listings need a public HTTPS image and this server usually is not one (`upload_card_picture` docstring).
- No eBay call is made while holding the database's write lock: syncs, followed-listing reads and the traffic report fetch everything first and write afterwards (comments in `sync_listings`, `refresh_tracked`, `sync_traffic`; commit 3904924).
- Followed listings are catalogue data like a manual price, shared by all users (m0009 docstring); their price feeds the normal price machinery change-only, so totals and history need no eBay knowledge (module docstring).
- `variant_id` is no foreign key in any eBay table: listings and sales outlive catalogue entries; `catalog_sync.py` keeps variants referenced by followed listings and unpublished drafts (m0009 docstring, `catalog_sync.py`).
- A token is renewed only for the scopes granted at connection time, so the analytics scope added later needs a reconnect; older connections keep working (m0011 docstring, commit ef093d6).
- eBay unlocks production keys only for applications that answer the deletion challenge and listen for deleted accounts; since eBay posts every deleted account, only notices naming a known account are verified and acted on (comment above `deletion_token`, commit 59c58a4).
- Every sale of a listing with a card is booked once as a done deal; a deal the user deletes does not come back (commit fad157e, m0014).
- Accounts without business policies (private sellers, typically) ship "direct": shipping and returns are written into each listing (`PRESET_DEFAULTS` comment, `ebay_policies`).
- Item specifics and other per-game choices live in a per-game template, while shipping and payment are the seller's and shared (`PRESET_DEFAULTS` comment, commit f3a2c76).
- An uploaded photo URL is reused for 25 days because eBay drops pictures no listing uses after 30 (ebay_photos docstring).
- Several values are cached per process and never invalidated: eBay's public keys and the shipping services per marketplace.
