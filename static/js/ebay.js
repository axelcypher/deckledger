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
const ebaySettingsState={gameId:null,dirty:false,policies:null,services:null};
const EBAY_DISPATCH_DAYS=[[0,'Am selben Tag'],[1,'1 Werktag'],[2,'2 Werktage'],[3,'3 Werktage'],[4,'4 Werktage'],[5,'5 Werktage'],[10,'10 Werktage']];
// "Name: Wert" lines (how the preset stores item specifics) <-> one row per specific.
const ebayAspectRows=text=>String(text||'').split('\n').map(line=>line.trim()).filter(Boolean).map(line=>{const at=line.indexOf(':');return at<0?[line,'']:[line.slice(0,at).trim(),line.slice(at+1).trim()]});
const ebayAspectRowHtml=([name,value])=>`<div class="ebay-aspect-row"><input class="dl-control" data-aspect-name value="${escapeHtml(name)}" placeholder="Merkmal, z. B. Besonderheiten" aria-label="Merkmal"><input class="dl-control" data-aspect-value value="${escapeHtml(value)}" placeholder="Wert, z. B. {features}" aria-label="Wert"><button type="button" class="ebay-tracked-remove" data-aspect-remove title="Merkmal entfernen" aria-label="Merkmal entfernen">×</button></div>`;

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
  body.innerHTML=`<section class="dl-modal-section"><h3>Versand, Rücknahme &amp; Standort <small>gilt für alle TCGs</small></h3>
      <div class="dl-segmented" role="tablist" id="ebay-shipping-mode">${[['direct','In der Vorlage'],['policies','eBay-Geschäftsrichtlinien']].map(([mode,label])=>`<button type="button" role="tab" data-ebay-shipping-mode="${mode}" aria-selected="${preset.shipping_mode===mode}" class="${preset.shipping_mode===mode?'active':''}">${label}</button>`).join('')}</div>
      <p class="dl-hint hidden" id="ebay-no-policies">Dein eBay-Konto nutzt keine Geschäftsrichtlinien (bei Privatkonten üblich) – Versand und Rücknahme stehen deshalb direkt in der Vorlage.</p>
      <div class="dl-grid" data-ebay-mode="policies">${policy('fulfillment_policy_id','Versand-Richtlinie')}${policy('payment_policy_id','Zahlungs-Richtlinie')}${policy('return_policy_id','Rücknahme-Richtlinie')}</div>
      <div class="dl-grid" data-ebay-mode="direct">
        <label class="dl-field"><span>Versandart</span><select class="dl-control" id="ebay-shipping-service"><option value="${escapeHtml(preset.shipping_service)}">${preset.shipping_service?escapeHtml(preset.shipping_service):status.connected?'Wird geladen …':'Erst eBay-Konto verbinden'}</option></select></label>
        ${field('ebay-shipping-cost','Versandkosten €',money2(preset.shipping_cost),'inputmode="decimal"')}${field('ebay-shipping-additional','Je weiteres Exemplar €',money2(preset.shipping_additional_cost),'inputmode="decimal"')}
        ${select('ebay-dispatch-days','Bearbeitungszeit',EBAY_DISPATCH_DAYS,preset.dispatch_days)}
        <label class="dl-check"><input type="checkbox" id="ebay-returns-accepted" ${preset.returns_accepted?'checked':''}><span><b>Rücknahme anbieten</b><small>Privatverkäufer müssen keine Rücknahme anbieten.</small></span></label>
        ${select('ebay-returns-days','Rücknahmefrist',[[14,'14 Tage'],[30,'30 Tage'],[60,'60 Tage']],preset.returns_days)}${select('ebay-return-paid-by','Rückversand zahlt',[['Buyer','Käufer'],['Seller','Verkäufer']],preset.return_shipping_paid_by)}</div>
      <div class="dl-grid">${field('ebay-postal-code','Postleitzahl',preset.postal_code,'autocomplete="postal-code"')}${field('ebay-location','Ort',preset.location)}
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
      </div>
      <h4>Artikelmerkmale <small class="muted">– jedes wird bei eBay ein eigenes Produktmerkmal, nicht Teil der Beschreibung. Ergibt der Wert nichts (z. B. {features} ohne Besonderheit), entfällt es.</small></h4>
      <div class="ebay-aspects" id="ebay-aspects">${ebayAspectRows(preset.aspects).map(ebayAspectRowHtml).join('')}</div>
      <button type="button" class="secondary-button" id="ebay-aspect-add">Merkmal hinzufügen</button>
      <p class="dl-hint">Platzhalter: ${EBAY_PLACEHOLDERS.map(name=>`<code>{${name}}</code>`).join(' ')}. <code>{features}</code> ist „1st Edition“ bei Erstauflagen, <code>{surface}</code> der Oberflächeneffekt von oben. Ein Preis auf dem Sheet (z. B. „4,50 €“) geht der Preisregel vor.</p>
    </section>
    <div class="dl-modal-actions"><button class="secondary-button" id="ebay-preset-reset">Texte zurücksetzen</button><span class="spacer"></span><button class="primary-button" id="ebay-preset-save">Speichern</button></div>`;
  body.addEventListener('input',()=>{ebaySettingsState.dirty=true});
  let shippingMode=preset.shipping_mode;
  const showMode=mode=>{
    shippingMode=mode;
    $$('[data-ebay-shipping-mode]',body).forEach(button=>{const on=button.dataset.ebayShippingMode===mode;button.classList.toggle('active',on);button.setAttribute('aria-selected',on)});
    $$('[data-ebay-mode]',body).forEach(group=>group.classList.toggle('hidden',group.dataset.ebayMode!==mode));
  };
  showMode(shippingMode);
  $$('[data-ebay-shipping-mode]',body).forEach(button=>button.onclick=()=>{showMode(button.dataset.ebayShippingMode);ebaySettingsState.dirty=true});
  const aspects=$('#ebay-aspects',body);
  const bindAspects=()=>$$('[data-aspect-remove]',aspects).forEach(button=>button.onclick=()=>{button.closest('.ebay-aspect-row').remove();ebaySettingsState.dirty=true});
  bindAspects();
  $('#ebay-aspect-add',body).onclick=()=>{aspects.insertAdjacentHTML('beforeend',ebayAspectRowHtml(['','']));bindAspects();$('.ebay-aspect-row:last-child [data-aspect-name]',aspects).focus()};
  const readAspects=()=>$$('.ebay-aspect-row',aspects).map(row=>[$('[data-aspect-name]',row).value.trim(),$('[data-aspect-value]',row).value.trim()]).filter(([name])=>name).map(([name,value])=>`${name}: ${value}`).join('\n');
  const read=()=>({
    game_id:gameId,game_label:$('#ebay-game-label',body).value,manufacturer:$('#ebay-manufacturer',body).value,
    surface_foil:$('#ebay-surface-foil',body).value,surface_normal:$('#ebay-surface-normal',body).value,
    title_template:$('#ebay-title-template',body).value,description_template:$('#ebay-description-template',body).value,aspects:readAspects(),
    category_id:$('#ebay-category',body).value,condition:$('#ebay-condition',body).value,quantity:$('#ebay-quantity',body).value,
    price_factor:$('#ebay-price-factor',body).value,price_min:$('#ebay-price-min',body).value,price_rounding:$('#ebay-price-rounding',body).value,price_fallback:$('#ebay-price-fallback',body).value,
    postal_code:$('#ebay-postal-code',body).value,location:$('#ebay-location',body).value,best_offer:$('#ebay-best-offer',body).checked,
    shipping_mode:shippingMode,shipping_service:$('#ebay-shipping-service',body).value,shipping_cost:$('#ebay-shipping-cost',body).value,
    shipping_additional_cost:$('#ebay-shipping-additional',body).value,dispatch_days:$('#ebay-dispatch-days',body).value,
    returns_accepted:$('#ebay-returns-accepted',body).checked,returns_days:$('#ebay-returns-days',body).value,return_shipping_paid_by:$('#ebay-return-paid-by',body).value,
    ...Object.fromEntries($$('[data-ebay-policy]',body).map(control=>[control.dataset.ebayPolicy,control.value])),
  });
  const save=async()=>{await api('/api/ebay/preset',{method:'PUT',body:JSON.stringify(read())});ebaySettingsState.dirty=false};
  $('#ebay-preset-save',body).onclick=async()=>{try{await save();toast(`Vorlage für ${games.find(game=>game.id===gameId)?.short_name||gameId} gespeichert`);renderEbayPreset(modal)}catch(error){toast(error.message)}};
  $('#ebay-preset-reset',body).onclick=()=>{
    if(!confirm('Titel, Beschreibung und Artikelmerkmale dieses TCGs auf den Standard zurücksetzen?'))return;
    $('#ebay-title-template',body).value=preset.defaults.title_template;$('#ebay-description-template',body).value=preset.defaults.description_template;
    aspects.innerHTML=ebayAspectRows(preset.defaults.aspects).map(ebayAspectRowHtml).join('');bindAspects();
    ebaySettingsState.dirty=true;
  };
  $$('[data-ebay-preset-game]',body).forEach(button=>button.onclick=async()=>{
    if(button.dataset.ebayPresetGame===gameId)return;
    // Switching keeps what was typed: the open game's template is saved first.
    if(ebaySettingsState.dirty){try{await save();toast('Vorlage gespeichert')}catch(error){toast(error.message);return}}
    ebaySettingsState.gameId=button.dataset.ebayPresetGame;renderEbayPreset(modal);
  });
  if(status.connected){loadEbayPolicies(body,preset,showMode);loadEbayShippingServices(body,preset)}
}

