// The card dialog.

async function openCard(identityId,variantId,refresh=false){
  if(!refresh){openOverlay('card-modal');advancedPanelExpanded=false}
  $('#card-dialog').innerHTML='<div class="page-loader"><span></span><p>Kartendetails werden geladen …</p></div>';
  const card=await api(`/api/cards/${identityId}`),variant=card.variants.find(v=>v.id===variantId)||card.variants[0],watchlists=await api(`/api/watchlists?game_id=${variant.game_id}`);
  // Closed while it was loading (a background refresh can still be on its way): there is nothing
  // to draw into, and keeping the card as "open" would mislead everything that checks for it.
  if($('#card-modal').classList.contains('hidden'))return;
  state.modalCard=card; state.modalVariant=variant; state.activeWatchlists=watchlists; renderCardModal();
}

let modalVariantSwitching=false;

async function closeVariantSettings(dialog){
  const settings=$$('.variant-setting.open',dialog);
  await Promise.all(settings.map(async setting=>{
    const options=$('.variant-setting-options',setting);
    const height=options?.getBoundingClientRect().height||0;
    const animation=height?options.animate(
      [{height:`${height}px`,opacity:1},{height:'0px',opacity:0}],
      {duration:220,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'}
    ):null;
    if(animation)await animation.finished.catch(()=>{});
    setting.classList.remove('open');
    $('[data-variant-setting-toggle]',setting)?.setAttribute('aria-expanded','false');
    animation?.cancel();
  }));
}

function preloadModalCardArt(variant){
  return new Promise(resolve=>{
    const image=new Image();
    const done=()=>resolve();
    image.onload=done; image.onerror=done; image.src=artUrl(variant.id,'full');
    if(image.complete)resolve();
  });
}

async function transitionModalVariant(nextVariant){
  if(!nextVariant||modalVariantSwitching)return;
  const dialog=$('#card-dialog');
  modalVariantSwitching=true;
  try{
    const closing=closeVariantSettings(dialog);
    if(nextVariant.id===state.modalVariant?.id){await closing;return}
    await Promise.all([closing,preloadModalCardArt(nextVariant)]);
    const outgoing=$$('.modal-card-frame-wrap,.modal-card-reflection-mask',dialog);
    await Promise.all(outgoing.map(element=>element.animate(
      [{opacity:1},{opacity:0}],{duration:170,easing:'ease-in-out',fill:'forwards'}
    ).finished.catch(()=>{})));
    state.modalVariant=nextVariant;
    renderCardModal();
    const incoming=$$('.modal-card-frame-wrap,.modal-card-reflection-mask',$('#card-dialog'));
    await Promise.all(incoming.map(async element=>{
      const animation=element.animate(
        [{opacity:0},{opacity:1}],{duration:200,easing:'ease-in-out',fill:'both'}
      );
      await animation.finished.catch(()=>{});
      animation.cancel();
    }));
  }finally{
    modalVariantSwitching=false;
  }
}

function renderCardModal(){
  const card=state.modalCard,v=state.modalVariant;
  const physicalVariants=card.variants.filter(x=>x.language===v.language);
  const idx=physicalVariants.findIndex(x=>x.id===v.id), gridIdx=state.cards.findIndex(c=>c.identity_id===card.id);
  const variantLabel=x=>`${x.set_code} · ${x.collector_number} · ${variantName(x)}`;
  const languages=[...new Set(card.variants.map(x=>x.language))];
  const modalTabs=[['collection','Overview'],['market','Price History'],['card','Info']];
  const quickMenuContent=`<div class="modal-relationship-popup"><div class="modal-relationship-title">Beziehungen</div>${modalRelationshipContent(card,v)}</div>`;
  const visual=finishPresentation(v);
  // Card names are "<Titel> - <Untertitel>" (Lorcana convention) -- split so the two
  // halves can be styled distinctly (mobile-modern: subtitle smaller + lighter). Safe
  // on names with no " - " at all (titleSub stays empty) and on extra dashes further in
  // the subtitle (only the FIRST " - " splits, the rest stays part of titleSub).
  const [titleMain,...titleSubParts]=card.canonical_name.split(' - ');
  const titleSub=titleSubParts.join(' - ');
  const titleHtml=escapeHtml(titleMain);
  // Feedback: the leading "- " read as if the subtitle belonged to the set-line below it
  // rather than the title above -- dropped (CSS pulls the line closer to the title too).
  const titleSubHtml=titleSub?`<span class="card-title-sub">${escapeHtml(titleSub)}</span>`:'';
  $('#card-dialog').innerHTML=`<div class="card-modal-layout">
    <button class="close-button" data-close="card-modal">×</button>
    <div class="modal-quick-menu-wrap">
      <button class="modal-menu-button" data-modal-menu-toggle aria-label="Menü" aria-expanded="false">⋯</button>
      <div class="modal-quick-menu hidden" id="modal-quick-menu">${quickMenuContent}</div>
    </div>
    <section class="card-stage"><div class="stage-backdrop"></div><div class="stage-platform-wrap"><img class="stage-platform-svg" src="/glass-plate.svg" alt="" aria-hidden="true"></div><div class="stage-flare-anchor" aria-hidden="true"><span class="stage-flare left"><i style="--ray-angle:-166deg;--ray-length:72px;--ray-alpha:.42"></i><i style="--ray-angle:-132deg;--ray-length:118px;--ray-alpha:.58;--ray-size:1.5px"></i><i style="--ray-angle:-94deg;--ray-length:82px;--ray-alpha:.36"></i><i style="--ray-angle:-58deg;--ray-length:142px;--ray-alpha:.72;--ray-size:1.5px"></i><i style="--ray-angle:-19deg;--ray-length:96px;--ray-alpha:.48"></i><i style="--ray-angle:23deg;--ray-length:128px;--ray-alpha:.62;--ray-size:2px"></i><i style="--ray-angle:61deg;--ray-length:76px;--ray-alpha:.35"></i><i style="--ray-angle:108deg;--ray-length:112px;--ray-alpha:.5;--ray-size:1.5px"></i><i style="--ray-angle:147deg;--ray-length:88px;--ray-alpha:.4"></i></span><span class="stage-flare right"><i style="--ray-angle:-173deg;--ray-length:126px;--ray-alpha:.6;--ray-size:1.5px"></i><i style="--ray-angle:-139deg;--ray-length:78px;--ray-alpha:.38"></i><i style="--ray-angle:-103deg;--ray-length:138px;--ray-alpha:.68;--ray-size:2px"></i><i style="--ray-angle:-66deg;--ray-length:91px;--ray-alpha:.44"></i><i style="--ray-angle:-27deg;--ray-length:116px;--ray-alpha:.56;--ray-size:1.5px"></i><i style="--ray-angle:14deg;--ray-length:70px;--ray-alpha:.34"></i><i style="--ray-angle:49deg;--ray-length:146px;--ray-alpha:.72;--ray-size:1.5px"></i><i style="--ray-angle:96deg;--ray-length:84px;--ray-alpha:.42"></i><i style="--ray-angle:139deg;--ray-length:108px;--ray-alpha:.5;--ray-size:2px"></i></span></div><div class="stage-flare-cross"></div><div class="variant-hint top">${idx>0?`<button data-variant-nav="-1">↑ ${escapeHtml(variantLabel(physicalVariants[idx-1]))}</button>`:''}</div><button class="card-nav-button prev" ${gridIdx<=0?'disabled':''} data-card-nav="-1">‹</button><div class="modal-card-frame-wrap card-tilt-zone"><div class="modal-card-frame card-finish-frame card-tilt ${visual.effect}" style="--foil-mask:url('${foilMaskUrl(v.id)}')"><img class="modal-card-image" draggable="false" src="${artUrl(v.id,'full')}" alt="${escapeHtml(card.canonical_name)}">${visual.effect!=='finish-normal'?'<div class="foil-fx foil-fx-a" aria-hidden="true"></div><div class="foil-fx foil-fx-b" aria-hidden="true"></div><div class="foil-fx foil-fx-c" aria-hidden="true"></div><div class="card-shine" aria-hidden="true"></div>':''}${visual.effect!=='finish-normal'&&v.game_id==='lorcana'?officialFoilLayerMarkup():''}</div><img class="modal-card-reflection" src="${artUrl(v.id,'full')}" alt="" aria-hidden="true"></div><button class="card-nav-button next" ${gridIdx<0||gridIdx>=state.cards.length-1?'disabled':''} data-card-nav="1">›</button><div class="variant-hint bottom">${idx<physicalVariants.length-1?`<button data-variant-nav="1">↓ ${escapeHtml(variantLabel(physicalVariants[idx+1]))}</button>`:''}</div></section>
    <aside class="modal-side"><header class="modal-head"><span class="eyebrow">${escapeHtml(v.set_code)} · ${escapeHtml(v.collector_number)}</span><h2>${titleHtml}</h2><b class="modal-price">${priceSymbolFirst(v.price)}</b>${titleSubHtml}<span class="modal-set-line">${escapeHtml(v.set_name)}<b class="meta-sep">·</b>#${escapeHtml(v.collector_number)} / ${v.printed_card_count!=null?v.printed_card_count:'–'}</span><span class="modal-rarity-line">${escapeHtml(v.rarity)}<b class="meta-sep">·</b>${escapeHtml(variantName(v))}</span><p>${escapeHtml(v.rarity)} · ${escapeHtml(variantName(v))}</p><div class="language-switcher" aria-label="Sprachversion">${languages.map(language=>`<button data-language="${language}" class="${language===v.language?'active':''}">${language}</button>`).join('')}</div></header><nav class="modal-tabs">${modalTabs.map(([key,label])=>`<button data-tab="${key}" class="${state.modalTab===key?'active':''}">${label}</button>`).join('')}</nav><div class="modal-content">${modalTabContent(card,v)}</div></aside>
    <div class="modal-action-bar ${advancedPanelExpanded?'is-expanded':''}"><div class="modal-footer-actions"><div class="modal-footer-quantity" aria-label="Menge"><span class="modal-footer-quantity-label">Owned</span><button type="button" class="modal-qty-btn" data-delta="-1" aria-label="Menge verringern">−</button><b>${v.quantity}</b><button type="button" class="modal-qty-btn" data-delta="1" aria-label="Menge erhöhen">＋</button><button type="button" class="modal-footer-advanced-toggle" id="footer-advanced-toggle" aria-expanded="${advancedPanelExpanded}" aria-label="${advancedPanelExpanded?'Zustände einklappen':'Zustände ausklappen'}"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 10 4.5-4 4.5 4"/></svg></button></div><button class="watchlist-action watchlist-action-icon modal-watch-bottom ${v.watchlisted?'active':''}" data-modal-quick-watch aria-label="${v.watchlisted?'Von der Watchlist entfernen':'Zur Watchlist hinzufügen'}" aria-pressed="${Boolean(v.watchlisted)}">${watchlistIcon(v.watchlisted)}</button></div><div class="modal-footer-advanced advanced-panel" id="footer-advanced-panel"></div></div>
  </div>`;
  // Keep the plate mask in a stationary coordinate system. The reflection image may rotate
  // inside it, but the mask host itself must never inherit that parallax transform.
  const reflection=$('.modal-card-reflection',$('#card-dialog'));
  if(reflection){
    const reflectionMask=document.createElement('div');
    reflectionMask.className='modal-card-reflection-mask';
    reflectionMask.setAttribute('aria-hidden','true');
    const reflectionMirror=document.createElement('div');
    reflectionMirror.className='modal-card-reflection-mirror';
    const reflectionPerspective=document.createElement('div');
    reflectionPerspective.className='modal-card-reflection-perspective';
    reflection.before(reflectionMask);
    reflectionMask.append(reflectionMirror);
    reflectionMirror.append(reflectionPerspective);
    reflectionPerspective.append(reflection);
  }
  $('[data-close="card-modal"]').onclick=()=>closeOverlay('card-modal');
  $('[data-modal-menu-toggle]')?.addEventListener('click',e=>{const menu=$('#modal-quick-menu');const open=menu.classList.toggle('hidden');e.currentTarget.setAttribute('aria-expanded',String(!open))});
  $('[data-modal-quick-watch]').onclick=async()=>{const listId=Number(state.activeWatchlists[0]?.id);const r=await post('/api/watchlist',{variant_id:v.id,list_id:listId});v.watchlisted=r.active;syncWatchlistIcon(card.id,v.id,r.active);renderCardModal();refreshWatchCount();toast(r.active?'Zur Watchlist hinzugefügt':'Von der Watchlist entfernt')};
  $$('[data-tab]',$('#card-dialog')).forEach(b=>b.onclick=()=>{state.modalTab=b.dataset.tab;renderCardModal()});
  $$('[data-language]',$('#card-dialog')).forEach(b=>b.onclick=()=>{
    const candidates=card.variants.filter(x=>x.language===b.dataset.language);
    const nextVariant=candidates.find(x=>x.artwork_id===v.artwork_id&&x.variant_code===v.variant_code)
      ||candidates.find(x=>x.set_code===v.set_code&&x.collector_number===v.collector_number&&x.variant_code===v.variant_code)
      ||candidates.find(x=>x.variant_code===v.variant_code)||candidates[0];
    transitionModalVariant(nextVariant);
  });
  $$('[data-variant-setting-toggle]',$('#card-dialog')).forEach(button=>button.onclick=()=>{
    const setting=button.closest('.variant-setting');
    const willOpen=!setting.classList.contains('open');
    $$('.variant-setting',$('#card-dialog')).forEach(item=>{
      item.classList.remove('open');
      $('[data-variant-setting-toggle]',item)?.setAttribute('aria-expanded','false');
    });
    if(willOpen){
      setting.classList.add('open');
      button.setAttribute('aria-expanded','true');
    }
  });
  $$('[data-variant-nav]',$('#card-dialog')).forEach(b=>b.onclick=()=>{state.modalVariant=physicalVariants[idx+Number(b.dataset.variantNav)];renderCardModal()});
  $$('.variant-option',$('#card-dialog')).forEach(b=>b.onclick=()=>transitionModalVariant(card.variants.find(v=>v.id===b.dataset.variant)));
  $$('[data-card-nav]',$('#card-dialog')).forEach(b=>b.onclick=()=>{const target=state.cards[gridIdx+Number(b.dataset.cardNav)];if(target)openCard(target.identity_id,target.variant_id,true)});
  const qtyControls=$$('.modal-qty-btn',$('#card-dialog')); qtyControls.forEach(b=>b.onclick=()=>changeQuantity(v.id,Number(b.dataset.delta)));
  const watch=$('.modal-watch',$('#card-dialog')); if(watch)watch.onclick=async()=>{const listId=Number($('#modal-watchlist')?.value||state.activeWatchlists[0]?.id);const r=await post('/api/watchlist',{variant_id:v.id,list_id:listId});v.watchlisted=r.active;syncWatchlistIcon(card.id,v.id,r.active);renderCardModal();refreshWatchCount();toast(r.active?'Zur gewählten Watchlist hinzugefügt':'Von der gewählten Watchlist entfernt')};
  const refresh=$('#price-refresh',$('#card-dialog')); if(refresh)refresh.onclick=async()=>{refresh.disabled=true;refresh.textContent='Preise werden geladen …';try{await post('/api/prices/sync',{});toast('Marktpreise aktualisiert');await openCard(card.id,v.id,true)}catch(error){toast(error.message||'Preisimport fehlgeschlagen');refresh.disabled=false;refresh.textContent='Preise aktualisieren'}};
  const advancedToggles=$$('#advanced-toggle,#footer-advanced-toggle',$('#card-dialog'));
  advancedToggles.forEach(toggle=>toggle.onclick=toggleAdvancedPanel);
  if(advancedToggles.length&&advancedPanelExpanded)loadAdvancedPanel();
  const manualPrice=$('#manual-price-form',$('#card-dialog'));
  if(manualPrice){
    // scheduleRefresh() redraws the view behind the dialog and then the dialog itself.
    const saved=async message=>{toast(message);state.boot=await api('/api/bootstrap');scheduleRefresh(0)};
    manualPrice.onsubmit=async event=>{
      event.preventDefault();
      try{await api(`/api/variants/${encodeURIComponent(v.id)}/manual-price`,{method:'PUT',body:JSON.stringify({amount:$('#manual-price-input').value})});await saved('Eigener Preis gespeichert')}
      catch(error){toast(error.message)}
    };
    const remove=$('#manual-price-remove',manualPrice);
    if(remove)remove.onclick=async()=>{
      try{await api(`/api/variants/${encodeURIComponent(v.id)}/manual-price`,{method:'DELETE'});await saved('Eigener Preis entfernt')}
      catch(error){toast(error.message)}
    };
  }
  if(state.modalTab==='market'&&$('#price-history-panel',$('#card-dialog')))loadPriceHistory(v.id);
  if(state.modalTab==='market')loadEbayTracked(v.id);
  if(state.modalTab==='collection')loadCardDeals(v.id);
  alignReflectionMask();
  // "wenn eine Karte geladen wird die foillayer dafür gezogen und gespeichert werden" -- fired
  // once per modal render, only when the generic foil markup above was actually emitted (same
  // Lorcana + non-normal-finish gate as officialFoilLayerMarkup()'s own condition, so this
  // never fires a wasted request for a card that has no official-layer <div>s to hydrate).
  if(visual.effect!=='finish-normal'&&v.game_id==='lorcana'){
    hydrateOfficialFoilLayers(v.id,$('.modal-card-frame',$('#card-dialog')));
  }
  // .stage-platform-wrap's height is auto, sized off the plate <img>'s own natural aspect
  // ratio -- not known until that image asynchronously finishes loading, so the call above
  // (synchronous, right after this markup is inserted) can measure a wrong/collapsed rect.
  // Re-running once it actually loads (or immediately via .complete if cached) guarantees at
  // least one correct measurement.
  const plateImg=$('.stage-platform-svg',$('#card-dialog'));
  if(plateImg){ if(plateImg.complete)alignReflectionMask(); else plateImg.addEventListener('load',alignReflectionMask,{once:true}) }
}

// Positions the SVG mask cutout (index.html: #mt-reflection-mask / #mt-reflection-cutout-wrap)
// that hides the card reflection specifically at the plate's front edge (id="glass_side",
// inlined into index.html -- see glass_plate_inline_markup() in app.py). The mask's solid base
// <rect> and the <g> wrapping glass_side both live in userSpaceOnUse pixel units, NOT
// objectBoundingBox fractions -- confirmed via isolated testing that glass_side's <use> paints
// zero pixels (correct geometry, nothing rasterized) when both its transform's scale factors are
// simultaneously tiny fractions, which is exactly what compressing the native 500x79 shape into
// a ~1-unit objectBoundingBox forces. So glass_side's native 500x79 coordinate space is instead
// mapped into real screen pixels matching whatever size the plate actually occupies --
// computed fresh from real getBoundingClientRect() rects every render, since the plate and the
// reflection are two independently-positioned elements (the card wrapper's width caps at 340px
// past a certain viewport size, which breaks any fixed
// proportionality between them -- a hardcoded value only ever lines up at one exact width).
function alignReflectionMask(){
  const dialog=$('#card-dialog');
  const stage=$('.card-stage',dialog);
  const plate=$('.stage-platform-wrap',dialog);
  const reflMask=$('.modal-card-reflection-mask',dialog);
  const cutout=document.getElementById('mt-reflection-cutout-wrap');
  const fadeRect=document.getElementById('mt-reflection-fade-rect');
  if(!stage||!plate||!reflMask||!cutout||!fadeRect)return;
  const s=stage.getBoundingClientRect(), p=plate.getBoundingClientRect();
  if(!s.height||!p.height)return;

  // The plate is sized from its responsive width while the stage is sized from the viewport
  // height. Resolve the plate once in real pixels and let every dependent visual consume the
  // same geometry: background seam, card contact point and both side lights. This prevents
  // those layers drifting apart when either viewport dimension changes.
  const plateLeft=p.left-s.left, plateTop=p.top-s.top;
  const plateEdgeTop=plateTop+(p.height*1.5/79)+6;
  const plateEdgeCenter=plateTop+(p.height*34.5/79)+6;
  const plateEdgeHeight=Math.max(1,2*(plateEdgeCenter-plateEdgeTop));
  // The visible separator is the upper half of stage-backdrop::after's ellipse. Resolve its
  // y coordinate at the flare's existing horizontal position (10% into the plate) so the
  // light core sits on that curve rather than inside the glass surface.
  const flareX=plateLeft+p.width*.1;
  const ellipseRx=s.width/2, ellipseRy=plateEdgeHeight/2;
  const normalizedX=Math.max(-1,Math.min(1,(flareX-ellipseRx)/ellipseRx));
  const flareEdgeY=plateEdgeCenter-ellipseRy*Math.sqrt(Math.max(0,1-normalizedX*normalizedX));
  stage.style.setProperty('--plate-left',`${plateLeft}px`);
  stage.style.setProperty('--plate-top',`${plateTop}px`);
  stage.style.setProperty('--plate-width',`${p.width}px`);
  stage.style.setProperty('--plate-height',`${p.height}px`);
  stage.style.setProperty('--plate-contact-y',`${plateTop+(p.height*34.5/79)}px`);
  stage.style.setProperty('--plate-edge-top-y',`${plateEdgeTop}px`);
  stage.style.setProperty('--plate-edge-center-y',`${plateEdgeCenter}px`);
  stage.style.setProperty('--plate-edge-height',`${plateEdgeHeight}px`);
  stage.style.setProperty('--flare-edge-local-y',`${flareEdgeY-plateTop}px`);

  // Aim the two broad spotlight blooms from their flare sources at the upper midpoint of the
  // responsive card. The source sits at 80% of the bloom box; sizing the box from the real
  // source-to-target distance leaves a soft continuation below the plate edge while carrying
  // the long axis slightly past the card's top edge.
  const cardWrap=$('.modal-card-frame-wrap',dialog);
  if(cardWrap){
    const c=cardWrap.getBoundingClientRect();
    const targetX=c.left+c.width/2-s.left, targetY=c.top-s.top;
    const sourceY=flareEdgeY-5;
    const leftSourceX=plateLeft+p.width*.1, rightSourceX=plateLeft+p.width*.9;
    const spotlightAngle=(sourceX)=>Math.atan2(targetX-sourceX,sourceY-targetY)*180/Math.PI;
    const spotlightDistance=(sourceX)=>Math.hypot(targetX-sourceX,targetY-sourceY);
    const distance=Math.max(spotlightDistance(leftSourceX),spotlightDistance(rightSourceX));
    stage.style.setProperty('--spotlight-left-angle',`${spotlightAngle(leftSourceX)}deg`);
    stage.style.setProperty('--spotlight-right-angle',`${spotlightAngle(rightSourceX)}deg`);
    stage.style.setProperty('--spotlight-height',`${Math.max(300,distance/.75)}px`);
    stage.style.setProperty('--spotlight-width',`${Math.max(170,Math.min(250,c.width*.9))}px`);
  }

  // The mask host covers the whole stage, not just the short reflection strip. This removes
  // the old horizontal clip at the resting card edge: a tilted reflection may extend upward
  // across the glass top, while the SVG mask below still cuts out only glass_side. Because the
  // host lives inside cardWrap, convert all stage/card coordinates into that local space.
  const reflection=$('.modal-card-reflection',reflMask);
  const reflectionMirror=$('.modal-card-reflection-mirror',reflMask);
  const reflectionPerspective=$('.modal-card-reflection-perspective',reflMask);
  if(!cardWrap||!reflection||!reflectionMirror||!reflectionPerspective)return;
  const c=cardWrap.getBoundingClientRect();
  reflMask.style.left=`${s.left-c.left}px`;
  reflMask.style.top=`${s.top-c.top}px`;
  reflMask.style.width=`${s.width}px`;
  reflMask.style.height=`${s.height}px`;
  // Reproduce the card's projection first, then mirror that complete projected plane around
  // the resting glass contact line. This is what makes the two edge distances diverge into a
  // wedge under combined pitch/yaw instead of remaining parallel.
  reflectionPerspective.style.perspectiveOrigin=`${c.left+c.width/2-s.left}px ${c.top+c.height/2-s.top}px`;
  reflectionMirror.style.transformOrigin=`0 ${c.bottom-s.top}px`;
  reflection.style.left=`${c.left-s.left}px`;
  reflection.style.top=`${c.top-s.top}px`;
  reflection.style.width=`${c.width}px`;
  reflection.style.height=`${c.height}px`;

  // Reading the now-stationary, stage-sized host after setting geometry forces those values to
  // participate in this same measurement pass.
  const r=reflMask.getBoundingClientRect();
  if(!r.height)return;
  // Pixel (userSpaceOnUse), not 0-1 objectBoundingBox fractions -- confirmed via isolated
  // testing that glass_side's <use> paints zero pixels when both its transform's scale factors
  // are simultaneously tiny fractions (compressing the native 500x79 shape into a ~1-unit box
  // forces exactly that). Real pixel-sized scale factors (order 0.6-0.8, matching the plate's
  // actual on-screen size) avoid the failure zone. The mask's own coordinate system here is the
  // stationary mask host's own pixel box, so the white base rect needs its size set to match.
  fadeRect.setAttribute('width',r.width);
  fadeRect.setAttribute('height',r.height);
  // The mask host itself never tilts or flips. Map the plate's native 500x79 coordinates
  // directly into this stationary box; only the reflection image inside it is transformed.
  const pxX=p.x-r.x, pxY=p.y-r.y;
  const scaleW=p.width/500, scaleH=p.height/79;
  cutout.setAttribute('transform',`translate(${pxX} ${pxY}) scale(${scaleW} ${scaleH})`);
}

function modalRelationshipContent(card,v){
  const physicalVariants=card.variants.filter(x=>x.language===v.language);
  const modalIsLorcana=v.game_id==='lorcana';
  return `<div class="detail-section-title">IDENTITÄT & DRUCKE</div><div class="relationship"><span>◇</span><div><b>Gameplay-Identität</b><small>${escapeHtml(card.canonical_name)} · sprachunabhängig</small></div></div><div class="relationship"><span>文</span><div><b>${new Set(card.variants.map(x=>x.language)).size} Sprachversionen</b><small>${[...new Set(card.variants.map(x=>x.language))].join(' · ')} · separat auswählbar</small></div></div><div class="relationship"><span>▤</span><div><b>${new Set(physicalVariants.map(x=>x.printing_id)).size} Drucke auf ${v.language}</b><small>Set- und Promo-Drucke dieser Sprache</small></div></div><div class="relationship"><span>✦</span><div><b>${physicalVariants.length} Ausführungen auf ${v.language}</b><small>${[...new Set(physicalVariants.map(x=>modalIsLorcana?lorcanaFinishLabel(x.finish,x.rarity):x.finish))].join(' · ')}</small></div></div><div class="relationship"><span>↗</span><div><b>Physisches Set</b><small>${escapeHtml(v.set_name)} (${escapeHtml(v.set_code)})</small></div></div>`;
}

function rulesTextHtml(text=''){
  const headingPattern=/(^|\n)([A-ZÄÖÜ0-9][A-ZÄÖÜ0-9 &'’!?.:+-]{1,}?)(?=\s+(?:[A-ZÄÖÜ][a-zäöüß]|[⟳↻]))/g;
  let html='',last=0,match;
  while((match=headingPattern.exec(text))){
    html+=escapeHtml(text.slice(last,match.index)).replace(/\n+/g,' ');
    html+=`<strong class="rules-keyword">${escapeHtml(match[2].trim())}</strong>`;
    last=match.index+match[0].length;
  }
  return html+escapeHtml(text.slice(last)).replace(/\n+/g,' ');
}

function modalTabContent(card,v){
  const physicalVariants=card.variants.filter(x=>x.language===v.language);
  const modalIsLorcana=v.game_id==='lorcana';
  if(state.modalTab==='collection'){
    const variantButtons=`<div class="variant-selector">${physicalVariants.map(x=>`<button class="variant-option ${x.id===v.id?'active':''}" data-variant="${x.id}">${finishThumb(x,artUrl(x.id),card.canonical_name,'variant-thumb')}<span><b>${escapeHtml(variantName(x))}</b><small>${escapeHtml(x.set_code)} · ${escapeHtml(x.collector_number)} · ${price(x.price)} · ${x.quantity}×</small></span></button>`).join('')}</div>`;
    const languages=[...new Set(card.variants.map(x=>x.language))];
    const mobilePicker=`<div class="variant-settings">
      <div class="variant-setting"><button type="button" class="variant-setting-row" data-variant-setting-toggle aria-expanded="false"><span>Sprache</span><b>${escapeHtml(v.language)}</b><i aria-hidden="true">›</i></button><div class="variant-setting-options"><div class="variant-setting-options-inner"><div class="language-switcher">${languages.map(language=>`<button data-language="${language}" class="${language===v.language?'active':''}">${escapeHtml(language)}</button>`).join('')}</div></div></div></div>
      <div class="variant-setting"><button type="button" class="variant-setting-row" data-variant-setting-toggle aria-expanded="false"><span>Print</span><b>${escapeHtml(variantName(v))}</b><i aria-hidden="true">›</i></button><div class="variant-setting-options"><div class="variant-setting-options-inner">${variantButtons}</div></div></div>
    </div>`;
    const desktopPicker=`<div class="desktop-variant-settings">${variantButtons}<div class="language-switcher desktop-language-switcher" aria-label="Sprachversion">${languages.map(language=>`<button data-language="${language}" class="${language===v.language?'active':''}">${escapeHtml(language)}</button>`).join('')}</div></div>`;
    const picker=window.innerWidth<=760?mobilePicker:desktopPicker;
    return `${picker}<div class="detail-section mobile-collection-controls"><div class="detail-section-title">DEINE SAMMLUNG</div><div class="modal-quantity"><span><b>Menge</b></span><div class="controls"><button class="modal-qty-btn" data-delta="-1">−</button><b>${v.quantity}</b><button class="modal-qty-btn" data-delta="1">＋</button></div></div><div class="watchlist-picker"><select id="modal-watchlist" class="select-control">${state.activeWatchlists.map(l=>`<option value="${l.id}">${escapeHtml(l.name)}</option>`).join('')}</select><button class="secondary-button watchlist-action watchlist-action-wide modal-watch ${v.watchlisted?'active':''}" aria-pressed="${Boolean(v.watchlisted)}">${watchlistIcon(v.watchlisted)}<span>Watchlist</span></button></div><button type="button" class="secondary-button advanced-toggle" id="advanced-toggle" aria-expanded="${advancedPanelExpanded}">Erweitert ${advancedPanelExpanded?'▴':'▾'}</button><div class="advanced-panel ${advancedPanelExpanded?'':'hidden'}" id="advanced-panel"></div></div><div class="detail-grid collection-info-grid"><div class="detail-field"><span>Sprachversion</span><b>${v.language}</b></div><div class="detail-field"><span>Sammlungswert</span><b>${(v.price==null&&!v.override_value)?'Kein Preis verfügbar':money((v.override_value||0)+(v.unpriced_quantity||0)*(v.price||0))}</b></div><div class="detail-field"><span>Datenquelle</span><b>${escapeHtml(v.source_type)}</b></div></div>`;
  }
  if(state.modalTab==='market'){
    const original=nativePrice(v);
    const conversion=original?` · ${escapeHtml(original)} in ${escapeHtml(v.price_native_currency)} · EZB ${date(v.price_exchange_date)}`:'';
    const secondaryMetric=v.price_avg30!=null
      ? `<div><span>30-Tage-Ø</span><b>${price(v.price_avg30)}</b></div>`
      : `<div><span>Originalpreis</span><b>${original||'Nicht verfügbar'}</b></div>`;
    const historyPanel=v.price==null?'':`<div class="price-history" id="price-history-panel"><div class="price-history-loading">Preisverlauf wird geladen …</div></div>`;
    return `<div class="price-hero"><span>${v.price_manual?'Eigener Preis':`${escapeHtml(v.price_source)} Marktpreis`}</span><b>${price(v.price)}</b><small>${v.price==null?'Kein eindeutig zugeordneter Preis verfügbar':`Stand ${date(v.price_observed_at)} · EUR${conversion}`}</small></div>${v.price==null?'':`<div class="market-metrics"><div><span>Niedrig</span><b>${price(v.price_low)}</b></div>${secondaryMetric}<div><span>Anbieter</span><b>${v.price_manual?'Manuell':escapeHtml(v.price_source)}</b></div></div>`}${historyPanel}<a class="price-source-link market-source-link" href="${escapeHtml(v.price_url)}" target="_blank" rel="noopener noreferrer"><span>↗</span><div><b>Preisquelle bei ${escapeHtml(v.price_source)} öffnen</b><small>${escapeHtml(v.collector_number)} · ${escapeHtml(modalIsLorcana?lorcanaFinishLabel(v.finish,v.rarity):v.finish)} · direkte Produktseite</small></div><span>→</span></a><div class="ebay-tracked" id="ebay-tracked-panel"></div><form class="manual-price" id="manual-price-form"><label for="manual-price-input">Eigener Preis</label><div class="manual-price-row"><input id="manual-price-input" inputmode="decimal" autocomplete="off" placeholder="z. B. 4,50" value="${v.price_manual?v.price.toFixed(2).replace('.',','):''}"><span>€</span><button class="secondary-button" type="submit">Speichern</button>${v.price_manual?'<button class="secondary-button" type="button" id="manual-price-remove">Entfernen</button>':''}</div><small>${v.price_manual?'Gilt anstelle der Preisquelle, bis du ihn entfernst.':'Für Karten ohne oder mit falschem Marktpreis. Gilt für diese Ausführung.'}</small></form><button class="secondary-button price-refresh" id="price-refresh">Preise aktualisieren</button>`;
  }
  if(state.modalTab==='card')return `<div class="detail-section modal-rules-section"><p class="rules-text">${rulesTextHtml(card.rules_text)}</p></div><div class="detail-grid modal-info-grid"><div class="detail-field"><span>Kartentyp</span><b>${escapeHtml(card.card_type)}</b></div><div class="detail-field"><span>${card.game_id==='vcard'?'Element':'Farbe'}</span><b>${escapeHtml(card.attributes.color)}</b></div><div class="detail-field"><span>${card.game_id==='vcard'?'Power Level':'Kosten'}</span><b>${card.game_id==='vcard'?(card.attributes.cost??'–'):card.attributes.cost}</b></div><div class="detail-field modal-info-legality"><span>Legalität</span><b>${escapeHtml(card.attributes.legality)}</b></div><div class="detail-field modal-info-set"><span>Set</span><b>${escapeHtml(v.set_name)}</b></div><div class="detail-field modal-info-rarity"><span>Seltenheit</span><b>${escapeHtml(v.rarity)}</b></div></div><a class="price-source-link modal-info-source" href="${escapeHtml(v.image_source_url)}" target="_blank" rel="noopener noreferrer"><span>▧</span><div><b>Bildquelle öffnen</b><small>${escapeHtml(v.image_source)}</small></div><span>→</span></a>`;
  return modalRelationshipContent(card,v);
}
