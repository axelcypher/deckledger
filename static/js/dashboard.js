// Dashboard: totals, banner, recent additions.

function renderDashboard(){
  const games=state.boot.games;
  const activeGame=games.find(game=>game.id===state.activeGameId)||games[0];
  const totalValue=activeGame?.value||0,copies=activeGame?.copies||0,unique=activeGame?.unique_cards||0;
  const dashboardStats=statPill([
    {label:'Gesamtwert',value:money(totalValue)},
    {label:'Karten',value:copies},
    {label:'Varianten',value:unique},
  ],{className:'hero-summary',columns:3,mobileColumns:3});
  content.innerHTML=`<div class="dashboard-shell">
    <section class="dashboard-hero">
      <div class="hero-top">
        <div class="hero-copy"><h1>Willkommen zurück, ${escapeHtml(state.boot.user.display_name.split(' ')[0])}.</h1><p>Deine Sammlung wächst. Hier siehst du ihren aktuellen Stand über alle Spiele hinweg.</p></div>
        ${dashboardStats}
      </div>
      <nav class="dashboard-quick-nav" aria-label="Schnellzugriff">
        <button type="button" data-dashboard-route="collection"><svg viewBox="0 0 24 24"><rect x="5" y="3" width="12" height="16" rx="2"/><path d="M9 21h9a2 2 0 0 0 2-2V7"/></svg><span>Sammlung</span><strong>${activeGame?.main_completion||0}%</strong></button>
        <button type="button" data-dashboard-route="game"><svg viewBox="0 0 24 24"><path d="m12 3 8 4-8 4-8-4 8-4Z"/><path d="m4 11 8 4 8-4M4 15l8 4 8-4"/></svg><span>Sets</span><strong>${activeGame?.set_count||0}</strong></button>
        <button type="button" data-dashboard-route="decks"><svg viewBox="0 0 24 24"><rect x="5" y="4" width="13" height="16" rx="2"/><path d="M9 2h9a2 2 0 0 1 2 2v13"/></svg><span>Decks</span><strong>${activeGame?.deck_count||0}</strong></button>
        <button type="button" data-dashboard-route="watchlist"><svg viewBox="0 0 24 24"><path d="M12 20.5s-7.5-4.6-9.8-9A5.4 5.4 0 0 1 12 6a5.4 5.4 0 0 1 9.8 5.5c-2.3 4.4-9.8 9-9.8 9Z"/></svg><span>Watchlist</span><strong>${activeGame?.watch_count||0}</strong></button>
        <button type="button" data-dashboard-route="sheets"><svg viewBox="0 0 24 24"><path d="M4 8h15m-4-4 4 4-4 4M20 16H5m4-4-4 4 4 4"/></svg><span>Sheets</span><strong>${activeGame?.sheet_count||0}</strong></button>
      </nav>
      <section id="home-banner" class="home-banner hidden">
        <div class="home-banner-head"><span class="eyebrow" id="home-banner-label">NEU &amp; ANGESAGT</span></div>
        <div class="home-banner-viewport"><div class="home-banner-track" id="home-banner-track"></div></div>
      </section>
      <section id="dashboard-recent" class="dashboard-recent hidden">
        <h2>Recent Additions</h2>
        <div id="dashboard-recent-list" class="dashboard-recent-list"></div>
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
    <button type="button" class="dashboard-account-button" data-dashboard-route="settings">Settings</button>
  </div>`;
  $$('[data-game]',content).forEach(el=>el.onclick=()=>{setActiveGame(el.dataset.game);routeTo('game',el.dataset.game)});
  $$('[data-dashboard-route]',content).forEach(el=>el.onclick=()=>{const route=el.dataset.dashboardRoute;routeTo(route,route==='game'?state.activeGameId:undefined)});
  const bannerTrack=$('#home-banner-track');
  bannerTrack.addEventListener('click',e=>{const card=e.target.closest('.banner-card');if(card)openCard(card.dataset.identity,card.dataset.variant)});
  // The reel waits while a mouse pointer rests on it. Set from pointer events, not with :hover --
  // on a touch screen :hover sticks to the last thing tapped, so opening a card from the reel
  // left it standing still until something else was tapped.
  bannerTrack.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse')bannerTrack.classList.add('paused')});
  bannerTrack.addEventListener('pointerleave',()=>bannerTrack.classList.remove('paused'));
  loadHomeBanner();
  loadRecentAdditions();
}

