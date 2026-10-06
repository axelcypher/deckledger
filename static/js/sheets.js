// Trade / sale sheets.

// ---- Trade / sale sheets ------------------------------------------------------------------
// Pick cards from the collection, group them into a sheet, get the sheet as images for a
// "want to sell" / "want to trade" post. The images are rendered by the server (sheet_render.py);
// this page is selection, options and preview.
const sheetView={options:null,payload:null,picker:{source:'collection',q:'',cards:null,wanted:null,found:null,foundFor:''},previewTimer:null};
// Where the cards of a sheet come from: what is offered comes out of the collection, what is
// looked for off the watchlists or out of the whole catalogue.
const SHEET_SOURCES={
  offer:[['collection','Sammlung'],['duplicates','Doppelte'],['surplus','Über Playset']],
  wanted:[['watchlist','Watchlists'],['catalog','Katalog']],
};
const SHEET_KIND_GROUPS={offer:['WTS','WTT'],wanted:['WTB','WTTF']};
const sheetIsWanted=kind=>SHEET_KIND_GROUPS.wanted.includes(kind.split('/')[0]);
const SHEET_KIND_TITLES={WTS:'Want to sell – ich verkaufe',WTT:'Want to trade – ich tausche',WTB:'Want to buy – ich kaufe',WTTF:'Want to trade for – ich suche zum Tausch'};
const SHEET_PICKER_LIMIT=80;

