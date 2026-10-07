// The logic in static/js that needs no browser: classification, sorting, limits, URLs, ordering.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';
import { loadScripts } from './scripts.mjs';

const { run } = loadScripts(['js/core.js', 'js/finish.js', 'js/filters.js', 'js/catalog.js', 'js/offline.js', 'js/collection.js', 'js/decks.js']);
const set = (expression, value) => run(`${expression}=${JSON.stringify(value)}`);

set('state.boot', {
  user: { id: 7 },
  settings: { defaultLanguages: { lorcana: 'DE' } },
  games: [
    { id: 'vcard', languages: ['EN'], playset_size: 3, copy_limits: { Mascot: 2 }, icon: 'vcard' },
    { id: 'lorcana', languages: ['DE', 'EN'], playset_size: 4, copy_limits: {}, icon: 'lorcana' },
    { id: 'homebrew', languages: ['EN'] },
  ],
});

describe('finish', () => {
  const cases = JSON.parse(readFileSync(new URL('../fixtures/finish_cases.json', import.meta.url), 'utf8'));

  test('foil prints get an effect, regular prints do not -- the same cards sheet_render.py marks', () => {
    // tests/test_trade_sheets.py checks sheet_render.is_holo against the same file.
    for (const variant of cases) {
      const effect = run(`finishPresentation(${JSON.stringify(variant)}).effect`);
      assert.equal(effect !== 'finish-normal', variant.holo, JSON.stringify(variant));
    }
  });

  test('tiers: premium rarities above parallels above plain foils', () => {
    const effect = variant => run(`finishPresentation(${JSON.stringify(variant)}).effect`);
    assert.equal(effect({ finish: 'Normal', rarity: 'Enchanted', game_id: 'lorcana' }), 'finish-aurora');
    assert.equal(effect({ finish: 'Silver', rarity: 'Rare', game_id: 'lorcana', is_parallel: 1 }), 'finish-foil');
    assert.equal(effect({ finish: 'parallel', variant_code: 'parallel', rarity: 'R', game_id: 'one-piece', is_parallel: 1 }), 'finish-prismatic');
    assert.equal(effect({}), 'finish-normal');
  });

  test('image URLs', () => {
    assert.equal(run(`artUrl('a b/c')`), '/art/a%20b%2Fc.svg?v=6&size=thumb');
    assert.equal(run(`artUrl('x','full')`), '/art/x.svg?v=6');
  });
});

describe('text and money', () => {
  test('escapeHtml neutralises markup', () => {
    assert.equal(run(`escapeHtml('<img src=x onerror="a&b">')`), '&lt;img src=x onerror=&quot;a&amp;b&quot;&gt;');
    assert.equal(run(`escapeHtml(null)`), '');
    assert.equal(run(`escapeHtml(0)`), '0');
  });

  test('a missing price is said, not shown as zero', () => {
    assert.equal(run(`price(null)`), 'Kein Preis verfügbar');
    assert.match(run(`price(0)`), /^0,00\s€$/);
    assert.match(run(`price(1234.5)`), /^1\.234,50\s€$/);
  });

  test('a search finds every word anywhere, or reads an expression', () => {
    // tests/test_search.py checks the server's side of the same rule.
    const found = (query, ...fields) => run(`searchMatcher(${JSON.stringify(query)})(${JSON.stringify(fields)})`);
    assert.equal(found('PL9|PL10', 'Monarch (PL10)'), true);
    assert.equal(found('PL9|PL10', 'Monarch (PL8)'), false);
    assert.equal(found('pl(8|9)', 'Ember (PL9)'), true);
    assert.equal(found('Ember (PL8)', 'ember (pl8)'), true);
    assert.equal(found('(PL9', 'Ember PL9'), true);
    assert.equal(found('Smugalana PL9', 'Smug Alana (PL9)'), true);
    assert.equal(found('SmugAlana Fractured Paradox 1st Ed', 'Smug Alana (PL9)', 'PL9', 'Fractured Paradox', '1st Edition Holo'), true);
    assert.equal(found('Godess', 'Goddess Sansindra'), true);
    assert.equal(found('Smug Alana PL8', 'Smug Alana (PL9)'), false);
    assert.equal(found('ember', null), false);
  });

  test('initials', () => {
    assert.equal(run(`initials('Alex Morgan Third')`), 'AM');
  });
});

