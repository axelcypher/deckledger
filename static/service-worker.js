// DeckLedger offline shell. Bump CACHE_VERSION whenever the caching strategy
// itself changes -- old caches are purged on activate. Static assets carry a
// content hash in their ?v= param (asset_url() in app.py), so a changed file
// always arrives under a new URL without anyone having to bump a number.
const CACHE_VERSION = 'v2';
const SHELL_CACHE = `deckledger-shell-${CACHE_VERSION}`;
const API_CACHE = `deckledger-api-${CACHE_VERSION}`;
const IMAGE_CACHE = `deckledger-images-${CACHE_VERSION}`;
// What the user saved for offline use on purpose (static/js/offline.js). Written by the page,
// only read here, never trimmed, and kept across changes of CACHE_VERSION.
const SAVED_CACHE = 'deckledger-offline-v1';
const KNOWN_CACHES = [SHELL_CACHE, API_CACHE, IMAGE_CACHE, SAVED_CACHE];

// Deliberately NOT precaching "/" here -- if install ever runs while logged
// out, fetching "/" resolves to the login page, not the app shell, and we'd
// cache the wrong thing under the app's URL. It gets cached lazily on first
// authenticated visit via the networkFirst runtime handler below instead.
const PRECACHE_ASSETS = ['/static/manifest.json'];

// How long the network may take before a saved copy is shown instead. The
// page shell is a few KB, so a server that has not delivered it after 3 s is
// not going to; API answers can legitimately take longer on a big catalogue.
const NAVIGATION_TIMEOUT_MS = 3000;
const API_TIMEOUT_MS = 15000;
// Once one request has shown the server to be unreachable, the requests that
// follow (a page load fires several) stop waiting out the full limit each.
const UNREACHABLE_TIMEOUT_MS = 1500;
const UNREACHABLE_WINDOW_MS = 30000;
// What a reverse proxy answers while the app container is down or restarting.
const GATEWAY_ERRORS = new Set([502, 503, 504]);
let unreachableUntil = 0;
// Every distinct search, filter and sort combination is its own API URL, and every card thumbnail
// its own image; left alone both caches only ever grow. Oldest entries go first.
const API_CACHE_LIMIT = 150;
const IMAGE_CACHE_LIMIT = 6000;
const IMAGE_TRIM_INTERVAL = 250;
let imagesStoredSinceTrim = 0;

async function trimCache(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - limit)).map(key => cache.delete(key)));
}

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then(cache => cache.addAll(PRECACHE_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => !KNOWN_CACHES.includes(key)).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function isImageRequest(url) {
  return /\/(art|set-logo|card-back|lorcana-filter-icon|op-filter-icon|icons)\//.test(url.pathname);
}
function isStaticAsset(url) {
  return url.pathname.startsWith('/static/');
}
function isApiRequest(url) {
  return url.pathname.startsWith('/api/');
}

async function saved(request) {
  const cache = await caches.open(SAVED_CACHE);
  return cache.match(request, { ignoreVary: true });
}

// The full-size image of a card that was saved without the large images: its thumbnail is
// better than a broken picture in the detail view.
async function savedThumbnail(request) {
  const url = new URL(request.url);
  if (!url.pathname.startsWith('/art/') || url.searchParams.has('size')) return undefined;
  url.searchParams.set('size', 'thumb');
  return (await caches.open(IMAGE_CACHE)).match(url.href) || saved(url.href);
}

// Card images are addressed by stable URLs and never change once fetched --
// safe to serve from cache first and only hit the network on a miss.
async function cacheFirst(event, cacheName) {
  const { request } = event;
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request) || await saved(request);
  if (cached) return cached;
  // While the server is known not to answer, an image that is not saved yet stays missing
  // rather than tying up a connection the next page or API attempt needs.
  if (Date.now() < unreachableUntil) return await savedThumbnail(request) || Response.error();
  let response;
  try {
    response = await fetch(request);
  } catch (error) {
    return await savedThumbnail(request) || Response.error();
  }
  // The server answers a card image it could not fetch with a generated stand-in (marked
  // X-Image-Source: placeholder). Keeping that would pin the stand-in for good.
  if (response.ok && response.headers.get('X-Image-Source') !== 'placeholder') {
    const stored = cache.put(request, response.clone());
    imagesStoredSinceTrim += 1;
    if (imagesStoredSinceTrim >= IMAGE_TRIM_INTERVAL) {
      imagesStoredSinceTrim = 0;
      event.waitUntil(stored.then(() => trimCache(cacheName, IMAGE_CACHE_LIMIT)).catch(() => {}));
    }
  }
  return response;
}

