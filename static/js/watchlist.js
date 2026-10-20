// Watchlists.

async function renderWatchlist(preserve=false){
  const stale=renderGuard();
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Watchlists werden geladen …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId), lists=await api(`/api/watchlists?game_id=${state.activeGameId}`);
  if(stale())return;
  state.activeWatchlists=lists;
  if(!state.watchlistId||!lists.some(l=>l.id===state.watchlistId))state.watchlistId=lists[0]?.id;
  if(!state.watchlistId){content.innerHTML='<div class="empty-state"><b>Noch keine Watchlist</b></div>';return}
  // The set switcher needs only the sets' names: the game's list as the catalogue already loaded
  // it, fetched alongside the cards when it is not there yet (every filter change redraws).
  const f=state.watchFilters,[data,sets]=await Promise.all([api(watchlistCardsUrl(state.watchlistId,f)),game.sets?Promise.resolve(game.sets):api(`/api/games/${game.id}/sets`)]);
  game.sets=sets;
  if(stale())return;
  const currentSet=sets.find(set=>set.id===f.set_id),rarityOptions=collectionRarityOptions(game.id,f.language);
  state.cards=data.cards.map(r=>({...r,variants:[r],variant_count:1,owned_variants:r.quantity?1:0,watchlisted:true,value:r.quantity*r.price}));
  // Selections don't survive a list switch (they're indices into a variant set that only makes
  // sense within the list they were made on) -- drop anything that isn't in the current view.
  const visibleVariantIds=new Set(state.cards.map(c=>c.variant_id));
  [...state.watchSelection].forEach(id=>{if(!visibleVariantIds.has(id))state.watchSelection.delete(id)});
  const otherLists=lists.filter(l=>l.id!==state.watchlistId);
  // Marktwert = Preis × gewünschte Menge, nicht 1× pro Karte: die Summe, die es kosten würde,
  // die Liste komplett zu erfüllen. Same label used in updateWatchTotals()'s lookup below.
  const valueLabel='Marktwert';
  const watchStats=statPill([
    {label:'Liste',value:escapeHtml(data.list.name)},
    {label:'Varianten',value:data.cards.length},
    {label:valueLabel,value:money(data.cards.reduce((a,c)=>a+(c.price||0)*(c.desired_quantity||1),0))},
  ],{className:'browser-summary',columns:3,mobileColumns:3});
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · PREISBEOBACHTUNG</span><h1>Watchlists</h1><p>Getrennte Listen für Kaufziele, Deckprojekte und Preisalarme.</p></div><div class="page-head-actions"><button class="primary-button" id="new-watchlist">＋ Neue Watchlist</button></div></div>
    <div class="list-tabs">${lists.map(l=>`<button data-list="${l.id}" class="${l.id===state.watchlistId?'active':''}"><b>${escapeHtml(l.name)}</b><span>${l.count} · ${money(l.value)}</span></button>`).join('')}</div>
    <div class="browser-summary-row">${watchStats}<div class="browser-summary-actions"><button class="secondary-button watch-selection-toggle ${state.watchSelectionMode?'active':''}" id="watch-selection-toggle" aria-pressed="${state.watchSelectionMode}"><span aria-hidden="true">${state.watchSelectionMode?'✓':'⌗'}</span>${state.watchSelectionMode?'Fertig':'Mehrfachauswahl'}</button><a class="secondary-button watch-export-link" id="export-watchlist" href="/api/watchlists/${state.watchlistId}/export.txt" download>Exportieren</a><button class="secondary-button" id="rename-watchlist">Umbenennen</button>${data.list.is_default?'':`<button class="danger-button" id="delete-watchlist">Löschen</button>`}</div></div>
    ${state.watchSelectionMode?`<div class="watch-bulkbar"><label class="watch-bulkbar-all"><input type="checkbox" id="watch-select-all" ${state.cards.length&&state.watchSelection.size===state.cards.length?'checked':''}> Alle auswählen</label><span class="watch-bulkbar-count" id="watch-bulkbar-count">${state.watchSelection.size?`${state.watchSelection.size} ausgewählt`:'Karten zum Auswählen anklicken'}</span><select id="watch-move-target" class="select-control" ${state.watchSelection.size&&otherLists.length?'':'disabled'}><option value="">Verschieben nach …</option>${otherLists.map(l=>`<option value="${l.id}">${escapeHtml(l.name)}</option>`).join('')}</select><button class="secondary-button" id="watch-move-btn" ${state.watchSelection.size&&otherLists.length?'':'disabled'}>Verschieben</button><button class="danger-button" id="watch-remove-btn" ${state.watchSelection.size?'':'disabled'}>Entfernen</button></div>`:''}
    <div class="card-toolbar-sticky watch-filter-shell"><div class="op-catalog-filterbar catalog-filter-mobile watch-filter-mobile">
      <div class="filter-search"><span>⌕</span><input id="watch-q" value="${escapeHtml(f.q)}" placeholder="Watchlist durchsuchen"></div>
      ${setSwitcherPopup('watch',sets,f.set_id||'__all__',currentSet?setFilterLabel(currentSet):'Alle Sets')}
      ${catalogGameFilterBar(game,f,'watch',rarityOptions)}
      <div class="toolbar-filter-anchor catalog-settings-control">
        ${filterMoreButton('watch-view-popup','Weitere Filter')}
        <div class="toolbar-filter-popup hidden" id="watch-view-popup">
          <label>Sprache<select id="watch-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(l=>`<option value="${l}" ${f.language===l?'selected':''}>${l}</option>`).join('')}</select></label>
          <label>Ausführung<select id="watch-finish" class="select-control"><option value="">Alle Varianten</option>${finishFilterOptions(game.id).map(x=>`<option ${f.finish===x?'selected':''}>${x}</option>`).join('')}</select></label>
        </div>
      </div>
      ${sortControl('watch-sort',[['added','Zuletzt hinzugefügt'],['name','Name A–Z'],['number','Nummer'],['price_high','Preis absteigend'],['price_low','Preis aufsteigend']],f.sort)}
      ${zoomControl('watch-zoom')}
    </div></div>
    <section class="card-grid ${state.watchSelectionMode?'watch-selection-mode':''}" style="--card-size:${state.zoom}px">${state.cards.length?state.cards.map(cardTile).join(''):'<div class="empty-state"><b>Keine Karten in dieser Ansicht</b><span>Passe die Filter an oder füge Karten hinzu.</span></div>'}</section>`;
  mountFilterPanel('watchlist',['.watch-filter-shell'],'Watchlist filtern & sortieren');
  bindZoomControl('watch-zoom',$('.card-grid',content),state.activeGameId);
  $('#watch-sort').value=f.sort;bindCardEvents(state.watchlistId);
  $$('.list-tabs button').forEach(b=>b.onclick=()=>{state.watchlistId=Number(b.dataset.list);state.watchSelection.clear();state.watchSelectionMode=false;renderWatchlist(true)});
  $$('[data-set-switch]',content).forEach(button=>button.onclick=()=>{f.set_id=button.dataset.setSwitch==='__all__'?'':button.dataset.setSwitch;renderWatchlist(true)});
  bindCatalogFilterControls(f,()=>renderWatchlist(true));
  $('#new-watchlist').onclick=async()=>{const name=prompt('Name der neuen Watchlist:','Deckprojekt');if(!name)return;const created=await post('/api/watchlists',{game_id:state.activeGameId,name});state.watchlistId=created.id;renderWatchlist(true);refreshWatchCount()};
  $('#watch-selection-toggle').onclick=()=>{state.watchSelectionMode=!state.watchSelectionMode;if(!state.watchSelectionMode)state.watchSelection.clear();renderWatchlist(true)};
  if($('#rename-watchlist'))$('#rename-watchlist').onclick=async()=>{const name=prompt('Neuer Name:',data.list.name);if(!name)return;await api(`/api/watchlists/${state.watchlistId}`,{method:'PATCH',body:JSON.stringify({name})});renderWatchlist(true)};
  if($('#delete-watchlist'))$('#delete-watchlist').onclick=async()=>{if(!confirm('Diese Watchlist wirklich löschen?'))return;await api(`/api/watchlists/${state.watchlistId}`,{method:'DELETE'});state.watchlistId=null;state.watchSelection.clear();renderWatchlist(true);refreshWatchCount()};
  bindBrowserFilters('watch',()=>renderWatchlist(true));
  $('#watch-language').onchange=event=>{f.language=event.target.value;f.rarity='';f.rarities=[];renderWatchlist(true)};
  bindWatchlistBulkActions();
  enhanceWatchlistTiles();
}

// Multi-selection is an explicit mode. Cards themselves become the selection targets so the
// artwork stays free of persistent checkboxes; these small DOM updates avoid a refetch for every
// selected card.
// The list's cards by variant: every tile looks itself up, and searching the list once per tile
// made a click in a long list cost time in proportion to its length squared.
const watchCardsByVariant=()=>new Map(state.cards.map(card=>[card.variant_id,card]));
function syncWatchSelectionTiles(){
  const cards=watchCardsByVariant();
  $$('.watch-selection-mode .card-tile',content).forEach(tile=>{
    const selected=state.watchSelection.has(tile.dataset.variant);
    const item=cards.get(tile.dataset.variant);
    tile.classList.toggle('watch-selected',selected);
    tile.setAttribute('aria-pressed',String(selected));
    tile.setAttribute('aria-label',`${item?.canonical_name||'Karte'} ${selected?'ausgewählt':'auswählen'}`);
  });
}
function toggleWatchCardSelection(tile){
  const variantId=tile.dataset.variant;
  if(state.watchSelection.has(variantId))state.watchSelection.delete(variantId);else state.watchSelection.add(variantId);
  syncWatchSelectionTiles();
  updateWatchBulkbar();
}
function updateWatchBulkbar(){
  const bar=$('.watch-bulkbar',content); if(!bar)return;
  const n=state.watchSelection.size,total=state.cards.length,hasTargets=$$('#watch-move-target option',bar).length>1;
  $('#watch-bulkbar-count',bar).textContent=n?`${n} ausgewählt`:'Karten zum Auswählen anklicken';
  $('#watch-move-target',bar).disabled=!n||!hasTargets;
  $('#watch-move-btn',bar).disabled=!n||!hasTargets;
  $('#watch-remove-btn',bar).disabled=!n;
  const all=$('#watch-select-all',bar);
  all.checked=n>0&&n===total;
  all.indeterminate=n>0&&n<total;
}
function bindWatchlistBulkActions(){
  const bar=$('.watch-bulkbar',content); if(!bar)return;
  $('#watch-select-all',bar).onchange=e=>{
    if(e.target.checked)state.cards.forEach(c=>state.watchSelection.add(c.variant_id));else state.watchSelection.clear();
    syncWatchSelectionTiles();
    updateWatchBulkbar();
  };
  $('#watch-move-btn',bar).onclick=async()=>{
    const targetId=Number($('#watch-move-target',bar).value); if(!targetId)return;
    const variantIds=[...state.watchSelection];
    const r=await post(`/api/watchlists/${state.watchlistId}/entries/move`,{variant_ids:variantIds,target_list_id:targetId});
    state.watchSelection.clear();state.watchSelectionMode=false;toast(`${r.moved} Karte${r.moved===1?'':'n'} verschoben`);
    renderWatchlist(true);refreshWatchCount();
  };
  $('#watch-remove-btn',bar).onclick=async()=>{
    const variantIds=[...state.watchSelection];
    if(!confirm(`${variantIds.length} Karte${variantIds.length===1?'':'n'} von dieser Watchlist entfernen?`))return;
    const r=await post(`/api/watchlists/${state.watchlistId}/entries/remove`,{variant_ids:variantIds});
    state.watchSelection.clear();state.watchSelectionMode=false;toast(`${r.removed} Karte${r.removed===1?'':'n'} entfernt`);
    renderWatchlist(true);refreshWatchCount();
  };
}
function enhanceWatchlistTiles(){
  const cards=watchCardsByVariant();
  $$('.card-tile',content).forEach(tile=>{
    const item=cards.get(tile.dataset.variant); if(!item)return;
    const info=$('.card-info',tile);
    if(state.watchSelectionMode){
      tile.classList.toggle('watch-selected',state.watchSelection.has(item.variant_id));
      tile.setAttribute('aria-pressed',String(state.watchSelection.has(item.variant_id)));
      tile.setAttribute('aria-label',`${item.canonical_name||'Karte'} ${state.watchSelection.has(item.variant_id)?'ausgewählt':'auswählen'}`);
    }
    info.insertAdjacentHTML('beforeend',`<div class="watch-desired" title="Gewünschte Anzahl Exemplare"><span>Gewünscht</span><button type="button" data-desired-delta="-1" aria-label="Weniger">−</button><b>${item.desired_quantity||1}</b><button type="button" data-desired-delta="1" aria-label="Mehr">＋</button></div>`);
    $('.watch-desired',tile).onclick=e=>e.stopPropagation();
    $$('.watch-desired [data-desired-delta]',tile).forEach(btn=>btn.onclick=()=>changeDesiredQuantity(item,Number(btn.dataset.desiredDelta),tile));
  });
}
async function changeDesiredQuantity(item,delta,tile){
  const el=$('.watch-desired b',tile),next=Math.max(1,Math.min(99,(item.desired_quantity||1)+delta));
  if(next===item.desired_quantity)return;
  const previous=item.desired_quantity;item.desired_quantity=next;el.textContent=String(next);
  updateWatchTotals();
  try{
    await api(`/api/watchlists/${state.watchlistId}/entries/${encodeURIComponent(item.variant_id)}`,{method:'PATCH',body:JSON.stringify({quantity:next})});
  }catch(error){
    item.desired_quantity=previous;el.textContent=String(previous);
    updateWatchTotals();
    toast(error.message||'Menge konnte nicht gespeichert werden');
  }
}
// Marktwert = Preis × gewünschte Menge -- patched locally (both the summary pill and the
// current list's own list-tabs badge) so a quantity click doesn't need a full refetch to be
// reflected, same reasoning as patchLocalQuantity above.
function updateWatchTotals(){
  const total=state.cards.reduce((a,c)=>a+(c.price||0)*(c.desired_quantity||1),0);
  const marktwertLabel=$$('.browser-summary [data-stat] span',content).find(span=>span.textContent==='Marktwert'||span.textContent==='Verkaufswert');
  if(marktwertLabel)marktwertLabel.nextElementSibling.textContent=money(total);
  const activeTabSpan=$(`.list-tabs button[data-list="${state.watchlistId}"] span`,content);
  if(activeTabSpan)activeTabSpan.textContent=`${state.cards.length} · ${money(total)}`;
}