async function loadEbayShippingServices(body,preset){
  const control=$('#ebay-shipping-service',body);
  try{ebaySettingsState.services??=await api('/api/ebay/shipping-services')}catch(error){control.options[0].textContent=preset.shipping_service||'Versandarten nicht geladen';toast(error.message);return}
  if(!body.isConnected)return;
  const groups=Object.groupBy?Object.groupBy(ebaySettingsState.services,service=>service.category||'Weitere'):{Versandarten:ebaySettingsState.services};
  control.innerHTML=`<option value="">Bitte wählen</option>${Object.entries(groups).map(([group,list])=>`<optgroup label="${escapeHtml(group)}">${list.map(service=>`<option value="${escapeHtml(service.id)}">${escapeHtml(service.name)}</option>`).join('')}</optgroup>`).join('')}`;
  control.value=preset.shipping_service;
}

async function loadEbayPolicies(body,preset,showMode){
  try{ebaySettingsState.policies??=await api('/api/ebay/policies')}catch(error){
    $$('[data-ebay-policy]',body).forEach(control=>{if(!control.value)control.options[0].textContent='Nicht geladen'});
    toast(error.message);return;
  }
  if(!body.isConnected)return;
  if(ebaySettingsState.policies.available===false){
    // Private accounts: no business policies to pick, so the choice is not offered at all.
    $('#ebay-shipping-mode',body).classList.add('hidden');$('#ebay-no-policies',body).classList.remove('hidden');
    showMode('direct');return;
  }
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
      <div class="sheet-section-head"><b>Meine Angebote</b>${listings.listings.length?'<button class="secondary-button" id="ebay-open-stats">Statistik</button>':''}<div class="deal-filter-chips">${filters.map(([id,label])=>`<button type="button" class="community-chip ${ebayView.listingFilter===id?'active':''}" data-ebay-filter="${id}">${label}</button>`).join('')}</div></div>
      ${shown.length?`<div class="ebay-listings">${shown.map(ebayListingRow).join('')}</div>`
        :`<div class="deck-zone-empty">${status.connected?'Keine Angebote in dieser Ansicht.':'Verbinde dein eBay-Konto, um deine Angebote hier zu sehen.'}</div>`}
    </section>
    ${listings.sales.length?`<section class="ebay-section"><div class="sheet-section-head"><b>Letzte Verkäufe</b></div><div class="ebay-sales">${listings.sales.map(sale=>`<div class="ebay-sale"><span>${ebayDate(sale.sold_at)}</span><b>${sale.quantity}× ${escapeHtml(sale.title)}</b><span>${escapeHtml(sale.buyer)}</span><strong>${ebayMoney(sale.price,sale.currency)}</strong></div>`).join('')}</div></section>`:''}`;
  bindSheetTabs();
  $('#ebay-open-settings')?.addEventListener('click',()=>openEbaySettings());
  $('#ebay-open-stats')?.addEventListener('click',()=>openEbayStats(listings));
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
      <small><em class="ebay-chip ${item.status==='active'?'is-live':item.status==='sold'?'is-sold':'is-ended'}">${statusLabel}</em> ${item.quantity_sold?`${item.quantity_sold} verkauft · `:''}${item.status==='active'?`${Math.max(0,item.quantity-item.quantity_sold)} verfügbar · `:''}seit ${date(item.started_at)}</small>
      <div class="ebay-listing-stats">${ebayListingStats(item)}</div>
      <small>${item.variant_id?`Karte: ${escapeHtml(item.canonical_name||item.variant_id)}${item.set_code?` (${escapeHtml(item.set_code)} ${escapeHtml(item.collector_number)})`:''}${item.market_price!=null?` · Marktpreis ${money(item.market_price)}`:''} <button type="button" class="link-button" data-ebay-unlink>lösen</button>`
        :'<button type="button" class="link-button" data-ebay-link>Karte zuordnen</button>'}</small>
      <div class="ebay-link-search hidden"><b class="ebay-link-heading">Vorschläge aus deiner Sammlung</b><div class="ebay-link-suggestions"><small class="muted">Wird gesucht …</small></div>
        <input class="select-control" placeholder="Oder in deiner Sammlung suchen: Name, Nummer, Set …"><div class="ebay-link-results"></div></div>
    </div>
    <strong>${ebayMoney(item.price,item.currency)}</strong>
  </div>`;
}

