// Deals: what was sold, bought or traded, with whom, and what became of it (deckledger/deals.py).
// They live next to the sheets: the list is the second tab of "Verkauf & Tausch", a deal opens
// in place of it. A deal can be started by hand or from an entry of the inbox.

const dealView={status:'',q:'',found:{give:null,get:null}};
const DEAL_STATES={open:'Offen',reserved:'Reserviert',done:'Abgeschlossen',cancelled:'Abgebrochen'};
const DEAL_KINDS={sale:'Verkauf',purchase:'Kauf',trade:'Tausch','':'Vorgang'};
const DEAL_EVENTS={created:'Angelegt',reserved:'Reserviert',open:'Wieder offen',done:'Abgeschlossen',received:'Empfang bestätigt',cancelled:'Abgebrochen'};
const DEAL_FILTERS=[['','Alle'],['open','Offen'],['reserved','Reserviert'],['transit','Unterwegs'],['done','Abgeschlossen'],['cancelled','Abgebrochen']];
const DEAL_PLATFORMS=['Reddit','Forum','Discord','Cardmarket','eBay','Kleinanzeigen','Lokal'];
const DEAL_SIDES={give:'Ich gebe',get:'Ich bekomme'};

const dealDate=iso=>{const date=new Date(iso);return isNaN(date)?'':date.toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit',year:'numeric'})};
const dealStatusLabel=deal=>deal.in_transit?'Unterwegs':DEAL_STATES[deal.status]||deal.status;
const dealStatusChip=deal=>`<span class="deal-status deal-status-${deal.in_transit?'transit':escapeHtml(deal.status)}">${dealStatusLabel(deal)}</span>`;
// "+4,00 €" for what came in, "−6,00 €" for what went out (shipping included), nothing for neither.
function dealMoney(deal){
  const net=deal.money_in-deal.money_out-deal.shipping;
  if(!deal.money_in&&!deal.money_out&&!deal.shipping)return '';
  return `<b class="deal-money ${net<0?'is-out':'is-in'}">${net<0?'−':'+'}${money(Math.abs(net))}</b>`;
}
const dealCardsLine=(deal,side)=>deal.cards.filter(card=>card.side===side).map(card=>`${card.quantity}× ${escapeHtml(card.name)}`).join(', ');

// The two tabs of "Verkauf & Tausch". Sheets and deals share the page, not the content.
function sheetTabsHtml(active){
  return `<div class="sheet-tabs" role="tablist">${[['sheets','Sheets'],['deals','Vorgänge'],['ebay','eBay']].map(([id,label])=>`<button type="button" role="tab" data-sheet-tab="${id}" aria-selected="${id===active}" class="${id===active?'active':''}">${label}</button>`).join('')}</div>`;
}
function bindSheetTabs(){
  $$('[data-sheet-tab]',content).forEach(button=>button.onclick=()=>{sheetView.tab=button.dataset.sheetTab;state.sheetId=null;state.dealId=null;renderSheets()});
}

async function renderDeals(){
  const game=state.boot.games.find(item=>item.id===state.activeGameId),guard=renderGuard();
  let data;
  try{data=await api(`/api/deals?${new URLSearchParams({game_id:game.id,status:dealView.status,q:dealView.q})}`)}catch(error){content.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(guard()||state.route!=='sheets'||state.dealId||sheetView.tab!=='deals')return;
  const totals=data.totals;
  content.innerHTML=`<div class="deck-page-head deck-overview-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · VERKAUF &amp; TAUSCH</span><h1>Vorgänge</h1><p>Was du verkauft, gekauft oder getauscht hast – mit wem, zu welchem Preis und was daraus wurde.</p></div><button class="primary-button" id="new-deal">＋ Neuer Vorgang</button></div>
    ${sheetTabsHtml('deals')}
    <div class="deal-filters"><div class="deal-filter-chips">${DEAL_FILTERS.map(([id,label])=>`<button type="button" class="community-chip ${dealView.status===id?'active':''}" data-deal-filter="${id}">${label}</button>`).join('')}</div>
      <div class="filter-search"><span>⌕</span><input id="deal-q" value="${escapeHtml(dealView.q)}" placeholder="Partner, Plattform oder Karte"></div></div>
    ${totals.done?`<div class="deal-totals"><span>Abgeschlossen: <b>${totals.done}</b></span><span>Einnahmen <b>${money(totals.money_in)}</b></span><span>Ausgaben <b>${money(totals.money_out)}</b></span><span>Versand <b>${money(totals.shipping)}</b></span><span>Saldo <b class="${totals.balance<0?'is-out':'is-in'}">${totals.balance<0?'−':'+'}${money(Math.abs(totals.balance))}</b></span></div>`:''}
    ${data.deals.length?`<div class="deal-list">${data.deals.map(deal=>{
      const gives=dealCardsLine(deal,'give'),gets=dealCardsLine(deal,'get');
      return `<button type="button" class="deal-row" data-deal="${deal.id}">
        <div class="deal-row-head"><b>${DEAL_KINDS[deal.kind]}${deal.partner?` · ${escapeHtml(deal.partner)}`:''}</b><small>${[deal.platform,dealDate(deal.completed_at||deal.updated_at)].filter(Boolean).map(escapeHtml).join(' · ')}</small></div>
        <div class="deal-row-cards">${gives?`<span><i>gebe</i> ${gives}</span>`:''}${gets?`<span><i>bekomme</i> ${gets}</span>`:''}${!gives&&!gets?'<span><i>noch keine Karten</i></span>':''}</div>
        <div class="deal-row-end">${dealMoney(deal)}${dealStatusChip(deal)}</div></button>`}).join('')}</div>`
    :`<div class="deck-empty"><span>⇄</span><h2>${dealView.status||dealView.q?'Kein Vorgang passt dazu':'Noch kein Vorgang'}</h2><p>Ein Vorgang hält fest, was du mit jemandem ausgemacht hast – egal ob über Reddit, ein Forum oder am Spieltisch. Reservieren, abschließen und den Empfang bestätigen bucht die Karten in deiner Sammlung.</p></div>`}`;
  bindSheetTabs();
  $('#new-deal').onclick=async()=>{try{const created=await post('/api/deals',{game_id:game.id});state.dealId=created.id;renderSheets()}catch(error){toast(error.message)}};
  $$('[data-deal-filter]',content).forEach(button=>button.onclick=()=>{dealView.status=button.dataset.dealFilter;renderDeals()});
  let timer;$('#deal-q').oninput=event=>{clearTimeout(timer);dealView.q=event.target.value;timer=setTimeout(()=>reRenderPreservingFocus('#deal-q',renderDeals),250)};
  $$('[data-deal]',content).forEach(row=>row.onclick=()=>{state.dealId=Number(row.dataset.deal);renderSheets()});
}

// What a card of a deal says about the collection, so nothing is promised twice.
function dealCardHint(deal,card){
  if(!card.known)return 'nicht mehr im Katalog';
  if(card.side==='get')return card.owned?`${card.owned}× schon in der Sammlung`:'';
  if(deal.status==='done'||deal.status==='cancelled')return '';
  const free=card.owned-card.reserved;
  if(!card.owned)return '<span class="sheet-warning">nicht in der Sammlung</span>';
  if(free<card.quantity)return `<span class="sheet-warning">nur ${Math.max(0,free)}× frei${card.reserved?` (${card.reserved} reserviert)`:''}</span>`;
  return `${card.owned}× in der Sammlung${card.reserved?` · ${card.reserved} anderweitig reserviert`:''}`;
}

function dealSideHtml(deal,side,editable){
  const cards=deal.cards.filter(card=>card.side===side),value=side==='give'?deal.value_give:deal.value_get;
  return `<section class="deal-side" data-deal-side="${side}">
    <div class="sheet-section-head"><b>${DEAL_SIDES[side]}</b><span>${cards.length?`${cards.reduce((sum,card)=>sum+card.quantity,0)} Karten · ${money(value)}`:''}</span></div>
    <div class="sheet-entries">${cards.length?cards.map(card=>`<div class="sheet-entry deal-card" data-variant="${escapeHtml(card.variant_id)}">
        ${card.known?finishThumb(card,artUrl(card.variant_id),card.name,'sheet-thumb'):'<span class="sheet-thumb deal-thumb-gone"></span>'}
        <div class="sheet-entry-copy"><b>${escapeHtml(card.name)}</b><small>${escapeHtml(card.detail)}</small>${dealCardHint(deal,card)?`<small>${dealCardHint(deal,card)}</small>`:''}</div>
        ${editable?`<label class="deal-price"><input inputmode="decimal" value="${card.unit_price==null?'':String(card.unit_price).replace('.',',')}" placeholder="Preis" aria-label="Preis je Stück"><span>€</span></label>
          <div class="sheet-stepper"><button type="button" data-deal-delta="-1" aria-label="Weniger">−</button><b>${card.quantity}</b><button type="button" data-deal-delta="1" aria-label="Mehr">＋</button></div>
          <button type="button" class="sheet-remove" title="Entfernen" aria-label="Entfernen">×</button>`
        :`<span class="deal-card-fixed">${card.quantity}×${card.unit_price==null?'':` · ${money(card.unit_price)}`}</span>`}
      </div>`).join(''):`<div class="deck-zone-empty">${side==='give'?'Nichts – du gibst nur Geld oder gar nichts.':'Nichts – du bekommst nur Geld oder gar nichts.'}</div>`}</div>
    ${editable?`<div class="deal-add"><div class="filter-search"><span>⌕</span><input data-deal-search="${side}" placeholder="${side==='give'?'Karte hinzufügen, die du abgibst':'Karte hinzufügen, die du bekommst'}"></div><div class="deal-found" data-deal-found="${side}"></div></div>`:''}
  </section>`;
}

async function renderDeal(){
  const dealId=state.dealId;
  let deal;
  try{deal=await api(`/api/deals/${dealId}`)}catch(error){state.dealId=null;toast(error.message);return renderSheets()}
  if(state.route!=='sheets'||state.dealId!==dealId)return;
  paintDeal(deal);
}

function paintDeal(deal){
  dealView.deal=deal;
  const editable=['open','reserved'].includes(deal.status),link=safeLink(deal.url)||(/^http:\/\//.test(deal.url)?deal.url:'');
  const hasGive=deal.cards.some(card=>card.side==='give'),hasGet=deal.cards.some(card=>card.side==='get');
  const actions=[];
  if(deal.status==='open'&&hasGive)actions.push(['reserved','Reservieren','secondary-button']);
  if(deal.status==='reserved')actions.push(['open','Reservierung aufheben','secondary-button']);
  if(editable)actions.push(['done','Abschließen','primary-button']);
  if(deal.in_transit)actions.push(['receive','Empfang bestätigen','primary-button']);
  if(editable)actions.push(['cancelled','Abbrechen','secondary-button']);
  if(deal.status==='done')actions.push(['cancelled','Stornieren','secondary-button']);
  if(deal.status==='cancelled')actions.push(['open','Wieder öffnen','secondary-button']);
  if(['open','cancelled'].includes(deal.status))actions.push(['delete','Löschen','secondary-button deal-delete']);
  const lock=editable?'':'disabled';
  content.innerHTML=`<div class="deal-editor">
    <header class="sheet-head"><button class="compact-back-button" id="deal-back" title="Alle Vorgänge" aria-label="Alle Vorgänge">←</button>
      <h1 class="deal-title">${DEAL_KINDS[deal.kind]}${deal.partner?` · ${escapeHtml(deal.partner)}`:''}</h1>${dealStatusChip(deal)}</header>
    ${deal.in_transit?'<p class="deal-note">Die Karten, die du bekommst, sind unterwegs. Sie kommen erst in deine Sammlung, wenn du den Empfang bestätigst.</p>':''}
    <div class="sheet-options deal-fields">
      <label>Partner<input id="deal-partner" value="${escapeHtml(deal.partner)}" maxlength="80" placeholder="Name oder Nutzername" ${lock}></label>
      <label>Plattform<input id="deal-platform" value="${escapeHtml(deal.platform)}" maxlength="40" list="deal-platforms" placeholder="z. B. Forum, Discord, Lokal" ${lock}><datalist id="deal-platforms">${DEAL_PLATFORMS.map(name=>`<option value="${name}">`).join('')}</datalist></label>
      <label>Link${link?` <a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">öffnen ↗</a>`:''}<input id="deal-url" type="url" value="${escapeHtml(deal.url)}" maxlength="500" placeholder="Post, Thread oder Nachricht (optional)" ${lock}></label>
    </div>
    <div class="deal-sides">${dealSideHtml(deal,'give',editable)}${dealSideHtml(deal,'get',editable)}</div>
    <div class="sheet-options deal-fields deal-money-fields">
      <label>Geld bekommen<span class="deal-price"><input id="deal-money-in" inputmode="decimal" value="${deal.money_in?String(deal.money_in).replace('.',','):''}" placeholder="0,00" ${lock}><span>€</span></span>${editable&&deal.value_give&&deal.value_give!==deal.money_in?`<button type="button" class="text-link" data-deal-take="money_in" data-value="${deal.value_give}">Kartenwert ${money(deal.value_give)} übernehmen</button>`:''}</label>
      <label>Geld gezahlt<span class="deal-price"><input id="deal-money-out" inputmode="decimal" value="${deal.money_out?String(deal.money_out).replace('.',','):''}" placeholder="0,00" ${lock}><span>€</span></span>${editable&&deal.value_get&&deal.value_get!==deal.money_out?`<button type="button" class="text-link" data-deal-take="money_out" data-value="${deal.value_get}">Kartenwert ${money(deal.value_get)} übernehmen</button>`:''}</label>
      <label>Versandkosten<span class="deal-price"><input id="deal-shipping" inputmode="decimal" value="${deal.shipping?String(deal.shipping).replace('.',','):''}" placeholder="0,00" ${lock}><span>€</span></span></label>
    </div>
    <label class="deal-note-field">Notiz<textarea id="deal-note" rows="2" maxlength="2000" placeholder="Absprachen, Sendungsnummer, Zustand …">${escapeHtml(deal.note)}</textarea></label>
    <div class="deal-actions">${actions.map(([action,label,style])=>`<button type="button" class="${style}" data-deal-action="${action}">${label}</button>`).join('')}</div>
    <div class="deal-events"><div class="sheet-section-head"><b>Verlauf</b></div>${deal.events.map(event=>`<div class="deal-event"><span>${escapeHtml(dealDate(event.at))} ${escapeHtml(new Date(event.at).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}))}</span><b>${DEAL_EVENTS[event.kind]||escapeHtml(event.kind)}</b>${event.detail?`<small>${escapeHtml(event.detail)}</small>`:''}</div>`).join('')}</div>
  </div>`;
  const path=`/api/deals/${deal.id}`;
  const apply=async(request)=>{try{paintDeal(await request)}catch(error){toast(error.message)}};
  const patch=change=>apply(api(path,{method:'PATCH',body:JSON.stringify(change)}));
  $('#deal-back').onclick=()=>{state.dealId=null;sheetView.tab='deals';renderSheets()};
  for(const [selector,field] of [['#deal-partner','partner'],['#deal-platform','platform'],['#deal-url','url'],['#deal-note','note'],['#deal-money-in','money_in'],['#deal-money-out','money_out'],['#deal-shipping','shipping']])
    $(selector).onchange=event=>patch({[field]:event.target.value.trim()});
  $$('[data-deal-take]',content).forEach(button=>button.onclick=()=>patch({[button.dataset.dealTake]:Number(button.dataset.value)}));
  $$('.deal-card',content).forEach(row=>{
    const side=row.closest('[data-deal-side]').dataset.dealSide,variantId=row.dataset.variant;
    const change=entry=>apply(post(`${path}/cards`,{side,variant_id:variantId,...entry}));
    $$('[data-deal-delta]',row).forEach(button=>button.onclick=()=>change({delta:Number(button.dataset.dealDelta)}));
    if($('.sheet-remove',row))$('.sheet-remove',row).onclick=()=>change({quantity:0});
    if($('.deal-price input',row))$('.deal-price input',row).onchange=event=>change({delta:0,unit_price:event.target.value.trim()});
  });
  $$('[data-deal-search]',content).forEach(input=>{
    const side=input.dataset.dealSearch,box=$(`[data-deal-found="${side}"]`,content);
    let timer;
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(async()=>{
      const query=input.value.trim();
      if(query.length<2){box.innerHTML='';return}
      let found;
      try{found=await api(`/api/search?${new URLSearchParams({game_id:deal.game_id,limit:30,q:query})}`)}catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
      if(input.value.trim()!==query)return;
      // What goes out comes from the collection: owned cards first.
      if(side==='give')found.sort((a,b)=>Boolean(b.quantity)-Boolean(a.quantity));
      box.innerHTML=found.length?found.map(card=>`<button type="button" class="sheet-pick" data-deal-pick="${escapeHtml(card.variant_id)}">${finishThumb(card,artUrl(card.variant_id),card.canonical_name,'sheet-thumb')}<span class="sheet-entry-copy"><b>${escapeHtml(card.canonical_name)}</b><small>${escapeHtml(card.set_code)} · ${escapeHtml(card.collector_number)} · ${escapeHtml(variantName(card))}${card.quantity?` · ${card.quantity}× im Besitz`:''}${card.price!=null?` · ${money(card.price)}`:''}</small></span><i>＋</i></button>`).join(''):'<div class="deck-zone-empty">Keine Treffer.</div>';
      $$('[data-deal-pick]',box).forEach(button=>button.onclick=()=>{
        const card=found.find(item=>item.variant_id===button.dataset.dealPick),known=deal.cards.find(item=>item.side===side&&item.variant_id===card.variant_id);
        // A card new to the deal starts at its market price; one that is there keeps its own.
        apply(post(`${path}/cards`,{side,variant_id:card.variant_id,delta:1,...(known||card.price==null?{}:{unit_price:card.price})}));
      });
    },220)};
  });
  $$('[data-deal-action]',content).forEach(button=>button.onclick=async()=>{
    const action=button.dataset.dealAction;
    if(action==='delete'){
      if(!confirm('Vorgang löschen? Er verschwindet aus dem Verlauf.'))return;
      try{await api(path,{method:'DELETE'});state.dealId=null;sheetView.tab='deals';renderSheets()}catch(error){toast(error.message)}
      return;
    }
    if(action==='done'&&!confirm(`Vorgang abschließen?${hasGive?' Was du abgibst, wird aus deiner Sammlung und von deinen Sheets genommen.':''}${hasGet?' Was du bekommst, gilt als unterwegs, bis du den Empfang bestätigst.':''}`))return;
    if(action==='cancelled'&&deal.status==='done'&&!confirm('Vorgang stornieren? Die Buchung in der Sammlung wird zurückgenommen; deine Sheets bleiben, wie sie sind.'))return;
    await apply(action==='receive'?post(`${path}/receive`,{}):post(`${path}/status`,{status:action}));
  });
}

// From an entry of the inbox: the partner, the link and the cards it names are filled in.
async function dealFromInbox(item){
  const side=sheetIsWanted(item.sheet_kind)?'get':'give';
  try{
    const created=await post('/api/deals',{game_id:item.game_id,partner:item.author,platform:'Reddit',url:safeLink(item.url)||safeLink(item.post_url),
      cards:item.matches.map(match=>({side,variant_id:match.variant_id,quantity:1}))});
    state.sheetId=null;state.dealId=created.id;renderSheets();
  }catch(error){toast(error.message)}
}

// In a card's detail view: what became of it in the user's deals.
async function loadCardDeals(variantId){
  let history;
  try{history=await api(`/api/variants/${encodeURIComponent(variantId)}/deals`)}catch{return}
  const box=$('#card-dialog .modal-content');
  if(!history.length||!box||state.modalVariant?.id!==variantId||state.modalTab!=='collection'||$('.card-deals',box))return;
  const what=row=>row.status==='reserved'?'reserviert für':row.side==='give'?'abgegeben an':row.in_transit?'unterwegs von':'bekommen von';
  box.insertAdjacentHTML('beforeend',`<div class="detail-section card-deals"><div class="detail-section-title">VERLAUF</div>${history.map(row=>`<button type="button" class="card-deal" data-card-deal="${row.deal_id}">
      <span>${escapeHtml(dealDate(row.completed_at||row.updated_at))}</span><b>${row.quantity}× ${what(row)} ${escapeHtml(row.partner||'–')}</b><small>${[row.platform,row.unit_price==null?'':`${money(row.unit_price)} je Stück`].filter(Boolean).map(escapeHtml).join(' · ')}</small></button>`).join('')}</div>`);
  $$('[data-card-deal]',box).forEach(button=>button.onclick=()=>{closeOverlay('card-modal');sheetView.tab='deals';state.sheetId=null;state.dealId=Number(button.dataset.cardDeal);routeTo('sheets')});
}
