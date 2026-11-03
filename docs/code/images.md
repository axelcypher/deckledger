# Images

Everything a card, set or game looks like: card artwork fetched from the publishers and cached on disk (bleed trimming, list thumbnails, luminance foil masks), the official Ravensburger foil layers for Lorcana, logos, set visuals, card backs, filter icons, and the static textures and presets of the WebGL Lorcana foil renderer in the browser.

## Public interface

### deckledger/images.py

Python functions other modules call:

- `card_image(row, variant_id) -> (path, mime, cache_key) | None` - the image every view should show: `cached_real_image()` plus bleed trimming for games with `Game.image_bleed` (the `cache_key` then gets a `-trim` suffix, so derived files of the untrimmed image are not reused). Used by `deckledger/sheets.py` and `deckledger/ebay.py`.
  - `row` must carry `game_id`, optionally `variant_attributes` (the `variants.attributes` JSON), and whatever the game's `remote_image_url` hook reads (`canonical_name`, `collector_number`, `language`, `set_code`, `variant_code`).
  - May download in the request thread (15 s timeout per URL, plus the game hook's own lookups) and may raise `OSError`; callers must catch it.
- `cached_real_image(row, variant_id)` - the untrimmed source image, same return shape.
- `cached_trimmed_image(path, cache_key, bleed)`, `cached_thumbnail(path, cache_key)`, `cached_foil_mask(path, cache_key)` - derived files, each made once; return None on failure (logged as a warning).
- `remote_image_url(row)` - `imageUrl` from the variant attributes, else the game's `remote_image_url` hook, else None.

HTTP routes, all `@login_required`:

| Method | Path | What it does |
|---|---|---|
| GET | `/art/<variant_id>.svg` | The card image (any image type despite the `.svg` name). `?size=thumb` gives the 360x504 WebP thumbnail. No image available: a generated SVG stand-in with `Cache-Control: private, max-age=3600`. 404 for an unknown variant. |
| GET | `/foil-mask/<variant_id>.webp` | Luminance mask from the card's own art; a flat, fully opaque fallback mask when there is no art. |
| GET | `/api/foil-layer-meta/<variant_id>` | `{"available": false}`, or `available`, `foil_type`, `foil_top_layer`, `hot_foil_color`, `second_hot_foil_color`, `mask_url`, `top_layer_mask_url`, `second_top_layer_mask_url`. Only games with `Game.official_foil_layers` (Lorcana) can be available, and only when Ravensburger has a base foil mask for the finish. Never a 4xx/5xx for a missing match. |
| GET | `/foil-layer-mask/<variant_id>/<kind>.jpg` | One of Ravensburger's masks, proxied and disk-cached; `kind` is `base`, `top` or `top2`; 404 when absent. |

Real images go out with `Cache-Control: public, max-age=31536000, immutable` and an `X-Image-Source` header (`local-provider-cache`, `local-thumbnail-cache`, `local-foil-mask-cache`, `foil-mask-fallback`, `ravensburger-foil-asset-cache`; `placeholder` for the SVG stand-in).

`deckledger/public_profile.py` serves `card_art` without sign-in on a public profile by calling `card_art.__wrapped__`; `login_required` must stay the view's only wrapper.

### deckledger/assets.py

- `set_visual_version(set_row) -> str` - cache-bust token for set logo URLs: `public-<mtime>-<size>` (hex) for a public override, else `provider-v3`. Used by `deckledger/catalog.py`.
- `public_set_visual(set_row) -> Path | None`, `remote_set_visual(set_row)`, `set_wordmark(set_row)`, `card_back_placeholder(game_row)` - helpers behind the routes.

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/game-logo/<game_id>` | login | Official wordmark from `Game.logo_url`, cached; 404 when the game has none, 502 on a failed fetch or non-image answer. |
| GET | `/set-logo/<set_id>` | login | Public override, else cached official set visual, else a generated SVG wordmark. 404 for an unknown set. |
| GET | `/card-back/<game_id>` | none | Admin upload (`card-backs/<game_id>.jpg` in the data dir), else `public/<game_id>-back.jpg`, else `static/assets/<game_id>/<game_id>-back.jpg`, else a generated SVG; 404 for an unknown game or an id outside `[a-z0-9-]+`. |
| GET | `/op-filter-icon/<name>` | none | One Piece filter icons from `public/icons/one-piece/` (name checked against a regex). |
| GET | `/lorcana-filter-icon/<name>` | none | Lorcana filter icons from `public/icons/lorcana/` (fixed list `LORCANA_FILTER_ICON_FILES`). |
| GET | `/glass-plate.svg`, `/foil-base-reflection.png` | none | Hand-tuned stage assets from `public/`. |
| GET | `/assets/lorcana/foil/<name>.png` | none | The shared foil effect textures in `public/assets/lorcana/foil/`. |
| GET | `/assets/lorcana/foil-presets.json` | none | `lorcana_foil_original_presets.json` from the repo root (gzipped when accepted). |

`set_logo` and `game_logo` are also called through `__wrapped__` by `deckledger/public_profile.py`. Card back uploads are written by `deckledger/admin.py` (`POST /api/admin/games/<game_id>/card-back`).

### ravensburger_foil.py (repo root)

No Flask or database dependency by design.

- `find_official_variant(image_url, language, finish) -> dict | None` - Ravensburger's sub-variant for one of our variants: our `imageUrl` is looked up among the catalog's `detail_image_url`/`thumbnail_url` values, then `finish` is compared with `foil_type` (`"Normal"` matches the `Regular` sub-variant without a `foil_type`). Only languages `en` and `de` exist; anything else gives None.
- `load_catalog(language) -> dict | None` - the parsed catalog, cached 24 h in memory and on disk; on a failed fetch a stale disk copy is served; never raises. The first call per language and TTL window downloads about 4 MB (60 s timeout) in the caller's thread.
- `cached_asset(url) -> Path | None` - download-once disk cache for a mask URL (stored as `.jpg`); None on failure or a payload under 200 bytes.
- `MASK_KIND_FIELDS` - `base`/`top`/`top2` -> catalog field; also the whitelist for the mask route's `kind`.

### lorcana_foil_original_presets.json (repo root)

Values extracted from the Disney Lorcana TCG Companion app's Unity build (`source` names the app version). Keys: `foil_types`, `top_layer_types`, `supported_combinations` (`"<foil>_<toplayer>"`, `None` for a missing side, -> material name), `shader_graphs`, `original_effect_textures`, `materials` (shader, keywords, textures, ints, floats, colors per material), `runtime_symbols`, `notes`. Only the browser reads it, via `/assets/lorcana/foil-presets.json`.

### static/lorcana-foil/ (browser)

- `FoilInteractionController.js` exports the singleton `foilInteractionController`: `attach(cardElement, foilInput) -> Promise<bool>`, `markPending(cardElement)`, `updatePointer(clientX, clientY)`, `fadeOutAndDetach()`. `attach` resolves false, leaving the card on its CSS foil effect, when WebGL is unsupported, `prefers-reduced-motion` is set, the combination has no material or shader family, or the card image cannot be loaded.
- `LorcanaFoilRenderer.js` exports `sharedRenderer`: one canvas and one WebGL context for the whole app, one compiled program per shader family, textures cached by URL (`prepare(input)`, `render(tilt, rotation)`, `resize()`, `detachDom()`).
- `presetResolver.js` loads the presets JSON once and maps `(foilType, topLayer)` to a material through `supported_combinations`; anything not listed gives null.
- `shaders/` holds the GLSL sources; `shaders/families/*.js` declare per shader family which preset keys and texture roles they bind.
- Wiring: `static/js/main.js` imports the controller lazily and uses it only for Lorcana cards in the card detail modal, with input built from the `/api/foil-layer-meta` answer that `hydrateOfficialFoilLayers()` in `static/js/finish.js` already fetched.

## Dependencies

- `deckledger/config.py` - data-dir cache paths (`IMAGE_*`, `CARD_BACK_UPLOAD_DIR`), `PUBLIC_*`, `ROOT`, `jload`.
- `deckledger/games` - per-game hooks: `remote_image_url`, `image_bleed`, `image_placeholders`, `official_foil_layers`, `logo_url`, `remote_set_visual`, `bundled_set_logo`.
- `deckledger/web.py` - `app`, `db`, `login_required`, `compress_file`.
- `sheet_render.py` - `trim_bleed()`.
- `ravensburger_foil.py` - official Lorcana foil data.
- Pillow - trimming, thumbnails, masks, fallback mask.
- `fcntl` - inter-process file locks (POSIX only).
- Publisher sites and CDNs - card images, logos, set pages; `api.lorcana.ravensburger.com` for the foil catalog.

## Decisions and assumptions

- Source images are cached in `card-image-sources/` under the SHA-256 of their URL, so variants with the same URL share one file. `card-images/<variant>.img` + `.mime` is the older per-variant cache (the One Piece provider still writes DON!! images there); it wins for a variant without `imageUrl` and is hard-linked into the URL cache rather than copied.
- A failed download leaves a `.miss` marker in `card-image-locks/`; the URL is not tried again for `MISS_RETRY_SECONDS` (600 s), because retries on every view each held one of the server's few threads for up to the 15 s timeout (code comment, commit 3904924).
- `imageFallbackUrls` in the variant attributes are tried after `imageUrl`, cached copies before any network call. A download matching one of the game's `image_placeholders` (size + SHA-256) counts as missing, and a cached copy of such a stand-in is deleted: VCard answers unpublished scans with a "?" image and 200 OK (commit 785a9eb).
- A download must be `image/*` (magic-byte sniffing when the CDN sends a wrong Content-Type) and at least 1000 bytes. Requests send a `Referer` of the image's own host because some CDNs (Cardmarket) reject hotlink-style requests.
- Bleed trimming (VCard print files) runs once per source image; an image already in cut-card proportions gets a `.keep` marker and is served unchanged. The card view, thumbnails, foil masks and sheets all use the trimmed image (commit c3bb6a9).
- Every derived file is generated under an exclusive `fcntl` lock, so concurrent workers make it once.
- Served images are `immutable` for a year; the frontend versions the URLs (`?v=` in `static/js/finish.js`), and that version has to move when existing images change (commit 785a9eb).
- The foil mask file name carries `-v2`; a change of the mask curve needs a new suffix so cached masks are not reused. The mask cuts only near-black regions (<= 18/255) and gives full alpha from 60/255.
- The foil mask fails open with a flat opaque mask because a CSS `mask-image` that fails to load hides the whole element.
- Ravensburger matching goes by image URL, not by id: our schema stores no `culture_invariant_id`, and our `finish` values equal Ravensburger's `foil_type` values (module docstring). The official layers and the WebGL renderer are a progressive enhancement: no match, no WebGL, reduced motion or an unknown combination keep the CSS foil effect, nothing errors.
- `public/` (env `PUBLIC_DIR`, mounted read-only in `compose.yaml`) overrides shipped assets without an image rebuild. Its files are sent with ETag and revalidation, not long-lived caching, so a file replaced in place wins at once. The public set folder listing is cached per process and re-read when the folder's mtime changes.
- Set visual lookup order: the game's `bundled_set_logo` (Lorcana: `public/icons/lorcana/set<NN>-logo.png`), then `public/sets/<stem><ext>` with stems from the set id and the set code (each as is and lower-cased) and extensions in `PUBLIC_IMAGE_EXTENSIONS`, then the official visual, then the wordmark.
- Logo and set visual fetches have no negative cache: a failed lookup runs again on the next request (the SVG fallback is browser-cached for a day).