async function renderSheets(){
  const game=state.boot.games.find(g=>g.id===state.activeGameId);
  sheetView.options=sheetView.options||await api('/api/trade-sheets/options');
  // The options are fetched once; a first visit that is left before they arrive draws nothing.
  if(state.route!=='sheets')return;
  if(state.dealId)return renderDeal();
  if(!state.sheetId&&sheetView.tab==='deals')return renderDeals();
  if(!state.sheetId&&sheetView.tab==='ebay')return renderEbay();
  if(!state.sheetId){
    content.innerHTML='<div class="page-loader"><span></span><p>Sheets werden geladen …</p></div>';
    const sheets=await api(`/api/trade-sheets?game_id=${encodeURIComponent(game.id)}`);
    if(state.route!=='sheets'||state.sheetId)return;
    content.innerHTML=`<div class="deck-page-head deck-overview-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · VERKAUF &amp; TAUSCH</span><h1>Deine Sheets</h1><p>Stelle Karten für WTS- und WTT-Posts zusammen und gib sie als Bild aus.</p></div><button class="primary-button" id="new-sheet">＋ Neues Sheet</button></div>
      ${sheetTabsHtml('sheets')}
      <section id="sheet-inbox" class="sheet-inbox hidden"></section>
      ${sheets.length?`<section class="sheet-overview-grid">${sheets.map(sheet=>`<button class="sheet-overview-card" data-sheet="${sheet.id}">
        <div class="sheet-overview-art">${sheet.card_count?`<img loading="lazy" src="/api/trade-sheets/${sheet.id}/image/1.jpg?scale=0.3&v=${encodeURIComponent(sheet.updated_at)}" alt="">`:'<span>Noch keine Karten</span>'}</div>
        <div class="sheet-overview-copy"><span class="sheet-kind sheet-kind-${sheet.kind.toLowerCase().replace('/','-')}">${escapeHtml(sheet.kind)}</span><h3>${escapeHtml(sheet.name)}</h3><small>${sheet.card_count} Karten · ${sheet.copies} Exemplare</small></div></button>`).join('')}</section>`
      :`<div class="deck-empty"><span>▦</span><h2>Dein erstes Sheet</h2><p>Wähle Karten aus deiner Sammlung, lege Menge und Preis fest und lade die fertige Collage herunter.</p><button class="primary-button" id="first-sheet">Sheet erstellen</button></div>`}`;
    const create=async()=>{const created=await post('/api/trade-sheets',{game_id:game.id,name:'Neues Sheet',kind:'WTS'});state.sheetId=created.id;renderSheets()};
    $('#new-sheet').onclick=create;$('#first-sheet')?.addEventListener('click',create);
    $$('.sheet-overview-card',content).forEach(card=>card.onclick=()=>{state.sheetId=Number(card.dataset.sheet);renderSheets()});
    bindSheetTabs();
    renderInbox();
    return;
  }
  content.innerHTML='<div class="page-loader"><span></span><p>Sheet wird geladen …</p></div>';
  let payload;
  try{payload=await api(`/api/trade-sheets/${state.sheetId}`)}catch(error){state.sheetId=null;return renderSheets()}
  if(state.route!=='sheets')return;
  sheetView.payload=payload;
  const sheet=payload.sheet,options=sheetView.options,wanted=sheetIsWanted(sheet.kind),sources=SHEET_SOURCES[wanted?'wanted':'offer'];
  if(!sources.some(([id])=>id===sheetView.picker.source))sheetView.picker.source=sources[0][0];
  const kindChip=kind=>`<button type="button" class="sheet-kind-chip sheet-kind-chip-${kind.toLowerCase()} ${sheet.kind.split('/').includes(kind)?'active':''}" data-sheet-kind="${kind}" aria-pressed="${sheet.kind.split('/').includes(kind)}" title="${SHEET_KIND_TITLES[kind]}"><i aria-hidden="true"></i>${kind}</button>`;
  content.innerHTML=`<div class="sheet-shell">
    <section class="sheet-editor">
      <header class="sheet-head"><button class="compact-back-button" id="sheet-back" title="Alle Sheets" aria-label="Alle Sheets">←</button>
        <div class="sheet-kinds" role="group" aria-label="Art des Sheets"><div class="segmented sheet-side-switch" role="radiogroup" aria-label="Biete oder Suche">${[['offer','Biete'],['wanted','Suche']].map(([side,label])=>`<button type="button" role="radio" data-sheet-side="${side}" aria-checked="${wanted===(side==='wanted')}" class="${wanted===(side==='wanted')?'active':''}">${label}</button>`).join('')}</div>${SHEET_KIND_GROUPS[wanted?'wanted':'offer'].map(kindChip).join('')}</div>
        <input id="sheet-name" class="sheet-name-input" value="${escapeHtml(sheet.name)}" maxlength="80" aria-label="Titel des Sheets">
        ${wanted?'':'<button class="secondary-button" id="sheet-ebay" title="Aus den Karten dieses Sheets eBay-Angebotsentwürfe machen (Menge und Preis vom Sheet)">eBay-Entwürfe</button>'}
        <button class="icon-button" id="sheet-delete" title="Sheet löschen" aria-label="Sheet löschen">🗑</button></header>
      <div class="sheet-options">
        <label>Zusatzzeile<input id="sheet-subtitle" value="${escapeHtml(sheet.subtitle)}" maxlength="80" placeholder="z. B. u/deinname · Datum"></label>
        <label>Sortierung<select id="sheet-sort" class="select-control"><option value="number">Set &amp; Nummer</option><option value="rarity">Seltenheit, dann Nummer</option></select></label>
        <label>Raster<select id="sheet-layout" class="select-control">${options.layouts.map(layout=>`<option value="${layout}">${layout==='auto'?'Automatisch nach Anzahl':layout.replace('x',' × ')}</option>`).join('')}</select></label>
      </div>
      <div class="sheet-backgrounds" role="radiogroup" aria-label="Hintergrund">${options.backgrounds.map(bg=>`<button type="button" role="radio" aria-checked="${sheet.background===bg.id}" data-sheet-background="${bg.id}" class="${sheet.background===bg.id?'active':''}" title="${escapeHtml(bg.label)}"><img src="/api/trade-sheets/backgrounds/${bg.id}.jpg" alt=""><span>${escapeHtml(bg.label)}</span>${bg.custom?`<i class="sheet-background-delete" role="button" tabindex="0" data-delete-background="${bg.id}" title="Hintergrund löschen" aria-label="Hintergrund ${escapeHtml(bg.label)} löschen">×</i>`:''}</button>`).join('')}<label class="sheet-background-upload" title="Eigenes Bild als Hintergrund hochladen (JPEG, PNG, WebP)"><input type="file" id="sheet-background-file" accept="image/jpeg,image/png,image/webp" hidden><b>＋</b><span>Eigenes Bild</span></label></div>
      <div id="sheet-preview" class="sheet-preview"></div>
      <div class="sheet-text"><div class="sheet-section-head"><b>Text für den Post</b><button class="secondary-button" id="sheet-copy-text">Kopieren</button></div><textarea id="sheet-text" readonly rows="6"></textarea></div>
      <div id="sheet-posts" class="sheet-posts"></div>
      <div id="sheet-scout" class="sheet-posts sheet-scout"></div>
      <div class="sheet-section-head"><b>Karten auf dem Sheet</b><span id="sheet-count"></span></div>
      <div id="sheet-entries" class="sheet-entries"></div>
    </section>
    <aside class="sheet-picker">
      <div class="sheet-section-head"><b>Karten hinzufügen</b></div>
      <div class="segmented sheet-source">${sources.map(([id,label])=>`<button type="button" data-sheet-source="${id}" class="${sheetView.picker.source===id?'active':''}">${label}</button>`).join('')}</div>
      <div class="filter-search"><span>⌕</span><input id="sheet-picker-q" value="${escapeHtml(sheetView.picker.q)}" placeholder="${wanted?'Karte suchen: Name, Nummer oder Set':'Name, Nummer oder Set'}"></div>
      <div id="sheet-picker-list" class="sheet-picker-list"></div>
    </aside></div>`;
  $('#sheet-sort').value=sheet.sort;$('#sheet-layout').value=sheet.layout;
  const patchSheet=async changes=>{try{applySheetPayload(await api(`/api/trade-sheets/${state.sheetId}`,{method:'PATCH',body:JSON.stringify(changes)}))}catch(error){toast(error.message)}};
  $('#sheet-back').onclick=()=>{state.sheetId=null;renderSheets()};
  $('#sheet-delete').onclick=async()=>{if(!confirm(`Sheet „${sheetView.payload.sheet.name}“ löschen?`))return;await api(`/api/trade-sheets/${state.sheetId}`,{method:'DELETE'});state.sheetId=null;toast('Sheet gelöscht');renderSheets()};
  $('#sheet-name').onchange=event=>patchSheet({name:event.target.value});
  $('#sheet-ebay')?.addEventListener('click',async event=>{
    if(!sheetView.payload.cards.length){toast('Auf dem Sheet liegen noch keine Karten.');return}
    const button=event.currentTarget;button.disabled=true;
    try{await createEbayDrafts({sheet_id:state.sheetId})}catch(error){toast(error.message)}
    button.disabled=false;
  });
  $('#sheet-subtitle').onchange=event=>patchSheet({subtitle:event.target.value});
  $('#sheet-sort').onchange=event=>patchSheet({sort:event.target.value});
  $('#sheet-layout').onchange=event=>patchSheet({layout:event.target.value});
  // Two pairs of switches: what is offered (sell, trade) and what is looked for (buy, trade for).
  // Within a pair one or both can be on, never neither; a sheet is on one side only, so a switch
  // of the other pair moves the sheet over -- and with it where the picker gets its cards from.
  // Offering and looking for are separate sheets: switching the side carries the choice over
  // (sell <-> buy, trade <-> trade for).
  $$('[data-sheet-side]',content).forEach(button=>button.onclick=async()=>{
    const current=sheetView.payload.sheet.kind,to=button.dataset.sheetSide;
    if(sheetIsWanted(current)===(to==='wanted'))return;
    const from=SHEET_KIND_GROUPS[to==='wanted'?'offer':'wanted'],target=SHEET_KIND_GROUPS[to];
    await patchSheet({kind:current.split('/').map(kind=>target[from.indexOf(kind)]).join('/')});
    renderSheets();
  });
  $$('[data-sheet-kind]',content).forEach(button=>button.onclick=async()=>{
    const current=sheetView.payload.sheet.kind,kind=button.dataset.sheetKind,active=current.split('/');
    const group=Object.values(SHEET_KIND_GROUPS).find(kinds=>kinds.includes(kind));
    let chosen=group.filter(item=>active.includes(item));
    if(!chosen.length)chosen=[kind];
    else if(!chosen.includes(kind))chosen=group.filter(item=>item===kind||chosen.includes(item));
    else if(chosen.length>1)chosen=chosen.filter(item=>item!==kind);
    const next=chosen.join('/');
    if(next===current)return;
    await patchSheet({kind:next});
    if(sheetIsWanted(next)!==sheetIsWanted(current)){renderSheets();return}
    $$('[data-sheet-kind]',content).forEach(item=>{const on=chosen.includes(item.dataset.sheetKind);item.classList.toggle('active',on);item.setAttribute('aria-pressed',String(on))});
  });
  $$('[data-sheet-background]',content).forEach(button=>button.onclick=()=>{$$('[data-sheet-background]',content).forEach(item=>{item.classList.toggle('active',item===button);item.setAttribute('aria-checked',String(item===button))});patchSheet({background:button.dataset.sheetBackground})});
  // The option list is cached; after an upload or a delete it is fetched again with the view.
  $('#sheet-background-file').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    const form=new FormData();form.append('file',file);
    try{
      const response=await fetch('/api/trade-sheets/backgrounds',{method:'POST',body:form}),created=await response.json().catch(()=>({}));
      if(!response.ok)throw new Error(created.error||`Upload fehlgeschlagen (${response.status})`);
      await api(`/api/trade-sheets/${state.sheetId}`,{method:'PATCH',body:JSON.stringify({background:created.id})});
      sheetView.options=null;toast('Hintergrund hinzugefügt');renderSheets();
    }catch(error){toast(error.message);event.target.value=''}
  };
  $$('[data-delete-background]',content).forEach(control=>control.onclick=async event=>{
    event.stopPropagation();
    if(!confirm('Diesen Hintergrund löschen? Sheets, die ihn verwenden, bekommen wieder den Standard-Hintergrund.'))return;
    try{await api(`/api/trade-sheets/backgrounds/${control.dataset.deleteBackground}`,{method:'DELETE'});sheetView.options=null;toast('Hintergrund gelöscht');renderSheets()}
    catch(error){toast(error.message)}
  });
  $('#sheet-copy-text').onclick=async()=>{try{await navigator.clipboard.writeText($('#sheet-text').value);toast('Text kopiert')}catch{$('#sheet-text').select();toast('Text markiert – mit Strg+C kopieren')}};
  $$('[data-sheet-source]',content).forEach(button=>button.onclick=()=>{sheetView.picker.source=button.dataset.sheetSource;$$('[data-sheet-source]',content).forEach(item=>item.classList.toggle('active',item===button));renderSheetPicker()});
  let searchTimer;$('#sheet-picker-q').oninput=event=>{sheetView.picker.q=event.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(renderSheetPicker,150)};
  applySheetPayload(payload,true);
  sheetView.picker.cards=null;sheetView.picker.wanted=null;sheetView.picker.found=null;
  renderSheetPosts();
  renderSheetScout();
  renderSheetPicker();
}

