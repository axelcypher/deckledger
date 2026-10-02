// Changing quantities, the collection view, conditions and gradings, price history.

// Patches just the number the user is currently looking at (the control they
// clicked, and the modal if it's open for this variant) instead of a full
// re-render, which would need a network round-trip we don't have offline.
// Everywhere else showing this variant's quantity (badges, dashboard stats,
// the collection page) stays stale until the next real sync -- acceptable
// since the offline indicator already tells the user a sync is pending.
// The card objects behind the grid: tiles that are not rendered yet (see mountTileFeed) and the
// detail view's previous/next navigation read their quantities from here.
function patchCardData(variantId,delta){
  for(const card of state.cards||[]){
    const variants=card.variants||[],hit=variants.find(item=>(item.variant_id||item.id)===variantId);
    if(!hit)continue;
    hit.quantity=Math.max(0,(Number(hit.quantity)||0)+delta);
    card.quantity=variants.reduce((sum,item)=>sum+(Number(item.quantity)||0),0);
    card.owned_variants=variants.filter(item=>item.quantity>0).length;
    if('foil_quantity'in card)card.foil_quantity=variants.find(item=>item.finish==='Silver'&&item.language===card.language)?.quantity||0;
  }
}
function updatePlaysetRibbons(tile,isLorcana,total){
  const held=selector=>parseInt($(`${selector} b`,tile)?.textContent,10)||0;
  const size=playsetSize(state.activeGameId);
  const html=isLorcana
    ?`${held('.quantity-control:not(.foil)')>=4?'<span class="playset-badge" title="Playset komplett · 4 Exemplare">✓</span>':''}${held('.quantity-control.foil')>=4?'<span class="playset-badge foil" title="Foil-Playset komplett · 4 Exemplare">✓</span>':''}`
    :(total>=size?`<span class="playset-badge" title="Playset komplett · ${size} Exemplare">✓</span>`:'');
  $('.playset-ribbons',tile)?.remove();
  if(html)$('.card-image-wrap',tile)?.insertAdjacentHTML('afterend',`<div class="playset-ribbons">${html}</div>`);
}

function patchLocalQuantity(variantId,delta,sourceButton){
  $$(`.quantity-control[data-variant="${variantId}"]`).forEach(control=>{
    const el=$('b',control),before=parseInt(el.textContent,10)||0,after=Math.max(0,before+delta);
    el.textContent=String(after);
    // The tile's own "owned" look and ×N pill, so the card reads as added right away instead of
    // only after the next full reload of the view.
    const tile=control.closest('.card-tile'),badges=tile&&$('.variant-badges',tile);
    if(!tile||!badges)return;
    if($('.variant-badge',badges)){
      // Lorcana shows one ×N badge per finish tier instead of a single pill.
      const badge=control.classList.contains('foil')?$('.variant-badge.tier-foil',badges):($('.variant-badge.tier-normal',badges)||$('.variant-badge.tier-premium',badges));
      if(badge){
        const count=Math.max(0,(parseInt(badge.textContent.slice(1),10)||0)+(after-before));
        badge.textContent=`×${count}`;badge.classList.toggle('owned',count>0);badge.classList.toggle('missing',count===0);
      }
      const any=$$('.variant-badge',badges).some(item=>(parseInt(item.textContent.slice(1),10)||0)>0);
      tile.classList.toggle('owned',any);tile.classList.toggle('missing',!any);
      updatePlaysetRibbons(tile,true,0);
      return;
    }
    const pill=$('.owned-pill',badges),total=Math.max(0,(pill?parseInt(pill.textContent.slice(1),10)||0:0)+(after-before));
    if(pill&&total)pill.textContent=`×${total}`;
    else if(pill)pill.remove();
    else if(total)badges.insertAdjacentHTML('beforeend',`<span class="owned-pill">×${total}</span>`);
    tile.classList.toggle('owned',total>0);tile.classList.toggle('missing',total===0);
    updatePlaysetRibbons(tile,false,total);
  });
  patchCardData(variantId,delta);
  if(sourceButton){
    const b=sourceButton.parentElement?.querySelector('b');
    if(b)b.textContent=String(Math.max(0,(parseInt(b.textContent,10)||0)+delta));
  }
  if(state.modalVariant?.id===variantId){
    $$('.modal-quantity .controls b,.modal-footer-quantity b',$('#card-dialog')).forEach(modalB=>{
      modalB.textContent=String(Math.max(0,(parseInt(modalB.textContent,10)||0)+delta));
    });
    state.modalVariant.quantity=Math.max(0,(state.modalVariant.quantity||0)+delta);
  }
}

