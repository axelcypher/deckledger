// Wiring of the page's fixed controls and start-up. Loaded last.

function wireGlobalEvents(){
  // Re-align the complete stage geometry and reflection mask on viewport changes.
  let modalGeometryFrame=0;
  const scheduleModalGeometry=()=>{
    cancelAnimationFrame(modalGeometryFrame);
    modalGeometryFrame=requestAnimationFrame(()=>{if(state.modalCard)alignReflectionMask()});
  };
  window.addEventListener('resize',scheduleModalGeometry);
  window.visualViewport?.addEventListener('resize',scheduleModalGeometry);
  // Parallax toolkit -- tilt-on-press + mouseover shine, matching the poke-holo.simey.me /
  // pokemon-cards-css reference interaction. Now wired to the card detail modal: the whole
  // stage card (.modal-card-frame-wrap) is a .card-tilt-zone, the card itself
  // (.modal-card-frame) is the .card-tilt element -- pressing+dragging it rotates it toward
  // the pointer, matching "wenn die Karte angeklickt wird".
  // --tilt-x/--tilt-y are set on the ZONE, not on .card-tilt directly, and read via var()
  // inheritance by both .card-tilt (foil-effects.css) AND .modal-card-reflection
  // (mobile-theme.css) -- CSS custom properties inherit to descendants by default, so one
  // write keeps the card and its floor reflection perfectly in sync without the JS needing to
  // know the reflection exists. Same reasoning for is-dragging: toggled on the zone itself, not
  // on individual descendants, so every consumer's own "no transition while dragging" rule
  // (keyed off .card-tilt-zone.is-dragging ...) reacts identically. Currently only the card
  // detail modal opts in; unused everywhere else the toolkit's classes aren't present.
  let cardTiltDragging=null;
  let cardTiltPointerId=null;
  // Loaded once, lazily -- non-Lorcana sessions / browsers where the dynamic import fails never
  // pay for this (foilInteractionController stays null forever, every call site below no-ops,
  // same fallback-to-CSS path as "WebGL not supported" -- see 12).
  let foilInteractionController=null;
  import('/static/lorcana-foil/FoilInteractionController.js').then(m=>{foilInteractionController=m.foilInteractionController}).catch(()=>{});
  // Builds the LorcanaFoilInput for whichever card is currently in the modal, reusing the
  // official-layer metadata hydrateOfficialFoilLayers() already fetched (not re-requested per
  // hover). null covers every "nothing to attach WebGL to" case at once: non-Lorcana, no
  // official match, meta not loaded yet, or the meta belongs to a card that's since been
  // switched away from (a fast language/variant change mid-fetch) -- every case falls back to
  // the plain CSS effect, never an error or a blank card (12).
  const foilInputForModal=()=>{
    const v=state.modalVariant;
    if(!v||v.game_id!=='lorcana')return null;
    const meta=state.modalFoilLayerMeta;
    if(!meta||!meta.available||state.modalFoilLayerMetaVariantId!==v.id)return null;
    return {
      cardImageUrl: artUrl(v.id,'full'),
      foilType: meta.foil_type||null,
      foilMaskUrl: meta.mask_url||null,
      topLayer: meta.foil_top_layer||null,
      topLayerMaskUrl: meta.top_layer_mask_url||null,
      secondTopLayerMaskUrl: meta.second_top_layer_mask_url||null,
      hotFoilColor: meta.hot_foil_color||null,
      secondHotFoilColor: meta.second_hot_foil_color||null,
    };
  };
  // Crossfade (5): is-webgl-active fades foil-fx-a/b out via the exact same opacity transition
  // they already have (foil-effects.css) while the canvas fades itself in -- only added once
  // attach() actually resolves true, so a WebGL failure/no-match leaves the class off and the
  // card simply keeps running its untouched idle CSS animation, satisfying "bleibt der bisherige
  // CSS-Foil aktiv" (12) without any extra branching here.
  const startWebglHover=(zone,clientX,clientY)=>{
    if(!foilInteractionController)return;
    const card=$('.card-tilt',zone); if(!card)return;
    const input=foilInputForModal(); if(!input)return;
    foilInteractionController.markPending(card);
    foilInteractionController.attach(card,input).then(ok=>{
      if(!ok)return;
      zone.classList.add('is-webgl-active');
      foilInteractionController.updatePointer(clientX,clientY);
    });
  };
  const stopWebglHover=zone=>{
    zone.classList.remove('is-webgl-active');
    foilInteractionController?.fadeOutAndDetach();
  };
  // ---- PARKED per explicit instruction ("der aktuelle interaktive Effekt soll erstmal
  // geparkt werden") -- the 1:1 Trainer-Gallery-Holo port (rainbow/hot-spot/glare reveal on
  // foil-fx-c/.card-shine, driven by is-parallax-active) while the WebGL replacement above is
  // evaluated. Nothing below is deleted -- setParallaxReveal, the foil-fx-c/.card-shine CSS
  // (foil-effects.css) and the is-parallax-active rules are all still complete and correct, just
  // not called from the live pointer wiring anymore (see startWebglHover/stopWebglHover above,
  // which is what pointerdown/pointerover/pointermove/pointerup/pointerout actually call now).
  // Re-enabling it later is: call setParallaxReveal(zone,...) again + toggle is-parallax-active
  // where startWebglHover/stopWebglHover are called below.
  // bt() is the reference's own remap/lerp helper (their bundled JS, minified as `bt`) --
  // ported verbatim, not reinvented: linear-interpolate `v` from [inMin,inMax] into [outMin,outMax].
  const tgRemap=(v,inMin,inMax,outMin,outMax)=>outMin+(v-inMin)/(inMax-inMin)*(outMax-outMin);
  const setParallaxReveal=(zone,clientX,clientY)=>{
    const r=zone.getBoundingClientRect();
    // fx/fy are the pointer's position as 0-100 within the card, matching the reference's own
    // --pointer-x/--pointer-y convention exactly.
    const fx=(clientX-r.left)/r.width*100, fy=(clientY-r.top)/r.height*100;
    // --tg-y: their `background.y = bt(pointerY, 0, 100, 33, 67)` -- narrow band, NOT the full
    // 0-100 range, feeds .card__shine's own background-position (vertical only; the reference's
    // horizontal background-position value is dead code there -- .card__shine has a single
    // background-image layer, so only the first of its two comma-separated position values
    // ever actually applies, per the CSS spec's layer-count truncation rule).
    zone.style.setProperty('--tg-y',`${tgRemap(fy,0,100,33,67).toFixed(2)}%`);
    // --tg-from-center: their exact formula, clamp(sqrt((y-50)^2+(x-50)^2)/50, 0, 1) -- feeds
    // both shine layers' brightness(...) filters, unitless (used inside calc() as a raw number).
    const fromCenter=Math.min(1,Math.max(0,Math.sqrt((fy-50)**2+(fx-50)**2)/50));
    zone.style.setProperty('--tg-from-center',fromCenter.toFixed(3));
    // --tg-spot-x/y: their `.card__shine:after` offset, `(pointer*0.5)+25` -- the hot-spot only
    // travels the CENTRAL half of the card (25%-75%), not edge-to-edge.
    zone.style.setProperty('--tg-spot-x',`${(fx*0.5+25).toFixed(2)}%`);
    zone.style.setProperty('--tg-spot-y',`${(fy*0.5+25).toFixed(2)}%`);
    // --shine-x/y (.card-shine = their .card__glare, a separate element): straight pointer
    // position, no remap -- matches `radial-gradient(... at var(--pointer-x) var(--pointer-y))`.
    const shine=$('.card-shine',zone);
    if(shine){
      shine.style.setProperty('--shine-x',`${fx.toFixed(2)}%`);
      shine.style.setProperty('--shine-y',`${fy.toFixed(2)}%`);
      shine.style.setProperty('--shine-opacity','1');
    }
  };
  const setCardTilt=(zone,clientX,clientY)=>{
    if(!$('.card-tilt',zone))return;
    const r=zone.getBoundingClientRect();
    // Pointer capture intentionally keeps reporting coordinates beyond the card while a drag
    // is active. Clamp those normalized values so `max` remains an actual angular limit.
    const px=Math.max(-1,Math.min(1,((clientX-r.left)/r.width-0.5)*2));
    const py=Math.max(-1,Math.min(1,((clientY-r.top)/r.height-0.5)*2));
    const max=14; // deg -- a real tilt like the reference, not a token wiggle
    zone.style.setProperty('--tilt-y',`${(px*max).toFixed(1)}deg`);
    zone.style.setProperty('--tilt-x',`${(-py*max).toFixed(1)}deg`);
    // setParallaxReveal(zone,clientX,clientY) -- PARKED, see the comment block above.
  };
  const resetCardTilt=zone=>{
    if(!$('.card-tilt',zone))return;
    zone.style.setProperty('--tilt-x','0deg'); zone.style.setProperty('--tilt-y','0deg');
  };
  document.addEventListener('pointerdown',e=>{
    const zone=e.target.closest('.card-tilt-zone'); if(!zone)return;
    if(cardTiltDragging&&cardTiltPointerId!==e.pointerId)return;
    cardTiltDragging=zone;
    cardTiltPointerId=e.pointerId;
    zone.classList.add('is-dragging');
    // Keep the complete gesture on the stationary tilt zone. The rotated card can move out
    // from underneath the pointer while it is being tilted; without capture that produces a
    // pointerout and the old handler snapped the card back to zero mid-drag.
    try{zone.setPointerCapture(e.pointerId)}catch{}
    setCardTilt(zone,e.clientX,e.clientY);
    // Touch/pen press has no hover state to trigger WebGL from otherwise -- this is its entry
    // point, same reasoning the parked CSS reveal used.
    startWebglHover(zone,e.clientX,e.clientY);
  });
  // pointerover (bubbles), pairs with pointerout below -- fires once per fresh entry into the
  // zone (relatedTarget check same as pointerout's), NOT on every move within it. Mouse only:
  // matches .card-shine's pre-existing "der Mouseover Shine-Effekt" scoping -- touch has no
  // hover to drive this from before an actual press.
  document.addEventListener('pointerover',e=>{
    if(e.pointerType!=='mouse')return;
    const zone=e.target.closest('.card-tilt-zone'); if(!zone)return;
    if(zone.contains(e.relatedTarget))return;
    startWebglHover(zone,e.clientX,e.clientY);
  });
  document.addEventListener('pointermove',e=>{
    if(cardTiltDragging&&e.pointerId===cardTiltPointerId){
      setCardTilt(cardTiltDragging,e.clientX,e.clientY);
      foilInteractionController?.updatePointer(e.clientX,e.clientY);
      return;
    }
    if(e.pointerType!=='mouse')return;
    const zone=e.target.closest('.card-tilt-zone'); if(!zone)return;
    foilInteractionController?.updatePointer(e.clientX,e.clientY);
  });
  document.addEventListener('pointerup',e=>{
    if(!cardTiltDragging||e.pointerId!==cardTiltPointerId)return;
    cardTiltDragging.classList.remove('is-dragging');
    resetCardTilt(cardTiltDragging);
    // Mouse release: the pointer is still hovering (releasing a button doesn't move the
    // cursor), so leave the WebGL layer running -- the hover handlers above keep driving it
    // until the mouse actually leaves. Touch/pen has no hover to fall back to once lifted, so
    // end it immediately along with the tilt.
    if(e.pointerType!=='mouse')stopWebglHover(cardTiltDragging);
    cardTiltDragging=null;
    cardTiltPointerId=null;
  });
  document.addEventListener('pointercancel',e=>{
    if(!cardTiltDragging||e.pointerId!==cardTiltPointerId)return;
    const zone=cardTiltDragging;
    zone.classList.remove('is-dragging');
    resetCardTilt(zone);
    stopWebglHover(zone);
    cardTiltDragging=null;
    cardTiltPointerId=null;
  });
  // pointerout (bubbles), not pointerleave (doesn't) -- delegation needs the bubbling version.
  // relatedTarget check confirms the pointer actually left the zone, not just moved to a child
  // element still inside it.
  document.addEventListener('pointerout',e=>{
    const zone=e.target.closest('.card-tilt-zone'); if(!zone)return;
    if(zone.contains(e.relatedTarget))return;
    // Pointer capture deliberately keeps an active drag alive even when the visual card has
    // tilted away from the cursor. Cleanup belongs to pointerup/pointercancel above.
    if(cardTiltDragging===zone&&e.pointerId===cardTiltPointerId)return;
    resetCardTilt(zone);
    stopWebglHover(zone);
  });
  $$('[data-route]').forEach(el=>el.onclick=()=>{if(el.dataset.route==='decks')state.deckId=null;if(el.dataset.route==='sheets'){state.sheetId=null;state.dealId=null}routeTo(el.dataset.route)});
  $('#sidebar-collapse').onclick=()=>{document.body.classList.toggle('sidebar-collapsed');post('/api/settings',{sidebarCollapsed:document.body.classList.contains('sidebar-collapsed')})};
  $('#user-avatar').onclick=()=>{const hidden=$('#user-popup').classList.toggle('hidden');$('#user-avatar').setAttribute('aria-expanded',String(!hidden))};
  document.addEventListener('click',e=>{const popup=$('#user-popup');if(!popup.classList.contains('hidden')&&!e.target.closest('#user-popup')&&e.target.id!=='user-avatar'){popup.classList.add('hidden');$('#user-avatar').setAttribute('aria-expanded','false')}});
  $('#user-popup').addEventListener('click',e=>{if(e.target.closest('.nav-item')){$('#user-popup').classList.add('hidden');$('#user-avatar').setAttribute('aria-expanded','false')}});
  // Delegated so it keeps working for toolbar filter popups that get rebuilt on every
  // re-render (renderSet/renderAllCards replace #content wholesale on each filter change).
  document.addEventListener('deckledger-filter-toggle',event=>{
    if(event.detail?.key)state.mobileFiltersOpen[event.detail.key]=Boolean(event.detail.open);
  });
  document.addEventListener('click',e=>{
    const toggle=e.target.closest('[data-filter-toggle]');
    if(toggle){const popup=document.getElementById(toggle.dataset.filterToggle);if(!popup)return;const hidden=popup.classList.toggle('hidden');toggle.setAttribute('aria-expanded',String(!hidden));return}
    $$('.toolbar-filter-popup:not(.hidden)').forEach(popup=>{if(!popup.contains(e.target))popup.classList.add('hidden')});
  });
  window.addEventListener('scroll',()=>$('#back-to-top').classList.toggle('hidden',window.scrollY<600),{passive:true});
  $('#back-to-top').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
  // Choosing another game keeps the view that is open and shows it for that game. Only a single
  // set has no counterpart there: it gives way to the new game's set overview. Settings and admin
  // do not depend on the game and are left as they are (a redraw would drop unsaved input).
  $('#global-game-filter').onchange=e=>{
    const gameId=e.target.value,route=state.route;
    setActiveGame(gameId);refreshWatchCount();
    if(route==='settings'||route==='admin')return;
    if(route==='set'||route==='game')routeTo('game',gameId);
    else if(route==='game-cards')routeTo('game-cards',gameId);
    else routeTo(route);
  };
  $('#edit-toggle').onclick=toggleEditMode;
  $('#edit-indicator').onclick=()=>setEditMode(false,true);
  // Where the sidebar is a narrow rail the panel shows only its icon; the panel itself is the switch then.
  $('#edit-panel').onclick=event=>{if(!event.target.closest('#edit-toggle')&&getComputedStyle($('#edit-toggle')).display==='none')toggleEditMode()};
  $('#mobile-edit-toggle')?.addEventListener('click',toggleEditMode);
  setEditMode(state.edit);
  const searchOpen=()=>{openOverlay('search-overlay');setTimeout(()=>$('#global-search-input').focus(),50)}; $('#global-search-open').onclick=searchOpen;
  document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();searchOpen()}if(e.key==='Escape'){setDeckCatalogOpen(false);$$('.overlay:not(.hidden)').forEach(x=>closeOverlay(x.id))}if(state.modalCard&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){const btn=e.key==='ArrowLeft'?'[data-card-nav="-1"]':e.key==='ArrowRight'?'[data-card-nav="1"]':e.key==='ArrowUp'?'[data-variant-nav="-1"]':'[data-variant-nav="1"]';$(btn,$('#card-dialog'))?.click()}});
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
    const button=$('#apply-import'),originalLabel=button.textContent;
    button.disabled=true;button.textContent='Wird importiert …';
    try{
      const strategy=$('input[name="strategy"]:checked').value;
      const r=importMode==='json'
        ?await post('/api/import/json/apply',{collection:importJsonData||[],...importJsonExtras,strategy})
        :await post('/api/import/apply',{game_id:$('#import-game').value,language:$('#import-language').value,condition:$('#import-condition').value,text:$('#import-text').value,strategy});
      const restored=[r.decks_restored&&`${r.decks_restored} Decks`,r.watchlist_entries_restored&&`${r.watchlist_entries_restored} Watchlist-Einträge`,r.deals_restored&&`${r.deals_restored} Vorgänge`,r.sheets_restored&&`${r.sheets_restored} Sheets`,r.decks_skipped&&`${r.decks_skipped} vorhandene Decks übersprungen`,r.sheets_skipped&&`${r.sheets_skipped} vorhandene Sheets übersprungen`].filter(Boolean);
      closeOverlay('import-modal');toast(`${r.applied} Einträge wurden importiert${restored.length?` · ${restored.join(' · ')}`:''}.`,'Rückgängig',async()=>{await post(`/api/import/${r.operation_id}/undo`,{});toast('Import wurde rückgängig gemacht.')});state.boot=await api('/api/bootstrap');routeTo('dashboard')
    }catch(error){
      toast(error.message);
    }finally{
      button.disabled=false;button.textContent=originalLabel;
    }
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