// Watchers, views, impressions and the place in the popularity ranking of one listing.
function ebayListingStats(item){
  const stat=(value,label,title)=>value==null?'':`<span title="${title}"><b>${Number(value).toLocaleString('de-DE')}</b> ${label}</span>`;
  return [item.rank?`<span class="ebay-rank" title="Platz unter deinen aktiven Angeboten nach Beliebtheit">#${item.rank}</span>`:'',
    stat(item.watch_count||0,'Beobachter','Wie viele das Angebot beobachten'),stat(item.view_count,'Aufrufe','Seitenaufrufe der letzten 90 Tage'),
    stat(item.impression_count,'Impressionen','Wie oft das Angebot in Suchergebnissen und Listen gezeigt wurde (90 Tage)')].join('');
}
const ebayPickButton=card=>`<button type="button" data-ebay-pick="${escapeHtml(card.variant_id)}"><span>${escapeHtml(card.canonical_name)}</span> <small>${escapeHtml(card.game_name||'')} · ${escapeHtml(card.set_name)} · ${escapeHtml(card.collector_number)} · ${escapeHtml(card.finish)} · ${card.language} · ${card.quantity}× in der Sammlung</small></button>`;

function bindEbayListingRow(row){
  const itemId=row.dataset.item;
  const link=async variantId=>{try{await api(`/api/ebay/listings/${encodeURIComponent(itemId)}`,{method:'PATCH',body:JSON.stringify({variant_id:variantId})});renderEbay()}catch(error){toast(error.message)}};
  $('[data-ebay-unlink]',row)?.addEventListener('click',()=>link(null));
  $('[data-ebay-link]',row)?.addEventListener('click',()=>{
    const box=$('.ebay-link-search',row),input=$('input',box),results=$('.ebay-link-results',box),suggestions=$('.ebay-link-suggestions',box);
    box.classList.toggle('hidden');
    if(box.classList.contains('hidden'))return;
    // Only cards of the collection are offered -- a listing sells something that is owned.
    api(`/api/ebay/listings/${encodeURIComponent(itemId)}/suggestions`).then(cards=>{
      suggestions.innerHTML=cards.map(ebayPickButton).join('')||'<small class="muted">Keine passende Karte in deiner Sammlung gefunden – such unten selbst.</small>';
      $$('[data-ebay-pick]',suggestions).forEach(button=>button.onclick=()=>link(button.dataset.ebayPick));
    }).catch(error=>{suggestions.innerHTML=`<small class="muted">${escapeHtml(error.message)}</small>`});
    let timer;
    input.oninput=()=>{clearTimeout(timer);timer=setTimeout(async()=>{
      const q=input.value.trim();if(q.length<2){results.innerHTML='';return}
      const rows=await api(`/api/search?owned=1&limit=8&q=${encodeURIComponent(q)}`).catch(()=>[]);
      results.innerHTML=rows.map(card=>ebayPickButton({...card,quantity:card.quantity})).join('')||'<small class="muted">Keine Karte deiner Sammlung passt.</small>';
      $$('[data-ebay-pick]',results).forEach(button=>button.onclick=()=>link(button.dataset.ebayPick));
    },200)};
  });
}