// Everything that depends on the sheet's content. Called after each change with the server's
// answer, so the entry list, the text and the picker's "on the sheet" marks never drift apart.
function applySheetPayload(payload,immediate=false){
  sheetView.payload=payload;
  const box=$('#sheet-entries');if(!box)return;
  const copies=payload.cards.reduce((sum,card)=>sum+card.quantity,0);
  $('#sheet-count').textContent=payload.cards.length?`${payload.cards.length} Karten · ${copies} Exemplare`:'';
  $('#sheet-text').value=payload.cards.length?payload.text:'';
  box.innerHTML=payload.cards.length?payload.cards.map(card=>`<div class="sheet-entry" data-variant="${escapeHtml(card.variant_id)}">
      ${finishThumb(card,artUrl(card.variant_id),card.canonical_name,'sheet-thumb')}
      <div class="sheet-entry-copy"><b>${escapeHtml(card.canonical_name)}${card.reserved?`<em class="sheet-reserved">${card.reserved>=card.quantity?'reserviert':`${card.reserved} reserviert`}</em>`:''}</b><small>${escapeHtml(card.set_code)} · ${escapeHtml(card.collector_number)} · ${escapeHtml(card.rarity)} · ${escapeHtml(variantName(card))}${card.price!=null?` · ${price(card.price)}`:''}</small>${sheetIsWanted(payload.sheet.kind)?(card.owned?`<small>${card.owned}× schon in der Sammlung</small>`:''):card.quantity>card.owned?`<small class="sheet-warning">Nur ${card.owned}× in der Sammlung</small>`:''}</div>
      <input class="sheet-label" value="${escapeHtml(card.label)}" maxlength="24" placeholder="Preis / Notiz" aria-label="Preis oder Notiz">
      <div class="sheet-stepper"><button type="button" data-sheet-delta="-1" aria-label="Weniger">−</button><b>${card.quantity}</b><button type="button" data-sheet-delta="1" aria-label="Mehr">＋</button></div>
      <button type="button" class="sheet-remove" title="Entfernen" aria-label="Entfernen">×</button></div>`).join('')
    :`<div class="deck-zone-empty">${sheetIsWanted(payload.sheet.kind)?'Noch keine Karten. Wähle rechts die Karten, die du suchst.':'Noch keine Karten. Wähle rechts Karten aus deiner Sammlung.'}</div>`;
  $$('.sheet-entry',box).forEach(row=>{
    const variantId=row.dataset.variant;
    $$('[data-sheet-delta]',row).forEach(button=>button.onclick=()=>changeSheetCard({variant_id:variantId,delta:Number(button.dataset.sheetDelta)}));
    $('.sheet-remove',row).onclick=()=>changeSheetCard({variant_id:variantId,quantity:0});
    $('.sheet-label',row).onchange=event=>changeSheetCard({variant_id:variantId,delta:0,label:event.target.value});
  });
  $$('#sheet-picker-list [data-pick]').forEach(markPickedRow);
  clearTimeout(sheetView.previewTimer);
  // Rendering a page takes around a second, so the images follow a burst of changes, not each one.
  sheetView.previewTimer=setTimeout(renderSheetPreview,immediate?0:700);
}