const OAUTH_ERROR_MESSAGES={
  not_configured:'Single Sign-On ist auf diesem Server nicht aktiviert.',
  state_mismatch:'Die Anmeldeanfrage ist abgelaufen oder ungültig. Bitte erneut versuchen.',
  provider_error:'Der Identity Provider hat die Anmeldung abgelehnt oder ist nicht erreichbar.',
  already_linked:'Diese SSO-Identität ist bereits mit einem anderen Konto verknüpft.',
};
async function init(){
  try{state.boot=await api('/api/bootstrap');const u=state.boot.user;$('#user-name').textContent=u.display_name;$('#user-role').textContent=u.role==='admin'?'Administrator':'Sammler';$('#user-avatar').textContent=initials(u.display_name);document.body.classList.toggle('is-admin',u.role==='admin');const gameOptions=state.boot.games.map(g=>`<option value="${g.id}">${escapeHtml(g.short_name)}</option>`).join('');$('#import-game').innerHTML=state.boot.games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');$('#global-game-filter').innerHTML=gameOptions;const settings=state.boot.settings||{};state.setZoom=settings.setZoom||3;setActiveGame(settings.activeGameId||state.boot.games[0].id,false);state.tileEditions=settings.tileEditions||{};if(settings.sidebarCollapsed)document.body.classList.add('sidebar-collapsed');document.body.classList.toggle('mobile-light',settings.mobileThemeAppearance==='light');wireGlobalEvents();await refreshWatchCount();document.body.dataset.route='dashboard';renderDashboard();
    // Landed here from a self-service SSO "Verbinden" click in Settings (the initiator was
    // already authenticated, so the callback couldn't show its error on the logged-out /login
    // page -- see app.py's oauth_failure_redirect()). Surface it as a toast instead, once.
    const params=new URLSearchParams(location.search);
    if(params.has('linked')){toast('SSO-Verbindung hergestellt');state.boot.user.oauth_linked=true;history.replaceState(null,'',location.pathname)}
    else if(params.has('oauth_error')){toast(OAUTH_ERROR_MESSAGES[params.get('oauth_error')]||'SSO-Verknüpfung fehlgeschlagen.');history.replaceState(null,'',location.pathname)}
    else if(params.has('ebay'))handleEbayReturn();
  }catch(error){content.innerHTML=`<div class="empty-state"><b>DeckLedger konnte nicht geladen werden</b><span>${escapeHtml(error.message)}</span></div>`}}

// The outbox is replayed once the signed-in user is known (see queueOfflineMutation), in case
// the app was reopened after being offline and is already back online.
init().then(()=>{
  updateOfflineIndicator();if(navigator.onLine)syncOfflineQueue();
  // The watcher works in the background; the badge catches up with it now and then.
  if(state.boot){setInboxCount(state.boot.inbox_new||0);setInterval(refreshInboxCount,INBOX_REFRESH_MS)}
  // Only an answer that really came from the server says who is signed in.
  if(state.boot&&!serverUnreachable)dropForeignOfflineSave();
});

// Registered independent of init() -- offline shell caching shouldn't block
// or be blocked by the initial data load. Service workers require a secure
// context (HTTPS or localhost), so this silently no-ops over plain HTTP.
if('serviceWorker' in navigator){
  window.addEventListener('load',()=>{
    navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
    // Without this the browser may drop the saved pages, images and queued changes on its own
    // when the device runs low on space.
    navigator.storage?.persist?.().catch(()=>{});
  });
}
