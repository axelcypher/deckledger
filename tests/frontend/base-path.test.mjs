// The app served under a path, the way Traefik serves it as ophir.<domain>/cards next to other
// Ophir apps: everything works there, and nothing the page loads misses the path -- an address
// written as /api/... or /static/... would reach the host's root instead of the app.
//
//   node --test tests/frontend/
import assert from 'node:assert/strict';
import { after, before, describe, test } from 'node:test';
import { chromePath, startBrowser, startServer } from './harness.mjs';

const BASE_PATH = '/cards';
const EMBER8 = 'vcard-print-ember8-en-normal';
const skip = !chromePath() && !process.env.CI ? 'Chrome not found' : false;

describe('app served under a path', { skip }, () => {
  let server, page;

  before(async () => {
    server = await startServer({ basePath: BASE_PATH });
    page = await startBrowser();
    await page.login(server.base, 'demo', 'deckledger');
  });
  after(async () => { await page?.close(); server?.stop(); });

  const noProblems = () => assert.deepEqual(page.problems.splice(0), []);
  // Requests to the app's own host that are not under its path.
  const outsideThePath = browser => browser.requests.filter(url => {
    const address = new URL(url);
    return address.origin === new URL(server.base).origin && !(address.pathname === BASE_PATH || address.pathname.startsWith(`${BASE_PATH}/`));
  });

  test('every view renders for every game', async () => {
    for (const game of ['vcard', 'lorcana', 'one-piece']) {
      await page.evaluate(`(setActiveGame(${JSON.stringify(game)}),1)`);
      for (const route of ['game', 'game-cards', 'collection', 'watchlist', 'decks', 'sheets', 'settings', 'dashboard']) {
        await page.route(route, route === 'game' || route === 'game-cards' ? game : undefined);
        assert.ok(await page.evaluate(`document.querySelector('#content').innerText.trim().length>20`), `${game}/${route} is empty`);
        assert.deepEqual(page.problems.splice(0), [], `${game}/${route}`);
      }
    }
    await page.evaluate(`(setActiveGame('vcard'),1)`);
  });

  test('cards can be added, opened and put into a deck', async () => {
    await page.route('set', 'vcard-test');
    await page.waitFor(`document.querySelectorAll('.card-tile img').length>=20`, { message: 'the card images of the set' });
    await page.evaluate(`post('/api/collection',{variant_id:${JSON.stringify(EMBER8)},delta:1})`);
    await page.evaluate(`openCard('vcard-card-ember8',${JSON.stringify(EMBER8)})`);
    await page.waitFor(`document.querySelector('#card-dialog [data-tab]')`, { message: 'the card to open' });
    await page.evaluate(`document.querySelector('#card-dialog').close?.()`);
    await page.route('decks');
    await page.evaluate(`document.querySelector('#new-deck, #first-deck, [data-new-deck]').click()`);
    await page.waitFor(`state.deckId&&!document.querySelector('#content .page-loader')`, { message: 'the new deck to open' });
    await page.evaluate(`post('/api/decks/'+state.deckId+'/cards',{variant_id:${JSON.stringify(EMBER8)},zone:'auto',delta:2}).then(()=>renderDeckbuilder(true))`);
    await page.waitFor(`document.querySelector('#content').innerText.includes('Ember (PL8)')`);
    // The export links get their address when the export dialog opens.
    await page.evaluate(`(document.querySelector('#deck-transfer-export-choice').click(),1)`);
    assert.ok((await page.evaluate(`document.querySelector('#deck-export-list').getAttribute('href')`)).startsWith(`${BASE_PATH}/api/decks/`));
    await page.evaluate(`(closeOverlay('deck-export-modal'),1)`);
    noProblems();
  });

  test('the service worker covers the app and nothing else on the host', async () => {
    // getRegistration() answers at once (ready would wait for good if the worker never activated).
    const scope = await page.waitFor(`navigator.serviceWorker.getRegistration().then(registration=>registration?.active&&registration.scope)`, { message: 'the service worker to be active' });
    assert.equal(scope, `${server.base}/`);
  });

  test('a shared collection opens under the path', async () => {
    await page.route('settings');
    await page.waitFor(`document.querySelector('#public-save')`, { message: 'the public profile settings to render' });
    await page.evaluate(`(()=>{document.querySelector('#public-enabled').checked=true;document.querySelector('#public-slug').value='alex';document.querySelector('[data-public-game="vcard"]').checked=true;document.querySelector('#public-save').click()})()`);
    await page.waitFor(`document.querySelector('#public-profile-body a')?.href.endsWith('${BASE_PATH}/u/alex')`, { message: 'the profile link to carry the path' });
    const visitor = await startBrowser();
    try {
      await visitor.goto(`${server.base}/u/alex`);
      await visitor.waitFor(`document.querySelector('.set-card')`, { message: 'the shared sets to render' });
      await visitor.evaluate(`document.querySelector('.set-card').click()`);
      await visitor.waitFor(`document.querySelectorAll('.card-tile').length>0`, { message: 'the cards of the set to render' });
      assert.deepEqual(visitor.problems, []);
      assert.deepEqual(outsideThePath(visitor), []);
    } finally { await visitor.close(); }
    noProblems();
  });

  test('signing out stays under the path', async () => {
    await page.evaluate(`document.querySelector('form[action$="/logout"]').submit()`);
    await page.waitFor(`location.pathname==='${BASE_PATH}/login'`, { message: 'the login page under the path' });
  });

  test('nothing the app loaded missed the path', () => {
    assert.ok(page.requests.length > 50, `only ${page.requests.length} requests seen`);
    assert.deepEqual(outsideThePath(page), []);
  });
});
