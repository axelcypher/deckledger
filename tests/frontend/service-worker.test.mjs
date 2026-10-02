// The service worker's decisions, without a browser: what comes from the network, what from a
// cache, and what the app is told when the server does not answer.
import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';
import { loadScripts } from './scripts.mjs';

const ORIGIN = 'http://deckledger.test';
const urlOf = request => new URL(typeof request === 'string' ? request : request.url, ORIGIN).href;

class FakeCache {
  entries = new Map();
  async match(request) { return this.entries.get(urlOf(request))?.clone(); }
  async put(request, response) { this.entries.set(urlOf(request), response); }
  async keys() { return [...this.entries.keys()].map(url => ({ url })); }
  async delete(request) { return this.entries.delete(urlOf(request)); }
  async addAll() {}
}

let worker;

// A worker with empty caches, a network the test scripts and timers the test fires by hand.
function startWorker() {
  const stores = new Map();
  const handlers = {};
  const timers = [];
  const network = { calls: [], answer: () => { throw new TypeError('offline'); } };
  const caches = {
    async open(name) { if (!stores.has(name)) stores.set(name, new FakeCache()); return stores.get(name); },
    async keys() { return [...stores.keys()]; },
    async delete(name) { return stores.delete(name); },
  };
  const sandbox = {
    caches, Response, Headers, Request, AbortController, Error, TypeError,
    location: { origin: ORIGIN },
    fetch: async (request, options = {}) => {
      network.calls.push(urlOf(request));
      const answer = await network.answer(urlOf(request), options);
      if (options.signal?.aborted) throw new DOMException('aborted', 'AbortError');
      return answer;
    },
    setTimeout: (callback, ms) => { timers.push({ callback, ms, cleared: false }); return timers.length; },
    clearTimeout: id => { if (timers[id - 1]) timers[id - 1].cleared = true; },
    addEventListener: (type, handler) => { handlers[type] = handler; },
    skipWaiting: async () => {},
    clients: { claim: async () => {} },
  };
  sandbox.self = sandbox;
  const { run } = loadScripts(['service-worker.js'], sandbox);
  return {
    run, stores, network, timers,
    cache: name => caches.open(name),
    /** What the worker answers a GET with; undefined when it leaves the request to the browser. */
    async get(path, { mode = 'cors', method = 'GET', origin = ORIGIN } = {}) {
      const background = [];
      const event = { request: { url: origin + path, method, mode, headers: new Headers() }, respondWith(promise) { this.answer = promise; }, waitUntil(promise) { background.push(promise); } };
      handlers.fetch(event);
      if (!event.answer) return undefined;
      const response = await event.answer;
      await Promise.all(background);
      return response;
    },
    async lifecycle(type) { const waits = []; handlers[type]({ waitUntil: promise => waits.push(promise) }); await Promise.all(waits); },
    fireTimers() { timers.filter(timer => !timer.cleared).forEach(timer => { timer.cleared = true; timer.callback(); }); },
  };
}

const json = (body, init) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' }, ...init });
const image = (source = 'local-thumbnail-cache') => new Response('image-bytes', { headers: { 'Content-Type': 'image/webp', 'X-Image-Source': source } });
const tick = () => new Promise(resolve => setImmediate(resolve));

beforeEach(() => { worker = startWorker(); });

describe('API answers', () => {
  test('come from the network and are kept as a fallback', async () => {
    worker.network.answer = () => json({ fresh: true });
    const response = await worker.get('/api/bootstrap');
    assert.deepEqual(await response.json(), { fresh: true });
    assert.equal(response.headers.get('X-DeckLedger-Stale'), null);
    assert.ok(await (await worker.cache('deckledger-api-v2')).match('/api/bootstrap'));
  });

  test('fall back to the kept copy, marked as stale, when the server cannot be reached', async () => {
    worker.network.answer = () => json({ version: 1 });
    await worker.get('/api/bootstrap');
    worker.network.answer = () => { throw new TypeError('offline'); };
    const response = await worker.get('/api/bootstrap');
    assert.deepEqual(await response.json(), { version: 1 });
    assert.equal(response.headers.get('X-DeckLedger-Stale'), '1');
  });

  test('fail like the network does when there is nothing kept', async () => {
    await assert.rejects(worker.get('/api/collection?game_id=vcard'), TypeError);
  });

  test('a server that accepts the request but never answers is given up on', async () => {
    worker.network.answer = () => json({ version: 1 });
    await worker.get('/api/bootstrap');
    worker.network.answer = () => new Promise(() => {});
    const pending = worker.get('/api/bootstrap');
    await tick();
    assert.deepEqual(worker.timers.filter(timer => !timer.cleared).map(timer => timer.ms), [15000]);
    worker.fireTimers();
    assert.equal((await pending).headers.get('X-DeckLedger-Stale'), '1');
    // Having seen that, the next requests do not each wait out the full limit again.
    const next = worker.get('/api/bootstrap');
    await tick();
    assert.equal(worker.timers.filter(timer => !timer.cleared).at(-1).ms, 1500);
    worker.fireTimers();
    await next;
  });

  test('what a proxy answers while the app is down counts as unreachable', async () => {
    worker.network.answer = () => json({ version: 1 });
    await worker.get('/api/bootstrap');
    worker.network.answer = () => new Response('Bad Gateway', { status: 502 });
    assert.equal((await worker.get('/api/bootstrap')).headers.get('X-DeckLedger-Stale'), '1');
    // An ordinary error from the app itself is passed on as it is.
    worker.network.answer = () => json({ error: 'nope' }, { status: 400 });
    const refused = await worker.get('/api/bootstrap');
    assert.equal(refused.status, 400);
    assert.equal(refused.headers.get('X-DeckLedger-Stale'), null);
  });

  test('an error answer does not replace the kept copy', async () => {
    worker.network.answer = () => json({ version: 1 });
    await worker.get('/api/bootstrap');
    worker.network.answer = () => json({ error: 'broken' }, { status: 500 });
    await worker.get('/api/bootstrap');
    assert.deepEqual(await (await (await worker.cache('deckledger-api-v2')).match('/api/bootstrap')).json(), { version: 1 });
  });

  test('what was saved for offline use is used when nothing else is kept', async () => {
    await (await worker.cache('deckledger-offline-v1')).put('/api/collection?game_id=vcard', json({ cards: ['saved'] }));
    const response = await worker.get('/api/collection?game_id=vcard');
    assert.deepEqual(await response.json(), { cards: ['saved'] });
    assert.equal(response.headers.get('X-DeckLedger-Stale'), '1');
  });

  test('only the newest answers are kept', async () => {
    worker.network.answer = url => json({ url });
    for (let index = 0; index < 160; index++) await worker.get(`/api/search?q=${index}`);
    const kept = (await (await worker.cache('deckledger-api-v2')).keys()).map(entry => entry.url);
    assert.equal(kept.length, 150);
    assert.ok(kept.at(-1).endsWith('q=159') && !kept.some(url => url.endsWith('q=5')));
  });
});