describe('sets', () => {
  const sets = [
    { id: 'a', code: 'OP-02', name: 'B', set_type: 'Booster Set', release_date: '2023-03-01' },
    { id: 'b', code: 'OP-10', name: 'A', set_type: 'Booster Set', release_date: '2024-06-01' },
    { id: 'c', code: 'ST-01', name: 'C', set_type: 'Starter Deck', release_date: '2022-12-01' },
    { id: 'd', code: 'P-001', name: 'D', set_type: 'Promo', release_date: null },
    { id: 'e', code: 'OP-03', name: 'E', set_type: 'Booster Set', release_dates: ['2023-06-01', '2023-09-01'] },
  ];

  test('groups by kind of product, in a fixed order', () => {
    set('state.setType', 'all'); set('state.setDirection', 'desc');
    const groups = run(`groupedSets(${JSON.stringify(sets)}).map(([group,items])=>[group,items.map(item=>item.id)])`);
    assert.deepEqual(groups, [['Booster', ['b', 'e', 'a']], ['Decks', ['c']], ['Promos', ['d']]]);
    assert.equal(run(`setGroup({set_type:'Something else'})`), 'Something else');
    assert.equal(run(`setGroup({})`), 'Weitere Sets');
  });

  test('newest first, sets without a date last in either direction', () => {
    const order = direction => run(`${JSON.stringify(sets)}.sort((a,b)=>compareSetRelease(a,b,'${direction}')).map(item=>item.id)`);
    assert.deepEqual(order('desc'), ['b', 'e', 'a', 'c', 'd']);
    assert.deepEqual(order('asc'), ['c', 'a', 'e', 'b', 'd']);
  });

  test('a set released in stages counts by its latest date', () => {
    assert.equal(run(`latestRelease({release_dates:['2023-06-01',null,'2023-09-01']})`), '2023-09-01');
    assert.equal(run(`latestRelease({release_date:'2022-01-01'})`), '2022-01-01');
    assert.equal(run(`latestRelease({})`), null);
  });

  test('filter labels abbreviate set names', () => {
    assert.equal(run(`setAbbreviation("Ursula's Return")`), 'UR');
    assert.equal(run(`setFilterLabel({id:'lorcana-4',code:'4',name:"Ursula's Return"})`), '4 UR');
    assert.equal(run(`setFilterLabel({id:'one-piece-op-01',code:'OP-01',name:'Romance Dawn'})`), 'OP-01 RD');
  });
});

describe('rules that come with the game', () => {
  test('playset size', () => {
    assert.deepEqual(run(`['vcard','lorcana','homebrew','unknown'].map(playsetSize)`), [3, 4, 4, 4]);
  });

  test('copies of a card a deck may hold', () => {
    set('state.activeGameId', 'vcard');
    assert.deepEqual(run(`['Mascot','VT'].map(card_type=>deckCopyLimit({card_type}))`), [2, 3]);
    set('state.activeGameId', 'lorcana');
    assert.equal(run(`deckCopyLimit({card_type:'Character'})`), 4);
    set('state.activeGameId', 'homebrew');
    assert.equal(run(`deckCopyLimit({card_type:'Anything'})`), 4);
  });

  test('a zone other than the main deck is limited by its size', () => {
    set('state.activeGameId', 'lorcana');
    assert.equal(run(`deckZoneMaximum({id:'main',target:60},{card_type:'Character'})`), 4);
    assert.equal(run(`deckZoneMaximum({id:'leader',target:1},{card_type:'Leader'})`), 1);
    assert.equal(run(`deckZoneMaximum({id:'cheer',target:20},{card_type:'Cheer'})`), 20);
    assert.equal(run(`deckZoneMaximum({id:'side'},{card_type:'X'})`), 4);
  });
});