function renderSheetPreview(){
  const box=$('#sheet-preview'),payload=sheetView.payload;if(!box||!payload)return;
  const stamp=encodeURIComponent(payload.sheet.updated_at),base=`/api/trade-sheets/${payload.sheet.id}/image`;
  box.innerHTML=payload.pages.length?payload.pages.map((page,index)=>`<figure class="sheet-page"><img src="${base}/${index+1}.jpg?scale=0.6&v=${stamp}" alt="Vorschau Seite ${index+1}" loading="lazy">
      <figcaption><span>${payload.pages.length>1?`Seite ${index+1} von ${payload.pages.length} · `:''}${page.columns} × ${page.rows} · ${page.count} Karten</span><a class="secondary-button" href="${base}/${index+1}.jpg?download=1" download>JPG</a><a class="secondary-button" href="${base}/${index+1}.png?download=1" download>PNG</a></figcaption></figure>`).join('')
    :'<div class="sheet-preview-empty">Die Vorschau erscheint, sobald Karten auf dem Sheet liegen.</div>';
}

async function changeSheetCard(change){
  try{applySheetPayload(await post(`/api/trade-sheets/${state.sheetId}/cards`,change))}catch(error){toast(error.message)}
}

function markPickedRow(row){
  const onSheet=sheetView.payload?.cards.find(card=>card.variant_id===row.dataset.pick);
  row.classList.toggle('on-sheet',Boolean(onSheet));
  const badge=$('.sheet-picked',row);if(badge)badge.textContent=onSheet?`${onSheet.quantity}× auf dem Sheet`:'';
}

