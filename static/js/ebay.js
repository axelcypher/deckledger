// eBay (deckledger/ebay.py): connecting the account and the listing preset in Settings, listings
// followed for a card's price in the price tab, drafts made from sheets and the collection, and
// the "eBay" tab next to Sheets and Vorgänge with the drafts and the account's own listings.

const ebayView={status:null,listingFilter:'active'};
const EBAY_RETURN_MESSAGES={
  connected:'eBay-Konto verbunden',
  denied:'Die Verbindung zu eBay wurde abgebrochen.',
  state:'Die eBay-Anmeldung ist abgelaufen. Bitte erneut verbinden.',
  failed:'eBay hat die Verbindung abgelehnt. Stimmen Schlüssel und RuName der eBay-Anwendung?',
  not_configured:'Die eBay-Anbindung ist auf diesem Server nicht eingerichtet.',
};
const EBAY_ROUNDINGS=[['none','Nicht runden'],['up49','Auf ,49 / ,99 aufrunden'],['up99','Auf ,99 aufrunden']];
const ebayDate=value=>value?new Intl.DateTimeFormat('de-DE',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'–';
const ebayMoney=(value,currency='EUR')=>value==null?'–':currencyMoney(value,currency||'EUR');

async function ebayStatus(refresh=false){
  if(!ebayView.status||refresh)ebayView.status=await api('/api/ebay/status');
  return ebayView.status;
}

// Back from eBay's sign-in page (see /ebay/callback): one toast, then Settings.
function handleEbayReturn(){
  const params=new URLSearchParams(location.search),result=params.get('ebay');
  if(!result)return;
  history.replaceState(null,'',location.pathname);
  toast(EBAY_RETURN_MESSAGES[result]||'eBay-Verbindung fehlgeschlagen.');
  ebayView.status=null;
  state.sheetId=null;state.dealId=null;sheetView.tab='ebay';
  routeTo('sheets');
  openEbaySettings();
}

// ---- Price tab: listings followed for this card -------------------------------------------
async function loadEbayTracked(variantId){
  const panel=$('#ebay-tracked-panel',$('#card-dialog'));if(!panel)return;
  let data;
  try{data=await api(`/api/variants/${encodeURIComponent(variantId)}/ebay-items`)}catch{panel.innerHTML='';return}
  if(state.modalVariant?.id!==variantId||state.modalTab!=='market')return;
  const fresh=$('#ebay-tracked-panel',$('#card-dialog'));if(!fresh)return;
  renderEbayTracked(fresh,variantId,data);
}
function ebayTrackedStatus(item){
  if(item.status==='active')return '';
  if(item.status==='ended')return '<em class="ebay-chip is-ended">beendet</em>';
  if(item.status==='pending')return '<em class="ebay-chip">wird gelesen</em>';
  return `<em class="ebay-chip is-error" title="${escapeHtml(item.error)}">Fehler</em>`;
}
function renderEbayTracked(panel,variantId,data){
  const items=data.items;
  panel.innerHTML=`<div class="ebay-tracked-head"><b>eBay-Angebote beobachten</b>${items.length?'<button type="button" class="link-button" id="ebay-tracked-refresh">Neu lesen</button>':''}</div>
    ${items.length?`<div class="ebay-tracked-list">${items.map(item=>`<div class="ebay-tracked-item ${item.status!=='active'?'is-inactive':''}">
      ${item.image_url?`<img src="${escapeHtml(item.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<span class="ebay-tracked-noimg">eBay</span>'}
      <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer"><b>${escapeHtml(item.title||`Artikel ${item.item_id}`)}</b><small>${ebayTrackedStatus(item)}${item.shipping!=null?`zzgl. ${ebayMoney(item.shipping,item.currency)} Versand · `:''}${item.checked_at?`gelesen ${ebayDate(item.checked_at)}`:''}</small></a>
      <strong>${ebayMoney(item.price,item.currency)}</strong>
      <button type="button" class="ebay-tracked-remove" data-ebay-untrack="${escapeHtml(item.item_id)}" title="Nicht mehr beobachten" aria-label="Nicht mehr beobachten">×</button></div>`).join('')}</div>`:''}
    ${data.can_track?`<form class="manual-price-row ebay-tracked-form" id="ebay-tracked-form"><input id="ebay-tracked-url" autocomplete="off" inputmode="url" placeholder="Link zum eBay-Angebot oder Artikelnummer"><button class="secondary-button" type="submit">Beobachten</button></form>
      <small class="muted">Der Median der aktiven Angebote gilt als Preis „eBay-Angebote“, wo kein Cardmarket- oder eigener Preis vorliegt. Neu gelesen wird alle ${6} Stunden.</small>`
      :'<small class="muted">eBay ist auf diesem Server nicht eingerichtet – ein Admin trägt die Schlüssel der eBay-Anwendung unter Admin → eBay ein.</small>'}`;
  const changed=async(message,payload)=>{toast(message);renderEbayTracked(panel,variantId,payload);state.boot=await api('/api/bootstrap');scheduleRefresh(0)};
  const form=$('#ebay-tracked-form',panel);
  if(form)form.onsubmit=async event=>{
    event.preventDefault();
    const url=$('#ebay-tracked-url',panel).value.trim();if(!url)return;
    const button=$('button',form);button.disabled=true;button.textContent='Wird gelesen …';
    try{await changed('Angebot wird beobachtet',await post(`/api/variants/${encodeURIComponent(variantId)}/ebay-items`,{url}))}
    catch(error){toast(error.message);button.disabled=false;button.textContent='Beobachten'}
  };
  $$('[data-ebay-untrack]',panel).forEach(button=>button.onclick=async()=>{
    try{await changed('Angebot wird nicht mehr beobachtet',await api(`/api/variants/${encodeURIComponent(variantId)}/ebay-items/${encodeURIComponent(button.dataset.ebayUntrack)}`,{method:'DELETE'}))}
    catch(error){toast(error.message)}
  });
  const refresh=$('#ebay-tracked-refresh',panel);
  if(refresh)refresh.onclick=async()=>{
    refresh.disabled=true;refresh.textContent='Wird gelesen …';
    try{await changed('Angebote neu gelesen',await post(`/api/variants/${encodeURIComponent(variantId)}/ebay-items/refresh`,{}))}
    catch(error){toast(error.message);refresh.disabled=false;refresh.textContent='Neu lesen'}
  };
}

// ---- Settings: one modal, opened from the eBay tab ---------------------------------------
// Account, shipping (the same for every game) and one listing template per game.
const EBAY_PLACEHOLDERS=['name','set_name','set_code','number','rarity','finish','language','language_name','game','character','manufacturer','features','surface','year','edition','condition','graded','quantity'];

function openEbaySettings(gameId=state.activeGameId){
  const modal=openSettingsModal({id:'ebay-settings-modal',eyebrow:'VERKAUF',title:'eBay-Einstellungen',
    intro:'Konto, Versand und je TCG eine Vorlage, aus der Angebotsentwürfe entstehen.',
    body:`<section class="dl-modal-section" id="ebay-account-section"><h3>Konto</h3><div id="ebay-account-body"><div class="page-loader compact"><span></span></div></div></section>
      <div id="ebay-preset-body"><div class="page-loader compact"><span></span></div></div>`});
  ebaySettingsState.gameId=gameId;ebaySettingsState.dirty=false;
  renderEbayAccount(modal);
  renderEbayPreset(modal);
}
const ebaySettingsState={gameId:null,dirty:false,policies:null};

async function renderEbayAccount(modal){
  const body=$('#ebay-account-body',modal);if(!body)return;
  let status;
  try{status=await ebayStatus(true)}catch(error){body.innerHTML=`<p class="dl-hint">${escapeHtml(error.message)}</p>`;return}
  if(!body.isConnected)return;
  body.innerHTML=!status.configured
    ?`<p class="dl-hint">Die eBay-Anbindung ist auf diesem Server nicht eingerichtet. ${state.boot.user.role==='admin'?'Trage die Schlüssel deiner eBay-Anwendung unter Admin → eBay ein.':'Ein Admin trägt die Schlüssel der eBay-Anwendung unter Admin → eBay ein.'}</p>`
    :status.connected
      ?`<div class="dl-account-row"><span class="user-settings-status is-connected"><i></i>Verbunden</span><div><b>${escapeHtml(status.username||'eBay-Konto')}</b><small>${escapeHtml(status.marketplace_label)}${status.environment==='sandbox'?' · Sandbox':''} · abgeglichen ${ebayDate(status.listings_synced_at)}</small>${status.last_error?`<small class="sheet-warning">${escapeHtml(status.last_error)}</small>`:''}</div>
          <button class="secondary-button" id="ebay-disconnect">Trennen</button><button class="primary-button" id="ebay-sync-now">Jetzt abgleichen</button></div>`
      :`<div class="dl-account-row"><span class="user-settings-status"><i></i>${status.expired?'Abgelaufen':'Nicht verbunden'}</span><div><small>${status.expired?'eBay erneuert die Verbindung nach 18 Monaten nicht von selbst. ':''}Du meldest dich bei eBay an und erlaubst DeckLedger den Zugriff auf deine Angebote. Dein eBay-Passwort sieht DeckLedger nie.</small></div>
          <a class="primary-button" href="/ebay/connect">Mit ${escapeHtml(status.marketplace_label)} verbinden</a></div>`;
  $('#ebay-disconnect',body)?.addEventListener('click',async()=>{
    if(!confirm('Verbindung zu eBay trennen? Bisher abgeglichene Angebote und Verkäufe bleiben sichtbar.'))return;
    try{await post('/api/ebay/disconnect',{});toast('eBay-Verbindung getrennt');ebaySettingsState.policies=null;renderEbayAccount(modal);renderEbayPreset(modal)}catch(error){toast(error.message)}
  });
  $('#ebay-sync-now',body)?.addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;button.textContent='Wird abgeglichen …';
    try{const r=await post('/api/ebay/listings/sync',{});toast(`${r.active} aktive Angebote abgeglichen`);renderEbayAccount(modal)}
    catch(error){toast(error.message);button.disabled=false;button.textContent='Jetzt abgleichen'}
  });
}

async function renderEbayPreset(modal){
  const body=$('#ebay-preset-body',modal);if(!body)return;
  const gameId=ebaySettingsState.gameId;
  let preset,status;
  try{[preset,status]=await Promise.all([api(`/api/ebay/preset?game_id=${encodeURIComponent(gameId)}`),ebayStatus()])}
  catch(error){body.innerHTML=`<p class="dl-hint">${escapeHtml(error.message)}</p>`;return}
  if(!body.isConnected||ebaySettingsState.gameId!==gameId)return;
  const conditions=status.conditions||{},games=state.boot.games;
  const field=(id,label,value,attrs='')=>`<label class="dl-field"><span>${label}</span><input class="dl-control" id="${id}" value="${escapeHtml(value??'')}" ${attrs}></label>`;
  const select=(id,label,options,value)=>`<label class="dl-field"><span>${label}</span><select class="dl-control" id="${id}">${options.map(([key,text])=>`<option value="${escapeHtml(key)}" ${String(value)===String(key)?'selected':''}>${escapeHtml(text)}</option>`).join('')}</select></label>`;
  const policy=(key,label)=>`<label class="dl-field"><span>${label}</span><select class="dl-control" data-ebay-policy="${key}"><option value="${escapeHtml(preset[key])}">${preset[key]?`Richtlinie ${escapeHtml(preset[key])}`:status.connected?'Wird geladen …':'Erst eBay-Konto verbinden'}</option></select></label>`;
  const money2=value=>value==null?'':String(value).replace('.',',');
  body.innerHTML=`<section class="dl-modal-section"><h3>Versand &amp; Standort <small>gilt für alle TCGs</small></h3>
      <div class="dl-grid">${policy('fulfillment_policy_id','Versand-Richtlinie')}${policy('payment_policy_id','Zahlungs-Richtlinie')}${policy('return_policy_id','Rücknahme-Richtlinie')}
        ${field('ebay-postal-code','Postleitzahl',preset.postal_code,'autocomplete="postal-code"')}${field('ebay-location','Ort',preset.location)}
        <label class="dl-check"><input type="checkbox" id="ebay-best-offer" ${preset.best_offer?'checked':''}><span><b>Preisvorschläge annehmen</b><small>Käufer können einen eigenen Preis vorschlagen.</small></span></label></div>
    </section>
    <section class="dl-modal-section"><h3>Vorlage je TCG</h3>
      <div class="dl-segmented" role="tablist">${games.map(game=>`<button type="button" role="tab" data-ebay-preset-game="${game.id}" aria-selected="${game.id===gameId}" class="${game.id===gameId?'active':''}">${escapeHtml(game.short_name)}</button>`).join('')}</div>
      <h4>Spiel</h4>
      <div class="dl-grid">${field('ebay-game-label','Spiel (Merkmal „Spiel“)',preset.game_label)}${field('ebay-manufacturer','Hersteller',preset.manufacturer)}
        ${field('ebay-surface-foil','Oberflächeneffekt bei Foil/Holo',preset.surface_foil)}${field('ebay-surface-normal','Oberflächeneffekt sonst',preset.surface_normal)}</div>
      <h4>Angebot</h4>
      <div class="dl-grid">${field('ebay-category','Kategorie-ID',preset.category_id,'inputmode="numeric"')}
        ${select('ebay-condition','Zustand',[['collection','Aus der Sammlung übernehmen'],...Object.entries(conditions)],preset.condition)}
        ${select('ebay-quantity','Menge je Angebot',[['one','1 Exemplar'],['owned','Alle Exemplare der Sammlung']],preset.quantity)}</div>
      <h4>Preis</h4>
      <div class="dl-grid">${field('ebay-price-factor','% vom Marktpreis',preset.price_factor,'inputmode="decimal"')}${field('ebay-price-min','Mindestpreis €',money2(preset.price_min),'inputmode="decimal"')}
        ${select('ebay-price-rounding','Rundung',EBAY_ROUNDINGS,preset.price_rounding)}${field('ebay-price-fallback','Ohne Marktpreis €',money2(preset.price_fallback),'inputmode="decimal" placeholder="leer: selbst eintragen"')}</div>
      <h4>Texte</h4>
      <div class="dl-grid dl-grid-wide">
        ${field('ebay-title-template','Titel (eBay erlaubt 80 Zeichen)',preset.title_template,'maxlength="400"')}
        <label class="dl-field"><span>Beschreibung – HTML erlaubt (z. B. &lt;p&gt;, &lt;b&gt;, &lt;br&gt;); ohne Tags werden Zeilenumbrüche übernommen</span><textarea class="dl-control" id="ebay-description-template" rows="7">${escapeHtml(preset.description_template)}</textarea></label>
        <label class="dl-field"><span>Artikelmerkmale – eine Zeile je Merkmal: <i>Name: Wert</i>. Bleibt der Wert leer, entfällt die Zeile.</span><textarea class="dl-control" id="ebay-aspects" rows="12">${escapeHtml(preset.aspects)}</textarea></label>
      </div>
      <p class="dl-hint">Platzhalter: ${EBAY_PLACEHOLDERS.map(name=>`<code>{${name}}</code>`).join(' ')}. <code>{features}</code> ist „1st Edition“ bei Erstauflagen, <code>{surface}</code> der Oberflächeneffekt von oben. Ein Preis auf dem Sheet (z. B. „4,50 €“) geht der Preisregel vor.</p>
    </section>
    <div class="dl-modal-actions"><button class="secondary-button" id="ebay-preset-reset">Texte zurücksetzen</button><span class="spacer"></span><button class="primary-button" id="ebay-preset-save">Speichern</button></div>`;
  $$('.dl-control,#ebay-best-offer',body).forEach(control=>control.addEventListener('input',()=>{ebaySettingsState.dirty=true}));
  const read=()=>({
    game_id:gameId,game_label:$('#ebay-game-label',body).value,manufacturer:$('#ebay-manufacturer',body).value,
    surface_foil:$('#ebay-surface-foil',body).value,surface_normal:$('#ebay-surface-normal',body).value,
    title_template:$('#ebay-title-template',body).value,description_template:$('#ebay-description-template',body).value,aspects:$('#ebay-aspects',body).value,
    category_id:$('#ebay-category',body).value,condition:$('#ebay-condition',body).value,quantity:$('#ebay-quantity',body).value,
    price_factor:$('#ebay-price-factor',body).value,price_min:$('#ebay-price-min',body).value,price_rounding:$('#ebay-price-rounding',body).value,price_fallback:$('#ebay-price-fallback',body).value,
    postal_code:$('#ebay-postal-code',body).value,location:$('#ebay-location',body).value,best_offer:$('#ebay-best-offer',body).checked,
    ...Object.fromEntries($$('[data-ebay-policy]',body).map(control=>[control.dataset.ebayPolicy,control.value])),
  });
  const save=async()=>{await api('/api/ebay/preset',{method:'PUT',body:JSON.stringify(read())});ebaySettingsState.dirty=false};
  $('#ebay-preset-save',body).onclick=async()=>{try{await save();toast(`Vorlage für ${games.find(game=>game.id===gameId)?.short_name||gameId} gespeichert`);renderEbayPreset(modal)}catch(error){toast(error.message)}};
  $('#ebay-preset-reset',body).onclick=()=>{
    if(!confirm('Titel, Beschreibung und Artikelmerkmale dieses TCGs auf den Standard zurücksetzen?'))return;
    $('#ebay-title-template',body).value=preset.defaults.title_template;$('#ebay-description-template',body).value=preset.defaults.description_template;$('#ebay-aspects',body).value=preset.defaults.aspects;
    ebaySettingsState.dirty=true;
  };
  $$('[data-ebay-preset-game]',body).forEach(button=>button.onclick=async()=>{
    if(button.dataset.ebayPresetGame===gameId)return;
    // Switching keeps what was typed: the open game's template is saved first.
    if(ebaySettingsState.dirty){try{await save();toast('Vorlage gespeichert')}catch(error){toast(error.message);return}}
    ebaySettingsState.gameId=button.dataset.ebayPresetGame;renderEbayPreset(modal);
  });
  if(status.connected)loadEbayPolicies(body,preset);
}

async function loadEbayPolicies(body,preset){
  try{ebaySettingsState.policies??=await api('/api/ebay/policies')}catch(error){
    $$('[data-ebay-policy]',body).forEach(control=>{if(!control.value)control.options[0].textContent='Nicht geladen'});
    toast(error.message);return;
  }
  if(!body.isConnected)return;
  const kinds={fulfillment_policy_id:'fulfillment',payment_policy_id:'payment',return_policy_id:'return'};
  $$('[data-ebay-policy]',body).forEach(control=>{
    const key=control.dataset.ebayPolicy,list=ebaySettingsState.policies[kinds[key]]||[];
    control.innerHTML=`<option value="">${list.length?'Bitte wählen':'Keine Richtlinie bei eBay angelegt'}</option>${list.map(policy=>`<option value="${escapeHtml(policy.id)}">${escapeHtml(policy.name)}</option>`).join('')}`;
    control.value=preset[key]||(list.length===1?list[0].id:'');
  });
}

// ---- Drafts from sheets and the collection ------------------------------------------------
function ebayBulkButtonHtml(){
  return '<button class="secondary-button" data-collection-bulk="ebay" title="Aus den ausgewählten Karten eBay-Angebotsentwürfe machen">eBay-Entwürfe</button>';
}
function ebayBulkActions(selected,count){
  return {ebay:async()=>createEbayDrafts({variant_ids:selected()})};
}
async function createEbayDrafts(payload){
  const r=await post('/api/ebay/drafts',payload);
  const skipped=r.skipped?` · ${r.skipped} übersprungen (schon ein offener Entwurf)`:'';
  toast(r.created?`${r.created} Angebotsentwurf${r.created===1?'':'e'} angelegt${skipped}`:`Keine neuen Entwürfe${skipped}`,'Öffnen',()=>{state.sheetId=null;state.dealId=null;sheetView.tab='ebay';routeTo('sheets')});
  return r;
}

// ---- The "eBay" tab -----------------------------------------------------------------------
async function renderEbay(){
  const game=state.boot.games.find(item=>item.id===state.activeGameId),guard=renderGuard();
  content.innerHTML='<div class="page-loader"><span></span><p>eBay wird geladen …</p></div>';
  let drafts,listings;
  try{[drafts,listings]=await Promise.all([api(`/api/ebay/drafts?game_id=${encodeURIComponent(game.id)}`),api(`/api/ebay/listings?game_id=${encodeURIComponent(game.id)}`)])}
  catch(error){content.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(guard()||state.route!=='sheets'||sheetView.tab!=='ebay'||state.sheetId)return;
  const status=ebayView.status=listings.status;
  const open=drafts.drafts.filter(draft=>draft.status!=='published'),done=drafts.drafts.filter(draft=>draft.status==='published');
  const shown=listings.listings.filter(item=>ebayView.listingFilter==='all'||item.status===ebayView.listingFilter||(ebayView.listingFilter==='ended'&&['ended','unsold'].includes(item.status)));
  const filters=[['active','Aktiv'],['sold','Verkauft'],['ended','Beendet'],['all','Alle']];
  content.innerHTML=`<div class="deck-page-head deck-overview-head"><div><span class="eyebrow">${escapeHtml(game.short_name).toUpperCase()} · VERKAUF &amp; TAUSCH</span><h1>eBay</h1><p>Entwürfe aus deinen Sheets und deiner Sammlung prüfen und einstellen, deine Angebote und Verkäufe im Blick.</p></div></div>
    ${sheetTabsHtml('ebay')}
    <div class="ebay-status-bar">${status.connected
      ?`<span class="user-settings-status is-connected"><i></i>${escapeHtml(status.username||'Verbunden')}</span><span class="muted">${escapeHtml(status.marketplace_label)} · abgeglichen ${ebayDate(status.listings_synced_at)}</span>${status.last_error?`<span class="sheet-warning">${escapeHtml(status.last_error)}</span>`:''}<button class="secondary-button" id="ebay-sync">Jetzt abgleichen</button>`
      :`<span class="user-settings-status"><i></i>${status.configured?'Kein eBay-Konto verbunden':'eBay nicht eingerichtet'}</span><span class="muted">Entwürfe kannst du trotzdem schon anlegen; zum Einstellen verbinde dein Konto.</span><button class="secondary-button" id="ebay-open-settings">eBay-Einstellungen</button>`}</div>
    <section class="ebay-section">
      <div class="sheet-section-head"><b>Entwürfe</b><span>${open.length?`${open.length} offen`:''}</span>${open.length?`<button class="secondary-button" id="ebay-verify-all" ${status.connected?'':'disabled'}>Alle prüfen</button><button class="primary-button" id="ebay-publish-all" ${status.connected?'':'disabled'}>Alle einstellen</button>`:''}</div>
      ${open.length?`<div class="ebay-drafts">${open.map(draft=>ebayDraftRow(draft,status)).join('')}</div>`
        :'<div class="deck-zone-empty">Noch keine Entwürfe. Erzeuge sie im Sheet mit „eBay-Entwürfe“ oder in der Sammlung über die Mehrfachauswahl.</div>'}
      ${done.length?`<details class="ebay-done"><summary>${done.length} eingestellt</summary><div class="ebay-drafts">${done.map(draft=>ebayDraftRow(draft,status)).join('')}</div></details>`:''}
    </section>
    <section class="ebay-section">
      <div class="sheet-section-head"><b>Meine Angebote</b><div class="deal-filter-chips">${filters.map(([id,label])=>`<button type="button" class="community-chip ${ebayView.listingFilter===id?'active':''}" data-ebay-filter="${id}">${label}</button>`).join('')}</div></div>
      ${shown.length?`<div class="ebay-listings">${shown.map(ebayListingRow).join('')}</div>`
        :`<div class="deck-zone-empty">${status.connected?'Keine Angebote in dieser Ansicht.':'Verbinde dein eBay-Konto, um deine Angebote hier zu sehen.'}</div>`}
    </section>
    ${listings.sales.length?`<section class="ebay-section"><div class="sheet-section-head"><b>Letzte Verkäufe</b></div><div class="ebay-sales">${listings.sales.map(sale=>`<div class="ebay-sale"><span>${ebayDate(sale.sold_at)}</span><b>${sale.quantity}× ${escapeHtml(sale.title)}</b><span>${escapeHtml(sale.buyer)}</span><strong>${ebayMoney(sale.price,sale.currency)}</strong></div>`).join('')}</div></section>`:''}`;
  bindSheetTabs();
  $('#ebay-open-settings')?.addEventListener('click',()=>openEbaySettings());
  $('#ebay-sync')?.addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;button.textContent='Wird abgeglichen …';
    try{const r=await post('/api/ebay/listings/sync',{});toast(`Abgeglichen: ${r.active} aktiv, ${r.sold} neue Verkäufe`);renderEbay()}
    catch(error){toast(error.message);button.disabled=false;button.textContent='Jetzt abgleichen'}
  });
  $$('[data-ebay-filter]',content).forEach(button=>button.onclick=()=>{ebayView.listingFilter=button.dataset.ebayFilter;renderEbay()});
  $$('.ebay-draft',content).forEach(row=>bindEbayDraftRow(row,drafts.drafts.find(draft=>draft.id===Number(row.dataset.draft))));
  $$('.ebay-listing',content).forEach(row=>bindEbayListingRow(row));
  const runAll=async(button,action,label)=>{
    const ids=open.map(draft=>draft.id);let ok=0;
    button.disabled=true;
    for(const [index,id] of ids.entries()){
      button.textContent=`${label} ${index+1}/${ids.length} …`;
      try{await post(`/api/ebay/drafts/${id}/${action}`,{});ok+=1}catch{}
    }
    toast(`${ok} von ${ids.length} ${action==='publish'?'eingestellt':'geprüft'}${ok<ids.length?' – Fehler stehen am Entwurf':''}`);
    renderEbay();
  };
  $('#ebay-verify-all')?.addEventListener('click',event=>runAll(event.currentTarget,'verify','Prüfe'));
  $('#ebay-publish-all')?.addEventListener('click',event=>{if(confirm(`${open.length} Angebote jetzt bei eBay einstellen? Dabei können Gebühren anfallen.`))runAll(event.currentTarget,'publish','Stelle ein')});
}

function ebayDraftRow(draft,status){
  const published=draft.status==='published';
  const conditions=status.conditions||{};
  const detail=[draft.set_code,draft.collector_number,draft.finish,draft.language].filter(Boolean).map(escapeHtml).join(' · ');
  return `<div class="ebay-draft ${published?'is-published':''} ${draft.status==='failed'?'is-failed':''}" data-draft="${draft.id}">
    ${finishThumb({...draft,game_id:draft.game_id},artUrl(draft.variant_id),draft.canonical_name||draft.title,'sheet-thumb')}
    <div class="ebay-draft-main">
      <input class="ebay-draft-title" value="${escapeHtml(draft.title)}" maxlength="80" aria-label="Titel" ${published?'disabled':''}><small class="ebay-draft-count">${draft.title.length}/80</small>
      <small>${detail}${draft.market_price!=null?` · Marktpreis ${money(draft.market_price)}`:''} · ${draft.owned}× in der Sammlung${draft.fees!=null?` · Gebühr ${money(draft.fees)}`:''}</small>
      ${draft.error?`<small class="sheet-warning">${escapeHtml(draft.error)}</small>`:''}
      ${published?'':`<details class="ebay-draft-more"><summary>Beschreibung &amp; Merkmale</summary><textarea class="select-control ebay-draft-description" rows="5">${escapeHtml(draft.description)}</textarea><small>${draft.aspects.map(([name,value])=>`${escapeHtml(name)}: ${escapeHtml(value)}`).join(' · ')||'Keine Merkmale'}</small></details>`}
    </div>
    ${published
      ?`<div class="ebay-draft-actions"><span class="ebay-chip is-live">eingestellt ${ebayDate(draft.published_at)}</span><a class="secondary-button" href="${escapeHtml(draft.item_url||'#')}" target="_blank" rel="noopener noreferrer">Ansehen</a><button type="button" class="icon-button" data-ebay-draft-delete title="Aus der Liste entfernen" aria-label="Aus der Liste entfernen">×</button></div>`
      :`<div class="ebay-draft-fields">
          <label><span>Preis €</span><input class="ebay-draft-price" inputmode="decimal" value="${draft.price==null?'':draft.price.toFixed(2).replace('.',',')}" placeholder="fehlt"></label>
          <label><span>Menge</span><input class="ebay-draft-quantity" type="number" min="1" max="999" value="${draft.quantity}"></label>
          <label><span>Zustand</span><select class="select-control ebay-draft-condition">${Object.entries(conditions).map(([id,label])=>`<option value="${id}" ${draft.condition===id?'selected':''}>${escapeHtml(label)}</option>`).join('')}</select></label>
        </div>
        <div class="ebay-draft-actions"><button type="button" class="secondary-button" data-ebay-draft-verify ${status.connected?'':'disabled'}>Prüfen</button><button type="button" class="primary-button" data-ebay-draft-publish ${status.connected?'':'disabled'}>Einstellen</button><button type="button" class="icon-button" data-ebay-draft-delete title="Entwurf löschen" aria-label="Entwurf löschen">🗑</button></div>`}
  </div>`;
}

function bindEbayDraftRow(row,draft){
  if(!draft)return;
  const id=draft.id;
  const save=async changes=>{
    try{const updated=await api(`/api/ebay/drafts/${id}`,{method:'PATCH',body:JSON.stringify(changes)});Object.assign(draft,updated);$('.sheet-warning',row)?.remove()}
    catch(error){toast(error.message)}
  };
  const title=$('.ebay-draft-title',row);
  if(title){title.oninput=()=>{$('.ebay-draft-count',row).textContent=`${title.value.length}/80`};title.onchange=()=>save({title:title.value})}
  $('.ebay-draft-description',row)?.addEventListener('change',event=>save({description:event.target.value}));
  $('.ebay-draft-price',row)?.addEventListener('change',event=>save({price:event.target.value.trim()}));
  $('.ebay-draft-quantity',row)?.addEventListener('change',event=>save({quantity:event.target.value}));
  $('.ebay-draft-condition',row)?.addEventListener('change',event=>save({condition:event.target.value}));
  $('[data-ebay-draft-delete]',row)?.addEventListener('click',async()=>{
    if(draft.status!=='published'&&!confirm('Diesen Entwurf löschen?'))return;
    try{await api(`/api/ebay/drafts/${id}`,{method:'DELETE'});row.remove()}catch(error){toast(error.message)}
  });
  $('[data-ebay-draft-verify]',row)?.addEventListener('click',async event=>{
    const button=event.currentTarget;button.disabled=true;button.textContent='Prüfe …';
    try{const r=await post(`/api/ebay/drafts/${id}/verify`,{});toast(`eBay hat den Entwurf geprüft · Angebotsgebühr ${money(r.fees)}${r.warnings.length?` · Hinweis: ${r.warnings[0]}`:''}`)}
    catch(error){toast(error.message)}
    renderEbay();
  });
  $('[data-ebay-draft-publish]',row)?.addEventListener('click',async event=>{
    if(!confirm(`„${draft.title}“ jetzt bei eBay einstellen?`))return;
    const button=event.currentTarget;button.disabled=true;button.textContent='Wird eingestellt …';
    try{const r=await post(`/api/ebay/drafts/${id}/publish`,{});toast('Angebot ist online','Ansehen',()=>window.open(r.url,'_blank','noopener'))}
    catch(error){toast(error.message)}
    renderEbay();
  });
}

function ebayListingRow(item){
  const statusLabel={active:'aktiv',sold:'verkauft',unsold:'nicht verkauft',ended:'beendet'}[item.status]||item.status;
  const image=item.variant_id?artUrl(item.variant_id):item.image_url;
  return `<div class="ebay-listing" data-item="${escapeHtml(item.item_id)}">
    ${image?`<img class="sheet-thumb" src="${escapeHtml(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">`:'<span class="sheet-thumb ebay-tracked-noimg">eBay</span>'}
    <div class="ebay-listing-main"><a href="${escapeHtml(item.url||'#')}" target="_blank" rel="noopener noreferrer"><b>${escapeHtml(item.title)}</b></a>
      <small><em class="ebay-chip ${item.status==='active'?'is-live':item.status==='sold'?'is-sold':'is-ended'}">${statusLabel}</em> ${item.quantity_sold?`${item.quantity_sold} verkauft · `:''}${item.status==='active'?`${Math.max(0,item.quantity-item.quantity_sold)} verfügbar · `:''}${item.watch_count?`${item.watch_count} beobachten · `:''}seit ${date(item.started_at)}</small>
      <small>${item.variant_id?`Karte: ${escapeHtml(item.canonical_name||item.variant_id)}${item.set_code?` (${escapeHtml(item.set_code)} ${escapeHtml(item.collector_number)})`:''}${item.market_price!=null?` · Marktpreis ${money(item.market_price)}`:''} <button type="button" class="link-button" data-ebay-unlink>lösen</button>`
        :'<button type="button" class="link-button" data-ebay-link>Karte zuordnen</button>'}</small>
      <div class="ebay-link-search hidden"><input class="select-control" placeholder="Karte suchen: Name oder Nummer"><div class="ebay-link-results"></div></div>
    </div>
    <strong>${ebayMoney(item.price,item.currency)}</strong>
  </div>`;
}

function bindEbayListingRow(row){
  const itemId=row.dataset.item;
  const link=async variantId=>{try{await api(`/api/ebay/listings/${encodeURIComponent(itemId)}`,{method:'PATCH',body:JSON.stringify({variant_id:variantId})});renderEbay()}catch(error){toast(error.message)}};
  $('[data-ebay-unlink]',row)?.addEventListener('click',()=>link(null));
  $('[data-ebay-link]',row)?.addEventListener('click',()=>{
    const box=$('.ebay-link-search',row),input=$('input',box),results=$('.ebay-link-results',box);
    box.classList.toggle('hidden');input.focus();
    let timer;
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(async()=>{
      const q=input.value.trim();if(q.length<2){results.innerHTML='';return}
      const rows=await api(`/api/search?game_id=${encodeURIComponent(state.activeGameId)}&limit=8&q=${encodeURIComponent(q)}`).catch(()=>[]);
      results.innerHTML=rows.map(card=>`<button type="button" data-ebay-pick="${escapeHtml(card.variant_id)}">${escapeHtml(card.canonical_name)} <small>${escapeHtml(card.set_name)} · ${escapeHtml(card.collector_number)} · ${escapeHtml(card.finish)} · ${card.language}</small></button>`).join('')||'<small class="muted">Keine Treffer</small>';
      $$('[data-ebay-pick]',results).forEach(button=>button.onclick=()=>link(button.dataset.ebayPick));
    },200)};
  });
}

// ---- Admin: the eBay application ----------------------------------------------------------
async function renderAdminEbay(){
  const slot=$('#admin-ebay-slot',content);if(!slot)return;
  let config;
  try{config=await api('/api/admin/ebay')}catch(error){slot.innerHTML='';return}
  if(!slot.isConnected)return;
  const locked=config.source==='environment',ro=locked?'disabled':'';
  slot.innerHTML=`<details class="settings-section oauth-admin-card ${locked?'is-locked':''}">
    <summary class="oauth-admin-summary"><div class="oauth-admin-head">
      <div class="oauth-admin-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 7h16l-1.4 11.2a2 2 0 0 1-2 1.8H7.4a2 2 0 0 1-2-1.8L4 7Z"/><path d="M8.5 7V6a3.5 3.5 0 0 1 7 0v1"/></svg></div>
      <div class="oauth-admin-title"><span>VERKAUF</span><h2>eBay</h2><p>Schlüssel der eBay-Anwendung für Kontoverbindung, Angebote und Preisbeobachtung.</p></div>
      <div class="oauth-admin-state"><span class="oauth-status ${config.client_id&&config.client_secret_set?'is-active':'is-inactive'}"><i></i>${config.client_id&&config.client_secret_set?'Eingerichtet':'Nicht eingerichtet'}</span><span class="oauth-source-badge">${locked?'Umgebung':'Admin UI'}</span></div>
      <span class="oauth-collapse-icon" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="m5 7.5 5 5 5-5"/></svg></span>
    </div></summary>
    <div class="oauth-admin-body"><div class="admin-form oauth-admin-form">
      <div class="oauth-form-group"><div class="oauth-form-group-head"><span>01</span><div><b>eBay-Anwendung</b><small>Im eBay Developer Program unter „Application Keys“ anlegen (Production-Keyset).</small></div></div>
        <div class="admin-form-grid oauth-provider-grid">
          <label class="oauth-field"><span>Client-ID (App ID)</span><input id="ebay-client-id" value="${escapeHtml(config.client_id||'')}" autocomplete="off" ${ro}></label>
          <label class="oauth-field"><span>Client-Secret (Cert ID)</span><input id="ebay-client-secret" type="password" autocomplete="new-password" placeholder="${config.client_secret_set?'••••••••  ·  leer lassen zum Beibehalten':'Client-Secret'}" ${ro}></label>
          <label class="oauth-field"><span>RuName</span><input id="ebay-ru-name" value="${escapeHtml(config.ru_name||'')}" autocomplete="off" ${ro}></label>
          <label class="oauth-field"><span>Umgebung</span><select id="ebay-environment" class="select-control" ${ro}><option value="production">Production</option><option value="sandbox">Sandbox (Test)</option></select></label>
          <label class="oauth-field"><span>Marktplatz</span><select id="ebay-marketplace" class="select-control" ${ro}>${Object.entries(config.marketplaces).map(([id,label])=>`<option value="${id}">${escapeHtml(label)}</option>`).join('')}</select></label>
        </div></div>
      <div class="oauth-form-group"><div class="oauth-form-group-head"><span>02</span><div><b>Rückleitung</b><small>Beim RuName unter „User Tokens → Your auth accepted URL“ und „Your auth declined URL“ eintragen. eBay verlangt dafür HTTPS.</small></div></div>
        <div class="admin-form-grid"><label class="oauth-field oauth-field-wide"><span>Callback-URL</span><input value="${escapeHtml(config.callback_url)}" readonly onclick="this.select()"></label></div></div>
      <div class="oauth-form-group"><div class="oauth-form-group-head"><span>03</span><div><b>Kontolöschungen</b><small>Unter „Application Keys → Notifications → Marketplace Account Deletion“ eintragen. eBay muss diese eine Adresse über HTTPS von außen erreichen; steht hier ein interner Host, die öffentliche Adresse mit demselben Pfad nehmen.</small></div></div>
        <div class="admin-form-grid">
          <label class="oauth-field oauth-field-wide"><span>Endpoint-URL</span><input value="${escapeHtml(config.deletion_endpoint)}" readonly onclick="this.select()"></label>
          <label class="oauth-field oauth-field-wide"><span>Verification Token</span><input value="${escapeHtml(config.deletion_token)}" readonly onclick="this.select()"></label>
        </div></div>
      <div class="oauth-admin-actions"><span>Für Preisbeobachtung reichen Client-ID und Secret; Kontoverbindung und Einstellen brauchen zusätzlich den RuName.</span>${locked?'':'<button class="primary-button" id="ebay-admin-save">eBay-Einstellungen speichern</button>'}</div>
    </div></div>
  </details>`;
  $('#ebay-environment',slot).value=config.environment;$('#ebay-marketplace',slot).value=config.marketplace;
  $('#ebay-admin-save',slot)?.addEventListener('click',async()=>{
    try{
      await post('/api/admin/ebay',{client_id:$('#ebay-client-id',slot).value,client_secret:$('#ebay-client-secret',slot).value,ru_name:$('#ebay-ru-name',slot).value,environment:$('#ebay-environment',slot).value,marketplace:$('#ebay-marketplace',slot).value});
      ebayView.status=null;toast('eBay-Einstellungen gespeichert');renderAdminEbay();
    }catch(error){toast(error.message)}
  });
}