describe('card images', () => {
  test('are fetched once and then served from the cache', async () => {
    worker.network.answer = () => image();
    await worker.get('/art/v1.svg?v=5&size=thumb');
    await worker.get('/art/v1.svg?v=5&size=thumb');
    assert.equal(worker.network.calls.length, 1);
  });

  test('a stand-in for an image that could not be fetched is not kept', async () => {
    worker.network.answer = () => image('placeholder');
    await worker.get('/art/v1.svg?v=5&size=thumb');
    await worker.get('/art/v1.svg?v=5&size=thumb');
    assert.equal(worker.network.calls.length, 2);
  });

  test('come from the offline save without asking the network', async () => {
    await (await worker.cache('deckledger-offline-v1')).put('/art/v1.svg?v=5&size=thumb', image());
    assert.equal(await (await worker.get('/art/v1.svg?v=5&size=thumb')).text(), 'image-bytes');
    assert.equal(worker.network.calls.length, 0);
  });

  test('the full-size image falls back to the saved thumbnail when the server is away', async () => {
    await (await worker.cache('deckledger-offline-v1')).put('/art/v1.svg?v=5&size=thumb', image());
    assert.equal(await (await worker.get('/art/v1.svg?v=5')).text(), 'image-bytes');
    // ... and without a thumbnail either, the image simply fails to load.
    assert.equal((await worker.get('/art/other.svg?v=5')).type, 'error');
  });
});

describe('static files and the page', () => {
  test('a file with a content hash is final once it is cached', async () => {
    worker.network.answer = () => new Response('script');
    await worker.get('/static/js/core.js?v=abc');
    await worker.get('/static/js/core.js?v=abc');
    assert.equal(worker.network.calls.length, 1);
  });

  test('a new version of a file replaces the cached one', async () => {
    worker.network.answer = () => new Response('script');
    await worker.get('/static/js/core.js?v=old');
    await worker.get('/static/js/core.js?v=new');
    const kept = (await (await worker.cache('deckledger-shell-v2')).keys()).map(entry => entry.url);
    assert.deepEqual(kept, [`${ORIGIN}/static/js/core.js?v=new`]);
  });

  test('a file without a hash is served from the cache and refreshed behind it', async () => {
    worker.network.answer = () => new Response('first');
    await worker.get('/static/manifest.json');
    worker.network.answer = () => new Response('second');
    assert.equal(await (await worker.get('/static/manifest.json')).text(), 'first');
    assert.equal(await (await worker.get('/static/manifest.json')).text(), 'second');
  });

  test('the page itself prefers the network and waits at most three seconds for it', async () => {
    worker.network.answer = () => new Response('<html>v1');
    await worker.get('/', { mode: 'navigate' });
    worker.network.answer = () => new Promise(() => {});
    const pending = worker.get('/', { mode: 'navigate' });
    await tick();
    assert.equal(worker.timers.filter(timer => !timer.cleared).at(-1).ms, 3000);
    worker.fireTimers();
    assert.equal(await (await pending).text(), '<html>v1');
  });

  test('writes and other origins are left alone', async () => {
    assert.equal(await worker.get('/api/collection', { method: 'POST' }), undefined);
    assert.equal(await worker.get('/css2?family=Inter', { origin: 'https://fonts.googleapis.com' }), undefined);
    assert.equal(await worker.get('/health'), undefined);
    assert.equal(worker.network.calls.length, 0);
  });
});

describe('updates', () => {
  test('caches of an older worker are removed, the offline save is not', async () => {
    for (const name of ['deckledger-shell-v1', 'deckledger-api-v1', 'deckledger-api-v2', 'deckledger-offline-v1', 'something-else']) await worker.cache(name);
    await worker.lifecycle('activate');
    assert.deepEqual([...worker.stores.keys()].sort(), ['deckledger-api-v2', 'deckledger-offline-v1']);
  });
});