async function renderSheetPicker(){
  const box=$('#sheet-picker-list');if(!box)return;
  const picker=sheetView.picker,gameId=state.activeGameId;
  const query=picker.q.trim(),found=searchMatcher(query),loading=()=>{box.innerHTML='<div class="page-loader compact"><span></span></div>'};
  const matches=card=>[card.canonical_name,card.collector_number,card.set_name,card.set_code].some(found);
  // "Über Playset": what is owned beyond a full playset, offered with exactly that surplus.
  const playset=state.boot.games.find(game=>game.id===gameId)?.playset_size||4;
  let cards=[],empty='Keine Karten in der Sammlung.';
  try{
    if(picker.source==='catalog'){
      // The whole catalogue is searched on the server; nothing is listed before there is a query.
      empty='Tippe mindestens zwei Zeichen, um im Katalog zu suchen.';
      if(query.length>=2){
        if(picker.foundFor!==`${gameId}:${query}`||!picker.found){loading();picker.found=await api(`/api/search?game_id=${encodeURIComponent(gameId)}&limit=${SHEET_PICKER_LIMIT}&q=${encodeURIComponent(query)}`);picker.foundFor=`${gameId}:${query}`}
        cards=picker.found;empty='Keine Treffer.';
      }
    }else if(picker.source==='watchlist'){
      if(!picker.wanted){
        loading();
        const lists=await api(`/api/watchlists?game_id=${encodeURIComponent(gameId)}`),seen=new Map();
        for(const list of lists)for(const card of (await api(`/api/watchlists/${list.id}/cards`)).cards){
          const known=seen.get(card.variant_id);
          if(known)known.pick_quantity=Math.max(known.pick_quantity,card.desired_quantity||1);
          else seen.set(card.variant_id,{...card,pick_quantity:card.desired_quantity||1});
        }
        picker.wanted=[...seen.values()];
      }
      cards=picker.wanted;empty='Deine Watchlists sind leer.';
    }else{
      if(!picker.cards){loading();picker.cards=(await api(`/api/collection?game_id=${encodeURIComponent(gameId)}&sort=number`)).cards}
      cards=picker.source==='surplus'?picker.cards.filter(card=>card.quantity>playset).map(card=>({...card,pick_quantity:card.quantity-playset}))
        :picker.cards.filter(card=>picker.source!=='duplicates'||card.quantity>1);
      if(picker.source==='surplus')empty=`Keine Karte liegt öfter als ${playset}× in der Sammlung.`;
      if(picker.source==='duplicates')empty='Keine doppelten Karten in der Sammlung.';
    }
  }catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(!$('#sheet-picker-list')||picker!==sheetView.picker)return;
  if(query&&picker.source!=='catalog'){cards=cards.filter(matches);empty='Keine Treffer.'}
  const shown=cards.slice(0,SHEET_PICKER_LIMIT);
  box.innerHTML=(['surplus','watchlist'].includes(picker.source)&&cards.length?`<button type="button" class="secondary-button sheet-take-all" id="sheet-take-all">Alle ${cards.length} übernehmen</button>`:'')
    +(shown.length?shown.map(card=>`<button type="button" class="sheet-pick" data-pick="${escapeHtml(card.variant_id)}" data-quantity="${card.pick_quantity||1}">
        ${finishThumb(card,artUrl(card.variant_id),card.canonical_name,'sheet-thumb')}
        <span class="sheet-entry-copy"><b>${escapeHtml(card.canonical_name)}</b><small>${escapeHtml(card.set_name||card.set_code||'')} · ${escapeHtml(card.collector_number)} · ${escapeHtml(variantName({...card,game_id:gameId}))} · ${card.quantity}× im Besitz</small><small class="sheet-picked"></small></span><i>＋</i></button>`).join('')
      :`<div class="deck-zone-empty">${empty}</div>`)
    +(cards.length>shown.length?`<div class="sheet-picker-more">${cards.length-shown.length} weitere – Suche eingrenzen</div>`:'');
  $$('[data-pick]',box).forEach(row=>{markPickedRow(row);row.onclick=()=>changeSheetCard({variant_id:row.dataset.pick,delta:Number(row.dataset.quantity)||1})});
  $('#sheet-take-all')?.addEventListener('click',()=>changeSheetCard({entries:cards.map(card=>({variant_id:card.variant_id,quantity:Math.min(99,card.pick_quantity||1)}))}));
}