// Every quantity click used to force a full refetch+re-render of the current view (and the
// modal, if open) -- for a set with thousands of tiles that's a lot of work for "+1", and
// clicking repeatedly in quick succession piled up overlapping refreshes on top of each other,
// which is what actually caused the lag (not the network request itself). Debouncing means a
// burst of rapid clicks triggers exactly one real refresh shortly after the burst ends, while
// patchLocalQuantity gives instant feedback on every individual click in the meantime.
let refreshDebounceTimer=null;
// After a quantity change the card grids are NOT rebuilt: the tile and the data behind it are
// already patched in place (patchLocalQuantity), and rebuilding replaced every button on the
// page, so a click landing in that moment was lost. Only the summary numbers are fetched again.
// Views without such a summary (watchlist, deck builder) still reload as a whole.
function scheduleRefresh(delayMs=900){
  clearTimeout(refreshDebounceTimer);
  refreshDebounceTimer=setTimeout(async()=>{
    // Numbers fetched before the last change has been saved would show the old state again.
    if(quantityChains.size){scheduleRefresh(delayMs);return}
    if(state.statsUrl)await refreshStats();
    else await refreshCurrentView();
    if(state.modalCard&&!quantityChains.size) await openCard(state.modalCard.id,state.modalVariant?.id,true);
  },delayMs);
}
async function refreshStats(){
  const url=state.statsUrl,items=state.statsItems;
  let data;
  try{data=await api(`${url}&stats_only=1`)}catch{return}
  if(url!==state.statsUrl)return; // the view changed while the numbers were on their way
  for(const [label,value] of items(data.stats)){
    const cell=$$('deckledger-stat-pill [data-stat]',content).find(item=>$('span',item)?.textContent===label);
    if(cell)$('b',cell).textContent=String(value);
  }
}

// Sent with every queueable collection change: the server applies a given id only once, so a
// change that is replayed after a lost answer cannot be counted twice.
const newRequestId=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;

// Changes to one card are sent strictly one after the other, in click order; different cards
// do not wait for each other.
const quantityChains=new Map();
function inQuantityOrder(variantId,task){
  const next=(quantityChains.get(variantId)||Promise.resolve()).then(task,task);
  quantityChains.set(variantId,next);
  const done=()=>{if(quantityChains.get(variantId)===next)quantityChains.delete(variantId)};
  next.then(done,done);
  return next;
}

async function changeQuantity(variantId,delta,quick=false){
  // any_condition: the tile shows one total across conditions, so its minus button may take a
  // copy from another ungraded condition when there is no Near Mint one left.
  const payload={variant_id:variantId,delta,condition:'Near Mint',any_condition:true,request_id:newRequestId()};
  // The number changes on the click itself, not once the server has answered.
  patchLocalQuantity(variantId,delta);
  scheduleRefresh();
  if(!navigator.onLine){
    await queueOfflineMutation(payload);
    await updateOfflineIndicator();
    toast('Offline gespeichert · wird bei Verbindung synchronisiert',null,null,'offline-saved');
    return;
  }
  return inQuantityOrder(variantId,async()=>{
    let r;
    try{
      r=await post('/api/collection',payload);
    }catch(error){
      if(error.isNetworkError){
        await queueOfflineMutation(payload);
        await updateOfflineIndicator();
        toast('Offline gespeichert · wird bei Verbindung synchronisiert',null,null,'offline-saved');
        return;
      }
      patchLocalQuantity(variantId,-delta); // the server refused: take the optimistic change back
      if(!error.isAuthError)toast(error.message||'Änderung fehlgeschlagen');
      return;
    }
    toast(quick?`Karte hinzugefügt · jetzt ${r.quantity}`:`Menge auf ${r.quantity} geändert`,'Rückgängig',async()=>{await post('/api/collection',{variant_id:variantId,quantity:r.before,condition:r.condition||'Near Mint'});await refreshCurrentView();if(state.modalCard)await openCard(state.modalCard.id,variantId,true)},`quantity-${variantId}`);
    scheduleRefresh();
  });
}

