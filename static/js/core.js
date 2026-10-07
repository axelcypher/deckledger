// Shared state, small helpers, the API client, navigation between views.

const state = {
  boot: null, route: 'dashboard', game: null, set: null, cards: [],
  edit: false, zoom: 220, setZoom: 3, setType: 'all', setSort: 'type', setDirection:'desc', language: 'combined',
  filter: 'all', sort: 'number', query: '', modalCard: null, modalVariant: null, modalTab: 'collection',
  modalFoilLayerMeta: null, modalFoilLayerMetaVariantId: null,
  activeGameId: null, watchlistId: null, activeWatchlists: [], watchSelection: new Set(), watchSelectionMode: false, collectionSelection: new Set(), collectionSelectionMode: false,  deckId: null, deckView: 'grid', deckZoom: 135, deckCatalogOpen: false,
  collapsedSetGroups: {},
  cardFilters: {rarity:'', rarities:[], costs:[], colors:[], inkwell:'', finish:'normal', foilMode:''},
  collectionFilters: {q:'',set_id:'',language:'all',rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'',mode:'all',sort:'number'},
  watchFilters: {q:'',set_id:'',language:'all',rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'',sort:'added'}, deckFilters:{q:'',set_id:'',language:'EN',rarity:'',rarities:[],type:'',color:'',sort:'number',colors:[],types:[],costs:[],attributes:[],kinds:[],bloomLevels:[],inkwell:''}, deckZone:'main', deckCatalogObserver:null,
  homeBanner:null, homeBannerTimer:null, mobileFiltersOpen:{}, tileEditions:{}
};

const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const content = $('#content');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const currencyMoney = (value,currency='EUR') => new Intl.NumberFormat('de-DE',{style:'currency',currency}).format(Number(value)||0);
const money = value => currencyMoney(value,'EUR');
const price = value => value == null ? 'Kein Preis verfügbar' : money(value);
// Symbol-before-number variant for the mobile card modal specifically (de-DE's
// Intl.NumberFormat currency style always puts the symbol after; this only reformats
// the one modal display, not the shared price()/money() used everywhere else).
const priceSymbolFirst = value => value == null ? 'Kein Preis verfügbar' : '€' + new Intl.NumberFormat('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value)||0);
const nativePrice = variant => variant.price_native==null||!variant.price_native_currency ? null : currencyMoney(variant.price_native,variant.price_native_currency);
const maxPrice = items => { const values=items.map(x=>x.price).filter(x=>x!=null); return values.length?Math.max(...values):null; };
const deckCostLabel = (value,unpriced=0) => `${money(value)}${unpriced?` · ${unpriced}× ohne Preis`:''}`;
const date = value => value ? new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'short',year:'numeric'}).format(new Date(value)) : '–';
const releaseDate = item => {
  const values=[...new Set((item?.release_dates||[]).filter(Boolean))].sort();
  if(values.length<2)return date(values[0]||item?.release_date);
  return `${date(values[0])} – ${date(values.at(-1))}`;
};

