// DeckLedger offline shell. Bump CACHE_VERSION whenever the caching strategy
// itself changes -- old caches are purged on activate. Static assets already
// have their own ?v=NN cache-busting param from index.html, so this doesn't
// need to track that separately.
const CACHE_VERSION = 'v1';
const SHELL_CACHE = `deckledger-shell-${CACHE_VERSION}`;
const API_CACHE = `deckledger-api-${CACHE_VERSION}`;
const IMAGE_CACHE = `deckledger-images-${CACHE_VERSION}`;
const KNOWN_CACHES = [SHELL_CACHE, API_CACHE, IMAGE_CACHE];

// Deliberately NOT precaching "/" here -- if install ever runs while logged
// out, fetching "/" resolves to the login page, not the app shell, and we'd
// cache the wrong thing under the app's URL. It gets cached lazily on first
// authenticated visit via the networkFirst runtime handler below instead.
const PRECACHE_ASSETS = ['/static/manifest.json'];

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

// Images/static assets change rarely and are addressed by content-stable
// URLs (or their own ?v= param) -- safe to serve from cache first and only
// hit the network on a miss.
async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

// API responses and the app shell page itself should always prefer a fresh
// network answer when online -- the cache is purely the offline fallback.
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener('fetch', event => {
  const { request } = event;
  // Never intercept mutations -- POST/PATCH/DELETE responses aren't
  // meaningfully cacheable, and offline write queuing is handled at the
  // application level (see app.js), not here.
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isImageRequest(url)) {
    event.respondWith(cacheFirst(request, IMAGE_CACHE));
  } else if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request, SHELL_CACHE));
  } else if (isApiRequest(url)) {
    event.respondWith(networkFirst(request, API_CACHE));
  } else if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, SHELL_CACHE));
  }
});