// ---- Statistics of the account's listings ----------------------------------------------------
const EBAY_STAT_SORTS=[
  ['popularity','Beliebtheit (Ranking)',item=>item.popularity,-1],['watch_count','Beobachter',item=>item.watch_count||0,-1],
  ['view_count','Aufrufe',item=>item.view_count??-1,-1],['impression_count','Impressionen',item=>item.impression_count??-1,-1],
  ['click_through_rate','Klickrate',item=>item.click_through_rate??-1,-1],['quantity_sold','Verkauft',item=>item.quantity_sold||0,-1],
  ['newest','Neueste zuerst',item=>item.started_at||'',-1],['oldest','Älteste zuerst',item=>item.started_at||'',1],
  ['price_desc','Preis absteigend',item=>item.price??-1,-1],['price_asc','Preis aufsteigend',item=>item.price??Infinity,1],
  ['title','Titel A–Z',item=>(item.title||'').toLowerCase(),1],
];
const ebayStatsView={sort:'popularity',status:'active'};

function openEbayStats(listings){
  const status=listings.status;
  const modal=openSettingsModal({id:'ebay-stats-modal',eyebrow:'EBAY',title:'Angebotsstatistik',
    intro:'Beobachter, Aufrufe und Impressionen deiner Angebote. Die Beliebtheit ist DeckLedgers eigenes Maß: ein Beobachter zählt wie zehn Aufrufe, ein Verkauf wie 25, hundert Impressionen wie ein Aufruf – eBay selbst verrät keinen Suchrang.',
    body:`${status.stats_need_reconnect?`<p class="dl-hint">Aufrufe und Impressionen liefert eBay erst, wenn du DeckLedger den Zugriff auf deine Verkaufsstatistik erlaubst. <a href="/ebay/connect">eBay neu verbinden</a></p>`:''}
      <div class="ebay-stats-tiles" id="ebay-stats-tiles"></div>
      <div class="ebay-stats-controls"><div class="deal-filter-chips">${[['active','Aktiv'],['all','Alle']].map(([id,label])=>`<button type="button" class="community-chip" data-ebay-stats-status="${id}">${label}</button>`).join('')}</div>
        <label class="dl-field"><span>Sortieren nach</span><select class="dl-control" id="ebay-stats-sort">${EBAY_STAT_SORTS.map(([id,label])=>`<option value="${id}">${label}</option>`).join('')}</select></label></div>
      <div class="ebay-stats-table" id="ebay-stats-table"></div>`});
  const render=()=>{
    const [, , key, direction]=EBAY_STAT_SORTS.find(([id])=>id===ebayStatsView.sort)||EBAY_STAT_SORTS[0];
    const rows=listings.listings.filter(item=>ebayStatsView.status==='all'||item.status==='active')
      .sort((a,b)=>{const x=key(a),y=key(b);return (x<y?-1:x>y?1:0)*direction});
    const sum=field=>rows.reduce((total,item)=>total+(item[field]||0),0),known=rows.some(item=>item.view_count!=null);
    $('#ebay-stats-tiles',modal).innerHTML=[[rows.length,'Angebote'],[sum('watch_count'),'Beobachter'],[known?sum('view_count'):'–','Aufrufe'],[known?sum('impression_count'):'–','Impressionen'],[sum('quantity_sold'),'Verkauft']]
      .map(([value,label])=>`<div><b>${typeof value==='number'?value.toLocaleString('de-DE'):value}</b><span>${label}</span></div>`).join('');
    $$('[data-ebay-stats-status]',modal).forEach(button=>button.classList.toggle('active',button.dataset.ebayStatsStatus===ebayStatsView.status));
    const percent=value=>value==null?'–':`${Number(value).toLocaleString('de-DE',{maximumFractionDigits:1})} %`;
    const number=value=>value==null?'–':Number(value).toLocaleString('de-DE');
    $('#ebay-stats-table',modal).innerHTML=rows.length?`<div class="ebay-stats-row is-head"><span>#</span><span>Angebot</span><span>Preis</span><span>Beobachter</span><span>Aufrufe</span><span>Impressionen</span><span>Klickrate</span><span>Verkauft</span><span>Beliebtheit</span></div>
      ${rows.map(item=>`<div class="ebay-stats-row"><span class="ebay-rank">${item.rank?`#${item.rank}`:'–'}</span>
        <a href="${escapeHtml(item.url||'#')}" target="_blank" rel="noopener noreferrer"><b>${escapeHtml(item.title)}</b><small>${item.variant_id?escapeHtml(item.canonical_name||''):'keine Karte zugeordnet'}</small></a>
        <span>${ebayMoney(item.price,item.currency)}</span><span>${number(item.watch_count||0)}</span><span>${number(item.view_count)}</span><span>${number(item.impression_count)}</span>
        <span>${percent(item.click_through_rate)}</span><span>${number(item.quantity_sold||0)}</span><span><b>${number(item.popularity)}</b></span></div>`).join('')}`
      :'<div class="deck-zone-empty">Keine Angebote in dieser Ansicht.</div>';
  };
  $('#ebay-stats-sort',modal).value=ebayStatsView.sort;
  $('#ebay-stats-sort',modal).onchange=event=>{ebayStatsView.sort=event.target.value;render()};
  $$('[data-ebay-stats-status]',modal).forEach(button=>button.onclick=()=>{ebayStatsView.status=button.dataset.ebayStatsStatus;render()});
  render();
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
