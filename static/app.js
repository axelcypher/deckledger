const state = {
  boot: null, route: 'dashboard', game: null, set: null, cards: [],
  edit: false, zoom: 220, setZoom: 3, setType: 'all', setSort: 'type', setDirection:'desc', language: 'combined',
  filter: 'all', sort: 'number', query: '', modalCard: null, modalVariant: null, modalTab: 'collection',
  activeGameId: null, watchlistId: null, activeWatchlists: [], deckId: null, deckView: 'grid', deckZoom: 135,
  collapsedSetGroups: {},
  cardFilters: {rarity:'', rarities:[], costs:[], colors:[], inkwell:'', finish:'normal', foilMode:''},
  collectionFilters: {q:'',set_id:'',language:'all',rarity:'',finish:'',mode:'all',sort:'number'},
  watchFilters: {q:'',set_id:'',language:'all',finish:'',sort:'added'}, deckFilters:{q:'',set_id:'',language:'EN',type:'',color:'',sort:'number',colors:[],types:[],costs:[],attributes:[],kinds:[],bloomLevels:[],inkwell:''}, deckZone:'main', deckCatalogObserver:null,
  homeBanner:null, homeBannerTimer:null
};

const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const content = $('#content');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const currencyMoney = (value,currency='EUR') => new Intl.NumberFormat('de-DE',{style:'currency',currency}).format(Number(value)||0);
const money = value => currencyMoney(value,'EUR');
const price = value => value == null ? 'Kein Preis verfügbar' : money(value);
const nativePrice = variant => variant.price_native==null||!variant.price_native_currency ? null : currencyMoney(variant.price_native,variant.price_native_currency);
const maxPrice = items => { const values=items.map(x=>x.price).filter(x=>x!=null); return values.length?Math.max(...values):null; };
const deckCostLabel = (value,unpriced=0) => `${money(value)}${unpriced?` · ${unpriced}× ohne Preis`:''}`;
const date = value => value ? new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'short',year:'numeric'}).format(new Date(value)) : '–';
const releaseDate = item => {
  const values=[...new Set((item?.release_dates||[]).filter(Boolean))].sort();
  if(values.length<2)return date(values[0]||item?.release_date);
  return `${date(values[0])} – ${date(values.at(-1))}`;
};
const artUrl = (variantId, size='thumb') => `/art/${encodeURIComponent(variantId)}.svg?v=3${size==='thumb'?'&size=thumb':''}`;
function finishPresentation(variant={}){
  const finish=String(variant.finish||'').trim();
  const descriptor=`${finish} ${variant.variant_code||''} ${variant.rarity||''}`.toLowerCase();
  const gameId=String(variant.game_id||state.activeGameId||'').toLowerCase();
  const premiumNamed=/manga|enchanted|iconic|epic|signature|signed/.test(descriptor);
  const premiumCode=/(?:^|[\s_-])(our|osr|sec|sp|ur|sy)(?:$|[\s_-])/i.test(descriptor);
  if(premiumNamed||premiumCode){
    return {effect:'finish-aurora'};
  }
  if(/parallel|alternate|alt art/.test(descriptor)||Number(variant.is_parallel)===1){
    return {effect:'finish-prismatic'};
  }
  const hololiveBaseFoil=gameId==='hololive'&&/^(s|sr)$/i.test(finish);
  if(hololiveBaseFoil||/foil|silver|satin|holo|rainbow|etched|textured|gold/.test(descriptor)){
    return {effect:'finish-foil'};
  }
  return {effect:'finish-normal'};
}
function finishThumb(variant,src,alt='',className=''){
  const visual=finishPresentation(variant);
  return `<span class="card-finish-frame finish-thumb ${visual.effect} ${className}"><img loading="lazy" src="${src}" alt="${escapeHtml(alt)}"></span>`;
}
const variantName = variant => {
  const label = variant.game_id==='lorcana' ? lorcanaFinishLabel(variant.finish,variant.rarity) : variant.finish;
  return variant.edition_label ? `${label} · ${variant.edition_label}` : label;
};
const api = async (url, options={}) => {
  const response = await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  if (response.status===401) { location.href='/login'; throw new Error('Nicht angemeldet'); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen');
  return data;
};
const post = (url,data) => api(url,{method:'POST',body:JSON.stringify(data)});

async function reRenderPreservingFocus(selector,renderFn){
  const active=document.activeElement;
  const hadFocus=active&&active.matches&&active.matches(selector);
  const selStart=hadFocus?active.selectionStart:null,selEnd=hadFocus?active.selectionEnd:null;
  await renderFn();
  if(hadFocus){
    const el=$(selector);
    if(el){el.focus();if(selStart!=null)try{el.setSelectionRange(selStart,selEnd)}catch{}}
  }
}

function toast(message, actionLabel, action) {
  const node=document.createElement('div'); node.className='toast';
  node.innerHTML=`<span>${escapeHtml(message)}</span>${actionLabel?`<button>${escapeHtml(actionLabel)}</button>`:''}`;
  if(actionLabel) $('button',node).onclick=async()=>{ await action?.(); node.remove(); };
  $('#toast-stack').append(node); setTimeout(()=>node.remove(),5000);
}

function setNav(route) {
  $$('.nav-item[data-route]').forEach(el=>el.classList.toggle('active',el.dataset.route===route));
}

function setActiveGame(gameId, persist=true) {
  const previousGameId=state.activeGameId;
  const changed=previousGameId && previousGameId!==gameId;
  const isInitial=!previousGameId;
  state.activeGameId=gameId; state.game=state.boot.games.find(g=>g.id===gameId) || state.boot.games[0];
  if($('#global-game-filter')) $('#global-game-filter').value=gameId;
  state.watchlistId=null; state.deckId=null;
  if(changed||isInitial){
    // Settings shows languages[0] as each game's assumed default even before
    // the user ever touches that dropdown (it's only actually saved once they
    // do) -- falling back to the same value here instead of a generic 'all'/
    // 'combined' keeps every page consistent with what Settings implies is
    // already the default, not just what got explicitly saved.
    const defaultLang=state.boot.settings?.defaultLanguages?.[gameId]||state.game.languages[0];
    state.language=defaultLang;state.setType='all';state.setSort='type';state.setDirection='desc';state.cardFilters={rarity:'',rarities:[],costs:[],colors:[],inkwell:'',finish:'normal',foilMode:''};
    state.collectionFilters={q:'',set_id:'',language:defaultLang,rarity:'',finish:'',mode:'all',sort:'number'};
    state.watchFilters={q:'',set_id:'',language:defaultLang,finish:'',sort:'added'};
    state.deckFilters={q:'',set_id:'',language:defaultLang,type:'',color:'',sort:'number',colors:[],types:[],costs:[],attributes:[],kinds:[],bloomLevels:[],inkwell:''};
    state.deckZone='main';
  }
  if(persist) post('/api/settings',{activeGameId:gameId});
}

function routeTo(route, data) {
  hideDeckImagePreview();closeDeckAddPopup();clearTimeout(state.homeBannerTimer);
  state.route=route; setNav(route); window.scrollTo({top:0,behavior:'smooth'});
  if(route==='dashboard') renderDashboard();
  if(route==='game') renderGame(data || state.game?.id);
  if(route==='game-cards') renderAllCards(data || state.game?.id);
  if(route==='set') renderSet(data || state.set?.id);
  if(route==='collection') renderCollection();
  if(route==='watchlist') renderWatchlist();
  if(route==='decks') renderDeckbuilder();
  if(route==='settings') renderSettings();
  if(route==='admin') renderAdmin();
  $('.sidebar').classList.remove('open');
}

function initials(name){return name.split(/\s+/).map(x=>x[0]).slice(0,2).join('').toUpperCase()}
function symbol(game){return {'lorcana':'✦','one-piece':'☠','hololive':'◈'}[game]||'◆'}

const SET_GROUP_ORDER=['Booster','Decks','Promos','Quests','Sammlungen','Produkte','Zubehör'];
function setGroup(set){
  const type=(set.set_type||'').toLowerCase();
  if(/booster|expansion/.test(type))return 'Booster';
  if(/starter|\bdecks?\b/.test(type))return 'Decks';
  if(/promo/.test(type))return 'Promos';
  if(/quest/.test(type))return 'Quests';
  if(/collection/.test(type))return 'Sammlungen';
  if(/product/.test(type))return 'Produkte';
  if(/accessory/.test(type))return 'Zubehör';
  return set.set_type||'Weitere Sets';
}
function groupedSets(sets){
  const visible=state.setType==='all'?sets:sets.filter(set=>setGroup(set)===state.setType);
  const groups=visible.reduce((all,set)=>{const group=setGroup(set);(all[group]??=[]).push(set);return all},{});
  const rank=group=>{const index=SET_GROUP_ORDER.indexOf(group);return index<0?SET_GROUP_ORDER.length:index};
  return Object.entries(groups).sort(([a],[b])=>rank(a)-rank(b)||a.localeCompare(b,'de')).map(([group,items])=>[
    group,
    items.sort((a,b)=>compareSetRelease(a,b,state.setDirection))
  ]);
}
function latestRelease(set){const values=(set.release_dates||[]).filter(Boolean);return values.length?values.sort().at(-1):set.release_date||null}
function compareSetRelease(a,b,direction='desc'){
  const ad=Date.parse(latestRelease(a))||0,bd=Date.parse(latestRelease(b))||0;
  if(Boolean(ad)!==Boolean(bd))return ad?-1:1;
  const factor=direction==='desc'?-1:1;
  if(ad!==bd)return (ad<bd?-1:1)*factor;
  const code=a.code.localeCompare(b.code,'de',{numeric:true,sensitivity:'base'});
  return code*factor;
}
function sortedSets(sets){
  const visible=state.setType==='all'?sets:sets.filter(set=>setGroup(set)===state.setType);
  const sorters={
    date:(a,b)=>compareSetRelease(a,b,state.setDirection),
    code:(a,b)=>a.code.localeCompare(b.code,'de',{numeric:true}),
    name:(a,b)=>a.name.localeCompare(b.name,'de'),
    value:(a,b)=>a.value-b.value,
    completion:(a,b)=>a.base_completion-b.base_completion,
  };
  const sorter=sorters[state.setSort]||sorters.date;
  return [...visible].sort((a,b)=>['type','date'].includes(state.setSort)?sorter(a,b):sorter(a,b)*(state.setDirection==='desc'?-1:1));
}

function renderDashboard(){
  const games=state.boot.games;
  const totalValue=games.reduce((a,g)=>a+g.value,0), copies=games.reduce((a,g)=>a+g.copies,0), unique=games.reduce((a,g)=>a+g.unique_cards,0);
  content.innerHTML=`<div class="dashboard-shell">
    <section class="dashboard-hero">
      <div class="hero-top">
        <div class="hero-copy"><h1>Willkommen zurück, ${escapeHtml(state.boot.user.display_name.split(' ')[0])}.</h1><p>Deine Sammlung wächst. Hier siehst du ihren aktuellen Stand über alle Spiele hinweg.</p></div>
        <div class="hero-summary"><div><b>${money(totalValue)}</b><span>Gesamtwert</span></div><div><b>${copies}</b><span>Karten</span></div><div><b>${unique}</b><span>Varianten</span></div></div>
      </div>
      <section id="home-banner" class="home-banner hidden">
        <div class="home-banner-head"><span class="eyebrow" id="home-banner-label">NEU &amp; ANGESAGT</span></div>
        <div class="home-banner-viewport"><div class="home-banner-track" id="home-banner-track"></div></div>
      </section>
    </section>
    <section class="game-grid">${games.map(g=>`
      <button class="game-tile" data-game="${g.id}" style="--accent:${g.accent}">
        <div class="game-visual"><img class="game-logo" src="/game-logo/${g.id}" alt="${escapeHtml(g.name)} Logo"></div>
        <div class="game-info">
          <div class="game-main-stat"><b>${money(g.value)}</b><span>Sammlungswert</span></div>
          <div class="progress-track"><span style="width:${g.completion}%"></span></div>
          <div class="game-stats"><span><b>${g.copies}</b> Karten</span><span><b>${g.unique_cards}</b> Varianten · ${g.completion}%</span></div>
        </div>
      </button>`).join('')}</section>
  </div>`;
  $$('[data-game]',content).forEach(el=>el.onclick=()=>{setActiveGame(el.dataset.game);routeTo('game',el.dataset.game)});
  const bannerTrack=$('#home-banner-track');
  bannerTrack.addEventListener('mouseenter',()=>clearTimeout(state.homeBannerTimer));
  bannerTrack.addEventListener('mouseleave',()=>{
    if(!state.homeBanner)return;
    const duration=Math.max(14,state.homeBanner.slides[state.homeBanner.index].cards.length*1.5);
    state.homeBannerTimer=setTimeout(advanceBanner,duration*1000);
  });
  bannerTrack.addEventListener('click',e=>{const card=e.target.closest('.banner-card');if(card)openCard(card.dataset.identity,card.dataset.variant)});
  loadHomeBanner();
}

function bannerCard(c,accent){
  return `<div class="banner-card" style="--accent:${accent}" data-identity="${c.identity_id}" data-variant="${c.variant_id}"><div class="banner-card-art card-finish-frame ${finishPresentation(c).effect}"><img loading="eager" decoding="async" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}"></div></div>`;
}

function preloadBannerImages(slides){
  for(const slide of slides)for(const c of slide.cards){const img=new Image();img.src=artUrl(c.variant_id)}
}

function paintBannerSlide(index){
  const slide=state.homeBanner.slides[index],track=$('#home-banner-track');
  if(!track)return 0;
  const cardsHtml=slide.cards.map(c=>bannerCard(c,slide.accent)).join('');
  track.innerHTML=cardsHtml+cardsHtml;
  const duration=Math.max(14,slide.cards.length*1.5);
  track.style.setProperty('--marquee-duration',`${duration}s`);
  // Restarting a CSS animation after changing its duration needs a real
  // reflow between clearing and reapplying it. A single synchronous
  // offsetWidth read is the classic trick but is unreliable in some Firefox
  // versions specifically for this case (the animation silently never
  // restarts on some loads) -- a double rAF forces the browser through an
  // actual paint in between and restarts consistently across engines.
  track.style.animation='none';
  requestAnimationFrame(()=>requestAnimationFrame(()=>{track.style.animation=''}));
  const label=$('#home-banner-label');
  if(label)label.textContent=`${slide.mode==='value'?'Wertvollste Karten':'Neueste Karten'} deiner Sammlung`;
  return duration;
}

async function advanceBanner(){
  const banner=$('#home-banner');
  if(!banner||!state.homeBanner)return;
  banner.classList.add('fading');
  await new Promise(r=>setTimeout(r,420));
  if(!$('#home-banner'))return;
  state.homeBanner.index=(state.homeBanner.index+1)%state.homeBanner.slides.length;
  const duration=paintBannerSlide(state.homeBanner.index);
  await new Promise(r=>requestAnimationFrame(r));
  banner.classList.remove('fading');
  state.homeBannerTimer=setTimeout(advanceBanner,duration*1000);
}

async function loadHomeBanner(){
  clearTimeout(state.homeBannerTimer);
  try{
    const data=await api('/api/home-banner');
    if(!data.slides.length||!$('#home-banner'))return;
    state.homeBanner={slides:data.slides,index:0};
    preloadBannerImages(data.slides);
    const duration=paintBannerSlide(0);
    $('#home-banner').classList.remove('hidden');
    state.homeBannerTimer=setTimeout(advanceBanner,duration*1000);
  }catch(error){/* banner is decorative; fail silently */}
}

function renderSettings(){
  const games=state.boot.games,settings=state.boot.settings||{},defaultLanguages=settings.defaultLanguages||{},banner=settings.homeBanner||{},modes=banner.modes||['newest'],excludedGames=banner.excludedGames||[];
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">KONTO</span><h1>Einstellungen</h1><p>Passe DeckLedger an deine Sammlung an.</p></div></div>
    <section class="settings-section">
      <h2>Standardsprache je Spiel</h2>
      <p class="muted">Wird als Vorauswahl in Sammlung, Deckbuilder und Import verwendet.</p>
      <div class="settings-grid">${games.map(g=>`<label class="settings-field"><span>${escapeHtml(g.name)}</span><select data-lang-game="${g.id}" class="select-control">${g.languages.map(l=>`<option value="${l}" ${(defaultLanguages[g.id]||g.languages[0])===l?'selected':''}>${l}</option>`).join('')}</select></label>`).join('')}</div>
    </section>
    <section class="settings-section">
      <h2>Startseiten-Banner</h2>
      <p class="muted">Wähle, welche Kartenlisten im "Neu &amp; Angesagt"-Banner rotieren.</p>
      <div class="settings-checklist">
        <label class="checkbox-row"><input type="checkbox" data-banner-mode="newest" ${modes.includes('newest')?'checked':''}> Die 20 neuesten Karten</label>
        <label class="checkbox-row"><input type="checkbox" data-banner-mode="value" ${modes.includes('value')?'checked':''}> Die 20 wertvollsten Karten</label>
      </div>
      <p class="muted settings-subhead">TCGs ausschließen</p>
      <div class="settings-checklist">${games.map(g=>`<label class="checkbox-row"><input type="checkbox" data-banner-exclude="${g.id}" ${excludedGames.includes(g.id)?'checked':''}> ${escapeHtml(g.name)}</label>`).join('')}</div>
      <p class="muted settings-hint">Standardmäßig rotiert das Banner endlos zwischen allen nicht ausgeschlossenen TCGs. Sind mehrere Kartenlisten aktiv, wechselt es zusätzlich zwischen ihnen.</p>
    </section>`;
  $$('[data-lang-game]',content).forEach(select=>select.onchange=async e=>{
    const updated={...(state.boot.settings.defaultLanguages||{}),[select.dataset.langGame]:e.target.value};
    await post('/api/settings',{defaultLanguages:updated});
    state.boot.settings.defaultLanguages=updated;
    toast('Standardsprache gespeichert');
  });
  $$('[data-banner-mode]',content).forEach(cb=>cb.onchange=async e=>{
    const current=state.boot.settings.homeBanner||{};
    let next=[...(current.modes||['newest'])];
    if(e.target.checked){if(!next.includes(cb.dataset.bannerMode))next.push(cb.dataset.bannerMode)}
    else{next=next.filter(m=>m!==cb.dataset.bannerMode);if(!next.length){e.target.checked=true;toast('Mindestens eine Kartenliste muss aktiv sein.');return}}
    const updated={...current,modes:next};
    await post('/api/settings',{homeBanner:updated});
    state.boot.settings.homeBanner=updated;
    toast('Banner-Einstellung gespeichert');
  });
  $$('[data-banner-exclude]',content).forEach(cb=>cb.onchange=async e=>{
    const current=state.boot.settings.homeBanner||{};
    let next=[...(current.excludedGames||[])];
    if(e.target.checked){if(!next.includes(cb.dataset.bannerExclude))next.push(cb.dataset.bannerExclude)}
    else{next=next.filter(id=>id!==cb.dataset.bannerExclude)}
    const updated={...current,excludedGames:next};
    await post('/api/settings',{homeBanner:updated});
    state.boot.settings.homeBanner=updated;
    toast('Banner-Einstellung gespeichert');
  });
}

const DECK_RULESET_OPTIONS=[['','Keine'],['lorcana-standard','Lorcana Standard'],['one-piece-standard','One Piece Standard'],['hololive-standard','hololive Standard']];
const PRICE_METHOD_OPTIONS=[['','Keine'],['cardmarket','Cardmarket'],['tcgcsv','TCGCSV / TCGplayer'],['yuyutei','Yuyutei']];

async function renderAdmin(){
  content.innerHTML='<div class="page-loader"><span></span><p>Admin-Bereich wird geladen …</p></div>';
  const [games,providers]=await Promise.all([api('/api/admin/games'),api('/api/admin/providers')]);
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">VERWALTUNG</span><h1>Admin</h1><p>TCGs, Katalog-Provider und Zuordnungen verwalten.</p></div></div>
    <section class="settings-section">
      <h2>TCGs</h2>
      <div class="admin-table">${games.map(g=>`<div class="admin-row" data-game-row="${g.id}">
        <div class="admin-row-head"><b>${escapeHtml(g.name)}</b><span class="muted">${g.id} · ${g.languages.join('/')}</span></div>
        <label>Preisquelle<select class="select-control" data-game-field="price_method" data-game="${g.id}">${PRICE_METHOD_OPTIONS.map(([v,l])=>`<option value="${v}" ${(g.price_method||'')===v?'selected':''}>${l}</option>`).join('')}</select></label>
        <label>Cardmarket-ID<input type="number" min="1" data-game-field="cardmarket_game_id" data-game="${g.id}" value="${g.cardmarket_game_id||''}" placeholder="z.B. 19" title="Numerische Spiel-ID aus der Cardmarket-URL, z.B. cardmarket.com/.../Games/19 → 19"></label>
        <label>Deck-Ruleset<select class="select-control" data-game-field="deck_ruleset" data-game="${g.id}">${DECK_RULESET_OPTIONS.map(([v,l])=>`<option value="${v}" ${(g.deck_ruleset||'')===v?'selected':''}>${l}</option>`).join('')}</select></label>
        <label>Card-Back<input type="file" accept="image/jpeg" data-card-back="${g.id}"></label>
        <button class="icon-button" data-delete-game="${g.id}" data-game-name="${escapeHtml(g.name)}" title="TCG löschen">✕</button>
      </div>`).join('')}</div>
      <details class="admin-add"><summary>+ Neues TCG anlegen</summary>
        <div class="admin-form">
          <label>Name<input id="admin-new-game-name" placeholder="z.B. Mein neues TCG"></label>
          <label>Kurzname<input id="admin-new-game-short" placeholder="z.B. MTCG"></label>
          <label>Sprachen (Komma-getrennt)<input id="admin-new-game-langs" placeholder="EN,DE" value="EN"></label>
          <label>Akzentfarbe<input id="admin-new-game-accent" type="color" value="#6366f1"></label>
          <button class="primary-button" id="admin-create-game">TCG anlegen</button>
        </div>
      </details>
    </section>
    <section class="settings-section">
      <h2>Katalog-Provider</h2>
      <p class="muted">Jeder Provider ist Python-Code, der bei Ausführung ein <code>fetch_catalog() -&gt; dict</code> definiert und in einem eigenen Prozess mit Zeitlimit läuft. Code läuft mit vollem Server-Zugriff — nur einfügen, dem du vertraust.</p>
      <div class="admin-table">${providers.map(p=>`<div class="admin-row" data-provider-row="${p.id}">
        <div class="admin-row-head"><b>${escapeHtml(p.label)}</b><span class="muted">${p.game_id} · Timeout ${p.timeout_seconds}s</span></div>
        <div class="admin-status"><span class="admin-status-badge status-${p.last_status||'none'}">${p.last_status||'noch nicht gelaufen'}</span><span class="muted">${p.last_run_at?date(p.last_run_at):'–'}</span></div>
        ${p.last_error?`<div class="admin-error">${escapeHtml(p.last_error)}</div>`:''}
        <div class="admin-row-actions">
          <button class="secondary-button" data-run-provider="${p.id}">Jetzt importieren</button>
          <button class="secondary-button" data-edit-provider="${p.id}">Code bearbeiten</button>
          <button class="icon-button" data-delete-provider="${p.id}" title="Löschen">✕</button>
        </div>
        <div class="admin-config-editor hidden" data-config-editor="${p.id}">
          <div class="admin-form-grid">
            <label>Min. Sets (Sicherheitsgrenze)<input type="number" min="0" data-provider-field="minimum_sets" value="${p.minimum_sets}"></label>
            <label>Min. Karten (Sicherheitsgrenze)<input type="number" min="0" data-provider-field="minimum_cards" value="${p.minimum_cards}"></label>
            <label>Zeitlimit (Sekunden)<input type="number" min="10" data-provider-field="timeout_seconds" value="${p.timeout_seconds}"></label>
          </div>
          <textarea class="admin-code-editor" data-provider-code rows="20" spellcheck="false">${escapeHtml(p.code||'')}</textarea>
          <button class="primary-button" data-save-code="${p.id}">Code speichern</button>
        </div>
      </div>`).join('')}</div>
      <details class="admin-add"><summary>+ Neuen Provider anlegen</summary>
        <div class="admin-form">
          <label>TCG<select id="admin-new-provider-game" class="select-control">${games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('')}</select></label>
          <label>Label<input id="admin-new-provider-label" placeholder="z.B. Mein TCG"></label>
          <div class="admin-form-grid">
            <label>Min. Sets (Sicherheitsgrenze)<input type="number" min="0" id="admin-new-provider-minsets" value="0"></label>
            <label>Min. Karten (Sicherheitsgrenze)<input type="number" min="0" id="admin-new-provider-mincards" value="0"></label>
            <label>Zeitlimit (Sekunden)<input type="number" min="10" id="admin-new-provider-timeout" value="300"></label>
          </div>
          <label>Code<textarea class="admin-code-editor" id="admin-new-provider-code" rows="16" spellcheck="false" placeholder="from catalog_provider_contract import empty_catalog, fetch, put_identity, slug

def fetch_catalog() -&gt; dict:
    catalog = empty_catalog()
    # ... Sets/Karten/Printings/Varianten in catalog eintragen ...
    return catalog"></textarea></label>
          <button class="primary-button" id="admin-create-provider">Provider anlegen</button>
        </div>
      </details>
    </section>
    <section class="settings-section">
      <h2>Manuelle Karten</h2>
      <p class="muted">Direkt eingetragene Karten – sofort wirksam, werden nie durch einen Sync-Lauf entfernt oder überschrieben.</p>
      <label>TCG<select id="admin-manual-game" class="select-control">${games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('')}</select></label>
      <div class="admin-table" id="admin-manual-cards-list"></div>
      <details class="admin-add"><summary>+ Karte manuell hinzufügen</summary>
        <div class="admin-form">
          <label>Vorhandenes Set<select id="admin-manual-set" class="select-control"><option value="">– neues Set unten anlegen –</option></select></label>
          <label>Neuer Set-Code<input id="admin-manual-new-set-code" placeholder="nur falls kein Set gewählt"></label>
          <label>Neuer Set-Name<input id="admin-manual-new-set-name"></label>
          <label>Kartenname<input id="admin-manual-name"></label>
          <label>Kartenschlüssel (optional, sonst aus Name)<input id="admin-manual-key"></label>
          <label>Regeltext<textarea id="admin-manual-rules" rows="3"></textarea></label>
          <label>Kartentyp<input id="admin-manual-type" placeholder="z.B. Character"></label>
          <label>Sammlernummer<input id="admin-manual-number"></label>
          <label>Sprache<input id="admin-manual-language" value="EN"></label>
          <label>Seltenheit<input id="admin-manual-rarity"></label>
          <label>Finish<input id="admin-manual-finish" value="Normal" placeholder="Normal, Foil, ..."></label>
          <label class="checkbox-row"><input type="checkbox" id="admin-manual-parallel"> Parallel / Alternative Art</label>
          <label>Bild-URL<input id="admin-manual-image" placeholder="https://..."></label>
          <button class="primary-button" id="admin-manual-create">Karte speichern</button>
        </div>
      </details>
    </section>`;
  $$('[data-game-field]',content).forEach(select=>select.onchange=async e=>{
    await api(`/api/admin/games/${select.dataset.game}`,{method:'PATCH',body:JSON.stringify({[select.dataset.gameField]:e.target.value||null})});
    toast('Gespeichert');
  });
  $$('[data-card-back]',content).forEach(input=>input.onchange=async e=>{
    const file=e.target.files[0]; if(!file)return;
    const form=new FormData(); form.append('file',file);
    const response=await fetch(`/api/admin/games/${input.dataset.cardBack}/card-back`,{method:'POST',body:form});
    if(response.ok)toast('Card-Back hochgeladen'); else toast('Upload fehlgeschlagen');
  });
  $$('[data-delete-game]',content).forEach(button=>button.onclick=async()=>{
    const name=button.dataset.gameName;
    if(!confirm(`"${name}" wirklich vollständig löschen?\n\nDas entfernt unwiderruflich: alle Sets, Karten, Sammlungen, Decks, Watchlists, Preisdaten und Katalog-Provider dieses TCGs für ALLE Nutzer.\n\nZum Bestätigen OK klicken.`))return;
    try{await api(`/api/admin/games/${button.dataset.deleteGame}`,{method:'DELETE'});toast('TCG gelöscht');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $('#admin-create-game').onclick=async()=>{
    const name=$('#admin-new-game-name').value.trim();
    if(!name){toast('Name ist erforderlich');return}
    const languages=$('#admin-new-game-langs').value.split(',').map(x=>x.trim()).filter(Boolean);
    try{
      await post('/api/admin/games',{name,short_name:$('#admin-new-game-short').value.trim()||name,languages,accent:$('#admin-new-game-accent').value});
      toast('TCG angelegt'); renderAdmin();
    }catch(error){toast(error.message)}
  };
  $$('[data-run-provider]',content).forEach(button=>button.onclick=async()=>{
    button.disabled=true; button.textContent='Läuft …';
    try{
      const result=await post(`/api/admin/providers/${button.dataset.runProvider}/run`,{});
      toast(result.status==='ok'?'Import erfolgreich':`Import fehlgeschlagen: ${result.error||'unbekannter Fehler'}`);
    }catch(error){toast(error.message)}
    renderAdmin();
  });
  $$('[data-edit-provider]',content).forEach(button=>button.onclick=()=>{
    $(`[data-config-editor="${button.dataset.editProvider}"]`,content).classList.toggle('hidden');
  });
  $$('[data-save-code]',content).forEach(button=>button.onclick=async()=>{
    const editor=$(`[data-config-editor="${button.dataset.saveCode}"]`,content);
    const payload={
      code:$('[data-provider-code]',editor).value,
      minimum_sets:Number($('[data-provider-field="minimum_sets"]',editor).value)||0,
      minimum_cards:Number($('[data-provider-field="minimum_cards"]',editor).value)||0,
      timeout_seconds:Number($('[data-provider-field="timeout_seconds"]',editor).value)||300,
    };
    try{await api(`/api/admin/providers/${button.dataset.saveCode}`,{method:'PATCH',body:JSON.stringify(payload)});toast('Code gespeichert');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $$('[data-delete-provider]',content).forEach(button=>button.onclick=async()=>{
    await api(`/api/admin/providers/${button.dataset.deleteProvider}`,{method:'DELETE'});
    toast('Provider gelöscht'); renderAdmin();
  });
  $('#admin-create-provider').onclick=async()=>{
    const payload={
      game_id:$('#admin-new-provider-game').value, label:$('#admin-new-provider-label').value.trim()||undefined,
      code:$('#admin-new-provider-code').value,
      minimum_sets:Number($('#admin-new-provider-minsets').value)||0,
      minimum_cards:Number($('#admin-new-provider-mincards').value)||0,
      timeout_seconds:Number($('#admin-new-provider-timeout').value)||300,
    };
    try{
      await post('/api/admin/providers',payload);
      toast('Provider angelegt'); renderAdmin();
    }catch(error){toast(error.message)}
  };
  $('#admin-manual-game').onchange=e=>loadManualCardsSection(e.target.value);
  $('#admin-manual-create').onclick=async()=>{
    const name=$('#admin-manual-name').value.trim();
    if(!name){toast('Kartenname ist erforderlich');return}
    const gameId=$('#admin-manual-game').value;
    const payload={
      canonical_name:name, key:$('#admin-manual-key').value.trim()||undefined,
      set_id:$('#admin-manual-set').value||undefined,
      new_set_code:$('#admin-manual-new-set-code').value.trim()||undefined,
      new_set_name:$('#admin-manual-new-set-name').value.trim()||undefined,
      rules_text:$('#admin-manual-rules').value.trim(), card_type:$('#admin-manual-type').value.trim()||undefined,
      collector_number:$('#admin-manual-number').value.trim()||undefined, language:$('#admin-manual-language').value.trim()||'EN',
      rarity:$('#admin-manual-rarity').value.trim()||undefined, finish:$('#admin-manual-finish').value.trim()||'Normal',
      is_parallel:$('#admin-manual-parallel').checked, image_url:$('#admin-manual-image').value.trim()||undefined,
    };
    try{
      await post(`/api/admin/games/${gameId}/manual-cards`,payload);
      toast('Karte gespeichert');
      $('#admin-manual-name').value='';$('#admin-manual-key').value='';$('#admin-manual-rules').value='';$('#admin-manual-number').value='';$('#admin-manual-rarity').value='';$('#admin-manual-image').value='';$('#admin-manual-parallel').checked=false;
      loadManualCardsSection(gameId);
    }catch(error){toast(error.message)}
  };
  loadManualCardsSection(games[0]?.id);
}

async function loadManualCardsSection(gameId){
  if(!gameId)return;
  $('#admin-manual-game').value=gameId;
  const setSelect=$('#admin-manual-set');
  const sets=await api(`/api/games/${gameId}/sets`);
  setSelect.innerHTML=`<option value="">– neues Set unten anlegen –</option>${sets.map(s=>`<option value="${s.id}">${escapeHtml(s.code)} · ${escapeHtml(s.name)}</option>`).join('')}`;
  await renderManualCards(gameId);
}

async function renderManualCards(gameId){
  const list=$('#admin-manual-cards-list');
  const cards=await api(`/api/admin/games/${gameId}/manual-cards`);
  list.innerHTML=cards.length?cards.map(c=>`<div class="admin-row" data-manual-card="${c.id}">
      <div class="admin-row-head"><b>${escapeHtml(c.canonical_name)}</b><span class="muted">${escapeHtml(c.id)}</span></div>
      <div class="muted">${c.printings.map(p=>`${escapeHtml(p.collector_number)} · ${escapeHtml(p.language)} · ${escapeHtml(p.rarity)} (${p.variants.map(v=>escapeHtml(gameId==='lorcana'?lorcanaFinishLabel(v.finish,p.rarity):v.finish)).join(', ')})`).join(' / ')||'keine Printings'}</div>
      <button class="icon-button" data-delete-manual-card="${c.id}" title="Löschen">✕</button>
    </div>`).join(''):'<p class="muted">Noch keine manuellen Karten für dieses TCG.</p>';
  $$('[data-delete-manual-card]',list).forEach(button=>button.onclick=async()=>{
    await api(`/api/admin/games/${gameId}/manual-cards/${button.dataset.deleteManualCard}`,{method:'DELETE'});
    toast('Karte gelöscht'); renderManualCards(gameId);
  });
}

async function renderGame(gameId){
  content.innerHTML='<div class="page-loader"><span></span><p>Sets werden geladen …</p></div>';
  setActiveGame(gameId,false); const game=state.boot.games.find(g=>g.id===gameId); state.game=game;
  const sets=await api(`/api/games/${gameId}/sets`); state.game.sets=sets;
  const completion=sets.length?Math.round(sets.reduce((a,s)=>a+s.base_completion,0)/sets.length):0;
  const groups=groupedSets(sets), flatSets=sortedSets(sets), availableGroups=[...new Set(sets.map(setGroup))].sort((a,b)=>{const ai=SET_GROUP_ORDER.indexOf(a),bi=SET_GROUP_ORDER.indexOf(b);return (ai<0?99:ai)-(bi<0?99:bi)||a.localeCompare(b,'de')});
  content.innerHTML=`
    <div class="breadcrumbs"><button data-route-back>Übersicht</button><span>›</span><b>${escapeHtml(game.short_name)}</b></div>
    <section class="game-banner" style="--accent:${game.accent}"><span class="game-banner-symbol"><img src="/game-logo/${game.id}" alt=""></span><div><h1>${escapeHtml(game.name)}</h1><p>${sets.length} Sets und Produkte · ${game.languages.join(' & ')}</p></div><div class="banner-value"><b>${money(game.value)}</b><span>SAMMLUNGSWERT · ${completion}% Ø FORTSCHRITT</span></div></section>
    <div class="toolbar set-overview-toolbar"><h2>Sets & Veröffentlichungen</h2><div class="toolbar-spacer"></div><button class="secondary-button all-cards-button" id="show-all-cards">Alle Karten</button><select id="set-type-filter" class="select-control" aria-label="Nach Set-Art filtern"><option value="all">Alle Set-Arten</option>${availableGroups.map(group=>`<option value="${escapeHtml(group)}" ${state.setType===group?'selected':''}>${escapeHtml(group)}</option>`).join('')}</select><select id="set-sort" class="select-control" aria-label="Sets sortieren"><option value="type">Art · gruppiert</option><option value="date">Erscheinungsdatum</option><option value="code">Setcode</option><option value="name">Name</option><option value="value">Sammlungswert</option><option value="completion">Fortschritt</option></select><select id="set-direction" class="select-control" aria-label="Sortierreihenfolge"><option value="desc">Neu → Alt · Nr. ↓</option><option value="asc">Alt → Neu · Nr. ↑</option></select><div class="zoom-control"><span>−</span><input id="set-zoom" type="range" min="2" max="4" value="${state.setZoom}"><span>＋</span></div></div>
    <div class="set-overview-groups">${state.setSort==='type'?(groups.length?groups.map(([group,items])=>setGroupSection(gameId,group,items)).join(''):'<div class="empty-state"><b>Keine Sets dieser Art</b><span>Wähle eine andere Set-Art.</span></div>'):(flatSets.length?`<div class="set-grid" style="--set-cols:${state.setZoom}">${flatSets.map(s=>setTile(s)).join('')}</div>`:'<div class="empty-state"><b>Keine Sets dieser Art</b><span>Wähle eine andere Set-Art.</span></div>')}</div>`;
  $('#set-sort').value=state.setSort;
  $('#set-direction').value=state.setDirection;
  $('[data-route-back]').onclick=()=>routeTo('dashboard');
  $('#show-all-cards').onclick=()=>routeTo('game-cards',gameId);
  $$('.set-card',content).forEach(el=>el.onclick=()=>routeTo('set',el.dataset.set));
  $$('[data-set-group-toggle]',content).forEach(button=>button.onclick=()=>{
    const section=button.closest('.set-group'),group=section.dataset.setGroup,key=`${gameId}:${group}`;
    const collapsed=!section.classList.contains('collapsed');
    state.collapsedSetGroups[key]=collapsed;section.classList.toggle('collapsed',collapsed);
    button.setAttribute('aria-expanded',String(!collapsed));$('.set-group-body',section).hidden=collapsed;
  });
  $('#set-type-filter').onchange=e=>{state.setType=e.target.value;renderGame(gameId)};
  $('#set-sort').onchange=e=>{state.setSort=e.target.value;renderGame(gameId)};
  $('#set-direction').onchange=e=>{state.setDirection=e.target.value;renderGame(gameId)};
  $('#set-zoom').oninput=e=>{state.setZoom=e.target.value;$$('.set-grid',content).forEach(grid=>grid.style.setProperty('--set-cols',state.setZoom));post('/api/settings',{setZoom:Number(state.setZoom)});};
}

function setGroupSection(gameId,group,items){
  const collapsed=Boolean(state.collapsedSetGroups[`${gameId}:${group}`]);
  return `<section class="set-group ${collapsed?'collapsed':''}" data-set-group="${escapeHtml(group)}"><button class="set-group-head" data-set-group-toggle aria-expanded="${!collapsed}"><span class="set-group-chevron">⌄</span><h3>${escapeHtml(group)}</h3><span>${items.length} ${items.length===1?'Set':'Sets'}</span></button><div class="set-group-body" ${collapsed?'hidden':''}><div class="set-grid" style="--set-cols:${state.setZoom}">${items.map(s=>setTile(s)).join('')}</div></div></section>`;
}

function setTile(s){
 return `<article class="set-card" data-set="${s.id}" style="--accent:${s.accent}"><div class="set-art"><img class="set-brand-logo set-brand-logo-${s.game_id}" loading="lazy" src="/set-logo/${encodeURIComponent(s.id)}?v=${encodeURIComponent(s.visual_version||'provider-v1')}" alt="${escapeHtml(s.name)} Logo"><span class="set-code">${escapeHtml(s.code)}</span><span class="set-kind">${escapeHtml(s.set_type)}</span></div><div class="set-card-info"><h3>${escapeHtml(s.name)}</h3><div class="set-meta">${releaseDate(s)} · ${s.printed_card_count ?? '–'} nummeriert</div><div class="badges">${s.classifications.map(c=>`<span class="badge">${escapeHtml(c)}</span>`).join('')}</div><div class="set-completion"><span>BASE<b>${s.base_completion}%</b></span><span>FOIL / PARALLEL<b>${s.foil_completion}%</b></span><span>MASTER<b>${s.master_completion}%</b></span><span>PLAYSET<b>${s.playset_completion}%</b></span><span style="margin-left:auto">WERT<b>${money(s.value)}</b></span></div></div></article>`;
}

async function renderSet(setId, preserve=false){
  if(!preserve) content.innerHTML='<div class="page-loader"><span></span><p>Kartenkatalog wird geladen …</p></div>';
  const f=state.cardFilters;
  const params=new URLSearchParams({language:state.language,mode:state.filter,sort:state.sort,q:state.query,rarity:f.rarity,foil:f.foilMode,finish:f.finish,rarities:f.rarities.join(','),costs:f.costs.join(','),colors:f.colors.join(','),inkwell:f.inkwell});
  const data=await api(`/api/sets/${setId}/cards?${params}`); state.set=data.set; state.cards=data.cards;
  const s=data.set, st=data.stats; const game=state.boot.games.find(g=>g.id===s.game_id); state.game=game;
  const availableSets=game.sets||await api(`/api/games/${game.id}/sets`);game.sets=availableSets;
  const setOptions=[...availableSets].sort((a,b)=>compareSetRelease(a,b,'desc'));
  const isLorcana=game.id==='lorcana';
  const foilDisplayActive=isLorcana&&f.finish==='foil';
  content.innerHTML=`
    <section class="set-compact-head">
      <div class="set-compact-title"><div class="breadcrumbs"><button class="breadcrumb-back" id="cards-back" title="Zurück zur Setübersicht" aria-label="Zurück zur Setübersicht">←</button><button data-dashboard>Übersicht</button><span>›</span><button data-game-back>${escapeHtml(game.short_name)}</button></div><h1><span>${escapeHtml(s.code)}</span>${escapeHtml(s.name)} <small>${releaseDate(s)} · ${s.printed_card_count} Karten</small></h1></div>
      <div class="compact-badges">${s.classifications.map(c=>`<span class="badge">${escapeHtml(c)}</span>`).join('')}</div>
      <div class="compact-stats"><div class="compact-stat"><span>Base</span><b>${st.base}%</b></div><div class="compact-stat"><span>Foil</span><b>${st.foil}%</b></div><div class="compact-stat"><span>Master</span><b>${st.master}%</b></div><div class="compact-stat"><span>Playset</span><b>${st.playset}%</b></div><div class="compact-stat"><span>Besitz / Fehlt</span><b>${st.owned} / ${st.missing}</b></div><div class="compact-stat value"><span>Setwert</span><b>${money(st.value)}</b></div></div>
    </section>
    <div class="card-toolbar-sticky">
      <div class="card-toolbar">
        ${isLorcana?'':`<select id="rarity-filter" class="select-control"><option value="">Alle Seltenheiten</option>${data.rarities.map(r=>`<option value="${escapeHtml(r)}" ${f.rarity===r?'selected':''}>${escapeHtml(r)}</option>`).join('')}</select>`}
        <div class="toolbar-spacer"></div><div class="zoom-control"><span>−</span><input id="card-zoom" type="range" min="110" max="320" value="${state.zoom}"><span>＋</span></div>
      </div>
      <div class="op-catalog-filterbar">
        ${setSwitcherPopup('set',setOptions,s.id,`${s.code} ${setAbbreviation(s.name)}`)}
        ${isLorcana?lorcanaCardFilterBar(f,'set'):''}
        <div class="toolbar-filter-anchor card-filter-end">
          <button type="button" class="secondary-button" data-filter-toggle="set-view-popup" aria-expanded="false">Ansicht ▾</button>
          <div class="toolbar-filter-popup hidden" id="set-view-popup">
            <label>Sprache<select id="language-filter" class="select-control"><option value="combined">Sprachen kombiniert</option>${s.languages.map(l=>`<option value="${l}" ${state.language===l?'selected':''}>${l}</option>`).join('')}</select></label>
            <label>Sortierung<select id="sort-filter" class="select-control"><option value="number">Nr. · Modulsortierung</option><option value="name">Name A–Z</option><option value="rarity">Seltenheit</option><option value="value">Marktwert</option><option value="quantity">Menge</option><option value="missing">Fehlend zuerst</option></select></label>
            ${isLorcana?lorcanaAnsichtExtras(f):''}
          </div>
        </div>
      </div>
    </div>
    <section class="card-grid" style="--card-size:${state.zoom}px">${data.cards.length?data.cards.map(card=>cardTile(card,foilDisplayActive)).join(''):'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe Suche oder Filter an.</span></div>'}</section>`;
  $('#sort-filter').value=state.sort;
  $('[data-dashboard]').onclick=()=>routeTo('dashboard'); $('[data-game-back]').onclick=()=>routeTo('game',game.id);
  $('#cards-back').onclick=()=>routeTo('game',game.id);
  $$('[data-set-switch]').forEach(b=>b.onclick=()=>{const val=b.dataset.setSwitch;val==='__all__'?routeTo('game-cards',game.id):routeTo('set',val)});
  bindCardEvents();
  $('#language-filter').onchange=e=>{state.language=e.target.value;f.rarity='';renderSet(setId,true)};
  $('#rarity-filter')?.addEventListener('change',e=>{f.rarity=e.target.value;renderSet(setId,true)});
  $('#sort-filter').onchange=e=>{state.sort=e.target.value;renderSet(setId,true);post('/api/settings',{[`sort_${game.id}`]:state.sort})};
  $$('[data-card-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardFilter,value=b.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(x=>x!==value):[...current,value];renderSet(setId,true)});
  $$('[data-card-single-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardSingleFilter,value=b.dataset.value;f[key]=f[key]===value?'':value;renderSet(setId,true)});
  $$('[data-card-mode]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardMode,value=b.dataset.value;f[key]=value;renderSet(setId,true)});
  $('#card-zoom').oninput=e=>{state.zoom=e.target.value;$('.card-grid').style.setProperty('--card-size',`${state.zoom}px`);post('/api/settings',{[`zoom_${game.id}`]:Number(state.zoom)})};
}

async function renderAllCards(gameId,preserve=false){
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Alle Karten werden zusammengestellt …</p></div>';
  setActiveGame(gameId,false);
  const f=state.cardFilters;
  const game=state.boot.games.find(item=>item.id===gameId),params=new URLSearchParams({language:state.language,mode:state.filter,sort:state.sort,set_order:state.setDirection,q:state.query,rarity:f.rarity,foil:f.foilMode,finish:f.finish,rarities:f.rarities.join(','),costs:f.costs.join(','),colors:f.colors.join(','),inkwell:f.inkwell});
  const [data,availableSets]=await Promise.all([api(`/api/games/${gameId}/cards?${params}`),game.sets?Promise.resolve(game.sets):api(`/api/games/${gameId}/sets`)]);game.sets=availableSets;state.game=game;state.cards=data.groups.flatMap(group=>group.cards);
  const setOptions=[...availableSets].sort((a,b)=>compareSetRelease(a,b,'desc'));
  const stats=data.stats;
  const isLorcana=game.id==='lorcana';
  const foilDisplayActive=isLorcana&&f.finish==='foil';
  content.innerHTML=`
    <section class="set-compact-head all-cards-head">
      <div class="set-compact-title"><div class="breadcrumbs"><button class="breadcrumb-back" id="cards-back" title="Zurück zur Setübersicht" aria-label="Zurück zur Setübersicht">←</button><button data-dashboard>Übersicht</button><span>›</span><button data-game-back>${escapeHtml(game.short_name)}</button></div><h1>Alle Karten <small>${stats.total} Karten in ${data.groups.length} Sets</small></h1></div>
      <div class="compact-stats"><div class="compact-stat"><span>Base</span><b>${stats.base}%</b></div><div class="compact-stat"><span>Foil</span><b>${stats.foil}%</b></div><div class="compact-stat"><span>Master</span><b>${stats.master}%</b></div><div class="compact-stat"><span>Playset</span><b>${stats.playset}%</b></div><div class="compact-stat"><span>Besitz / Fehlt</span><b>${stats.owned} / ${stats.missing}</b></div><div class="compact-stat value"><span>Gesamtwert</span><b>${money(stats.value)}</b></div></div>
    </section>
    <div class="card-toolbar-sticky">
      <div class="card-toolbar browser-toolbar">
        <div class="filter-search"><span>⌕</span><input id="all-card-search" value="${escapeHtml(state.query)}" placeholder="Alle Sets durchsuchen"></div>
        ${isLorcana?'':`<select id="all-rarity-filter" class="select-control"><option value="">Alle Seltenheiten</option>${data.rarities.map(r=>`<option value="${escapeHtml(r)}" ${f.rarity===r?'selected':''}>${escapeHtml(r)}</option>`).join('')}</select>`}
        <div class="toolbar-spacer"></div><div class="zoom-control"><span>−</span><input id="all-card-zoom" type="range" min="110" max="320" value="${state.zoom}"><span>＋</span></div>
      </div>
      <div class="op-catalog-filterbar">
        ${setSwitcherPopup('all',setOptions,'__all__','Alle Karten')}
        ${isLorcana?lorcanaCardFilterBar(f,'all'):''}
        <div class="toolbar-filter-anchor card-filter-end">
          <button type="button" class="secondary-button" data-filter-toggle="all-view-popup" aria-expanded="false">Ansicht ▾</button>
          <div class="toolbar-filter-popup hidden" id="all-view-popup">
            <label>Sprache<select id="all-language-filter" class="select-control"><option value="combined">Sprachen kombiniert</option>${game.languages.map(language=>`<option value="${language}" ${state.language===language?'selected':''}>${language}</option>`).join('')}</select></label>
            <label>Sortierung<select id="all-sort-filter" class="select-control"><option value="number">Nr. · Modulsortierung</option><option value="name">Name A–Z</option><option value="rarity">Seltenheit</option><option value="value">Marktwert</option><option value="quantity">Menge</option><option value="missing">Fehlend zuerst</option></select></label>
            <label>Setreihenfolge<select id="all-set-order" class="select-control"><option value="desc">Sets: Neu → Alt · Nr. ↓</option><option value="asc">Sets: Alt → Neu · Nr. ↑</option></select></label>
            ${isLorcana?lorcanaAnsichtExtras(f):''}
          </div>
        </div>
      </div>
    </div>
    <div class="all-card-groups">${data.groups.length?data.groups.map(group=>`<section class="all-card-set"><header class="set-card-divider"><img loading="lazy" src="/set-logo/${encodeURIComponent(group.set.id)}?v=${encodeURIComponent(group.set.visual_version||'provider-v1')}" alt=""><div><span>${escapeHtml(group.set.code)}</span><h2>${escapeHtml(group.set.name)}</h2></div><small>${releaseDate(group.set)} · ${group.cards.length} Karten</small></header><div class="card-grid" style="--card-size:${state.zoom}px">${group.cards.map(card=>cardTile(card,foilDisplayActive)).join('')}</div></section>`).join(''):'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe Suche oder Filter an.</span></div>'}</div>`;
  $('#all-sort-filter').value=state.sort;$('#all-set-order').value=state.setDirection;bindCardEvents();
  $('[data-dashboard]').onclick=()=>routeTo('dashboard');$('[data-game-back]').onclick=()=>routeTo('game',gameId);$('#cards-back').onclick=()=>routeTo('game',gameId);
  $$('[data-set-switch]').forEach(b=>b.onclick=()=>{const val=b.dataset.setSwitch;val==='__all__'?renderAllCards(gameId,true):routeTo('set',val)});
  $('#all-language-filter').onchange=event=>{state.language=event.target.value;f.rarity='';renderAllCards(gameId,true)};
  $('#all-rarity-filter')?.addEventListener('change',event=>{f.rarity=event.target.value;renderAllCards(gameId,true)});
  $('#all-sort-filter').onchange=event=>{state.sort=event.target.value;renderAllCards(gameId,true);post('/api/settings',{[`sort_${game.id}`]:state.sort})};
  $('#all-set-order').onchange=event=>{state.setDirection=event.target.value;renderAllCards(gameId,true)};
  $$('[data-card-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardFilter,value=b.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(x=>x!==value):[...current,value];renderAllCards(gameId,true)});
  $$('[data-card-single-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardSingleFilter,value=b.dataset.value;f[key]=f[key]===value?'':value;renderAllCards(gameId,true)});
  $$('[data-card-mode]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardMode,value=b.dataset.value;f[key]=value;renderAllCards(gameId,true)});
  let timer;$('#all-card-search').oninput=event=>{clearTimeout(timer);state.query=event.target.value;timer=setTimeout(()=>reRenderPreservingFocus('#all-card-search',()=>renderAllCards(gameId,true)),280)};
  $('#all-card-zoom').oninput=event=>{state.zoom=event.target.value;$$('.card-grid',content).forEach(grid=>grid.style.setProperty('--card-size',`${state.zoom}px`));post('/api/settings',{[`zoom_${game.id}`]:Number(state.zoom)})};
}

const cardCycleRegistry=new Map();
const LORCANA_PREMIUM_TIER={Epic:'Epic',Mythisch:'Epic',Enchanted:'Enchanted',Verzaubert:'Enchanted',Iconic:'Iconic',Ikonisch:'Iconic'};

function lorcanaVariantGroups(languageVariants){
  const normal=languageVariants.filter(x=>x.finish==='Normal');
  const foil=languageVariants.filter(x=>x.finish==='Silver');
  const premium=languageVariants.filter(x=>LORCANA_PREMIUM_TIER[x.rarity]);
  const groups=[];
  if(normal.length)groups.push({tier:'normal',items:normal});
  if(foil.length)groups.push({tier:'foil',items:foil});
  if(premium.length)groups.push({tier:'premium',items:premium,rarityLabel:LORCANA_PREMIUM_TIER[premium[0].rarity]});
  return groups;
}

function lorcanaVariantBadges(languageVariants){
  return lorcanaVariantGroups(languageVariants).map(g=>{
    const qty=g.items.reduce((a,x)=>a+(Number(x.quantity)||0),0);
    return `<span class="variant-badge tier-${g.tier} ${qty?'owned':'missing'}" title="${g.rarityLabel?escapeHtml(g.rarityLabel):''}">×${qty}</span>`;
  }).join('');
}

function cardTile(card,foilDisplayActive=false){
  const languageVariants=card.variants.filter(x=>x.language===card.language);
  // When the server switched the representative to the foil printing (foil filter engaged),
  // honor that choice first -- otherwise always default to the Normal finish, as before.
  const v=languageVariants.find(x=>x.variant_id===card.variant_id)||languageVariants.find(x=>x.finish==='Normal')||languageVariants.find(x=>['standard','normal'].includes(x.variant_code))||languageVariants[0]||card.variants[0];
  const isLorcana=(v.game_id||state.activeGameId)==='lorcana';
  const foil=(isLorcana&&!foilDisplayActive)?languageVariants.find(x=>x.finish==='Silver'):null;
  const visual=finishPresentation(v);
  // Hovering cycles through a card's other printings. For Lorcana specifically,
  // the Silver/foil finish is skipped -- every card has one, so it adds no
  // information -- only genuinely rarer premium tiers (Enchanted etc.) cycle in.
  const premiumVariants=isLorcana?(lorcanaVariantGroups(languageVariants).find(g=>g.tier==='premium')?.items||[]).filter(x=>x!==v):[];
  const cycle=isLorcana?[v,...premiumVariants]:[v,...languageVariants.filter(x=>x!==v)];
  cardCycleRegistry.set(v.variant_id,cycle);
  const badgesHtml=isLorcana?lorcanaVariantBadges(languageVariants):(card.quantity?`<span class="owned-pill">×${card.quantity}</span>`:'');
  const quantityHtml=isLorcana&&foil
    ?`<div class="quantity-stack"><div class="quantity-control foil" data-variant="${foil.variant_id}"><button data-delta="-1">−</button><b>${foil.quantity}</b><button data-delta="1">＋</button></div><div class="quantity-control" data-variant="${v.variant_id}"><button data-delta="-1">−</button><b>${v.quantity}</b><button data-delta="1">＋</button></div></div>
      <div class="quick-add-stack"><button class="quick-add foil" data-variant="${foil.variant_id}">＋ 1 Foil</button><button class="quick-add" data-variant="${v.variant_id}">＋ 1 hinzufügen</button></div>`
    :`<div class="quantity-control" data-variant="${v.variant_id}"><button data-delta="-1">−</button><b>${v.quantity}</b><button data-delta="1">＋</button></div><button class="quick-add" data-variant="${v.variant_id}">＋ 1 hinzufügen</button>`;
  const imageHtml=cycle.length>1
    ?`<div class="card-flip-stack"><img class="cycle-img front" loading="lazy" decoding="async" src="${artUrl(v.variant_id)}" alt="${escapeHtml(card.canonical_name)}"><img class="cycle-img back" loading="lazy" decoding="async" alt="" aria-hidden="true"></div>`
    :`<img loading="lazy" decoding="async" src="${artUrl(v.variant_id)}" alt="${escapeHtml(card.canonical_name)}">`;
  // A playset (4 copies) gets its own badge per finish -- base and foil count
  // separately, so a 4x foil playset doesn't need 4 base copies too to show.
  const ribbons=isLorcana
    ?`${v.quantity>=4?`<span class="playset-badge" title="Playset komplett · 4 Exemplare">✓</span>`:''}${foil&&foil.quantity>=4?`<span class="playset-badge foil" title="Foil-Playset komplett · 4 Exemplare">✓</span>`:''}`
    :(card.quantity>=4?`<span class="playset-badge" title="Playset komplett · 4 Exemplare">✓</span>`:'');
  const playsetHtml=ribbons?`<div class="playset-ribbons">${ribbons}</div>`:'';
  return `<article class="card-tile ${card.quantity?'owned':'missing'}" data-identity="${card.identity_id}" data-variant="${v.variant_id}">
    <div class="card-image-wrap card-finish-frame ${visual.effect}">${imageHtml}<button class="watch-button ${card.watchlisted?'active':''}" title="Watchlist">${card.watchlisted?'♥':'♡'}</button><div class="variant-badges">${badgesHtml}</div>${quantityHtml}</div>
    ${playsetHtml}
    <div class="card-info"><b>${escapeHtml(card.canonical_name)}</b><div class="card-subline"><span>${escapeHtml(card.collector_number)} · ${escapeHtml(card.rarity)} · ${v.language}</span><span class="card-price">${price(v.price)}</span></div>${state.zoom>175?`<div class="variant-chips">${languageVariants.slice(0,3).map(x=>`<span class="variant-chip">${escapeHtml(isLorcana?lorcanaFinishLabel(x.finish,x.rarity):x.finish)}</span>`).join('')}</div>`:''}</div></article>`;
}

function bindCardEvents(watchlistId=null){
  $$('.card-tile',content).forEach(tile=>{
    tile.onclick=e=>{if(e.target.closest('.watch-button,.quantity-control,.quick-add'))return;openCard(tile.dataset.identity,tile.dataset.variant)};
    $('.watch-button',tile).onclick=async e=>{e.stopPropagation();const r=await post('/api/watchlist',{variant_id:tile.dataset.variant,...(watchlistId?{list_id:watchlistId}:{})});e.currentTarget.classList.toggle('active',r.active);e.currentTarget.textContent=r.active?'♥':'♡';toast(r.active?'Zur Watchlist hinzugefügt':'Von der Watchlist entfernt');refreshWatchCount();if(state.route==='watchlist')renderWatchlist(true)};
    $$('.quantity-control',tile).forEach(row=>{const variantId=row.dataset.variant||tile.dataset.variant;$$('button',row).forEach(btn=>btn.onclick=e=>{e.stopPropagation();changeQuantity(variantId,Number(btn.dataset.delta))})});
    $$('.quick-add',tile).forEach(btn=>btn.onclick=e=>{e.stopPropagation();changeQuantity(btn.dataset.variant||tile.dataset.variant,1,true)});
    const cycle=cardCycleRegistry.get(tile.dataset.variant);
    const stack=$('.card-flip-stack',tile);
    if(cycle&&cycle.length>1&&stack){
      const frame=$('.card-image-wrap',tile),priceEl=$('.card-price',tile);
      let [frontEl,backEl]=$$('.cycle-img',stack);
      if(!frontEl.classList.contains('front')){[frontEl,backEl]=[backEl,frontEl]}
      let cycleIndex=0,cycleTimer=null;
      const advance=()=>{
        cycleIndex=(cycleIndex+1)%cycle.length;
        const variant=cycle[cycleIndex];
        backEl.src=artUrl(variant.variant_id);
        frontEl.classList.replace('front','back');
        backEl.classList.replace('back','front');
        frame.className=`card-image-wrap card-finish-frame ${finishPresentation(variant).effect}`;
        if(priceEl)priceEl.textContent=price(variant.price);
        [frontEl,backEl]=[backEl,frontEl];
      };
      const reset=()=>{
        cycleIndex=0;
        const variant=cycle[0];
        frontEl.src=artUrl(variant.variant_id);
        frontEl.classList.add('front');frontEl.classList.remove('back');
        backEl.classList.add('back');backEl.classList.remove('front');
        frame.className=`card-image-wrap card-finish-frame ${finishPresentation(variant).effect}`;
        if(priceEl)priceEl.textContent=price(variant.price);
      };
      tile.addEventListener('mouseenter',()=>{cycleIndex=0;cycleTimer=setInterval(advance,1800)});
      tile.addEventListener('mouseleave',()=>{clearInterval(cycleTimer);reset()});
    }
  });
}

async function refreshCurrentView(){
  if(state.route==='set') await renderSet(state.set.id,true);
  else if(state.route==='game-cards') await renderAllCards(state.activeGameId,true);
  else if(state.route==='collection') await renderCollection(true);
  else if(state.route==='watchlist') await renderWatchlist(true);
  else if(state.route==='decks') await renderDeckbuilder(true);
}

async function changeQuantity(variantId,delta,quick=false){
  const r=await post('/api/collection',{variant_id:variantId,delta,condition:'Near Mint'});
  toast(quick?'Karte hinzugefügt':`Menge auf ${r.quantity} geändert`,'Rückgängig',async()=>{await post('/api/collection',{variant_id:variantId,quantity:r.before,condition:'Near Mint'});await refreshCurrentView()});
  await refreshCurrentView();
  if(state.modalCard) await openCard(state.modalCard.id,variantId,true);
}

// Lives outside `state` on purpose -- it's transient UI state for the currently open modal,
// not something that should survive navigation or be considered part of the app's data model.
let advancedPanelExpanded=false;

async function changeCollectionEntry(variantId,{condition='Near Mint',delta=0,quantity,isGraded=false,gradeLabel='',priceOverride}={}){
  const payload={variant_id:variantId,condition,is_graded:isGraded,grade_label:gradeLabel};
  if(quantity!==undefined)payload.quantity=quantity;else payload.delta=delta;
  if(priceOverride!==undefined)payload.price_override=priceOverride;
  await post('/api/collection',payload);
  await refreshCurrentView();
  if(state.modalCard) await openCard(state.modalCard.id,variantId,true);
}

function advancedCollectionPanelHtml(entries){
  const byCondition={}; entries.filter(e=>!e.is_graded).forEach(e=>byCondition[e.condition]=e);
  const graded=entries.filter(e=>e.is_graded);
  return `<div class="detail-section-title">MENGE NACH ZUSTAND</div>
    <div class="condition-rows">${COLLECTION_CONDITIONS.map(c=>{const e=byCondition[c],qty=e?e.quantity:0;return `<div class="condition-row"><span>${escapeHtml(c)}</span><div class="controls"><button type="button" class="condition-qty-btn" data-condition="${escapeHtml(c)}" data-delta="-1" ${qty<=0?'disabled':''}>−</button><b>${qty}</b><button type="button" class="condition-qty-btn" data-condition="${escapeHtml(c)}" data-delta="1">＋</button></div></div>`}).join('')}</div>
    <div class="detail-section-title">GEGRADETE EXEMPLARE</div>
    <div class="graded-rows">${graded.length?graded.map(e=>`<div class="graded-row"><div class="graded-row-label"><b>${escapeHtml(e.grade_label)}</b><small>${escapeHtml(e.condition)}</small></div><div class="controls"><button type="button" class="graded-qty-btn" data-condition="${escapeHtml(e.condition)}" data-grade="${escapeHtml(e.grade_label)}" data-delta="-1">−</button><b>${e.quantity}</b><button type="button" class="graded-qty-btn" data-condition="${escapeHtml(e.condition)}" data-grade="${escapeHtml(e.grade_label)}" data-delta="1">＋</button></div><input type="number" step="0.01" class="graded-price-input select-control" data-condition="${escapeHtml(e.condition)}" data-grade="${escapeHtml(e.grade_label)}" value="${e.price_override??''}" placeholder="Preis-Override €"></div>`).join(''):'<p class="muted">Noch keine gegradeten Exemplare.</p>'}</div>
    <button type="button" class="secondary-button" id="add-graded-toggle">+ Gegradetes Exemplar</button>
    <div class="graded-add-form hidden" id="graded-add-form">
      <input type="text" id="new-grade-label" placeholder="Grading, z.B. PSA 10">
      <select id="new-grade-condition" class="select-control">${COLLECTION_CONDITIONS.map(c=>`<option value="${escapeHtml(c)}" ${c==='Near Mint'?'selected':''}>${escapeHtml(c)}</option>`).join('')}</select>
      <input type="number" id="new-grade-price" step="0.01" placeholder="Preis-Override €">
      <button type="button" class="primary-button" id="add-graded-submit">Hinzufügen</button>
    </div>`;
}

function wireAdvancedPanel(variantId){
  $$('.condition-qty-btn').forEach(b=>b.onclick=()=>changeCollectionEntry(variantId,{condition:b.dataset.condition,delta:Number(b.dataset.delta)}));
  $$('.graded-qty-btn').forEach(b=>b.onclick=()=>changeCollectionEntry(variantId,{condition:b.dataset.condition,delta:Number(b.dataset.delta),isGraded:true,gradeLabel:b.dataset.grade}));
  $$('.graded-price-input').forEach(input=>input.onchange=()=>changeCollectionEntry(variantId,{condition:input.dataset.condition,delta:0,isGraded:true,gradeLabel:input.dataset.grade,priceOverride:input.value===''?null:Number(input.value)}));
  $('#add-graded-toggle').onclick=()=>$('#graded-add-form').classList.toggle('hidden');
  $('#add-graded-submit').onclick=()=>{
    const label=$('#new-grade-label').value.trim();
    if(!label){toast('Bitte eine Grading-Bezeichnung eingeben');return}
    const condition=$('#new-grade-condition').value, priceRaw=$('#new-grade-price').value;
    changeCollectionEntry(variantId,{condition,delta:1,isGraded:true,gradeLabel:label,priceOverride:priceRaw===''?undefined:Number(priceRaw)});
  };
}

async function loadAdvancedPanel(){
  const panel=$('#advanced-panel'); if(!panel)return;
  const variantId=state.modalVariant.id;
  const entries=await api(`/api/collection/entries/${variantId}`);
  panel.innerHTML=advancedCollectionPanelHtml(entries);
  wireAdvancedPanel(variantId);
}

function toggleAdvancedPanel(){
  advancedPanelExpanded=!advancedPanelExpanded;
  const btn=$('#advanced-toggle');
  btn.setAttribute('aria-expanded',String(advancedPanelExpanded));
  btn.textContent=`Erweitert ${advancedPanelExpanded?'▴':'▾'}`;
  $('#advanced-panel').classList.toggle('hidden',!advancedPanelExpanded);
  if(advancedPanelExpanded)loadAdvancedPanel();
}

async function renderWatchlist(preserve=false){
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Watchlists werden geladen …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId), lists=await api(`/api/watchlists?game_id=${state.activeGameId}`);state.activeWatchlists=lists;
  if(!state.watchlistId||!lists.some(l=>l.id===state.watchlistId))state.watchlistId=lists[0]?.id;
  if(!state.watchlistId){content.innerHTML='<div class="empty-state"><b>Noch keine Watchlist</b></div>';return}
  const f=state.watchFilters, params=new URLSearchParams(f), data=await api(`/api/watchlists/${state.watchlistId}/cards?${params}`), sets=await api(`/api/games/${state.activeGameId}/sets`);
  state.cards=data.cards.map(r=>({...r,variants:[r],variant_count:1,owned_variants:r.quantity?1:0,watchlisted:true,value:r.quantity*r.price}));
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · PREISBEOBACHTUNG</span><h1>Watchlists</h1><p>Getrennte Listen für Kaufziele, Deckprojekte und Preisalarme.</p></div><div class="page-head-actions"><button class="primary-button" id="new-watchlist">＋ Neue Watchlist</button></div></div>
    <div class="list-tabs">${lists.map(l=>`<button data-list="${l.id}" class="${l.id===state.watchlistId?'active':''}"><b>${escapeHtml(l.name)}</b><span>${l.count} · ${money(l.value)}</span></button>`).join('')}</div>
    <div class="browser-summary"><div><span>LISTE</span><b>${escapeHtml(data.list.name)}</b></div><div><span>VARIANTEN</span><b>${data.cards.length}</b></div><div><span>MARKTWERT</span><b>${money(data.cards.reduce((a,c)=>a+c.price,0))}</b></div><div class="browser-summary-actions"><button class="secondary-button" id="rename-watchlist">Umbenennen</button>${data.list.is_default?'':`<button class="danger-button" id="delete-watchlist">Löschen</button>`}</div></div>
    <div class="card-toolbar browser-toolbar"><div class="filter-search"><span>⌕</span><input id="watch-q" value="${escapeHtml(f.q)}" placeholder="Watchlist durchsuchen"></div><select id="watch-set" class="select-control"><option value="">Alle Sets</option>${sets.map(s=>`<option value="${s.id}" ${f.set_id===s.id?'selected':''}>${escapeHtml(s.code)} · ${escapeHtml(s.name)}</option>`).join('')}</select><select id="watch-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(l=>`<option value="${l}" ${f.language===l?'selected':''}>${l}</option>`).join('')}</select><select id="watch-finish" class="select-control"><option value="">Alle Varianten</option>${['Normal','Foil','Parallel','Enchanted','Manga','OSR','OUR'].map(x=>`<option ${f.finish===x?'selected':''}>${x}</option>`).join('')}</select><select id="watch-sort" class="select-control"><option value="added">Zuletzt hinzugefügt</option><option value="name">Name</option><option value="number">Nummer</option><option value="price_high">Preis absteigend</option><option value="price_low">Preis aufsteigend</option></select></div>
    <section class="card-grid" style="--card-size:${state.zoom}px">${state.cards.length?state.cards.map(cardTile).join(''):'<div class="empty-state"><b>Keine Karten in dieser Ansicht</b><span>Passe die Filter an oder füge Karten hinzu.</span></div>'}</section>`;
  $('#watch-sort').value=f.sort;bindCardEvents(state.watchlistId);
  $$('.list-tabs button').forEach(b=>b.onclick=()=>{state.watchlistId=Number(b.dataset.list);renderWatchlist(true)});
  $('#new-watchlist').onclick=async()=>{const name=prompt('Name der neuen Watchlist:','Deckprojekt');if(!name)return;const created=await post('/api/watchlists',{game_id:state.activeGameId,name});state.watchlistId=created.id;renderWatchlist(true);refreshWatchCount()};
  $('#rename-watchlist').onclick=async()=>{const name=prompt('Neuer Name:',data.list.name);if(!name)return;await api(`/api/watchlists/${state.watchlistId}`,{method:'PATCH',body:JSON.stringify({name})});renderWatchlist(true)};
  if($('#delete-watchlist'))$('#delete-watchlist').onclick=async()=>{if(!confirm('Diese Watchlist wirklich löschen?'))return;await api(`/api/watchlists/${state.watchlistId}`,{method:'DELETE'});state.watchlistId=null;renderWatchlist(true);refreshWatchCount()};
  bindBrowserFilters('watch',()=>renderWatchlist(true));
}

async function renderCollection(preserve=false){
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Sammlung wird zusammengestellt …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId),f=state.collectionFilters,params=new URLSearchParams({game_id:state.activeGameId,...f}),data=await api(`/api/collection?${params}`);state.cards=data.cards;
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · SAMMLUNG</span><h1>Meine Karten</h1><p>Durchsuchbare Variantenansicht mit denselben Werkzeugen wie im Set-Katalog.</p></div><div class="page-head-actions"><button class="secondary-button" id="collection-export">Exportieren</button></div></div>
    <div class="browser-summary"><div><span>VARIANTEN</span><b>${data.stats.variants}</b></div><div><span>EXEMPLARE</span><b>${data.stats.copies}</b></div><div><span>MARKTWERT</span><b>${money(data.stats.value)}</b></div></div>
    <div class="card-toolbar browser-toolbar"><div class="filter-search"><span>⌕</span><input id="collection-q" value="${escapeHtml(f.q)}" placeholder="Sammlung durchsuchen"></div><select id="collection-set" class="select-control"><option value="">Alle Sets</option>${data.sets.map(s=>`<option value="${s.id}" ${f.set_id===s.id?'selected':''}>${escapeHtml(s.code)} · ${escapeHtml(s.name)}</option>`).join('')}</select><select id="collection-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(l=>`<option value="${l}" ${f.language===l?'selected':''}>${l}</option>`).join('')}</select><select id="collection-rarity" class="select-control"><option value="">Alle Seltenheiten</option>${['Common','Uncommon','Rare','Super Rare','Legendary','Secret Rare'].map(x=>`<option ${f.rarity===x?'selected':''}>${x}</option>`).join('')}</select><select id="collection-finish" class="select-control"><option value="">Alle Varianten</option>${['Normal','Foil','Parallel','Enchanted','Manga','OSR','OUR'].map(x=>`<option ${f.finish===x?'selected':''}>${x}</option>`).join('')}</select><select id="collection-mode" class="select-control"><option value="all">Alle Karten</option><option value="duplicates">Nur Duplikate</option><option value="watchlisted">Auf Watchlist</option></select><select id="collection-sort" class="select-control"><option value="number">Nummer</option><option value="name">Name</option><option value="set">Set</option><option value="rarity">Seltenheit</option><option value="value">Wert</option><option value="quantity">Menge</option></select></div>
    <section class="card-grid" style="--card-size:${state.zoom}px">${data.cards.length?data.cards.map(cardTile).join(''):'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe deine Filter an.</span></div>'}</section>`;
  $('#collection-mode').value=f.mode;$('#collection-sort').value=f.sort;$('#collection-export').onclick=()=>{setIeMode('export');openOverlay('import-modal')};bindCardEvents();bindBrowserFilters('collection',()=>renderCollection(true));
}

function bindBrowserFilters(prefix,render){
  const target=prefix==='watch'?state.watchFilters:state.collectionFilters;let timer;
  $(`#${prefix}-q`).oninput=e=>{clearTimeout(timer);target.q=e.target.value;timer=setTimeout(()=>reRenderPreservingFocus(`#${prefix}-q`,render),250)};
  const mappings=prefix==='watch'?['set','language','finish','sort']:['set','language','rarity','finish','mode','sort'];
  mappings.forEach(key=>$(`#${prefix}-${key}`).onchange=e=>{target[key==='set'?'set_id':key]=e.target.value;render()});
}

let deckImagePreview=null;
function hideDeckImagePreview(){
  if(!deckImagePreview)return;
  deckImagePreview.classList.remove('visible');
  deckImagePreview.setAttribute('aria-hidden','true');
}
function positionDeckImagePreview(anchor){
  if(!deckImagePreview)return;
  const gap=14,margin=14,anchorRect=anchor.getBoundingClientRect();
  const previewRect=deckImagePreview.getBoundingClientRect();
  const roomRight=window.innerWidth-anchorRect.right-gap;
  const roomLeft=anchorRect.left-gap;
  let left=roomRight>=previewRect.width||roomRight>=roomLeft
    ? anchorRect.right+gap
    : anchorRect.left-gap-previewRect.width;
  let top=anchorRect.top+(anchorRect.height-previewRect.height)/2;
  left=Math.max(margin,Math.min(left,window.innerWidth-previewRect.width-margin));
  top=Math.max(margin,Math.min(top,window.innerHeight-previewRect.height-margin));
  deckImagePreview.style.left=`${Math.round(left)}px`;
  deckImagePreview.style.top=`${Math.round(top)}px`;
}
function bindDeckImagePreviews(){
  hideDeckImagePreview();
  if(!window.matchMedia('(hover: hover) and (pointer: fine)').matches)return;
  if(!deckImagePreview){
    deckImagePreview=document.createElement('div');
    deckImagePreview.className='deck-image-preview';
    deckImagePreview.setAttribute('aria-hidden','true');
    deckImagePreview.innerHTML='<img alt="Vergrößerte Kartenvorschau">';
    document.body.append(deckImagePreview);
  }
  $$('img[data-deck-preview]').forEach(thumb=>{
    thumb.onmouseenter=()=>{
      const image=$('img',deckImagePreview);
      image.src=thumb.dataset.fullSrc||thumb.currentSrc||thumb.src;
      image.alt=thumb.alt||'Vergrößerte Kartenvorschau';
      deckImagePreview.classList.add('visible');
      deckImagePreview.setAttribute('aria-hidden','false');
      positionDeckImagePreview(thumb);
    };
    thumb.onmouseleave=hideDeckImagePreview;
  });
}

function deckCatalogCard(c,profile,quantities={}){
  const zone=profile.zones.find(item=>item.id===c.suggested_zone)||profile.zones[0];
  const quantity=Number(quantities[c.variant_id])||0;
  const maximum=zone.id==='cheer'?20:zone.id==='don'?10:zone.id==='leader'||zone.id==='oshi'?1:4;
  const counter=`<div class="catalog-deck-counter" title="Menge im Deck"><button data-catalog-delta="-1" ${quantity<1?'disabled':''}>−</button><b>${quantity}</b><button data-catalog-delta="1" ${quantity>=maximum?'disabled':''}>＋</button></div>`;
  if(state.deckView==='grid')return `<article class="catalog-card-grid" data-catalog-variant="${c.variant_id}" data-identity="${c.identity_id}"><div class="catalog-grid-image card-finish-frame ${finishPresentation(c).effect}"><img loading="lazy" decoding="async" fetchpriority="low" data-deck-preview data-full-src="${artUrl(c.variant_id,'full')}" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}">${counter}</div><b>${escapeHtml(c.canonical_name)}</b><small>${escapeHtml(c.collector_number)} · ${c.language}</small><span>${escapeHtml(zone.name)}</span></article>`;
  return `<div class="catalog-card" data-catalog-variant="${c.variant_id}" data-identity="${c.identity_id}"><img loading="lazy" decoding="async" fetchpriority="low" data-deck-preview data-full-src="${artUrl(c.variant_id,'full')}" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}"><div><b>${escapeHtml(c.canonical_name)}</b><small>${escapeHtml(c.collector_number)} · ${c.language} · ${escapeHtml(c.rarity)}${c.owned?` · ${c.owned}× vorhanden`:''}</small><span>${escapeHtml(zone.name)}</span></div>${counter}</div>`;
}

function deckContentCard(c,forceGrid=false,coverVariantId=null){
  const quantity=Number(c.quantity)||0,owned=Math.min(Number(c.owned_quantity)||0,quantity),complete=owned>=quantity;
  const status=`${owned}/${quantity} vorhanden${complete?'':` · ${quantity-owned} fehlt`}`;
  const automatic=Boolean(c.auto_filled),grid=forceGrid||state.deckView==='grid';
  const number=c.card_type==='DON!!'?'DON!!':c.collector_number||c.set_code||'–';
  const data=`data-identity="${c.identity_id}" data-variant="${c.variant_id}" data-zone="${c.zone||'main'}"${automatic?' data-auto="true"':''}`;
  const coverButton=automatic?'':`<button type="button" class="deck-cover-button ${coverVariantId===c.variant_id?'active':''}" data-deck-cover="${c.variant_id}" title="${coverVariantId===c.variant_id?'Aktuelles Deckcover':'Als Deckcover verwenden'}" aria-label="${coverVariantId===c.variant_id?'Aktuelles Deckcover':'Als Deckcover verwenden'}">${coverVariantId===c.variant_id?'★':'☆'}</button>`;
  const quantityControl=automatic?`<span class="deck-auto-label">Automatisch · ${quantity}×</span>`:`<div class="deck-qty"><button data-delta="-1">−</button><b>${quantity}</b><button data-delta="1">＋</button></div>`;
  if(grid)return `<article class="deck-card-tile ${complete?'deck-owned':'deck-missing'} ${automatic?'deck-auto-card':''}" ${data}><div class="deck-card-tile-image card-finish-frame ${finishPresentation(c).effect}"><img loading="lazy" decoding="async" data-deck-preview data-full-src="${artUrl(c.variant_id,'full')}" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}">${coverButton}<span class="deck-ownership-dot" title="${escapeHtml(status)}">${owned}/${quantity}</span><span class="deck-number-badge">${escapeHtml(number)}</span></div>${quantityControl}</article>`;
  return `<div class="deck-card-row ${complete?'deck-owned':'deck-missing'} ${automatic?'deck-auto-card':''}" ${data}><img loading="lazy" decoding="async" data-deck-preview data-full-src="${artUrl(c.variant_id,'full')}" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}"><div><b>${escapeHtml(c.canonical_name)}</b><small>${escapeHtml(number)} · ${c.language} · ${escapeHtml(c.rarity)}</small><span class="deck-card-ownership">${escapeHtml(status)}</span></div>${coverButton}<span class="deck-card-price">${price(c.price)}</span>${quantityControl}</div>`;
}

const OP_COLOR_FILTERS=[['Red','#c42536'],['Green','#168b64'],['Blue','#297eb5'],['Purple','#9749a3'],['Black','#24292f'],['Yellow','#e6d93b']];
const OP_TYPE_FILTERS=[['Leader','Leader'],['Stage','Stage'],['Character','Character'],['Event','Event'],['DON!!','DON']];
const OP_ATTRIBUTE_FILTERS=[['Slash','Slasher','#297eb5'],['Strike','Strike','#e6d93b'],['Special','Special','#9749a3'],['Ranged','Ranged','#c42536'],['Wisdom','Wisdom','#168b64']];
function opColorIcon(color,rotation){
  return `<span class="op-color-glyph" style="--op-color:${color};--op-rotation:${rotation}deg"><img src="/op-filter-icon/color.svg?v=2" alt="" aria-hidden="true"></span>`;
}
function opFilterPanel(filters){
  const selected=(key,value)=>(filters[key]||[]).includes(String(value));
  const pills=(items,key)=>items.map(([value,label])=>`<button type="button" class="op-filter-chip ${selected(key,value)?'active':''}" data-op-filter="${key}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('');
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${pills(OP_TYPE_FILTERS,'types')}</div></div>
    <div class="op-filter-group op-costs"><span>Kosten</span><div>${Array.from({length:10},(_,index)=>index+1).map(cost=>`<button type="button" class="op-image-filter ${selected('costs',cost)?'active':''}" data-op-filter="costs" data-value="${cost}" aria-label="Kosten ${cost}" title="Kosten ${cost}"><img src="/op-filter-icon/cost-${cost}.png?v=2" alt="${cost}"></button>`).join('')}</div></div>
    <div class="op-filter-group op-colors"><span>Farbe</span><div>${OP_COLOR_FILTERS.map(([name,color],index)=>`<button type="button" class="op-color-filter ${selected('colors',name)?'active':''}" data-op-filter="colors" data-value="${name}" aria-label="${name}" title="${name}">${opColorIcon(color,index*60)}</button>`).join('')}</div></div>
    <div class="op-filter-group op-attributes"><span>Attribut</span><div>${OP_ATTRIBUTE_FILTERS.map(([value,label,color])=>{const iconUrl=`/op-filter-icon/attribute-${value.toLowerCase()}.svg?v=2`;return `<button type="button" class="op-image-filter ${selected('attributes',value)?'active':''}" data-op-filter="attributes" data-value="${value}" aria-label="${label}" title="${label}"><span class="op-attribute-glyph" style="--op-attribute-color:${color};--op-attribute-icon:url('${iconUrl}')"><img src="${iconUrl}" alt="${label}"></span></button>`}).join('')}</div></div>`;
}

const LORCANA_TYPE_FILTERS=[['Character','Charakter'],['Action','Aktion'],['Item','Gegenstand'],['Location','Ort']];
const LORCANA_INK_FILTERS=[['Amber','Bernstein'],['Amethyst','Amethyst'],['Emerald','Smaragd'],['Ruby','Rubin'],['Sapphire','Saphir'],['Steel','Stahl']];
// Keys are language-independent -- a click matches both the EN and DE printed rarity label
// (see LORCANA_RARITY_KEYS server-side), so the icon filter works the same in any language view.
const LORCANA_RARITY_FILTERS=[
  ['common','Gewöhnlich / Common','common.svg'],['uncommon','Ungewöhnlich / Uncommon','uncommon.svg'],['rare','Selten / Rare','rare.svg'],
  ['super-rare','Episch / Super Rare','super_rare.svg'],['legendary','Legendär / Legendary','legendary.svg'],['epic','Mythisch / Epic','epic.png'],
  ['enchanted','Verzaubert / Enchanted','enchanted.png'],['iconic','Ikonisch / Iconic','iconic.png'],['special','Speziell / Special','promo.png'],
];
// LorcanaJSON's special-edition finishes (Lava, Magma, CalendarWave, ...) are internal pattern
// codenames, not something a player recognizes -- show the card's own rarity instead. Silver is
// Lorcana's standard foil finish and always reads as "Foil".
const LORCANA_PLAIN_FINISHES=new Set(['Normal','Satin']);
const COLLECTION_CONDITIONS=['Mint','Near Mint','Excellent','Good','Light Played','Played','Poor'];
function lorcanaFinishLabel(finish,rarity){
  if(finish==='Silver')return 'Foil';
  if(LORCANA_PLAIN_FINISHES.has(finish))return finish;
  return rarity||finish;
}
function lorcanaRarityPopup(filters,prefix){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  const popupId=`${prefix}-rarity-popup`;
  return `<div class="toolbar-filter-anchor">
    <button type="button" class="secondary-button" data-filter-toggle="${popupId}" aria-expanded="false">Seltenheit ▾</button>
    <div class="toolbar-filter-popup align-left rarity-popup hidden" id="${popupId}">
      ${LORCANA_RARITY_FILTERS.map(([key,label,file])=>`<button type="button" class="op-image-filter ${active('rarities',key)?'active':''}" data-card-filter="rarities" data-value="${key}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><img src="/lorcana-filter-icon/${file}?v=1" alt="${escapeHtml(label)}"></button>`).join('')}
    </div>
  </div>`;
}
function lorcanaCardFilterBar(filters,prefix){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `
    ${lorcanaRarityPopup(filters,prefix)}
    <div class="op-filter-group game-costs"><span>Kosten</span><div>${Array.from({length:7},(_,i)=>i+1).map(cost=>`<button type="button" class="op-number-filter lorcana-cost-filter ${active('costs',cost)?'active':''}" data-card-filter="costs" data-value="${cost}" aria-label="Kosten ${cost===7?'7+':cost}"><img src="/lorcana-filter-icon/cost.png?v=2" alt="" aria-hidden="true"><span>${cost===7?'7+':cost}</span></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inks"><span>Tintenfarbe</span><div>${LORCANA_INK_FILTERS.map(([value,label])=>`<button type="button" class="lorcana-color-filter ${active('colors',value)?'active':''}" data-card-filter="colors" data-value="${value}" title="${label}" aria-label="${label}"><img src="/lorcana-filter-icon/${value.toLowerCase()}.svg?v=1" alt="" aria-hidden="true"></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inkability"><span>Tintbarkeit</span><div><button type="button" class="lorcana-icon-filter ${filters.inkwell==='true'?'active':''}" data-card-single-filter="inkwell" data-value="true" title="Tintbar" aria-label="Tintbar"><img src="/lorcana-filter-icon/inkable.png?v=2" alt="" aria-hidden="true"></button><button type="button" class="lorcana-icon-filter ${filters.inkwell==='false'?'active':''}" data-card-single-filter="inkwell" data-value="false" title="Nicht tintbar" aria-label="Nicht tintbar"><img src="/lorcana-filter-icon/uninkable.png?v=1" alt="" aria-hidden="true"></button></div></div>`;
}
function lorcanaAnsichtExtras(filters){
  return `<label>Ausführung<div class="segmented"><button type="button" data-card-mode="finish" data-value="normal" class="${filters.finish==='normal'?'active':''}">Normal</button><button type="button" data-card-mode="finish" data-value="foil" class="${filters.finish==='foil'?'active':''}">Foil</button></div></label>
    <label>Foil-Besitz<div class="segmented"><button type="button" data-card-mode="foilMode" data-value="" class="${filters.foilMode===''?'active':''}">Alle</button><button type="button" data-card-mode="foilMode" data-value="owned" class="${filters.foilMode==='owned'?'active':''}">Im Besitz</button><button type="button" data-card-mode="foilMode" data-value="missing" class="${filters.foilMode==='missing'?'active':''}">Fehlend</button></div></label>`;
}
function setAbbreviation(name){
  return name.replace(/[^\p{L}\s]/gu,'').split(/\s+/).filter(Boolean).map(w=>w[0]).join('').toUpperCase();
}
function setSwitcherPopup(prefix,setOptions,currentSetId,currentLabel){
  const popupId=`${prefix}-set-popup`;
  return `<div class="toolbar-filter-anchor">
    <button type="button" class="secondary-button set-switch-trigger" data-filter-toggle="${popupId}" aria-expanded="false">${escapeHtml(currentLabel)} ▾</button>
    <div class="toolbar-filter-popup align-left set-switch-popup hidden" id="${popupId}">
      <button type="button" class="set-switch-option ${currentSetId==='__all__'?'active':''}" data-set-switch="__all__">Alle Karten</button>
      ${setOptions.map(item=>`<button type="button" class="set-switch-option ${item.id===currentSetId?'active':''}" data-set-switch="${item.id}">${escapeHtml(item.code)} · ${escapeHtml(item.name)}</button>`).join('')}
    </div>
  </div>`;
}
const HOLOLIVE_KIND_FILTERS=[['oshi','Oshi'],['holomem','Holomem'],['buzz','Buzz'],['support','Support'],['cheer','Cheer']];
const HOLOLIVE_BLOOM_FILTERS=[['Debut','Debut'],['1st','1st'],['2nd','2nd'],['Spot','Spot']];
function deckFilterPills(items,key,filters){
  const active=value=>(filters[key]||[]).includes(String(value));
  return items.map(([value,label])=>`<button type="button" class="op-filter-chip ${active(value)?'active':''}" data-deck-filter="${key}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('');
}
function lorcanaFilterPanel(filters){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${deckFilterPills(LORCANA_TYPE_FILTERS,'types',filters)}</div></div>
    <div class="op-filter-group game-costs"><span>Kosten</span><div>${Array.from({length:7},(_,i)=>i+1).map(cost=>`<button type="button" class="op-number-filter lorcana-cost-filter ${active('costs',cost)?'active':''}" data-deck-filter="costs" data-value="${cost}" aria-label="Kosten ${cost===7?'7+':cost}"><img src="/lorcana-filter-icon/cost.png?v=2" alt="" aria-hidden="true"><span>${cost===7?'7+':cost}</span></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inks"><span>Tintenfarbe</span><div>${LORCANA_INK_FILTERS.map(([value,label])=>`<button type="button" class="lorcana-color-filter ${active('colors',value)?'active':''}" data-deck-filter="colors" data-value="${value}" title="${label}" aria-label="${label}"><img src="/lorcana-filter-icon/${value.toLowerCase()}.svg?v=1" alt="" aria-hidden="true"></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inkability"><span>Tintbarkeit</span><div><button type="button" class="lorcana-icon-filter ${filters.inkwell==='true'?'active':''}" data-single-filter="inkwell" data-value="true" title="Tintbar" aria-label="Tintbar"><img src="/lorcana-filter-icon/inkable.png?v=2" alt="" aria-hidden="true"></button><button type="button" class="lorcana-icon-filter ${filters.inkwell==='false'?'active':''}" data-single-filter="inkwell" data-value="false" title="Nicht tintbar" aria-label="Nicht tintbar"><img src="/lorcana-filter-icon/uninkable.png?v=1" alt="" aria-hidden="true"></button></div></div>`;
}
function hololiveFilterPanel(filters){
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${deckFilterPills(HOLOLIVE_KIND_FILTERS,'kinds',filters)}</div></div>
    <div class="op-filter-group"><span>Bloom-Level</span><div>${deckFilterPills(HOLOLIVE_BLOOM_FILTERS,'bloomLevels',filters)}</div></div>`;
}

function deckOverviewCard(deck,game,formats){
  const profile=formats.find(item=>item.id===deck.format_id)||formats[0];
  const backUrl=`/card-back/${game.id}`;
  const cover=deck.cover_variant_id?`<img loading="lazy" src="${artUrl(deck.cover_variant_id)}" alt="">`:'';
  const missingLabel=deck.missing_copies?`${deck.missing_copies} fehlen`:deck.required_copies?'Vollständig':'Leer';
  const missingClass=deck.missing_copies?'missing':deck.required_copies?'complete':'';
  return `<button class="deck-overview-card" data-deck="${deck.id}" style="--accent:${game.accent}">
    <div class="deck-stack">
      <span class="deck-stack-card deck-stack-back deck-stack-back-2" style="background-image:url('${backUrl}')"></span>
      <span class="deck-stack-card deck-stack-back deck-stack-back-1" style="background-image:url('${backUrl}')"></span>
      <div class="deck-stack-card deck-stack-top${deck.cover_variant_id?'':' empty'}" ${deck.cover_variant_id?'':`style="background-image:url('${backUrl}')"`}>${cover}</div>
    </div>
    <div class="deck-overview-badge">
      <span class="deck-overview-format">${escapeHtml(profile.name)}</span>
      <h3>${escapeHtml(deck.name)}</h3>
      <div class="deck-overview-stats"><b>${money(deck.deck_value)}</b><span class="deck-overview-missing ${missingClass}">${escapeHtml(missingLabel)}</span></div>
    </div>
  </button>`;
}

function closeDeckAddPopup(){const modal=$('#deck-add-modal');if(modal)modal.remove();document.body.style.overflow='';}

function openDeckAddPopup(card,profile){
  closeDeckAddPopup();
  const zone=profile.zones.find(item=>item.id===card.suggested_zone)||profile.zones[0];
  const maximum=zone.id==='cheer'?20:zone.id==='don'?10:zone.id==='leader'||zone.id==='oshi'?1:4;
  const modal=document.createElement('div');modal.id='deck-add-modal';modal.className='overlay deck-add-overlay';
  modal.innerHTML=`<div class="deck-add-dialog"><button class="close-button" data-deck-add-close>×</button><div class="deck-add-image card-finish-frame ${finishPresentation(card).effect}"><img src="${artUrl(card.variant_id,'full')}" alt="${escapeHtml(card.canonical_name)}"></div><div class="deck-add-copy"><span class="eyebrow">KARTE HINZUFÜGEN</span><h2>${escapeHtml(card.canonical_name)}</h2><p>${escapeHtml(card.collector_number)} · ${card.language} · ${escapeHtml(card.rarity)}</p><div class="automatic-zone"><span>Automatischer Bereich</span><b>${escapeHtml(zone.name)}</b><small>${escapeHtml(card.card_type)} wird nach dem Regelprofil einsortiert.</small></div><label>Menge<select id="deck-add-quantity" class="select-control">${Array.from({length:maximum},(_,index)=>`<option value="${index+1}">${index+1}</option>`).join('')}</select></label><div class="dialog-actions"><button class="secondary-button" data-deck-add-close>Abbrechen</button><button class="primary-button" id="confirm-deck-add">Zu ${escapeHtml(zone.name)} hinzufügen</button></div></div></div>`;
  document.body.append(modal);document.body.style.overflow='hidden';
  $$('[data-deck-add-close]',modal).forEach(button=>button.onclick=closeDeckAddPopup);modal.onmousedown=event=>{if(event.target===modal)closeDeckAddPopup()};
  $('#confirm-deck-add',modal).onclick=async event=>{event.currentTarget.disabled=true;const quantity=Number($('#deck-add-quantity',modal).value);try{const result=await post(`/api/decks/${state.deckId}/cards`,{variant_id:card.variant_id,zone:'auto',delta:quantity});closeDeckAddPopup();state.deckZone=result.zone;toast(`${quantity}× zu ${zone.name} hinzugefügt`);renderDeckbuilder(true)}catch(error){event.currentTarget.disabled=false;toast(error.message)}};
}

async function renderDeckbuilder(preserve=false,catalogPosition=null){
  state.deckCatalogObserver?.disconnect();state.deckCatalogObserver=null;
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Deckbuilder wird geladen …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId),[formats,decks]=await Promise.all([api(`/api/games/${state.activeGameId}/formats`),api(`/api/decks?game_id=${state.activeGameId}`)]);
  if(!state.deckId){
    content.innerHTML=`<div class="deck-page-head deck-overview-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · OFFIZIELLE REGELPROFILE</span><h1>Deine Decks</h1><p>Wähle ein Deck oder beginne ein neues.</p></div><button class="primary-button" id="new-deck">＋ Neues Deck</button></div>${decks.length?`<section class="deck-overview-grid">${decks.map(deck=>deckOverviewCard(deck,game,formats)).join('')}<button class="deck-create-card" id="deck-create-tile"><span>＋</span><b>Neues Deck</b><small>${escapeHtml(formats[0]?.name||'Regelprofil wählen')}</small></button></section>`:`<div class="deck-empty"><span>▱</span><h2>Dein erstes ${escapeHtml(game.short_name)}-Deck</h2><p>Der Builder prüft Kartenzahl, Kopienlimits, Farben und Formatlegalität.</p><button class="primary-button" id="first-deck">Deck erstellen</button></div>`}`;
    $$('.deck-overview-card').forEach(button=>button.onclick=()=>{state.deckId=Number(button.dataset.deck);renderDeckbuilder()});
    $('#new-deck').onclick=()=>createDeck(formats[0]);$('#deck-create-tile')?.addEventListener('click',()=>createDeck(formats[0]));$('#first-deck')?.addEventListener('click',()=>createDeck(formats[0]));return;
  }
  if(!decks.some(deck=>deck.id===state.deckId)){state.deckId=null;return renderDeckbuilder(true)}
  const f=state.deckFilters,isOnePiece=game.id==='one-piece',isLorcana=game.id==='lorcana',isHololive=game.id==='hololive';
  const initialCatalogLimit=Math.max(72,Math.min(20000,Number(catalogPosition?.loadedCount)||72));
  const params=new URLSearchParams({game_id:state.activeGameId,q:f.q||'',sort:f.sort||'number',limit:String(initialCatalogLimit),offset:'0'});
  if(isOnePiece){
    params.set('language','EN');
    ['colors','types','costs','attributes'].forEach(key=>{if(f[key]?.length)params.set(key,f[key].join(','))});
  }else{
    params.set('language',f.language||'all');
    if(isLorcana){
      ['colors','types','costs'].forEach(key=>{if(f[key]?.length)params.set(key,f[key].join(','))});
      if(f.inkwell)params.set('inkwell',f.inkwell);
    }
    if(isHololive){
      ['kinds','bloomLevels'].forEach(key=>{if(f[key]?.length)params.set(key,f[key].join(','))});
    }
  }
  const [detail,catalog]=await Promise.all([api(`/api/decks/${state.deckId}`),api(`/api/deckbuilder/catalog?${params}`)]),profile=formats.find(x=>x.id===detail.deck.format_id)||formats[0];
  if(!profile.zones.some(z=>z.id===state.deckZone))state.deckZone=profile.zones[0].id;
  const validation=detail.validation, zoneCards=detail.cards.filter(c=>c.zone===state.deckZone), currentZone=profile.zones.find(z=>z.id===state.deckZone);
  const summary=detail.summary;
  const purchaseSummary=`<div class="deck-purchase-summary"><div><span>In deiner Sammlung</span><b>${summary.owned_copies} / ${summary.required_copies}</b><small>${summary.complete_entries} Kartenpositionen vollständig</small></div><div class="${summary.missing_copies?'missing':'complete'}"><span>Fehlende Exemplare</span><b>${summary.missing_copies}</b><small>${summary.missing_entries} Kartenpositionen betroffen</small></div><div class="purchase-cost"><span>Fehlende Karten kaufen</span><b>${money(summary.missing_cost)}</b><small>${summary.missing_unpriced_copies?`${summary.missing_unpriced_copies} fehlende Exemplare ohne Preis`:'Alle fehlenden Karten eingepreist'}</small></div></div>`;
  const deckStatChips=`<div class="deck-stat-chips"><div><span>Vorhanden</span><b>${summary.owned_copies}/${summary.required_copies}</b></div><div class="${summary.missing_copies?'missing':'complete'}"><span>Fehlt</span><b>${summary.missing_copies}</b></div><div><span>Nachkauf</span><b>${money(summary.missing_cost)}</b></div><div><span>Deckwert</span><b>${money(summary.deck_value)}</b></div></div>`;
  const validationNotice=validation.valid?'':`<div class="deck-validation invalid"><span class="validation-icon">!</span><div><b>Deck noch nicht spielbereit</b><small>${escapeHtml(validation.errors[0]||validation.warnings[0])}</small></div><span class="validation-count">${validation.errors.length} Fehler · ${validation.warnings.length} Hinweise</span></div>`;
  const deckQuantities=detail.cards.reduce((all,card)=>{all[card.variant_id]=(all[card.variant_id]||0)+card.quantity;return all},{});
  const coverVariantId=detail.deck.cover_variant_id||null;
  let deckWorkspace;
  if(isOnePiece){
    const leaders=detail.cards.filter(c=>c.zone==='leader'),mainCards=detail.cards.filter(c=>c.zone==='main'),explicitDon=detail.cards.filter(c=>c.zone==='don');
    const donCards=[...explicitDon,...(detail.default_don?[detail.default_don]:[])];
    const mainCount=mainCards.reduce((total,card)=>total+card.quantity,0),explicitDonCount=explicitDon.reduce((total,card)=>total+card.quantity,0),displayDonCount=donCards.reduce((total,card)=>total+card.quantity,0);
    deckWorkspace=`<div class="op-deck-overview"><div class="op-leader-panel"><div class="op-section-label"><span>Leader</span><b>${leaders.reduce((total,card)=>total+card.quantity,0)} / 1</b></div><div class="op-leader-card">${leaders.length?leaders.map(card=>deckContentCard(card,true,coverVariantId)).join(''):'<div class="op-leader-empty"><span>＋</span><small>Leader im Katalog wählen</small></div>'}</div></div><div class="op-deck-stats">${deckStatChips}</div></div>
      ${validationNotice}
      <section class="op-deck-section"><div class="op-section-head"><div><span class="eyebrow">DECK</span><h2>Deckkarten</h2></div><b>${mainCount} / 50</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${mainCards.length?mainCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Deckkarten hinzugefügt.</div>'}</div></section>
      <section class="op-deck-section op-don-section"><div class="op-section-head"><div><span class="eyebrow">DON!! · OPTIONAL</span><h2>DON-Karten</h2><small>${detail.default_don?`${detail.default_don.quantity} Standard-DON werden automatisch ergänzt.`:'Alle DON-Karten sind explizit gewählt.'}</small></div><b>${displayDonCount} / 10${explicitDonCount<10?' auto':''}</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${donCards.map(card=>deckContentCard(card,false,coverVariantId)).join('')}</div></section>`;
  }else if(isLorcana){
    const mainCards=detail.cards.filter(card=>card.zone==='main'),mainCount=mainCards.reduce((total,card)=>total+card.quantity,0);
    deckWorkspace=`<div class="generic-deck-overview"><div class="op-deck-stats">${deckStatChips}</div></div>${validationNotice}
      <section class="op-deck-section"><div class="op-section-head"><div><span class="eyebrow">DECK</span><h2>Lorcana-Karten</h2></div><b>${mainCount} / 60+</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${mainCards.length?mainCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Karten hinzugefügt.</div>'}</div></section>`;
  }else if(isHololive){
    const oshiCards=detail.cards.filter(card=>card.zone==='oshi'),mainCards=detail.cards.filter(card=>card.zone==='main'),cheerCards=detail.cards.filter(card=>card.zone==='cheer');
    const count=cards=>cards.reduce((total,card)=>total+card.quantity,0);
    deckWorkspace=`<div class="op-deck-overview"><div class="op-leader-panel"><div class="op-section-label"><span>Oshi</span><b>${count(oshiCards)} / 1</b></div><div class="op-leader-card">${oshiCards.length?oshiCards.map(card=>deckContentCard(card,true,coverVariantId)).join(''):'<div class="op-leader-empty"><span>＋</span><small>Oshi im Katalog wählen</small></div>'}</div></div><div class="op-deck-stats">${deckStatChips}</div></div>${validationNotice}
      <section class="op-deck-section"><div class="op-section-head"><div><span class="eyebrow">MAIN DECK</span><h2>Holomem & Support</h2></div><b>${count(mainCards)} / 50</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${mainCards.length?mainCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Main-Deck-Karten hinzugefügt.</div>'}</div></section>
      <section class="op-deck-section"><div class="op-section-head"><div><span class="eyebrow">CHEER DECK</span><h2>Cheer-Karten</h2></div><b>${count(cheerCards)} / 20</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${cheerCards.length?cheerCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Cheer-Karten hinzugefügt.</div>'}</div></section>`;
  }else{
    deckWorkspace=`${purchaseSummary}${validationNotice}<nav class="zone-tabs">${profile.zones.map(z=>`<button data-zone="${z.id}" class="${z.id===state.deckZone?'active':''}"><span>${escapeHtml(z.name)}</span><b>${validation.counts[z.id]||0} / ${z.target}</b></button>`).join('')}</nav><div class="deck-zone-head"><div><span class="eyebrow">${escapeHtml(currentZone.name).toUpperCase()}</span><h2>${zoneCards.reduce((a,c)=>a+c.quantity,0)} Karten</h2></div><div class="deck-errors">${validation.errors.slice(0,3).map(x=>`<span>! ${escapeHtml(x)}</span>`).join('')}${validation.warnings.slice(0,2).map(x=>`<span class="warning">△ ${escapeHtml(x)}</span>`).join('')}</div></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${zoneCards.length?zoneCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Karten in diesem Bereich.</div>'}</div>`;
  }
  const viewTools=`<div class="op-filter-group catalog-view-tools"><span>Ansicht</span><div><div class="deck-view-toggle segmented" aria-label="Ansicht"><button data-deck-view="list" class="${state.deckView==='list'?'active':''}" title="Liste">☷</button><button data-deck-view="grid" class="${state.deckView==='grid'?'active':''}" title="Kacheln">▦</button></div><label class="catalog-zoom ${state.deckView==='grid'?'':'hidden'}"><input id="deck-zoom" type="range" min="105" max="190" step="5" value="${state.deckZoom}" aria-label="Kachelgröße"><output id="deck-zoom-value">${state.deckZoom}%</output></label></div></div>`;
  const languageControl=isOnePiece?'':`<select id="deck-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(language=>`<option value="${language}" ${f.language===language?'selected':''}>${language}</option>`).join('')}</select>`;
  const gameFilters=isOnePiece?opFilterPanel(f):isLorcana?lorcanaFilterPanel(f):isHololive?hololiveFilterPanel(f):'';
  const filters=`<div class="op-catalog-controls"><div class="filter-search"><span>⌕</span><input id="deck-q" value="${escapeHtml(f.q)}" placeholder="${isOnePiece?'Englische Karten':'Karten'} suchen"></div>${languageControl}<select id="deck-sort" class="select-control"><option value="number">Nummer</option><option value="name">Name</option><option value="cost">Kosten</option><option value="rarity">Seltenheit</option></select><div class="toolbar-spacer"></div>${viewTools}</div>${gameFilters?`<div class="op-catalog-filterbar">${gameFilters}</div>`:''}`;
  content.innerHTML=`<div class="deck-shell deck-shell-editor" style="--deck-card-size:${state.deckZoom}px;--catalog-card-size:${state.deckZoom}px">
    <section class="deck-editor"><header class="deck-editor-head"><button class="compact-back-button" id="deck-overview-back" title="Alle Decks" aria-label="Alle Decks">←</button><div class="deck-title-field"><input id="deck-name" value="${escapeHtml(detail.deck.name)}" aria-label="Deckname"></div><select id="deck-format" class="select-control">${formats.map(x=>`<option value="${x.id}" ${x.id===detail.deck.format_id?'selected':''}>${escapeHtml(x.name)}</option>`).join('')}</select><a href="${escapeHtml(validation.rules_url)}" target="_blank" rel="noopener">Regeln ↗</a><button id="deck-import-open" class="icon-button" title="Deckliste importieren">↧</button><button id="delete-deck" class="icon-button" title="Deck löschen">⌫</button></header>
      ${deckWorkspace}
    </section>
    <aside class="deck-catalog"><div class="deck-catalog-title"><div><b>Kartenkatalog</b><span>${catalog.pagination.total} Basiskarten · ${isOnePiece?'EN':f.language==='all'?'alle Sprachen':f.language}</span></div></div>${filters}<div class="catalog-card-list ${state.deckView==='grid'?'catalog-card-grid-list':''}">${catalog.cards.map(c=>deckCatalogCard(c,profile,deckQuantities)).join('')}<div class="deck-catalog-sentinel">${catalog.pagination.has_more?'Weitere Karten werden geladen …':'Alle Treffer geladen'}</div></div></aside></div>`;
  bindDeckImagePreviews();
  const catalogList=$('.catalog-card-list'),sentinel=$('.deck-catalog-sentinel');
  const catalogSnapshot=()=>({scrollTop:catalogList.scrollTop,loadedCount:catalog.cards.length});
  if(catalogPosition)requestAnimationFrame(()=>{catalogList.scrollTop=Math.max(0,Number(catalogPosition.scrollTop)||0)});
  $('#deck-overview-back').onclick=()=>{state.deckId=null;renderDeckbuilder()};
  $$('[data-deck-view]').forEach(button=>button.onclick=()=>{state.deckView=button.dataset.deckView;renderDeckbuilder(true)});
  $('#deck-zoom')?.addEventListener('input',event=>{state.deckZoom=Number(event.target.value);const shell=$('.deck-shell-editor');shell.style.setProperty('--deck-card-size',`${state.deckZoom}px`);shell.style.setProperty('--catalog-card-size',`${state.deckZoom}px`);$('#deck-zoom-value').textContent=`${state.deckZoom}%`});
  $$('.zone-tabs button').forEach(b=>b.onclick=()=>{state.deckZone=b.dataset.zone;renderDeckbuilder(true)});
  $$('.deck-editor .deck-qty button').forEach(b=>b.onclick=async()=>{const position=catalogSnapshot(),row=b.closest('[data-variant]');await post(`/api/decks/${state.deckId}/cards`,{variant_id:row.dataset.variant,zone:row.dataset.zone||state.deckZone,delta:Number(b.dataset.delta)});renderDeckbuilder(true,position)});
  $$('[data-deck-cover]').forEach(button=>button.onclick=async event=>{event.stopPropagation();const position=catalogSnapshot();await api(`/api/decks/${state.deckId}`,{method:'PATCH',body:JSON.stringify({cover_variant_id:button.dataset.deckCover})});toast('Deckcover gespeichert');renderDeckbuilder(true,position)});
  $$('.deck-editor [data-variant]:not([data-auto])').forEach(row=>row.onclick=e=>{if(e.target.closest('button'))return;openCard(row.dataset.identity,row.dataset.variant)});
  const bindCatalogRows=(root=catalogList)=>{
    $$('[data-catalog-delta]:not([data-bound])',root).forEach(button=>{button.dataset.bound='1';button.onclick=async event=>{event.stopPropagation();const position=catalogSnapshot(),row=button.closest('[data-catalog-variant]');button.disabled=true;try{await post(`/api/decks/${state.deckId}/cards`,{variant_id:row.dataset.catalogVariant,zone:'auto',delta:Number(button.dataset.catalogDelta)});renderDeckbuilder(true,position)}catch(error){button.disabled=false;toast(error.message)}}});
    $$('[data-catalog-variant]:not([data-row-bound])',root).forEach(row=>{row.dataset.rowBound='1';row.onclick=event=>{if(event.target.closest('.catalog-deck-counter'))return;openCard(row.dataset.identity,row.dataset.catalogVariant)}});
  };
  bindCatalogRows();
  let catalogLoading=false;
  const loadMoreCatalog=async()=>{
    if(catalogLoading||!catalog.pagination.has_more)return;
    catalogLoading=true;sentinel.classList.add('loading');
    params.set('offset',String(catalog.cards.length));
    params.set('limit','72');
    try{
      const page=await api(`/api/deckbuilder/catalog?${params}`);
      sentinel.insertAdjacentHTML('beforebegin',page.cards.map(c=>deckCatalogCard(c,profile,deckQuantities)).join(''));
      catalog.cards.push(...page.cards);catalog.pagination=page.pagination;
      sentinel.textContent=page.pagination.has_more?'Weitere Karten werden geladen …':'Alle Treffer geladen';
      bindCatalogRows();bindDeckImagePreviews();
    }catch(error){sentinel.textContent='Weitere Karten konnten nicht geladen werden.';toast(error.message)}
    finally{catalogLoading=false;sentinel.classList.remove('loading')}
  };
  if(catalog.pagination.has_more&&'IntersectionObserver'in window){state.deckCatalogObserver=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))loadMoreCatalog()},{root:catalogList,rootMargin:'240px'});state.deckCatalogObserver.observe(sentinel)}
  $('#deck-name').onchange=e=>saveDeckMeta(e.target.value,$('#deck-format').value,detail.deck.notes||'');$('#deck-format').onchange=e=>saveDeckMeta($('#deck-name').value,e.target.value,detail.deck.notes||'');
  $('#delete-deck').onclick=async()=>{if(!confirm(`Deck „${detail.deck.name}“ löschen?`))return;await api(`/api/decks/${state.deckId}`,{method:'DELETE'});state.deckId=null;renderDeckbuilder()};
  $('#deck-import-open').onclick=()=>{$('#deck-import-preview').classList.remove('visible');$('#deck-import-preview').innerHTML='';$('#deck-apply-import').disabled=true;openOverlay('deck-import-modal')};
  let timer;$('#deck-q').oninput=e=>{clearTimeout(timer);f.q=e.target.value;timer=setTimeout(()=>reRenderPreservingFocus('#deck-q',()=>renderDeckbuilder(true)),250)};
  $('#deck-sort').value=f.sort;$('#deck-sort').onchange=event=>{f.sort=event.target.value;renderDeckbuilder(true)};
  if(isOnePiece){
    $$('[data-op-filter]').forEach(button=>button.onclick=()=>{const key=button.dataset.opFilter,value=button.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(item=>item!==value):[...current,value];renderDeckbuilder(true)});
  }else{
    $('#deck-language').onchange=event=>{f.language=event.target.value;renderDeckbuilder(true)};
    $$('[data-deck-filter]').forEach(button=>button.onclick=()=>{const key=button.dataset.deckFilter,value=button.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(item=>item!==value):[...current,value];renderDeckbuilder(true)});
    $$('[data-single-filter]').forEach(button=>button.onclick=()=>{const key=button.dataset.singleFilter,value=button.dataset.value;f[key]=f[key]===value?'':value;renderDeckbuilder(true)});
  }
}

async function createDeck(profile){const r=await post('/api/decks',{game_id:state.activeGameId,name:'Neues Deck',format_id:profile?.id});state.deckId=r.id;state.deckZone=profile?.zones?.[0]?.id||'main';renderDeckbuilder()}
async function saveDeckMeta(name,format_id,notes){await api(`/api/decks/${state.deckId}`,{method:'PATCH',body:JSON.stringify({name,format_id,notes})});toast('Deck gespeichert');renderDeckbuilder(true)}

async function openCard(identityId,variantId,refresh=false){
  if(!refresh){openOverlay('card-modal');advancedPanelExpanded=false}
  $('#card-dialog').innerHTML='<div class="page-loader"><span></span><p>Kartendetails werden geladen …</p></div>';
  const card=await api(`/api/cards/${identityId}`); state.modalCard=card; state.modalVariant=card.variants.find(v=>v.id===variantId)||card.variants[0];state.activeWatchlists=await api(`/api/watchlists?game_id=${state.modalVariant.game_id}`); renderCardModal();
}

function renderCardModal(){
  const card=state.modalCard,v=state.modalVariant;
  const physicalVariants=card.variants.filter(x=>x.language===v.language);
  const idx=physicalVariants.findIndex(x=>x.id===v.id), gridIdx=state.cards.findIndex(c=>c.identity_id===card.id);
  const variantLabel=x=>`${x.set_code} · ${x.collector_number} · ${variantName(x)}`;
  const languages=[...new Set(card.variants.map(x=>x.language))];
  const visual=finishPresentation(v);
  $('#card-dialog').innerHTML=`<div class="card-modal-layout"><section class="card-stage"><div class="variant-hint top">${idx>0?`<button data-variant-nav="-1">↑ ${escapeHtml(variantLabel(physicalVariants[idx-1]))}</button>`:''}</div><button class="card-nav-button prev" ${gridIdx<=0?'disabled':''} data-card-nav="-1">‹</button><div class="modal-card-frame card-finish-frame ${visual.effect}"><img class="modal-card-image" src="${artUrl(v.id,'full')}" alt="${escapeHtml(card.canonical_name)}"></div><button class="card-nav-button next" ${gridIdx<0||gridIdx>=state.cards.length-1?'disabled':''} data-card-nav="1">›</button><div class="variant-hint bottom">${idx<physicalVariants.length-1?`<button data-variant-nav="1">↓ ${escapeHtml(variantLabel(physicalVariants[idx+1]))}</button>`:''}</div></section>
  <aside class="modal-side"><header class="modal-head"><span class="eyebrow">${escapeHtml(v.set_code)} · ${escapeHtml(v.collector_number)}</span><h2>${escapeHtml(card.canonical_name)}</h2><p>${escapeHtml(v.rarity)} · ${escapeHtml(variantName(v))}</p><div class="language-switcher" aria-label="Sprachversion">${languages.map(language=>`<button data-language="${language}" class="${language===v.language?'active':''}">${language}</button>`).join('')}</div><button class="close-button" data-close="card-modal">×</button></header><nav class="modal-tabs">${[['collection','Sammlung'],['market','Markt'],['card','Karte'],['relationships','Beziehungen']].map(([key,label])=>`<button data-tab="${key}" class="${state.modalTab===key?'active':''}">${label}</button>`).join('')}</nav><div class="modal-content">${modalTabContent(card,v)}</div></aside></div>`;
  $('[data-close="card-modal"]').onclick=()=>closeOverlay('card-modal');
  $$('[data-tab]',$('#card-dialog')).forEach(b=>b.onclick=()=>{state.modalTab=b.dataset.tab;renderCardModal()});
  $$('[data-language]',$('#card-dialog')).forEach(b=>b.onclick=()=>{
    const candidates=card.variants.filter(x=>x.language===b.dataset.language);
    state.modalVariant=candidates.find(x=>x.artwork_id===v.artwork_id&&x.variant_code===v.variant_code)
      ||candidates.find(x=>x.set_code===v.set_code&&x.collector_number===v.collector_number&&x.variant_code===v.variant_code)
      ||candidates.find(x=>x.variant_code===v.variant_code)||candidates[0];
    renderCardModal();
  });
  $$('[data-variant-nav]',$('#card-dialog')).forEach(b=>b.onclick=()=>{state.modalVariant=physicalVariants[idx+Number(b.dataset.variantNav)];renderCardModal()});
  $$('.variant-option',$('#card-dialog')).forEach(b=>b.onclick=()=>{state.modalVariant=card.variants.find(v=>v.id===b.dataset.variant);renderCardModal()});
  $$('[data-card-nav]',$('#card-dialog')).forEach(b=>b.onclick=()=>{const target=state.cards[gridIdx+Number(b.dataset.cardNav)];if(target)openCard(target.identity_id,target.variant_id,true)});
  const qtyControls=$$('.modal-qty-btn',$('#card-dialog')); qtyControls.forEach(b=>b.onclick=()=>changeQuantity(v.id,Number(b.dataset.delta)));
  const watch=$('.modal-watch',$('#card-dialog')); if(watch)watch.onclick=async()=>{const listId=Number($('#modal-watchlist')?.value||state.activeWatchlists[0]?.id);const r=await post('/api/watchlist',{variant_id:v.id,list_id:listId});v.watchlisted=r.active;renderCardModal();refreshWatchCount();toast(r.active?'Zur gewählten Watchlist hinzugefügt':'Von der gewählten Watchlist entfernt')};
  const refresh=$('#price-refresh',$('#card-dialog')); if(refresh)refresh.onclick=async()=>{refresh.disabled=true;refresh.textContent='Preise werden geladen …';try{await post('/api/prices/sync',{});toast('Marktpreise aktualisiert');await openCard(card.id,v.id,true)}catch(error){toast(error.message||'Preisimport fehlgeschlagen');refresh.disabled=false;refresh.textContent='Preise aktualisieren'}};
  const advancedToggle=$('#advanced-toggle',$('#card-dialog'));
  if(advancedToggle){advancedToggle.onclick=toggleAdvancedPanel; if(advancedPanelExpanded)loadAdvancedPanel()}
}

function modalTabContent(card,v){
  const physicalVariants=card.variants.filter(x=>x.language===v.language);
  const modalIsLorcana=v.game_id==='lorcana';
  if(state.modalTab==='collection')return `<div class="detail-section"><div class="detail-section-title">AUSFÜHRUNG · SPRACHE ${v.language}</div><div class="variant-selector">${physicalVariants.map(x=>`<button class="variant-option ${x.id===v.id?'active':''}" data-variant="${x.id}">${finishThumb(x,artUrl(x.id),card.canonical_name,'variant-thumb')}<span><b>${escapeHtml(variantName(x))}</b><small>${escapeHtml(x.set_code)} · ${escapeHtml(x.collector_number)} · ${price(x.price)} · ${x.quantity}×</small></span></button>`).join('')}</div></div><div class="detail-section"><div class="detail-section-title">DEINE SAMMLUNG</div><div class="modal-quantity"><span><b>Menge</b></span><div class="controls"><button class="modal-qty-btn" data-delta="-1">−</button><b>${v.quantity}</b><button class="modal-qty-btn" data-delta="1">＋</button></div></div><div class="watchlist-picker"><select id="modal-watchlist" class="select-control">${state.activeWatchlists.map(l=>`<option value="${l.id}">${escapeHtml(l.name)}</option>`).join('')}</select><button class="secondary-button modal-watch">♡ Watchlist umschalten</button></div><button type="button" class="secondary-button advanced-toggle" id="advanced-toggle" aria-expanded="${advancedPanelExpanded}">Erweitert ${advancedPanelExpanded?'▴':'▾'}</button><div class="advanced-panel ${advancedPanelExpanded?'':'hidden'}" id="advanced-panel"></div></div><div class="detail-grid"><div class="detail-field"><span>Sprachversion</span><b>${v.language}</b></div><div class="detail-field"><span>Sammlungswert</span><b>${(v.price==null&&!v.override_value)?'Kein Preis verfügbar':money((v.override_value||0)+(v.unpriced_quantity||0)*(v.price||0))}</b></div><div class="detail-field"><span>Datenquelle</span><b>${escapeHtml(v.source_type)}</b></div></div>`;
  if(state.modalTab==='market'){
    const original=nativePrice(v);
    const conversion=original?` · ${escapeHtml(original)} in ${escapeHtml(v.price_native_currency)} · EZB ${date(v.price_exchange_date)}`:'';
    const secondaryMetric=v.price_avg30!=null
      ? `<div><span>30-Tage-Ø</span><b>${price(v.price_avg30)}</b></div>`
      : `<div><span>Originalpreis</span><b>${original||'Nicht verfügbar'}</b></div>`;
    return `<div class="price-hero"><span>${escapeHtml(v.price_source)} Marktpreis</span><b>${price(v.price)}</b><small>${v.price==null?'Kein eindeutig zugeordneter Preis verfügbar':`Stand ${date(v.price_observed_at)} · EUR${conversion}`}</small></div>${v.price==null?'':`<div class="market-metrics"><div><span>Niedrig</span><b>${price(v.price_low)}</b></div>${secondaryMetric}<div><span>Anbieter</span><b>${escapeHtml(v.price_source)}</b></div></div>`}<a class="price-source-link" href="${escapeHtml(v.price_url)}" target="_blank" rel="noopener noreferrer"><span>↗</span><div><b>Preisquelle bei ${escapeHtml(v.price_source)} öffnen</b><small>${escapeHtml(v.collector_number)} · ${escapeHtml(modalIsLorcana?lorcanaFinishLabel(v.finish,v.rarity):v.finish)} · direkte Produktseite</small></div><span>→</span></a><button class="secondary-button price-refresh" id="price-refresh">Preise aktualisieren</button>`;
  }
  if(state.modalTab==='card')return `<div class="detail-section"><div class="detail-section-title">KARTENTEXT</div><p class="rules-text">${escapeHtml(card.rules_text)}</p></div><div class="detail-grid"><div class="detail-field"><span>Kartentyp</span><b>${escapeHtml(card.card_type)}</b></div><div class="detail-field"><span>Farbe</span><b>${escapeHtml(card.attributes.color)}</b></div><div class="detail-field"><span>Kosten</span><b>${card.attributes.cost}</b></div><div class="detail-field"><span>Legalität</span><b>${escapeHtml(card.attributes.legality)}</b></div><div class="detail-field"><span>Set</span><b>${escapeHtml(v.set_name)}</b></div><div class="detail-field"><span>Seltenheit</span><b>${escapeHtml(v.rarity)}</b></div></div><a class="price-source-link" href="${escapeHtml(v.image_source_url)}" target="_blank" rel="noopener noreferrer"><span>▧</span><div><b>Bildquelle öffnen</b><small>${escapeHtml(v.image_source)}</small></div><span>→</span></a>`;
  return `<div class="detail-section-title">IDENTITÄT & DRUCKE</div><div class="relationship"><span>◇</span><div><b>Gameplay-Identität</b><small>${escapeHtml(card.canonical_name)} · sprachunabhängig</small></div></div><div class="relationship"><span>文</span><div><b>${new Set(card.variants.map(x=>x.language)).size} Sprachversionen</b><small>${[...new Set(card.variants.map(x=>x.language))].join(' · ')} · separat auswählbar</small></div></div><div class="relationship"><span>▤</span><div><b>${new Set(physicalVariants.map(x=>x.printing_id)).size} Drucke auf ${v.language}</b><small>Set- und Promo-Drucke dieser Sprache</small></div></div><div class="relationship"><span>✦</span><div><b>${physicalVariants.length} Ausführungen auf ${v.language}</b><small>${[...new Set(physicalVariants.map(x=>modalIsLorcana?lorcanaFinishLabel(x.finish,x.rarity):x.finish))].join(' · ')}</small></div></div><div class="relationship"><span>↗</span><div><b>Physisches Set</b><small>${escapeHtml(v.set_name)} (${escapeHtml(v.set_code)})</small></div></div>`;
}

function openOverlay(id){$(`#${id}`).classList.remove('hidden');document.body.style.overflow='hidden';if(id==='global-search-open')return}
function closeOverlay(id){$(`#${id}`).classList.add('hidden');document.body.style.overflow='';if(id==='card-modal'){state.modalCard=null}}

async function refreshWatchCount(){if(!state.activeGameId)return;const lists=await api(`/api/watchlists?game_id=${state.activeGameId}`);$('#watch-count').textContent=lists.reduce((a,l)=>a+l.count,0)}

async function doSearch(q){
  const wrap=$('#search-results'); if(q.trim().length<2){wrap.innerHTML='<div class="empty-search"><b>Finde jede Karte. Sofort.</b><span>Suche nach Name, Set oder Sammlernummer.</span></div>';return}
  const rows=await api(`/api/search?q=${encodeURIComponent(q)}&game_id=${encodeURIComponent(state.activeGameId)}`); if(!rows.length){wrap.innerHTML='<div class="empty-search"><b>Keine Treffer</b><span>Versuche einen anderen Namen oder eine Nummer.</span></div>';return}
  const groups=Object.groupBy?Object.groupBy(rows,x=>x.game_name):rows.reduce((a,x)=>((a[x.game_name]??=[]).push(x),a),{});
  wrap.innerHTML=Object.entries(groups).map(([game,items])=>`<div class="search-group-title">${escapeHtml(game).toUpperCase()}</div>${items.map(r=>`<button class="search-result" data-id="${r.identity_id}" data-variant="${r.variant_id}">${finishThumb(r,artUrl(r.variant_id),r.canonical_name,'search-thumb')}<div><b>${escapeHtml(r.canonical_name)}</b><small>${escapeHtml(r.set_name)} · ${escapeHtml(r.collector_number)} · ${r.language} · ${escapeHtml(r.game_id==='lorcana'?lorcanaFinishLabel(r.finish,r.rarity):r.finish)}</small></div><span class="search-price">${money(r.price)}</span></button>`).join('')}`).join('');
  $$('.search-result',wrap).forEach(el=>el.onclick=()=>{closeOverlay('search-overlay');openCard(el.dataset.id,el.dataset.variant)});
}

let importMode='text', importJsonData=null;

async function previewImport(){
  const box=$('#import-preview');box.classList.add('visible');
  let rows;
  if(importMode==='json'){
    if(!importJsonData||!importJsonData.length){box.innerHTML='<div class="empty-state">Bitte zuerst eine JSON-Backup-Datei wählen.</div>';$('#apply-import').disabled=true;return[]}
    rows=await post('/api/import/json/preview',{collection:importJsonData});
  } else {
    const payload={game_id:$('#import-game').value,language:$('#import-language').value,condition:$('#import-condition').value,text:$('#import-text').value};
    rows=await post('/api/import/preview',payload);
  }
  box.innerHTML=rows.length?rows.map(r=>`<div class="import-row"><span>${r.line}</span><div><b>${r.match?escapeHtml(r.match.canonical_name):escapeHtml(r.number||r.original)}</b><small>${r.quantity||'–'}× · ${r.language||'–'} · ${r.match?escapeHtml(r.match.game_id==='lorcana'?lorcanaFinishLabel(r.match.finish,r.match.rarity):r.match.finish):escapeHtml(r.message||'Kein Treffer')}</small></div><span class="import-status ${r.status}">${r.status==='matched'?'Gefunden':r.status==='ambiguous'?'Prüfen':'Fehlt'}</span></div>`).join(''):'<div class="empty-state">Keine Zeilen erkannt.</div>';
  $('#apply-import').disabled=!rows.some(r=>r.status==='matched');return rows;
}

async function previewDeckImport(){
  const box=$('#deck-import-preview');box.classList.add('visible');
  const rows=await post(`/api/decks/${state.deckId}/import/preview`,{text:$('#deck-import-text').value});
  box.innerHTML=rows.length?rows.map(r=>`<div class="import-row"><span>${r.line}</span><div><b>${r.match?escapeHtml(r.match.canonical_name):escapeHtml(r.original)}</b><small>${r.quantity}× · ${r.match?escapeHtml(r.match.set_name):escapeHtml(r.message||'Kein Treffer')}${r.alt_printings?` · +${r.alt_printings} weitere Drucke`:''}</small></div><span class="import-status ${r.status}">${r.status==='matched'?'Gefunden':r.status==='ambiguous'?'Prüfen':'Fehlt'}</span></div>`).join(''):'<div class="empty-state">Keine Zeilen erkannt.</div>';
  $('#deck-apply-import').disabled=!rows.some(r=>r.status==='matched');return rows;
}

function setImportMode(mode){
  importMode=mode;
  $$('.import-mode-toggle button').forEach(b=>b.classList.toggle('active',b.dataset.importMode===mode));
  $('#import-text-fields').classList.toggle('hidden',mode!=='text');
  $('#import-json-fields').classList.toggle('hidden',mode!=='json');
  $('#import-preview').classList.remove('visible');$('#import-preview').innerHTML='';
  $('#apply-import').disabled=true;
}
function setIeMode(mode){
  $$('.importexport-toggle button').forEach(b=>b.classList.toggle('active',b.dataset.ieMode===mode));
  $('#importexport-import-section').classList.toggle('hidden',mode!=='import');
  $('#importexport-export-section').classList.toggle('hidden',mode!=='export');
  $('#importexport-title').textContent=mode==='import'?'Listenimport':'Sammlung exportieren';
  $('#importexport-eyebrow').textContent=mode==='import'?'SAMMLUNG ERWEITERN':'DATENHOHEIT';
}

async function handleImportJsonFile(file){
  const nameLabel=$('#import-json-filename');
  if(!file){importJsonData=null;nameLabel.textContent='';return}
  try{
    const parsed=JSON.parse(await file.text());
    importJsonData=Array.isArray(parsed)?parsed:(parsed.collection||[]);
    nameLabel.textContent=`${file.name} · ${importJsonData.length} Einträge`;
  }catch(error){
    importJsonData=null;nameLabel.textContent='';
    toast('Die Datei ist kein gültiges JSON-Backup.');
  }
}

function wireGlobalEvents(){
  $$('[data-route]').forEach(el=>el.onclick=()=>{if(el.dataset.route==='decks')state.deckId=null;routeTo(el.dataset.route)});
  $('#mobile-menu').onclick=()=>$('.sidebar').classList.toggle('open');
  $('#sidebar-collapse').onclick=()=>{document.body.classList.toggle('sidebar-collapsed');post('/api/settings',{sidebarCollapsed:document.body.classList.contains('sidebar-collapsed')})};
  $('#user-avatar').onclick=()=>{const hidden=$('#user-popup').classList.toggle('hidden');$('#user-avatar').setAttribute('aria-expanded',String(!hidden))};
  document.addEventListener('click',e=>{const popup=$('#user-popup');if(!popup.classList.contains('hidden')&&!e.target.closest('#user-popup')&&e.target.id!=='user-avatar'){popup.classList.add('hidden');$('#user-avatar').setAttribute('aria-expanded','false')}});
  $('#user-popup').addEventListener('click',e=>{if(e.target.closest('.nav-item')){$('#user-popup').classList.add('hidden');$('#user-avatar').setAttribute('aria-expanded','false')}});
  // Delegated so it keeps working for toolbar filter popups that get rebuilt on every
  // re-render (renderSet/renderAllCards replace #content wholesale on each filter change).
  document.addEventListener('click',e=>{
    const toggle=e.target.closest('[data-filter-toggle]');
    if(toggle){const popup=document.getElementById(toggle.dataset.filterToggle);if(!popup)return;const hidden=popup.classList.toggle('hidden');toggle.setAttribute('aria-expanded',String(!hidden));return}
    $$('.toolbar-filter-popup:not(.hidden)').forEach(popup=>{if(!popup.contains(e.target))popup.classList.add('hidden')});
  });
  window.addEventListener('scroll',()=>$('#back-to-top').classList.toggle('hidden',window.scrollY<600),{passive:true});
  $('#back-to-top').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
  $('#global-game-filter').onchange=e=>{setActiveGame(e.target.value);refreshWatchCount();if(['collection','watchlist','decks'].includes(state.route))routeTo(state.route);else routeTo('game',e.target.value)};
  $('#edit-toggle').onclick=()=>{state.edit=!state.edit;document.body.classList.toggle('editing',state.edit);$('#edit-panel').classList.toggle('on',state.edit);$('#edit-toggle').setAttribute('aria-checked',String(state.edit));toast(state.edit?'Edit Mode aktiviert':'Edit Mode beendet')};
  const searchOpen=()=>{openOverlay('search-overlay');setTimeout(()=>$('#global-search-input').focus(),50)}; $('#global-search-open').onclick=searchOpen;
  document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();searchOpen()}if(e.key==='Escape')$$('.overlay:not(.hidden)').forEach(x=>closeOverlay(x.id));if(state.modalCard&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){const btn=e.key==='ArrowLeft'?'[data-card-nav="-1"]':e.key==='ArrowRight'?'[data-card-nav="1"]':e.key==='ArrowUp'?'[data-variant-nav="-1"]':'[data-variant-nav="1"]';$(btn,$('#card-dialog'))?.click()}});
  let searchTimer;$('#global-search-input').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>doSearch(e.target.value),220)};
  $$('.overlay').forEach(o=>o.addEventListener('mousedown',e=>{if(e.target===o)closeOverlay(o.id)}));$$('[data-close]').forEach(b=>b.onclick=()=>closeOverlay(b.dataset.close));
  const syncImportLanguage=()=>{const gameId=$('#import-game').value;const game=state.boot.games.find(g=>g.id===gameId);const lang=state.boot.settings?.defaultLanguages?.[gameId]||game?.languages[0];if(lang)$('#import-language').value=lang};
  $('#importexport-open').onclick=()=>{setIeMode('import');setImportMode('text');$('#import-game').value=state.activeGameId;syncImportLanguage();openOverlay('import-modal')};
  $('#import-game').onchange=syncImportLanguage;
  $$('.importexport-toggle button').forEach(btn=>btn.onclick=()=>setIeMode(btn.dataset.ieMode));
  $$('.import-mode-toggle button').forEach(btn=>btn.onclick=()=>setImportMode(btn.dataset.importMode));
  $('#import-json-file').onchange=e=>handleImportJsonFile(e.target.files[0]);
  $('#preview-import').onclick=previewImport;
  $('#apply-import').onclick=async()=>{
    const strategy=$('input[name="strategy"]:checked').value;
    const r=importMode==='json'
      ?await post('/api/import/json/apply',{collection:importJsonData||[],strategy})
      :await post('/api/import/apply',{game_id:$('#import-game').value,language:$('#import-language').value,condition:$('#import-condition').value,text:$('#import-text').value,strategy});
    closeOverlay('import-modal');toast(`${r.applied} Einträge wurden importiert.`,'Rückgängig',async()=>{await post(`/api/import/${r.operation_id}/undo`,{});toast('Import wurde rückgängig gemacht.')});state.boot=await api('/api/bootstrap');routeTo('dashboard')
  };
  $('#deck-preview-import').onclick=previewDeckImport;
  $('#deck-apply-import').onclick=async()=>{
    const strategy=$('input[name="deck-import-strategy"]:checked').value;
    const r=await post(`/api/decks/${state.deckId}/import/apply`,{text:$('#deck-import-text').value,strategy});
    closeOverlay('deck-import-modal');
    toast(`${r.applied} Karten importiert${r.skipped_zone?`, ${r.skipped_zone} ohne passende Zone übersprungen`:''}.`);
    renderDeckbuilder(true);
  };
}

async function init(){
  try{state.boot=await api('/api/bootstrap');const u=state.boot.user;$('#user-name').textContent=u.display_name;$('#user-role').textContent=u.role==='admin'?'Administrator':'Sammler';$('#user-avatar').textContent=initials(u.display_name);document.body.classList.toggle('is-admin',u.role==='admin');const gameOptions=state.boot.games.map(g=>`<option value="${g.id}">${escapeHtml(g.short_name)}</option>`).join('');$('#import-game').innerHTML=state.boot.games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');$('#global-game-filter').innerHTML=gameOptions;const settings=state.boot.settings||{};state.setZoom=settings.setZoom||3;setActiveGame(settings.activeGameId||state.boot.games[0].id,false);if(settings.sidebarCollapsed)document.body.classList.add('sidebar-collapsed');wireGlobalEvents();await refreshWatchCount();renderDashboard()}catch(error){content.innerHTML=`<div class="empty-state"><b>DeckLedger konnte nicht geladen werden</b><span>${escapeHtml(error.message)}</span></div>`}}

init();
