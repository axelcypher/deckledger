// Deck builder.

function setDeckCatalogOpen(open){
  state.deckCatalogOpen=Boolean(open);
  const mobileCatalog=window.matchMedia('(max-width:760px)').matches;
  const catalog=$('.deck-catalog');
  document.body.classList.toggle('deck-catalog-visible',state.deckCatalogOpen);
  catalog?.classList.toggle('is-open',state.deckCatalogOpen);
  $('.deck-catalog-backdrop')?.classList.toggle('is-open',state.deckCatalogOpen);
  catalog?.setAttribute('aria-hidden',String(mobileCatalog&&!state.deckCatalogOpen));
  catalog?.toggleAttribute('inert',mobileCatalog&&!state.deckCatalogOpen);
  $('#deck-catalog-open')?.setAttribute('aria-expanded',String(state.deckCatalogOpen));
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
  const maximum=deckZoneMaximum(zone,c);
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

// Copy limit of a regular deck card (leader/DON-style zones have their own fixed sizes).
function deckCopyLimit(card){const rules=gameRules(state.activeGameId);return rules.copy_limits?.[card.card_type]??rules.playset_size??4}
// The main deck is limited per card, every other zone (leader, DON!!, cheer, ...) by its size.
function deckZoneMaximum(zone,card){return zone.id==='main'?deckCopyLimit(card):(zone.target||deckCopyLimit(card))}
function closeDeckAddPopup(){const modal=$('#deck-add-modal');if(modal)modal.remove();document.body.style.overflow='';}

function openDeckAddPopup(card,profile){
  closeDeckAddPopup();
  const zone=profile.zones.find(item=>item.id===card.suggested_zone)||profile.zones[0];
  const maximum=deckZoneMaximum(zone,card);
  const modal=document.createElement('div');modal.id='deck-add-modal';modal.className='overlay deck-add-overlay';
  modal.innerHTML=`<div class="deck-add-dialog"><button class="close-button" data-deck-add-close>×</button><div class="deck-add-image card-finish-frame ${finishPresentation(card).effect}"><img src="${artUrl(card.variant_id,'full')}" alt="${escapeHtml(card.canonical_name)}"></div><div class="deck-add-copy"><span class="eyebrow">KARTE HINZUFÜGEN</span><h2>${escapeHtml(card.canonical_name)}</h2><p>${escapeHtml(card.collector_number)} · ${card.language} · ${escapeHtml(card.rarity)}</p><div class="automatic-zone"><span>Automatischer Bereich</span><b>${escapeHtml(zone.name)}</b><small>${escapeHtml(card.card_type)} wird nach dem Regelprofil einsortiert.</small></div><label>Menge<select id="deck-add-quantity" class="select-control">${Array.from({length:maximum},(_,index)=>`<option value="${index+1}">${index+1}</option>`).join('')}</select></label><div class="dialog-actions"><button class="secondary-button" data-deck-add-close>Abbrechen</button><button class="primary-button" id="confirm-deck-add">Zu ${escapeHtml(zone.name)} hinzufügen</button></div></div></div>`;
  document.body.append(modal);document.body.style.overflow='hidden';
  $$('[data-deck-add-close]',modal).forEach(button=>button.onclick=closeDeckAddPopup);modal.onmousedown=event=>{if(event.target===modal)closeDeckAddPopup()};
  $('#confirm-deck-add',modal).onclick=async event=>{event.currentTarget.disabled=true;const quantity=Number($('#deck-add-quantity',modal).value);try{const result=await post(`/api/decks/${state.deckId}/cards`,{variant_id:card.variant_id,zone:'auto',delta:quantity});closeDeckAddPopup();state.deckZone=result.zone;toast(`${quantity}× zu ${zone.name} hinzugefügt`);renderDeckbuilder(true)}catch(error){event.currentTarget.disabled=false;toast(error.message)}};
}

// renderDeckbuilder does several awaited round-trips before it ever touches the DOM. Typing
// fast in the catalog search re-triggers it every ~250ms (the debounce in #deck-q below), and
// if one call is still mid-flight when the next fires, both are racing to write content.innerHTML
// and to restore focus/selection on the rebuilt #deck-q afterwards -- whichever happens to
// resolve LAST wins, even if it was the OLDER (now-stale) request. That's what caused the
// cursor to silently jump backwards mid-word: an earlier, slower render finishing after a newer
// one and re-applying its now-outdated selectionRange on top of text the user had since kept
// typing. Fixed with a generation token: every call grabs the current counter, and only the
// call that's still the newest by the time it's ready to touch the DOM is allowed to.
let deckRenderToken=0;
async function renderDeckbuilder(preserve=false,catalogPosition=null){
  const myDeckRenderToken=++deckRenderToken;
  state.deckCatalogObserver?.disconnect();state.deckCatalogObserver=null;
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Deckbuilder wird geladen …</p></div>';
  const game=state.boot.games.find(g=>g.id===state.activeGameId),[formats,decks]=await Promise.all([api(`/api/games/${state.activeGameId}/formats`),api(`/api/decks?game_id=${state.activeGameId}`)]);
  if(!state.deckId){
    if(myDeckRenderToken!==deckRenderToken)return;
    setDeckCatalogOpen(false);
    content.innerHTML=`<div class="deck-page-head deck-overview-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · OFFIZIELLE REGELPROFILE</span><h1>Deine Decks</h1><p>Wähle ein Deck oder beginne ein neues.</p></div><button class="primary-button" id="new-deck">＋ Neues Deck</button></div>${decks.length?`<section class="deck-overview-grid">${decks.map(deck=>deckOverviewCard(deck,game,formats)).join('')}<button class="deck-create-card" id="deck-create-tile"><span>＋</span><b>Neues Deck</b><small>${escapeHtml(formats[0]?.name||'Regelprofil wählen')}</small></button></section>`:`<div class="deck-empty"><span>▱</span><h2>Dein erstes ${escapeHtml(game.short_name)}-Deck</h2><p>Der Builder prüft Kartenzahl, Kopienlimits, Farben und Formatlegalität.</p><button class="primary-button" id="first-deck">Deck erstellen</button></div>`}`;
    $$('.deck-overview-card').forEach(button=>button.onclick=()=>{state.deckId=Number(button.dataset.deck);renderDeckbuilder()});
    $('#new-deck').onclick=()=>createDeck(formats[0]);$('#deck-create-tile')?.addEventListener('click',()=>createDeck(formats[0]));$('#first-deck')?.addEventListener('click',()=>createDeck(formats[0]));return;
  }
  if(myDeckRenderToken!==deckRenderToken)return;
  if(!decks.some(deck=>deck.id===state.deckId)){state.deckId=null;return renderDeckbuilder(true)}
  const f=state.deckFilters,isOnePiece=game.id==='one-piece',isLorcana=game.id==='lorcana',isHololive=game.id==='hololive',isVcard=game.id==='vcard';
  const initialCatalogLimit=Math.max(72,Math.min(20000,Number(catalogPosition?.loadedCount)||72));
  const params=new URLSearchParams({game_id:state.activeGameId,q:f.q||'',sort:f.sort||'number',limit:String(initialCatalogLimit),offset:'0'});
  if(f.rarity)params.set('rarity',f.rarity);
  if(f.rarities?.length)params.set('rarities',f.rarities.join(','));
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
      ['colors','kinds','bloomLevels'].forEach(key=>{if(f[key]?.length)params.set(key,f[key].join(','))});
    }
    if(isVcard){
      ['colors','types','costs'].forEach(key=>{if(f[key]?.length)params.set(key,f[key].join(','))});
    }
  }
  const [detail,catalog]=await Promise.all([api(`/api/decks/${state.deckId}`),api(`/api/deckbuilder/catalog?${params}`)]),profile=formats.find(x=>x.id===detail.deck.format_id)||formats[0];
  if(!profile.zones.some(z=>z.id===state.deckZone))state.deckZone=profile.zones[0].id;
  const validation=detail.validation, zoneCards=detail.cards.filter(c=>c.zone===state.deckZone), currentZone=profile.zones.find(z=>z.id===state.deckZone);
  const summary=detail.summary;
  const purchaseSummary=`<div class="deck-purchase-summary"><div><span>In deiner Sammlung</span><b>${summary.owned_copies} / ${summary.required_copies}</b><small>${summary.complete_entries} Kartenpositionen vollständig</small></div><div class="${summary.missing_copies?'missing':'complete'}"><span>Fehlende Exemplare</span><b>${summary.missing_copies}</b><small>${summary.missing_entries} Kartenpositionen betroffen</small></div><div class="purchase-cost"><span>Fehlende Karten kaufen</span><b>${money(summary.missing_cost)}</b><small>${summary.missing_unpriced_copies?`${summary.missing_unpriced_copies} fehlende Exemplare ohne Preis`:'Alle fehlenden Karten eingepreist'}</small></div></div>`;
  const deckStatChips=statPill([
    {label:'Vorhanden',value:`${summary.owned_copies}/${summary.required_copies}`},
    {label:'Fehlt',value:summary.missing_copies,className:summary.missing_copies?'missing':'complete'},
    {label:'Nachkauf',value:money(summary.missing_cost)},
    {label:'Deckwert',value:money(summary.deck_value)},
  ],{className:'deck-stat-chips',columns:4,mobileColumns:4});
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
  }else if(isVcard){
    const mainCards=detail.cards.filter(card=>card.zone==='main'),mainCount=mainCards.reduce((total,card)=>total+card.quantity,0);
    deckWorkspace=`<div class="generic-deck-overview"><div class="op-deck-stats">${deckStatChips}</div></div>${validationNotice}
      <section class="op-deck-section"><div class="op-section-head"><div><span class="eyebrow">DECK</span><h2>VCard-Karten</h2></div><b>${mainCount} / 50</b></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${mainCards.length?mainCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Karten hinzugefügt.</div>'}</div></section>`;
  }else{
    deckWorkspace=`${purchaseSummary}${validationNotice}<nav class="zone-tabs">${profile.zones.map(z=>`<button data-zone="${z.id}" class="${z.id===state.deckZone?'active':''}"><span>${escapeHtml(z.name)}</span><b>${validation.counts[z.id]||0} / ${z.target}</b></button>`).join('')}</nav><div class="deck-zone-head"><div><span class="eyebrow">${escapeHtml(currentZone.name).toUpperCase()}</span><h2>${zoneCards.reduce((a,c)=>a+c.quantity,0)} Karten</h2></div><div class="deck-errors">${validation.errors.slice(0,3).map(x=>`<span>! ${escapeHtml(x)}</span>`).join('')}${validation.warnings.slice(0,2).map(x=>`<span class="warning">△ ${escapeHtml(x)}</span>`).join('')}</div></div><div class="deck-card-list ${state.deckView==='grid'?'deck-card-grid':''}">${zoneCards.length?zoneCards.map(card=>deckContentCard(card,false,coverVariantId)).join(''):'<div class="deck-zone-empty">Noch keine Karten in diesem Bereich.</div>'}</div>`;
  }
  const viewTools=`<div class="op-filter-group catalog-view-tools"><span>Ansicht</span><div><div class="deck-view-toggle segmented" aria-label="Ansicht"><button data-deck-view="list" class="${state.deckView==='list'?'active':''}" title="Liste">☷</button><button data-deck-view="grid" class="${state.deckView==='grid'?'active':''}" title="Kacheln">▦</button></div><label class="catalog-zoom ${state.deckView==='grid'?'':'hidden'}"><input id="deck-zoom" type="range" min="105" max="190" step="5" value="${state.deckZoom}" aria-label="Kachelgröße"><output id="deck-zoom-value">${state.deckZoom}%</output></label></div></div>`;
  const deckRarity=isLorcana?lorcanaRarityPopup(f,'deck','deck'):gameRarityPopup(catalog.rarities||[],f,'deck','deck');
  const deckTypes=isOnePiece
    ?`<div class="op-filter-group op-types"><div>${deckFilterPills(OP_TYPE_FILTERS,'types',f)}</div></div>`
    :isLorcana?`<div class="op-filter-group op-types"><div>${deckFilterPills(LORCANA_TYPE_FILTERS,'types',f)}</div></div>`
    :isHololive?`<div class="op-filter-group op-types"><div>${deckFilterPills(HOLOLIVE_KIND_FILTERS,'kinds',f)}</div></div>`
    :isVcard?`<div class="op-filter-group op-types"><div>${deckFilterPills(VCARD_TYPE_FILTERS,'types',f)}</div></div>`:'';
  const deckSecondaryTypes=isHololive?`<div class="op-filter-group"><div>${deckFilterPills(HOLOLIVE_BLOOM_FILTERS,'bloomLevels',f)}</div></div>`:'';
  const sharedGameFilters=isOnePiece?opCardFilterGroups(f,'deck'):isLorcana?lorcanaCardFilterGroups(f,'deck'):isHololive?hololiveCardFilterGroups(f,'deck'):isVcard?vcardCardFilterGroups(f,'deck'):'';
  const deckAttributes=isOnePiece?`<div class="op-filter-group op-attributes"><div>${OP_ATTRIBUTE_FILTERS.map(([value,label,color])=>{const iconUrl=`/op-filter-icon/attribute-${value.toLowerCase()}.svg?v=2`;return `<button type="button" class="op-image-filter ${(f.attributes||[]).includes(value)?'active':''}" ${filterData('deck','attributes',value)} aria-label="${label}" title="${label}"><span class="op-attribute-glyph" style="--op-attribute-color:${color};--op-attribute-icon:url('${iconUrl}')"><img src="${iconUrl}" alt="${label}"></span></button>`}).join('')}</div></div>`:'';
  const filters=`<div class="op-catalog-filterbar catalog-filter-mobile deck-catalog-filter-mobile">
    <div class="deck-catalog-filter-row deck-catalog-filter-primary">
      <div class="filter-search"><span>⌕</span><input id="deck-q" value="${escapeHtml(f.q)}" placeholder="Karten suchen"></div>
      ${deckRarity}
      <div class="toolbar-filter-anchor catalog-settings-control">
        <button type="button" class="icon-button" data-filter-toggle="deck-view-popup" aria-expanded="false" aria-label="Ansicht und Sortierung" title="Ansicht und Sortierung">⚙</button>
        <div class="toolbar-filter-popup hidden" id="deck-view-popup">
          ${isOnePiece?'':`<label>Sprache<select id="deck-language" class="select-control"><option value="all">Alle Sprachen</option>${game.languages.map(language=>`<option value="${language}" ${f.language===language?'selected':''}>${language}</option>`).join('')}</select></label>`}
          <label>Sortierung<select id="deck-sort" class="select-control"><option value="number">Nummer</option><option value="name">Name</option><option value="cost">Kosten</option><option value="rarity">Seltenheit</option></select></label>
          ${viewTools}
        </div>
      </div>
      ${deckTypes}
    </div>
    <div class="deck-catalog-filter-row deck-catalog-filter-secondary">${deckSecondaryTypes}${sharedGameFilters}${deckAttributes}</div>
  </div>`;
  if(myDeckRenderToken!==deckRenderToken)return;
  content.innerHTML=`<div class="deck-shell deck-shell-editor" style="--deck-card-size:${state.deckZoom}px;--catalog-card-size:${state.deckZoom}px">
    <section class="deck-editor"><header class="deck-editor-head"><button class="compact-back-button" id="deck-overview-back" title="Alle Decks" aria-label="Alle Decks">←</button><div class="deck-title-field"><input id="deck-name" value="${escapeHtml(detail.deck.name)}" aria-label="Deckname"></div><select id="deck-format" class="select-control">${formats.map(x=>`<option value="${x.id}" ${x.id===detail.deck.format_id?'selected':''}>${escapeHtml(x.name)}</option>`).join('')}</select><a class="deck-rules-button" href="${escapeHtml(validation.rules_url)}" target="_blank" rel="noopener"><span>Regeln</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-9 9"/><path d="M18 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg></a><button id="deck-actions-open" class="icon-button deck-header-menu-button" title="Deckaktionen" aria-label="Deckaktionen öffnen"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h.01M12 12h.01M18 12h.01"/></svg></button></header>
      <button type="button" class="deck-catalog-open-button" id="deck-catalog-open" aria-controls="deck-card-catalog" aria-expanded="${state.deckCatalogOpen}"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="4" width="12" height="16" rx="2"/><path d="M9 2h9a2 2 0 0 1 2 2v13M11 9v6M8 12h6"/></svg><span><b>Karten hinzufügen</b><small>${catalog.pagination.total} Basiskarten durchsuchen</small></span><i>›</i></button>
      ${deckWorkspace}
    </section>
    <button type="button" class="deck-catalog-backdrop ${state.deckCatalogOpen?'is-open':''}" aria-label="Kartenkatalog schließen"></button>
    <aside id="deck-card-catalog" class="deck-catalog ${state.deckCatalogOpen?'is-open':''}"><div class="deck-catalog-title"><div><b>Kartenkatalog</b><span>${catalog.pagination.total} Basiskarten · ${isOnePiece?'EN':f.language==='all'?'alle Sprachen':f.language}</span></div><button type="button" class="deck-catalog-close" id="deck-catalog-close" aria-label="Kartenkatalog schließen">×</button></div>${filters}<div class="catalog-card-list ${state.deckView==='grid'?'catalog-card-grid-list':''}">${catalog.cards.map(c=>deckCatalogCard(c,profile,deckQuantities)).join('')}<div class="deck-catalog-sentinel">${catalog.pagination.has_more?'Weitere Karten werden geladen …':'Alle Treffer geladen'}</div></div></aside></div>`;
  mountFilterPanel('deck-catalog',['.deck-catalog-filter-mobile'],'Karten filtern & sortieren');
  setDeckCatalogOpen(state.deckCatalogOpen);
  bindDeckImagePreviews();
  const catalogList=$('.catalog-card-list'),sentinel=$('.deck-catalog-sentinel');
  const catalogSnapshot=()=>({scrollTop:catalogList.scrollTop,loadedCount:catalog.cards.length});
  if(catalogPosition)requestAnimationFrame(()=>{catalogList.scrollTop=Math.max(0,Number(catalogPosition.scrollTop)||0)});
  $('#deck-catalog-open').onclick=()=>setDeckCatalogOpen(true);
  $('#deck-catalog-close').onclick=()=>setDeckCatalogOpen(false);
  $('.deck-catalog-backdrop').onclick=()=>setDeckCatalogOpen(false);
  $('#deck-overview-back').onclick=()=>{setDeckCatalogOpen(false);state.deckId=null;renderDeckbuilder()};
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
  $('#deck-actions-open').onclick=()=>openOverlay('deck-transfer-modal');
  $('#deck-delete-choice').onclick=async()=>{closeOverlay('deck-transfer-modal');if(!confirm(`Deck „${detail.deck.name}“ löschen?`))return;await api(`/api/decks/${state.deckId}`,{method:'DELETE'});setDeckCatalogOpen(false);state.deckId=null;renderDeckbuilder()};
  $('#deck-transfer-import-choice').onclick=()=>{closeOverlay('deck-transfer-modal');$('#deck-import-preview').classList.remove('visible');$('#deck-import-preview').innerHTML='';$('#deck-apply-import').disabled=true;openOverlay('deck-import-modal')};
  // hrefs set right before opening (not baked into the static template markup) since the target
  // deck id is only known once a deck is actually open -- plain <a href download> links, same
  // pattern as the collection export cards (#importexport-export-section), no extra JS fetch
  // needed for the file download itself.
  $('#deck-transfer-export-choice').onclick=()=>{
    closeOverlay('deck-transfer-modal');
    $('#deck-export-list').href=`/api/decks/${state.deckId}/export/list.txt`;
    $('#deck-export-missing').href=`/api/decks/${state.deckId}/export/missing.txt`;
    openOverlay('deck-export-modal');
  };
  let timer;$('#deck-q').oninput=e=>{clearTimeout(timer);f.q=e.target.value;timer=setTimeout(()=>reRenderPreservingFocus('#deck-q',()=>renderDeckbuilder(true)),250)};
  $('#deck-sort').value=f.sort;$('#deck-sort').onchange=event=>{f.sort=event.target.value;renderDeckbuilder(true)};
  $('#deck-language')?.addEventListener('change',event=>{f.language=event.target.value;f.rarity='';f.rarities=[];renderDeckbuilder(true)});
  $$('[data-deck-filter]').forEach(button=>button.onclick=()=>{const key=button.dataset.deckFilter,value=button.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(item=>item!==value):[...current,value];renderDeckbuilder(true)});
  $$('[data-single-filter]').forEach(button=>button.onclick=()=>{const key=button.dataset.singleFilter,value=button.dataset.value;f[key]=f[key]===value?'':value;renderDeckbuilder(true)});
}

async function createDeck(profile){const r=await post('/api/decks',{game_id:state.activeGameId,name:'Neues Deck',format_id:profile?.id});state.deckId=r.id;state.deckZone=profile?.zones?.[0]?.id||'main';renderDeckbuilder()}
async function saveDeckMeta(name,format_id,notes){await api(`/api/decks/${state.deckId}`,{method:'PATCH',body:JSON.stringify({name,format_id,notes})});toast('Deck gespeichert');renderDeckbuilder(true)}