describe('quantity buttons on a card tile', () => {
  const variant = (finish, quantity = 0, code = finish.toLowerCase().replace(/ /g, '-')) =>
    ({ variant_id: `v-${code}`, finish, variant_code: finish === 'Normal' ? 'normal' : code, language: 'EN', game_id: 'vcard', quantity, rarity: 'Uncommon', price: null });
  const html = (variants, game = 'vcard') => run(`cardTile(${JSON.stringify({ identity_id: 'c', canonical_name: 'Card', collector_number: '001', rarity: 'Uncommon', language: 'EN', quantity: 0,
    variant_id: variants[0].variant_id, variants: variants.map(item => ({ ...item, game_id: game })) })})`);
  const tile = (variants, game) => {
    const markup = html(variants, game);
    return {
      controls: [...markup.matchAll(/class="quantity-control( foil)?" data-variant="([^"]+)"/g)].map(match => `${match[2]}${match[1] ? ' (foil)' : ''}`),
      quick: [...markup.matchAll(/class="quick-add( foil)?" data-variant="([^"]+)">([^<]+)</g)].map(match => `${match[2]}: ${match[3].trim()}`),
      image: markup.match(/class="card-tile [^"]*" data-identity="c" data-variant="([^"]+)"/)[1],
    };
  };
  const everyPrint = [variant('Normal'), variant('Holo'), variant('1st Edition'), variant('1st Edition Holo')];
  const games = [
    { id: 'vcard', languages: ['EN'], playset_size: 3, tile_editions: [
      { id: 'base', label: 'Limited / Unlimited', regular: 'Normal', foil: 'Holo', foil_label: 'Holo' },
      { id: 'first', label: '1st Edition', regular: '1st Edition', foil: '1st Edition Holo', foil_label: 'Holo' }] },
    { id: 'lorcana', languages: ['EN'], playset_size: 4, tile_editions: [{ id: 'base', label: '', regular: 'Normal', foil: 'Silver', foil_label: 'Foil' }] },
    { id: 'one-piece', languages: ['EN'], playset_size: 4, tile_editions: [] },
  ];

  test('a VCard tile counts the regular print and its Holo', () => {
    set('state.boot.games', games); set('state.activeGameId', 'vcard'); set('state.tileEditions', {});
    assert.deepEqual(tile(everyPrint), {
      controls: ['v-holo (foil)', 'v-normal'], quick: ['v-holo: ＋ 1 Holo', 'v-normal: ＋ 1 hinzufügen'], image: 'v-normal' });
  });

  test('the switch moves the buttons to the 1st Edition prints', () => {
    set('state.tileEditions', { vcard: 'first' });
    assert.deepEqual(tile(everyPrint), {
      controls: ['v-1st-edition-holo (foil)', 'v-1st-edition'], quick: ['v-1st-edition-holo: ＋ 1 Holo', 'v-1st-edition: ＋ 1 hinzufügen'], image: 'v-1st-edition' });
    assert.match(html(everyPrint), /· 1st Edition</);
  });

  test('a card that does not exist in the chosen print run keeps the buttons of the one it has', () => {
    assert.deepEqual(tile([variant('Normal'), variant('Holo')]).controls, ['v-holo (foil)', 'v-normal']);
    // ... one that only exists as a Holo gets a single button for that ...
    set('state.tileEditions', {});
    assert.deepEqual(tile([variant('Holo')]), { controls: ['v-holo'], quick: ['v-holo: ＋ 1 hinzufügen'], image: 'v-holo' });
    // ... and one without a 1st Edition Holo a single one in that print run.
    set('state.tileEditions', { vcard: 'first' });
    assert.deepEqual(tile([variant('Normal'), variant('Holo'), variant('1st Edition')]).controls, ['v-1st-edition']);
    set('state.tileEditions', {});
  });

  test('Lorcana keeps Normal and Foil, other games one button', () => {
    set('state.activeGameId', 'lorcana');
    assert.deepEqual(tile([variant('Normal'), variant('Silver')], 'lorcana').quick, ['v-silver: ＋ 1 Foil', 'v-normal: ＋ 1 hinzufügen']);
    assert.deepEqual(tile([variant('Magma')], 'lorcana').controls, ['v-magma'], 'a premium print has no foil of its own');
    set('state.activeGameId', 'one-piece');
    assert.deepEqual(tile([variant('standard'), variant('parallel')], 'one-piece').controls, ['v-standard']);
    set('state.activeGameId', 'vcard');
  });

  test('the switch only exists for games with several print runs', () => {
    assert.match(run(`tileEditionSwitch('vcard')`), /data-tile-edition="base" class="active">Limited \/ Unlimited<.*data-tile-edition="first" class="">1st Edition</);
    assert.equal(run(`tileEditionSwitch('lorcana')`), '');
    assert.equal(run(`tileEditionSwitch('one-piece')`), '');
  });
});

describe('views and their URLs', () => {
  test('a view starts with the default language of its game', () => {
    assert.equal(run(`defaultCollectionFilters('lorcana').language`), 'DE');
    assert.equal(run(`defaultCollectionFilters('vcard').language`), 'EN');
    assert.equal(run(`defaultWatchFilters('lorcana').sort`), 'added');
  });

  test('the offline save asks for exactly what the views ask for', () => {
    assert.equal(run(`collectionUrl('vcard',defaultCollectionFilters('vcard'))`),
      '/api/collection?game_id=vcard&q=&set_id=&language=EN&rarity=&rarities=&costs=&colors=&inkwell=&finish=&mode=all&sort=number');
    assert.equal(run(`watchlistCardsUrl(12,defaultWatchFilters('vcard'))`),
      '/api/watchlists/12/cards?q=&set_id=&language=EN&rarity=&rarities=&costs=&colors=&inkwell=&finish=&sort=added');
    set('state.activeGameId', 'lorcana');
    run(`setActiveGame('vcard',false)`);
    assert.deepEqual(run(`[state.collectionFilters,state.watchFilters]`), run(`[defaultCollectionFilters('vcard'),defaultWatchFilters('vcard')]`));
  });
});

describe('ordering', () => {
  test('changes to one card run one after the other, different cards side by side', async () => {
    const order = await run(`(async()=>{
      const log=[],wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      const task=(name,ms)=>async()=>{log.push('start '+name);await wait(ms);log.push('end '+name)};
      await Promise.all([inQuantityOrder('a',task('a1',30)),inQuantityOrder('a',task('a2',1)),inQuantityOrder('b',task('b1',5))]);
      return {log,left:quantityChains.size};
    })()`);
    assert.deepEqual(order.log.filter(entry => / a\d$/.test(entry)), ['start a1', 'end a1', 'start a2', 'end a2']);
    assert.ok(order.log.indexOf('end b1') < order.log.indexOf('end a1'), 'b did not wait for a');
    assert.equal(order.left, 0);
  });

  test('a failed change does not block the ones queued behind it', async () => {
    const result = await run(`(async()=>{
      const failed=inQuantityOrder('c',async()=>{throw new Error('boom')}).catch(error=>error.message);
      const next=inQuantityOrder('c',async()=>'ran');
      return [await failed,await next];
    })()`);
    assert.deepEqual(result, ['boom', 'ran']);
  });

  test('a render that was overtaken knows it', () => {
    assert.deepEqual(run(`(()=>{const first=renderGuard(),second=renderGuard();return [first(),second()]})()`), [true, false]);
    assert.equal(run(`(()=>{const guard=renderGuard();renderGeneration++;return guard()})()`), true);
  });
});