// Static files. A URL with a content hash can only ever mean one file, so the
// cached copy is final. Everything else (ES modules that import each other by
// plain path, icons) is served from cache for speed and refreshed in the
// background, so a changed file is picked up on the following load instead of
// staying frozen at whatever version was cached first.
async function staticAsset(event, cacheName) {
  const { request } = event;
  const url = new URL(request.url);
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached && (url.searchParams.has('v') || Date.now() < unreachableUntil)) return cached;
  const refresh = fetch(request).then(async response => {
    if (response.ok) {
      await cache.put(request, response.clone());
      // Earlier versions of the same file are dead weight once this one is stored.
      const stored = await cache.keys();
      await Promise.all(stored
        .filter(entry => { const other = new URL(entry.url); return other.pathname === url.pathname && other.search !== url.search; })
        .map(entry => cache.delete(entry)));
    }
    return response;
  });
  if (!cached) return refresh;
  event.waitUntil(refresh.catch(() => {}));
  return cached;
}

// Marks an answer that came out of the cache because the server did not
// respond, so the app can say so instead of passing old data off as current.
function staleCopy(cached) {
  const headers = new Headers(cached.headers);
  headers.set('X-DeckLedger-Stale', '1');
  return new Response(cached.body, { status: cached.status, statusText: cached.statusText, headers });
}

// The page shell and API answers always prefer the network; the cache is the
// fallback for "the server cannot be reached", which includes a connection
// that just hangs (away from the home network, server restarting) -- waiting
// for the browser's own timeout there takes a minute or more per request.
async function networkFirst(event, cacheName, timeoutMs) {
  const { request } = event;
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request) || await saved(request);
  const controller = new AbortController();
  const network = fetch(request, { signal: controller.signal }).then(response => {
    if (response.ok) {
      const stored = cache.put(request, response.clone());
      if (cacheName === API_CACHE) event.waitUntil(stored.then(() => trimCache(cacheName, API_CACHE_LIMIT)).catch(() => {}));
    }
    return response;
  });
  // Without a saved copy there is nothing to fall back to: wait for whatever the network gives.
  if (!cached) return network;
  const limit = Date.now() < unreachableUntil ? UNREACHABLE_TIMEOUT_MS : timeoutMs;
  let timer;
  try {
    const response = await Promise.race([
      network,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('timeout')), limit); }),
    ]);
    if (GATEWAY_ERRORS.has(response.status)) throw new Error('gateway');
    unreachableUntil = 0;
    return response;
  } catch (err) {
    // Given up on: a request left hanging keeps one of the browser's few connections to the
    // server occupied, and the next attempts would queue behind it.
    network.catch(() => {});
    controller.abort();
    unreachableUntil = Date.now() + UNREACHABLE_WINDOW_MS;
    return staleCopy(cached);
  } finally {
    clearTimeout(timer);
  }
}

self.addEventListener('fetch', event => {
  const { request } = event;
  // Never intercept mutations -- POST/PATCH/DELETE responses aren't
  // meaningfully cacheable, and offline write queuing is handled at the
  // application level (see static/js/offline.js), not here.
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isImageRequest(url)) {
    event.respondWith(cacheFirst(event, IMAGE_CACHE));
  } else if (isStaticAsset(url)) {
    event.respondWith(staticAsset(event, SHELL_CACHE));
  } else if (isApiRequest(url)) {
    event.respondWith(networkFirst(event, API_CACHE, API_TIMEOUT_MS));
  } else if (request.mode === 'navigate') {
    event.respondWith(networkFirst(event, SHELL_CACHE, NAVIGATION_TIMEOUT_MS));
  }
});
