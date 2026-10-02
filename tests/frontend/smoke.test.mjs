// Drives the real app in a headless Chrome: every view renders, and the things people do most
// (adding cards, opening a card, lists, decks, sheets, admin) work end to end without a single
// error in the console.
//
//   node --test tests/frontend/
//
// Needs Chrome (CHROME_BIN overrides the lookup) and the Python test requirements; set
// DECKLEDGER_PYTHON when "python" is not the interpreter that has them.
import assert from 'node:assert/strict';
import { after, before, describe, test } from 'node:test';
import { chromePath, sleep, startBrowser, startServer } from './harness.mjs';

const EMBER8 = 'vcard-print-ember8-en-normal';
const skip = !chromePath() && !process.env.CI ? 'Chrome not found' : false;

describe('app in the browser', { skip }, () => {
  let server, page;

  before(async () => {
    server = await startServer();
    page = await startBrowser();
    await page.login(server.base, 'demo', 'deckledger');
    await page.evaluate(`(setActiveGame('vcard'),1)`);
    await page.route('dashboard');
  });
  after(async () => { await page?.close(); server?.stop(); });

  const noProblems = () => assert.deepEqual(page.problems.splice(0), []);
  const owned = async variantId => (await page.evaluate(`api('/api/collection/entries/${variantId}')`)).reduce((sum, entry) => sum + entry.quantity, 0);
  const text = selector => page.evaluate(`document.querySelector(${JSON.stringify(selector)})?.innerText`);

  test('starts on the dashboard of the signed-in user', async () => {
    assert.equal(await text('#user-name'), 'Alex Morgan');
    assert.equal(await page.evaluate(`state.route`), 'dashboard');
    assert.ok(await page.evaluate(`document.querySelectorAll('[data-dashboard-route]').length>=4`));
    noProblems();
  });

  test('every view renders for every game', async () => {
    for (const game of ['vcard', 'lorcana', 'one-piece']) {
      await page.evaluate(`(setActiveGame(${JSON.stringify(game)}),1)`);
      for (const route of ['game', 'game-cards', 'collection', 'watchlist', 'decks', 'sheets', 'settings', 'dashboard']) {
        await page.route(route, route === 'game' || route === 'game-cards' ? game : undefined);
        assert.equal(await page.evaluate(`document.body.dataset.route`), route);
        assert.ok(await page.evaluate(`document.querySelector('#content').innerText.trim().length>20`), `${game}/${route} is empty`);
        assert.deepEqual(page.problems.splice(0), [], `${game}/${route}`);
      }
    }
    await page.evaluate(`(setActiveGame('vcard'),1)`);
  });

  test('leaving a view while it loads does not let it draw over the next one', async () => {
    // Regression: the sheets view fetched its options first and then put its loading placeholder
    // over whatever view had been opened in the meantime -- for good.
    for (const slow of ['sheets', 'decks', 'collection', 'watchlist', 'game-cards']) {
      await page.evaluate(`(sheetView.options=null,routeTo(${JSON.stringify(slow)}${slow === 'game-cards' ? `,'vcard'` : ''}),routeTo('settings'),1)`);
      await sleep(700);
      assert.equal(await page.evaluate(`document.body.dataset.route`), 'settings');
      assert.ok(await page.evaluate(`!document.querySelector('#content .page-loader')&&/Einstellungen|Settings/.test(document.querySelector('#content').innerText)`), `${slow} drew over the settings`);
    }
    noProblems();
  });

  test('a set lists its cards', async () => {
    await page.route('set', 'vcard-test');
    await page.waitFor(`document.querySelectorAll('.card-tile').length>=20`);
    assert.match(await text('#content'), /Ember \(PL8\)/);
    noProblems();
  });

  test('quick clicks on the add button all count', async () => {
    await page.evaluate(`(setEditMode(true),1)`);
    await page.route('set', 'vcard-test');
    const button = `document.querySelector('.quantity-control[data-variant="${EMBER8}"] button[data-delta="1"]')`;
    await page.waitFor(button);
    // Three clicks without waiting for any answer in between.
    await page.evaluate(`(()=>{const add=${button};add.click();add.click();add.click()})()`);
    await page.waitFor(`document.querySelector('.quantity-control[data-variant="${EMBER8}"] b').textContent==='3'`, { message: 'the tile to show 3' });
    await page.waitFor(`api('/api/collection/entries/${EMBER8}').then(entries=>entries.reduce((sum,entry)=>sum+entry.quantity,0)===3)`, { message: 'the server to have 3' });
    await page.evaluate(`${button.replace('data-delta="1"', 'data-delta="-1"')}.click()`);
    await page.waitFor(`api('/api/collection/entries/${EMBER8}').then(entries=>entries.reduce((sum,entry)=>sum+entry.quantity,0)===2)`);
    assert.equal(await page.evaluate(`document.querySelector('.quantity-control[data-variant="${EMBER8}"] b').textContent`), '2');
    await page.evaluate(`(setEditMode(false),1)`);
    noProblems();
  });

  test('the collection shows what was added', async () => {
    await page.route('collection');
    await page.waitFor(`document.querySelectorAll('.card-tile').length===1`);
    assert.match(await text('.card-tile'), /Ember \(PL8\)/);
    noProblems();
  });

  test('a card opens with its tabs, and a price can be entered by hand', async () => {
    await page.evaluate(`openCard('vcard-card-ember8',${JSON.stringify(EMBER8)})`);
    await page.waitFor(`document.querySelector('#card-dialog .modal-tabs, #card-dialog [data-tab]')`);
    const tabs = await page.evaluate(`[...document.querySelectorAll('#card-dialog [data-tab]')].map(button=>button.dataset.tab)`);
    assert.ok(tabs.length >= 3 && tabs.includes('market'), `tabs: ${tabs}`);
    for (const tab of tabs) {
      await page.evaluate(`document.querySelector('#card-dialog [data-tab="${tab}"]').click()`);
      assert.ok(await page.evaluate(`document.querySelector('#card-dialog .modal-content').innerText.trim().length>20`), tab);
    }
    await page.evaluate(`(state.modalTab='market',renderCardModal(),1)`);
    await page.evaluate(`(()=>{document.querySelector('#manual-price-input').value='4,50';document.querySelector('#manual-price-form').requestSubmit()})()`);
    await page.waitFor(`state.modalVariant?.price===4.5&&document.querySelector('#manual-price-remove')`, { message: 'the manual price to be shown' });
    assert.equal(await page.evaluate(`api('/api/collection?game_id=vcard').then(data=>data.stats.value)`), 9);
    await page.evaluate(`document.querySelector('#manual-price-remove').click()`);
    await page.waitFor(`state.modalVariant?.price==null&&!document.querySelector('#manual-price-remove')`);
    await page.evaluate(`(closeOverlay('card-modal'),1)`);
    noProblems();
  });

  test('closing a card while it reloads in the background is harmless', async () => {
    // Regression: the refresh that follows a change finished after the dialog was closed and
    // then failed on the card that was no longer there.
    await page.evaluate(`openCard('vcard-card-ember8',${JSON.stringify(EMBER8)})`);
    await page.waitFor(`document.querySelector('#card-dialog [data-tab]')`);
    await page.evaluate(`(openCard('vcard-card-ember8',${JSON.stringify(EMBER8)},true),closeOverlay('card-modal'),1)`);
    await sleep(600);
    assert.equal(await page.evaluate(`state.modalCard`), null);
    noProblems();
  });

  test('a card can be put on a watchlist and shows up there', async () => {
    await page.evaluate(`post('/api/watchlist',{variant_id:${JSON.stringify(EMBER8)}})`);
    await page.route('watchlist');
    await page.waitFor(`document.querySelectorAll('.card-tile').length===1`);
    assert.match(await text('.list-tabs'), /Merkliste/);
    noProblems();
  });

  test('a deck can be created and filled', async () => {
    await page.route('decks');
    await page.evaluate(`document.querySelector('#new-deck, #first-deck, [data-new-deck]').click()`);
    await page.waitFor(`state.deckId&&!document.querySelector('#content .page-loader')`, { message: 'the new deck to open' });
    const deckId = await page.evaluate(`state.deckId`);
    await page.evaluate(`post('/api/decks/${deckId}/cards',{variant_id:${JSON.stringify(EMBER8)},zone:'auto',delta:2}).then(()=>renderDeckbuilder(true))`);
    await page.waitFor(`document.querySelector('#content').innerText.includes('Ember (PL8)')`);
    noProblems();
  });

  test('a sheet can be built from the collection and renders as an image', async () => {
    await page.route('sheets');
    await page.evaluate(`document.querySelector('#first-sheet, #new-sheet').click()`);
    await page.waitFor(`state.sheetId&&document.querySelector('#sheet-picker-list [data-pick]')`, { message: 'the picker to list the collection' });
    await page.evaluate(`document.querySelector('#sheet-picker-list [data-pick="${EMBER8}"]').click()`);
    await page.waitFor(`document.querySelectorAll('#sheet-entries .sheet-entry').length===1`);
    await page.waitFor(`(()=>{const image=document.querySelector('#sheet-preview img');return image&&image.complete&&image.naturalWidth>300})()`, { message: 'the rendered sheet image' });
    assert.match(await page.evaluate(`document.querySelector('#sheet-text').value`), /Ember \(PL8\)/);
    assert.deepEqual(await page.evaluate(`[...document.querySelectorAll('[data-sheet-source]')].map(button=>button.dataset.sheetSource)`), ['collection', 'duplicates', 'surplus']);
    noProblems();
  });

  test('search finds cards by name', async () => {
    await page.evaluate(`doSearch('Tide')`);
    await page.waitFor(`document.querySelector('#search-results')?.innerText.includes('Tide (PL8)')`);
    noProblems();
  });

  test('a change made while offline is queued and sent once the connection is back', async () => {
    await page.evaluate(`(setEditMode(true),1)`);
    await page.route('set', 'vcard-test');
    const before = await owned(EMBER8);
    await page.offline(true);
    await page.evaluate(`document.querySelector('.quantity-control[data-variant="${EMBER8}"] button[data-delta="1"]').click()`);
    await page.waitFor(`listOfflineMutations().then(items=>items.length===1)`, { message: 'the change to be queued' });
    assert.equal(await page.evaluate(`document.querySelector('.quantity-control[data-variant="${EMBER8}"] b').textContent`), String(before + 1));
    await page.offline(false);
    await page.evaluate(`syncOfflineQueue()`);
    await page.waitFor(`listOfflineMutations().then(items=>items.length===0)`, { message: 'the queue to drain' });
    assert.equal(await owned(EMBER8), before + 1);
    await page.evaluate(`(setEditMode(false),1)`);
    // Requests that fail while offline are logged by the browser itself; that is expected here.
    page.problems.splice(0);
  });

  test('saving for offline use makes the collection available without the server', async () => {
    await page.waitFor(`navigator.serviceWorker.controller`, { message: 'the service worker to control the page' });
    await page.route('settings');
    await page.evaluate(`document.querySelector('#offline-save-start').click()`);
    const info = await page.waitFor(`offlineSaveInfo()`, { message: 'the save to finish' });
    assert.equal(info.cards, 1);
    assert.ok(info.files >= 20, `only ${info.files} files saved`);
    await page.waitFor(`document.querySelector('#offline-save-body')?.innerText.includes('1 Karte gespeichert')`);
    // Without the service worker's own caches, only what was saved on purpose is left.
    await page.evaluate(`caches.keys().then(keys=>Promise.all(keys.filter(key=>!key.startsWith('deckledger-offline')).map(key=>caches.delete(key))))`);
    await page.offline(true);
    try {
      await page.route('collection');
      await page.waitFor(`document.querySelectorAll('.card-tile').length===1`, { message: 'the saved collection to render offline' });
      await page.route('watchlist');
      await page.waitFor(`document.querySelectorAll('.card-tile').length===1`, { message: 'the saved watchlist to render offline' });
      await page.evaluate(`openCard('vcard-card-ember8',${JSON.stringify(EMBER8)})`);
      await page.waitFor(`document.querySelector('#card-dialog [data-tab]')`, { message: 'the saved card details to open offline' });
      await page.evaluate(`(closeOverlay('card-modal'),1)`);
    } finally { await page.offline(false); }
    await page.evaluate(`api('/api/bootstrap')`);   // back online: lets the app notice the server again
    page.problems.splice(0);
  });

  test('saved data of another account is dropped', async () => {
    await page.evaluate(`caches.open(OFFLINE_SAVE_CACHE).then(cache=>cache.put(OFFLINE_SAVE_MARKER,new Response(JSON.stringify({userId:-1,cards:5,savedAt:new Date().toISOString()}))))`);
    assert.equal((await page.evaluate(`offlineSaveInfo()`)).userId, -1);
    await page.evaluate(`dropForeignOfflineSave()`);
    assert.equal(await page.evaluate(`offlineSaveInfo()`), null);
    assert.equal(await page.evaluate(`caches.open(OFFLINE_SAVE_CACHE).then(cache=>cache.keys()).then(keys=>keys.length)`), 0);
    noProblems();
  });

  test('the phone layout renders its tab bar and views', async () => {
    const phone = await startBrowser({ width: 400, height: 860, mobile: true });
    try {
      await phone.login(server.base, 'demo', 'deckledger');
      assert.ok(await phone.evaluate(`getComputedStyle(document.querySelector('#mobile-tabbar')).display!=='none'`));
      for (const route of ['collection', 'watchlist', 'decks', 'sheets', 'dashboard']) await phone.route(route);
      assert.deepEqual(phone.problems, []);
    } finally { await phone.close(); }
  });

  test('an admin manages accounts', async () => {
    const admin = await startBrowser();
    try {
      await admin.login(server.base, 'admin', 'admin');
      await admin.route('admin');
      await admin.waitFor(`document.querySelectorAll('[data-user-row]').length===2`);
      await admin.evaluate(`(()=>{document.querySelector('#admin-new-user-name').value='mia';document.querySelector('#admin-new-user-password').value='geheim-1234';document.querySelector('#admin-create-user').click()})()`);
      await admin.waitFor(`document.querySelectorAll('[data-user-row]').length===3`, { message: 'the new account to be listed' });
      assert.deepEqual(admin.problems, []);
    } finally { await admin.close(); }
  });

  test('a normal account has no admin view', async () => {
    assert.equal(await page.evaluate(`fetch('/api/admin/users').then(response=>response.status)`), 403);
    await sleep(50);
    page.problems.splice(0);
  });
});