const bannerDuration=cards=>Math.max(30,cards.length*3.5);

function bannerCard(c,accent){
  return `<div class="banner-card" style="--accent:${accent}" data-identity="${c.identity_id}" data-variant="${c.variant_id}"><div class="banner-card-art card-finish-frame ${finishPresentation(c).effect}"><img loading="eager" decoding="async" src="${artUrl(c.variant_id)}" alt="${escapeHtml(c.canonical_name)}"></div><div class="banner-card-meta"><b title="${escapeHtml(c.canonical_name)}">${escapeHtml(c.canonical_name)}</b><span>${price(c.price)}</span></div></div>`;
}

function preloadBannerImages(slides){
  for(const slide of slides)for(const c of slide.cards){const img=new Image();img.src=artUrl(c.variant_id)}
}

function paintBannerSlide(index){
  const slide=state.homeBanner.slides[index],track=$('#home-banner-track');
  if(!track)return 0;
  const cardsHtml=slide.cards.map(c=>bannerCard(c,slide.accent)).join('');
  track.innerHTML=`<div class="home-banner-group">${cardsHtml}</div><div class="home-banner-group" aria-hidden="true">${cardsHtml}</div>`;
  const duration=bannerDuration(slide.cards);
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
  if(label)label.textContent='Highlights';
  return duration;
}

async function loadHomeBanner(){
  clearTimeout(state.homeBannerTimer);
  try{
    const data=await api('/api/home-banner');
    const matching=data.slides.filter(slide=>slide.game_id===state.activeGameId);
    const seen=new Set(),cards=[];
    for(const slide of matching)for(const card of slide.cards){
      if(seen.has(card.identity_id))continue;
      seen.add(card.identity_id);cards.push(card);
    }
    if(!cards.length||!$('#home-banner'))return;
    const reel={...matching[0],cards};
    state.homeBanner={slides:[reel],index:0};
    preloadBannerImages([reel]);
    paintBannerSlide(0);
    $('#home-banner').classList.toggle('always-moving',Boolean(state.boot.settings?.homeBanner?.alwaysMoving));
    $('#home-banner').classList.remove('hidden');
  }catch(error){/* banner is decorative; fail silently */}
}

async function loadRecentAdditions(){
  const section=$('#dashboard-recent'),list=$('#dashboard-recent-list');
  if(!section||!list)return;
  try{
    const cards=await api(`/api/home-recent?game_id=${encodeURIComponent(state.activeGameId)}`);
    if(!cards.length||!$('#dashboard-recent-list'))return;
    list.innerHTML=cards.slice(0,4).map(card=>`<button type="button" class="dashboard-recent-row" data-recent-identity="${card.identity_id}" data-recent-variant="${card.variant_id}">
      <img loading="lazy" decoding="async" src="${artUrl(card.variant_id)}" alt="">
      <span class="dashboard-recent-copy"><b>${escapeHtml(card.canonical_name)}</b><small>${escapeHtml(card.set_name)} · #${escapeHtml(card.collector_number)}</small></span>
      <span class="dashboard-recent-price">${price(card.price)}</span>
    </button>`).join('');
    section.classList.remove('hidden');
    $$('[data-recent-variant]',list).forEach(row=>row.onclick=()=>openCard(row.dataset.recentIdentity,row.dataset.recentVariant));
  }catch(error){/* recent additions are supplementary; keep the dashboard usable */}
}