const variantName = variant => {
  const label = variant.game_id==='lorcana' ? lorcanaFinishLabel(variant.finish,variant.rarity) : variant.finish;
  return variant.edition_label ? `${label} · ${variant.edition_label}` : label;
};
// True while the service worker is answering from its saved copies because the server did not
// respond (it marks those answers) -- shown in the indicator so old data is never mistaken for
// the current state.
let serverUnreachable=false;
const api = async (url, options={}) => {
  let response;
  try{
    response = await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  }catch(networkError){
    // fetch() itself throwing (before any Response exists) means the request never reached the
    // server -- offline, DNS hiccup, connection refused. Tagged so callers (the offline outbox)
    // can queue-and-retry ONLY this case, not genuine server-side errors below.
    const error=new Error('Keine Verbindung zum Server');
    error.isNetworkError=true;
    throw error;
  }
  if (response.status===401) { location.href='/login'; const error=new Error('Nicht angemeldet'); error.isAuthError=true; throw error; }
  const stale=response.headers.get('X-DeckLedger-Stale')==='1';
  if(stale!==serverUnreachable){
    serverUnreachable=stale;
    updateOfflineIndicator();
    if(!stale)syncOfflineQueue(); // the server is back: send whatever was queued meanwhile
  }
  let data;
  try{
    data = await response.json();
  }catch(parseError){
    // A non-JSON body (an HTML error page from an unhandled server exception, a proxy's error
    // page, ...) means something broke server-side -- surface the HTTP status, not the raw
    // "Unexpected token '<'" parse error, which is meaningless to a user.
    throw new Error(`Serverfehler (${response.status})`);
  }
  if (!response.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen');
  return data;
};
const post = (url,data) => api(url,{method:'POST',body:JSON.stringify(data)});

// Debounced search inputs (collection/watchlist/deck-catalog/all-cards) all call this after
// each pause in typing. renderFn() is async and can take a moment (network round-trip, full
// re-render) -- type fast enough and a SECOND call can start for the same selector before the
// first one's renderFn() has resolved. Whichever finishes last used to win unconditionally, so
// an earlier (now-stale) call finishing after a newer one would re-apply its own, by-then-
// outdated selectionRange on top of text the user had since kept typing -- felt like the
// cursor randomly jumping backwards mid-word. The per-selector token below makes only the
// most-recently-STARTED call for a given selector allowed to touch focus/selection afterwards.
const reRenderTokens=new Map();
async function reRenderPreservingFocus(selector,renderFn){
  const myToken=(reRenderTokens.get(selector)||0)+1;
  reRenderTokens.set(selector,myToken);
  const active=document.activeElement;
  const hadFocus=active&&active.matches&&active.matches(selector);
  const selStart=hadFocus?active.selectionStart:null,selEnd=hadFocus?active.selectionEnd:null;
  await renderFn();
  if(reRenderTokens.get(selector)!==myToken)return;
  if(hadFocus){
    const el=$(selector);
    if(el){el.focus();if(selStart!=null)try{el.setSelectionRange(selStart,selEnd)}catch{}}
  }
}

function toast(message, actionLabel, action, key) {
  // A keyed toast replaces its predecessor: five quick "+1" clicks leave one message, not five.
  if(key)$$('.toast',$('#toast-stack')).filter(old=>old.dataset.key===key).forEach(old=>old.remove());
  const node=document.createElement('div'); node.className='toast'; if(key)node.dataset.key=key;
  node.innerHTML=`<span>${escapeHtml(message)}</span>${actionLabel?`<button>${escapeHtml(actionLabel)}</button>`:''}`;
  if(actionLabel) $('button',node).onclick=async()=>{ await action?.(); node.remove(); };
  $('#toast-stack').append(node); setTimeout(()=>node.remove(),5000);
}

function setNav(route) {
  $$('.nav-item[data-route]').forEach(el=>el.classList.toggle('active',el.dataset.route===route));
  $$('.mt-tab[data-route]').forEach(el=>el.classList.toggle('active',el.dataset.route===route));
  $$('.mobile-profile-button[data-route]').forEach(el=>el.classList.toggle('active',el.dataset.route===route));
}

function setEditMode(enabled,announce=false){
  state.edit=Boolean(enabled);
  document.body.classList.toggle('editing',state.edit);
  $('#edit-panel')?.classList.toggle('on',state.edit);
  $('#edit-panel')?.setAttribute('title',state.edit?'Bearbeiten ist an':'Bearbeiten ist aus');
  $('#edit-toggle')?.setAttribute('aria-checked',String(state.edit));
  $('#edit-toggle')?.setAttribute('aria-label',state.edit?'Bearbeitungsmodus deaktivieren':'Bearbeitungsmodus aktivieren');
  const mobileToggle=$('#mobile-edit-toggle');
  mobileToggle?.classList.toggle('active',state.edit);
  mobileToggle?.setAttribute('aria-pressed',String(state.edit));
  mobileToggle?.setAttribute('aria-label',state.edit?'Edit Mode deaktivieren':'Edit Mode aktivieren');
  mobileToggle?.setAttribute('title',state.edit?'Edit Mode deaktivieren':'Edit Mode aktivieren');
  if(announce)toast(state.edit?'Edit Mode aktiviert':'Edit Mode beendet');
}

function toggleEditMode(){setEditMode(!state.edit,true)}

// The filters a view starts with and the URL it asks the server with. Saving for offline use
// (js/offline.js) fetches exactly these, so what it stores is what the views look for later.
const gameDefaultLanguage=gameId=>state.boot.settings?.defaultLanguages?.[gameId]||state.boot.games.find(game=>game.id===gameId)?.languages[0];
const defaultCollectionFilters=gameId=>({q:'',set_id:'',language:gameDefaultLanguage(gameId),rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'',mode:'all',sort:'number'});
const defaultWatchFilters=gameId=>({q:'',set_id:'',language:gameDefaultLanguage(gameId),rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'',sort:'added'});
const collectionUrl=(gameId,filters)=>`/api/collection?${new URLSearchParams({game_id:gameId,...filters})}`;
const watchlistCardsUrl=(listId,filters)=>`/api/watchlists/${listId}/cards?${new URLSearchParams(filters)}`;

function setActiveGame(gameId, persist=true) {
  const previousGameId=state.activeGameId;
  const changed=previousGameId && previousGameId!==gameId;
  const isInitial=!previousGameId;
  state.activeGameId=gameId; state.game=state.boot.games.find(g=>g.id===gameId) || state.boot.games[0];
  if($('#global-game-filter')) $('#global-game-filter').value=gameId;
  const iconName=state.game.icon||'generic';
  if($('#global-game-icon'))$('#global-game-icon').style.setProperty('--tcg-icon',`url('/static/tcg-icons/${iconName}.svg?v=3')`);
  if($('#global-game-picker'))$('#global-game-picker').title=`${state.game.short_name} auswählen`;
  state.watchlistId=null; state.watchSelection.clear(); state.watchSelectionMode=false; state.collectionSelection.clear(); state.collectionSelectionMode=false; state.deckId=null; state.sheetId=null; state.dealId=null;
  if(changed||isInitial){
    // Settings shows languages[0] as each game's assumed default even before
    // the user ever touches that dropdown (it's only actually saved once they
    // do) -- falling back to the same value here instead of a generic 'all'/
    // 'combined' keeps every page consistent with what Settings implies is
    // already the default, not just what got explicitly saved.
    const defaultLang=state.boot.settings?.defaultLanguages?.[gameId]||state.game.languages[0];
    state.language=defaultLang;state.setType='all';state.setSort='type';state.setDirection='desc';state.cardFilters={rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'normal',foilMode:''};
    state.collectionFilters=defaultCollectionFilters(gameId);
    state.watchFilters=defaultWatchFilters(gameId);
    state.deckFilters={q:'',set_id:'',language:defaultLang,rarity:'',rarities:[],type:'',color:'',sort:'number',colors:[],types:[],costs:[],attributes:[],kinds:[],bloomLevels:[],inkwell:''};
    state.deckZone='main';
  }
  if(persist) post('/api/settings',{activeGameId:gameId});
}

// A view that finishes loading after the user has moved on must not draw over what is on screen
// by then -- neither over another view nor over a newer request for the same one. Every async
// render takes a guard first and asks it after each wait; routeTo() invalidates all of them.
let renderGeneration=0;
function renderGuard(){const mine=++renderGeneration;return ()=>mine!==renderGeneration}

function routeTo(route, data) {
  renderGeneration++;deckRenderToken++;
  hideDeckImagePreview();closeDeckAddPopup();setDeckCatalogOpen(false);clearTimeout(state.homeBannerTimer);
  state.mobileFiltersOpen={};
  if(route!=='watchlist'){state.watchSelection.clear();state.watchSelectionMode=false}
  if(route!=='collection'){state.collectionSelection.clear();state.collectionSelectionMode=false}
  state.statsUrl=null;state.statsItems=null;tileFeed?.observer?.disconnect();tileFeed=null;
  state.route=route; document.body.dataset.route=route; setNav(route); window.scrollTo({top:0,behavior:'smooth'});
  if(route==='dashboard') renderDashboard();
  if(route==='game') renderGame(data || state.game?.id);
  if(route==='game-cards') renderAllCards(data || state.game?.id);
  if(route==='set') renderSet(data || state.set?.id);
  if(route==='collection') renderCollection();
  if(route==='watchlist') renderWatchlist();
  if(route==='decks') renderDeckbuilder();
  if(route==='sheets') renderSheets();
  if(route==='settings') renderSettings();
  if(route==='admin') renderAdmin();
}

function initials(name){return name.split(/\s+/).map(x=>x[0]).slice(0,2).join('').toUpperCase()}

function mountFilterPanel(key,selectors,label='Filter'){
  const targets=selectors.map(selector=>$(selector,content)).filter(Boolean);
  if(!targets.length)return;
  const panel=document.createElement('deckledger-filter-panel');
  panel.setAttribute('filter-key',key);
  panel.setAttribute('label',label);
  panel.dataset.mobileFilterShell=key;
  if(state.mobileFiltersOpen[key])panel.setAttribute('open','');
  targets[0].before(panel);
  targets.forEach(target=>{
    target.classList.add('shared-filter-content');
    panel.contentElement.append(target);
  });
}

function statPill(items,{className='',columns=items.length,mobileColumns=Math.min(items.length,3)}={}){
  const safeColumns=Math.max(1,Math.min(8,Number(columns)||items.length||1));
  const safeMobileColumns=Math.max(1,Math.min(8,Number(mobileColumns)||Math.min(safeColumns,3)));
  const classAttribute=className?` class="${escapeHtml(className)}"`:'';
  return `<deckledger-stat-pill${classAttribute} columns="${safeColumns}" mobile-columns="${safeMobileColumns}">${items.map(item=>{
    const itemClass=item.className?` class="${escapeHtml(item.className)}"`:'';
    return `<div data-stat${itemClass}><span>${escapeHtml(item.label)}</span><b>${item.value}</b></div>`;
  }).join('')}</deckledger-stat-pill>`;
}

// Bug fix: setting overflow:hidden on body ALONE doesn't reliably lock page scroll -- confirmed
// via a same-origin test (window.scrollBy still moved window.scrollY with only body locked).
// documentElement (<html>) is the actual scrolling element in some engines/configurations, so
// both need the lock. Root cause of the mobile card-modal's fixed footer bar appearing to
// "scroll with the page": the page underneath was still scrollable, and on real mobile browsers
// that competing scroll (plus the address-bar show/hide it triggers) is what made the bar look
// unanchored, even though position:fixed itself was working correctly the whole time.
function openOverlay(id){$(`#${id}`).classList.remove('hidden');document.documentElement.style.overflow='hidden';document.body.style.overflow='hidden';if(id==='global-search-open')return}
function closeOverlay(id){$(`#${id}`).classList.add('hidden');document.documentElement.style.overflow='';document.body.style.overflow='';if(id==='card-modal'){state.modalCard=null}}

async function refreshWatchCount(){
  if(!state.activeGameId)return;
  const lists=await api(`/api/watchlists?game_id=${state.activeGameId}`);
  const count=lists.reduce((a,l)=>a+l.count,0);
  $('#watch-count').textContent=count;
  const mtBadge=$('#mt-watch-count');
  if(mtBadge){mtBadge.textContent=count;mtBadge.classList.toggle('hidden',count===0)}
}

// What a search box's text matches, as on the server (deckledger/search.py): every word somewhere
// in the fields, spaces/case/accents aside and a letter off allowed ("Smugalana PL9", "Godess");
// with | ^ $ \ [ ] * + ? { } the text as typed or read as a regular expression ("PL9|PL10").
const searchFold=text=>String(text??'').normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
const searchWords=text=>searchFold(text).match(/[\p{L}\p{N}_]+/gu)||[];
function searchWithin(word,other,limit){
  if(Math.abs(word.length-other.length)>limit)return false;
  let previous=[...Array(other.length+1).keys()];
  for(let i=1;i<=word.length;i++){
    const current=[i];
    for(let j=1;j<=other.length;j++)current.push(Math.min(previous[j]+1,current[j-1]+1,previous[j-1]+(word[i-1]!==other[j-1])));
    if(Math.min(...current)>limit)return false;
    previous=current;
  }
  return previous[other.length]<=limit;
}
function searchNearMiss(token,words){
  if(token.length<4)return false;
  const limit=token.length<8?1:2;
  return [...words,...words.slice(1).map((word,i)=>words[i]+word)].some(word=>searchWithin(token,word,limit)
    ||[token.length-1,token.length,token.length+1].some(size=>size>0&&size<word.length&&searchWithin(token,word.slice(0,size),limit)));
}
function searchMatcher(query){
  query=query.trim();
  if(/[|^$\\[\]*+?{}]/.test(query)){
    const literal=query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    let pattern;
    try{pattern=new RegExp(query.length<=200?`${literal}|(?:${query})`:literal,'i')}catch{pattern=new RegExp(literal,'i')}
    return fields=>fields.some(text=>text!=null&&pattern.test(String(text)));
  }
  const tokens=[...new Set(searchWords(query))];
  return fields=>{
    const present=fields.filter(text=>text!=null&&text!=='').map(searchWords);
    return tokens.every(token=>present.some(words=>words.join('').includes(token)||searchNearMiss(token,words)));
  };
}

async function doSearch(q){
  const wrap=$('#search-results'); if(q.trim().length<2){wrap.innerHTML='<div class="empty-search"><b>Finde jede Karte. Sofort.</b><span>Name, Nummer, Set oder Ausführung – in beliebiger Reihenfolge, z.&nbsp;B. „Smugalana PL9 1st Ed“. Alternativen mit |: PL9|PL10</span></div>';return}
  const rows=await api(`/api/search?q=${encodeURIComponent(q)}&game_id=${encodeURIComponent(state.activeGameId)}`); if(!rows.length){wrap.innerHTML='<div class="empty-search"><b>Keine Treffer</b><span>Versuche einen anderen Namen oder eine Nummer.</span></div>';return}
  const groups=Object.groupBy?Object.groupBy(rows,x=>x.game_name):rows.reduce((a,x)=>((a[x.game_name]??=[]).push(x),a),{});
  wrap.innerHTML=Object.entries(groups).map(([game,items])=>`<div class="search-group-title">${escapeHtml(game).toUpperCase()}</div>${items.map(r=>`<button class="search-result" data-id="${r.identity_id}" data-variant="${r.variant_id}">${finishThumb(r,artUrl(r.variant_id),r.canonical_name,'search-thumb')}<div><b>${escapeHtml(r.canonical_name)}</b><small>${escapeHtml(r.set_name)} · ${escapeHtml(r.collector_number)} · ${r.language} · ${escapeHtml(r.game_id==='lorcana'?lorcanaFinishLabel(r.finish,r.rarity):r.finish)}</small></div><span class="search-price">${r.price==null?"–":money(r.price)}</span></button>`).join('')}`).join('');
  $$('.search-result',wrap).forEach(el=>el.onclick=()=>{closeOverlay('search-overlay');openCard(el.dataset.id,el.dataset.variant)});
}

// Light, dark or what the system uses. The resolved choice is also kept in a cookie: the login
// page and the first paint of the app read it before any settings have loaded.
const systemDark=globalThis.matchMedia?.('(prefers-color-scheme: dark)');
const appearancePreference=()=>['light','dark','auto'].includes(state.boot?.settings?.mobileThemeAppearance)?state.boot.settings.mobileThemeAppearance:'dark';
function applyAppearance(){
  const preference=appearancePreference();
  const light=preference==='light'||(preference==='auto'&&!systemDark?.matches);
  document.body.classList.toggle('mobile-light',light);
  document.cookie=`deckledger_theme=${light?'light':'dark'};path=/;max-age=31536000;samesite=lax`;
}
systemDark?.addEventListener?.('change',()=>{if(state.boot&&appearancePreference()==='auto')applyAppearance()});