// Lives outside `state` on purpose -- it's transient UI state for the currently open modal,
// not something that should survive navigation or be considered part of the app's data model.
let advancedPanelExpanded=false;

async function changeCollectionEntry(variantId,{condition='Near Mint',delta=0,quantity,isGraded=false,gradeLabel='',priceOverride,sourceButton}={}){
  const payload={variant_id:variantId,condition,is_graded:isGraded,grade_label:gradeLabel};
  if(quantity!==undefined)payload.quantity=quantity;else payload.delta=delta;
  if(priceOverride!==undefined)payload.price_override=priceOverride;
  const isPureDelta=quantity===undefined&&priceOverride===undefined;
  if(isPureDelta)payload.request_id=newRequestId();
  if(!navigator.onLine){
    if(!isPureDelta){toast('Preis-Override braucht eine Verbindung -- offline nicht verfügbar');return}
    await queueOfflineMutation(payload);
    patchLocalQuantity(variantId,delta,sourceButton);
    await updateOfflineIndicator();
    toast('Offline gespeichert · wird bei Verbindung synchronisiert');
    return;
  }
  if(isPureDelta){
    // Same rapid-click case as changeQuantity -- the condition/graded steppers in the
    // Erweitert panel are just as prone to being clicked repeatedly in quick succession.
    patchLocalQuantity(variantId,delta,sourceButton);
    scheduleRefresh();
    return inQuantityOrder(variantId,async()=>{
      try{
        await post('/api/collection',payload);
      }catch(error){
        if(error.isNetworkError){
          await queueOfflineMutation(payload);
          await updateOfflineIndicator();
          toast('Offline gespeichert · wird bei Verbindung synchronisiert',null,null,'offline-saved');
          return;
        }
        patchLocalQuantity(variantId,-delta,sourceButton);
        if(!error.isAuthError)toast(error.message||'Änderung fehlgeschlagen');
        return;
      }
      scheduleRefresh();
    });
  }
  try{
    await post('/api/collection',payload);
  }catch(error){
    toast(error.message||'Änderung fehlgeschlagen');
    return;
  }
  // Absolute-value writes (price override, initial quantity on a new graded copy) are
  // deliberate, infrequent edits, not rapid-fire -- refresh immediately for correctness.
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
    <button type="button" class="secondary-button" id="add-graded-toggle"><span>Gegradetes Exemplar</span><i aria-hidden="true">＋</i></button>
    <div class="graded-add-form hidden" id="graded-add-form">
      <input type="text" id="new-grade-label" placeholder="Grading, z.B. PSA 10">
      <select id="new-grade-condition" class="select-control">${COLLECTION_CONDITIONS.map(c=>`<option value="${escapeHtml(c)}" ${c==='Near Mint'?'selected':''}>${escapeHtml(c)}</option>`).join('')}</select>
      <input type="number" id="new-grade-price" step="0.01" placeholder="Preis-Override €">
      <button type="button" class="primary-button" id="add-graded-submit">Hinzufügen</button>
    </div>`;
}

function wireAdvancedPanel(variantId,panel){
  $$('.condition-qty-btn',panel).forEach(b=>b.onclick=()=>changeCollectionEntry(variantId,{condition:b.dataset.condition,delta:Number(b.dataset.delta),sourceButton:b}));
  $$('.graded-qty-btn',panel).forEach(b=>b.onclick=()=>changeCollectionEntry(variantId,{condition:b.dataset.condition,delta:Number(b.dataset.delta),isGraded:true,gradeLabel:b.dataset.grade,sourceButton:b}));
  $$('.graded-price-input',panel).forEach(input=>input.onchange=()=>changeCollectionEntry(variantId,{condition:input.dataset.condition,delta:0,isGraded:true,gradeLabel:input.dataset.grade,priceOverride:input.value===''?null:Number(input.value)}));
  const gradedToggle=$('#add-graded-toggle',panel), gradedForm=$('#graded-add-form',panel);
  if(gradedToggle)gradedToggle.onclick=()=>gradedForm?.classList.toggle('hidden');
  const gradedSubmit=$('#add-graded-submit',panel);
  if(gradedSubmit)gradedSubmit.onclick=()=>{
    const label=$('#new-grade-label',panel).value.trim();
    if(!label){toast('Bitte eine Grading-Bezeichnung eingeben');return}
    const condition=$('#new-grade-condition',panel).value, priceRaw=$('#new-grade-price',panel).value;
    changeCollectionEntry(variantId,{condition,delta:1,isGraded:true,gradeLabel:label,priceOverride:priceRaw===''?undefined:Number(priceRaw)});
  };
}

function activeAdvancedPanel(){
  const useFooter=document.body.classList.contains('mobile-modern');
  return $(useFooter?'#footer-advanced-panel':'#advanced-panel');
}

async function loadAdvancedPanel(){
  const panel=activeAdvancedPanel(); if(!panel)return;
  const variantId=state.modalVariant.id;
  const entries=await api(`/api/collection/entries/${variantId}`);
  if(panel!==activeAdvancedPanel()||variantId!==state.modalVariant?.id)return;
  panel.innerHTML=advancedCollectionPanelHtml(entries);
  wireAdvancedPanel(variantId,panel);
}

function toggleAdvancedPanel(){
  advancedPanelExpanded=!advancedPanelExpanded;
  $$('#advanced-toggle,#footer-advanced-toggle').forEach(btn=>{
    btn.setAttribute('aria-expanded',String(advancedPanelExpanded));
    if(btn.id==='footer-advanced-toggle'){
      btn.setAttribute('aria-label',advancedPanelExpanded?'Zustände einklappen':'Zustände ausklappen');
    }else{
      btn.textContent=`Erweitert ${advancedPanelExpanded?'▴':'▾'}`;
    }
  });
  $('#advanced-panel')?.classList.toggle('hidden',!advancedPanelExpanded);
  $('.modal-action-bar')?.classList.toggle('is-expanded',advancedPanelExpanded);
  if(advancedPanelExpanded)loadAdvancedPanel();
}

async function loadPriceHistory(variantId){
  const panel=$('#price-history-panel',$('#card-dialog')); if(!panel)return;
  let history;
  try{
    history=await api(`/api/variants/${encodeURIComponent(variantId)}/price-history`);
  }catch(error){
    if(state.modalVariant?.id!==variantId||state.modalTab!=='market')return;
    const staleCatchPanel=$('#price-history-panel',$('#card-dialog'));
    if(staleCatchPanel)staleCatchPanel.innerHTML=`<div class="price-history-empty">Preisverlauf konnte nicht geladen werden.</div>`;
    return;
  }
  // Same staleness guard as loadAdvancedPanel -- the modal can have moved on to a different
  // variant, or away from the Markt tab entirely, by the time this resolves.
  if(state.modalVariant?.id!==variantId||state.modalTab!=='market')return;
  const freshPanel=$('#price-history-panel',$('#card-dialog')); if(!freshPanel)return;
  freshPanel.innerHTML=priceHistoryPanelHtml(history.points);
}

function priceHistoryPanelHtml(points){
  if(points.length<2){
    return `<div class="price-history-empty">Noch nicht genug Preisdaten für einen Verlauf.</div>`;
  }
  const first=points[0],last=points[points.length-1];
  const delta=last.amount-first.amount;
  const deltaPct=first.amount?(delta/first.amount)*100:0;
  // money() already prefixes negative amounts with "-" via Intl.NumberFormat -- only the
  // positive case needs an explicit "+" added here.
  const trendClass=delta>0.001?'up':delta<-0.001?'down':'flat';
  const sign=delta>0?'+':'';
  const rangeLabel=`${date(first.date)} – ${date(last.date)}`;
  return `<div class="price-history-head"><span>Verlauf · ${escapeHtml(rangeLabel)}</span><b class="price-history-delta ${trendClass}">${sign}${money(delta)} · ${sign}${deltaPct.toFixed(1)}%</b></div>${priceHistorySvg(points)}`;
}

function priceHistorySvg(points){
  const width=300,height=88,padTop=8,padBottom=8;
  const amounts=points.map(p=>p.amount);
  const min=Math.min(...amounts),max=Math.max(...amounts),range=max-min;
  // A flat/near-flat series (range===0) would otherwise all normalize to the same edge instead
  // of a centered line -- 0.5 keeps it visually centered instead of pinned to the bottom.
  const normalize=amount=>range>0?(amount-min)/range:0.5;
  const t0=new Date(points[0].date).getTime(),t1=new Date(points[points.length-1].date).getTime(),timeSpan=(t1-t0)||1;
  const coords=points.map(p=>{
    const x=((new Date(p.date).getTime()-t0)/timeSpan)*width;
    const y=padTop+(1-normalize(p.amount))*(height-padTop-padBottom);
    return [x,y];
  });
  const linePath=coords.map(([x,y],i)=>`${i===0?'M':'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const areaPath=`${linePath} L${coords[coords.length-1][0].toFixed(1)},${height} L0,${height} Z`;
  const markers=coords.map(([x,y],i)=>`<circle class="price-history-point" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.4"><title>${escapeHtml(date(points[i].date))}: ${escapeHtml(money(points[i].amount))}</title></circle>`).join('');
  return `<div class="price-history-chart"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Preisverlauf-Diagramm">
      <path class="price-history-area" d="${areaPath}"/>
      <path class="price-history-line" d="${linePath}"/>
      ${markers}
    </svg></div>
    <div class="price-history-range"><span>Tief ${money(min)}</span><span>Hoch ${money(max)}</span></div>`;
}

function finishFilterOptions(gameId){
  if(gameId==='vcard')return ['Normal','Holo','1st Edition','1st Edition Holo'];
  return ['Normal','Foil','Parallel','Enchanted','Manga','OSR','OUR'];
}
function collectionRarityOptions(gameId,language){
  if(gameId==='one-piece')return ['C','UC','R','SR','SEC','L','P','DON!!','SP CARD','TR'];
  if(gameId==='vcard')return ['Mascot','Support','World','Uncommon','Rare','Ultra Rare','Secret Rare','Paradox','Box Topper','Promo'];
  if(gameId==='hololive')return ['C','U','R','RR','SR','S','OSR','OC','SY','P','HR'];
  if(gameId==='lorcana'){
    const de=['Gewöhnlich','Ungewöhnlich','Selten','Episch','Legendär','Mythisch','Verzaubert','Ikonisch','Speziell'];
    const en=['Common','Uncommon','Rare','Super Rare','Legendary','Epic','Enchanted','Iconic','Special'];
    return language==='DE'?de:language==='EN'?en:[...de,...en];
  }
  return ['Common','Uncommon','Rare','Super Rare','Legendary','Secret Rare'];
}

async function renderCollection(preserve=false){
  const stale=renderGuard();
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Sammlung wird zusammengestellt …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId),f=state.collectionFilters,params=new URLSearchParams({game_id:state.activeGameId,...f}),data=await api(`/api/collection?${params}`);
  if(stale())return;
  state.cards=data.cards;
  const currentSet=data.sets.find(set=>set.id===f.set_id),rarityOptions=collectionRarityOptions(game.id,f.language);
  const collectionStats=statPill([
    {label:'Varianten',value:data.stats.variants},
    {label:'Exemplare',value:data.stats.copies},
    {label:'Marktwert',value:money(data.stats.value)},
  ],{className:'browser-summary',columns:3,mobileColumns:3});
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · SAMMLUNG</span><h1>Meine Karten</h1><p>Durchsuchbare Variantenansicht mit denselben Werkzeugen wie im Set-Katalog.</p></div><div class="page-head-actions"><button class="secondary-button" id="collection-export">Exportieren</button></div></div>
    ${collectionStats}
    <div class="card-toolbar-sticky collection-filter-shell"><div class="op-catalog-filterbar catalog-filter-mobile collection-filter-mobile">
      <div class="filter-search"><span>⌕</span><input id="collection-q" value="${escapeHtml(f.q)}" placeholder="Sammlung durchsuchen"></div>
      ${setSwitcherPopup('collection',data.sets,f.set_id||'__all__',currentSet?setFilterLabel(currentSet):'Alle Sets')}
      ${catalogGameFilterBar(game,f,'collection',rarityOptions)}
      <div class="toolbar-filter-anchor catalog-settings-control">
        <button type="button" class="icon-button" data-filter-toggle="collection-view-popup" aria-expanded="false" aria-label="Weitere Filter" title="Weitere Filter">⚙</button>
        <div class="toolbar-filter-popup hidden" id="collection-view-popup">
          <label>Sprache<select id="collection-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(l=>`<option value="${l}" ${f.language===l?'selected':''}>${l}</option>`).join('')}</select></label>
          <label>Ausführung<select id="collection-finish" class="select-control"><option value="">Alle Varianten</option>${finishFilterOptions(game.id).map(x=>`<option ${f.finish===x?'selected':''}>${x}</option>`).join('')}</select></label>
          <label>Bestand<select id="collection-mode" class="select-control"><option value="all">Alle Karten</option><option value="duplicates">Nur Duplikate</option><option value="watchlisted">Auf Watchlist</option></select></label>
          <label>Sortierung<select id="collection-sort" class="select-control"><option value="number">Nummer</option><option value="name">Name</option><option value="set">Set</option><option value="rarity">Seltenheit</option><option value="value">Wert</option><option value="quantity">Menge</option></select></label>
        </div>
      </div>
    </div></div>
    <section class="card-grid" style="--card-size:${state.zoom}px">${data.cards.length?'':'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe deine Filter an.</span></div>'}</section>`;
  mountTileFeed($('.card-grid',content),[{cards:data.cards}],{key:`collection:${state.activeGameId}`,preserve,render:card=>cardTile(card)});
  state.statsUrl=`/api/collection?${params}`;
  state.statsItems=stats=>[['Varianten',stats.variants],['Exemplare',stats.copies],['Marktwert',money(stats.value)]];
  mountFilterPanel('collection',['.collection-filter-shell'],'Sammlung filtern & sortieren');
  $('#collection-mode').value=f.mode;$('#collection-sort').value=f.sort;$('#collection-export').onclick=()=>{setIeMode('export');openOverlay('import-modal')};
  $$('[data-set-switch]',content).forEach(button=>button.onclick=()=>{f.set_id=button.dataset.setSwitch==='__all__'?'':button.dataset.setSwitch;renderCollection(true)});
  bindCatalogFilterControls(f,()=>renderCollection(true));
  bindCardEvents();bindBrowserFilters('collection',()=>renderCollection(true));
  $('#collection-language').onchange=event=>{f.language=event.target.value;f.rarity='';f.rarities=[];renderCollection(true)};
}
