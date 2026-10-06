// Admin view.

const PRICE_METHOD_OPTIONS=[['','Keine'],['cardmarket','Cardmarket'],['tcgcsv','TCGCSV / TCGplayer'],['yuyutei','Yuyutei']];

function closeAdminPriceSources(){
  $('#admin-price-source-modal')?.remove();
  document.documentElement.style.overflow='';
  document.body.style.overflow='';
}

function openAdminPriceSources(games){
  closeAdminPriceSources();
  const modal=document.createElement('div');
  modal.id='admin-price-source-modal';
  modal.className='overlay admin-price-source-overlay';
  modal.setAttribute('role','dialog');
  modal.setAttribute('aria-modal','true');
  modal.setAttribute('aria-labelledby','admin-price-source-title');
  modal.innerHTML=`<div class="utility-dialog price-source-dialog">
    <div class="dialog-head price-source-dialog-head">
      <div><span class="eyebrow">PREISDATEN</span><h2 id="admin-price-source-title">Preisquellen</h2><p>Lege je TCG fest, welcher Marktplatz für Preise verwendet wird.</p></div>
      <button class="close-button" type="button" data-price-source-close aria-label="Dialog schließen">×</button>
    </div>
    <div class="price-source-list">${games.map(g=>`<div class="price-source-row" data-price-source-game="${g.id}">
      <div class="price-source-game"><span class="price-source-game-logo"><img src="/game-logo/${g.id}" alt=""></span><div><b>${escapeHtml(g.name)}</b><small>${escapeHtml(g.id)}</small></div></div>
      <label><span>Preisquelle</span><select class="select-control" data-price-method>${PRICE_METHOD_OPTIONS.map(([value,label])=>`<option value="${value}" ${(g.price_method||'')===value?'selected':''}>${label}</option>`).join('')}</select></label>
      <label class="price-source-market-id"><span>Cardmarket Spiel-ID</span><input type="number" min="1" inputmode="numeric" data-cardmarket-game-id value="${g.cardmarket_game_id||''}" placeholder="z. B. 19"></label>
    </div>`).join('')}</div>
    <div class="dialog-actions price-source-actions"><button class="secondary-button" type="button" data-price-source-close>Abbrechen</button><span class="spacer"></span><button class="primary-button" type="button" id="admin-price-source-save">Preisquellen speichern</button></div>
  </div>`;
  document.body.append(modal);
  document.documentElement.style.overflow='hidden';
  document.body.style.overflow='hidden';
  const syncMarketField=row=>{
    const isCardmarket=$('[data-price-method]',row).value==='cardmarket';
    const field=$('[data-cardmarket-game-id]',row);
    field.disabled=!isCardmarket;
    row.classList.toggle('uses-cardmarket',isCardmarket);
  };
  $$('[data-price-source-game]',modal).forEach(row=>{
    syncMarketField(row);
    $('[data-price-method]',row).onchange=()=>syncMarketField(row);
  });
  $$('[data-price-source-close]',modal).forEach(button=>button.onclick=closeAdminPriceSources);
  modal.onmousedown=event=>{if(event.target===modal)closeAdminPriceSources()};
  $('#admin-price-source-save',modal).onclick=async event=>{
    const button=event.currentTarget;
    button.disabled=true;
    try{
      await Promise.all($$('[data-price-source-game]',modal).map(row=>api(`/api/admin/games/${row.dataset.priceSourceGame}`,{
        method:'PATCH',
        body:JSON.stringify({
          price_method:$('[data-price-method]',row).value||null,
          cardmarket_game_id:$('[data-cardmarket-game-id]',row).value||null,
        }),
      })));
      closeAdminPriceSources();
      toast('Preisquellen gespeichert');
      renderAdmin();
    }catch(error){button.disabled=false;toast(error.message)}
  };
  $('#admin-price-source-title',modal)?.focus?.();
}

async function renderAdmin(){
  const stale=renderGuard();
  content.innerHTML='<div class="page-loader"><span></span><p>Admin-Bereich wird geladen …</p></div>';
  const [games,providers,oauth,users]=await Promise.all([api('/api/admin/games'),api('/api/admin/providers'),api('/api/admin/oauth'),api('/api/admin/users')]);
  if(stale())return;
  const oauthLocked=oauth.source==='file',ro=oauthLocked?'disabled':'';
  content.innerHTML=`<div class="page-head compact-page-head"><div><span class="eyebrow">VERWALTUNG</span><h1>Admin</h1><p>Benutzer, TCGs, Katalog-Provider und Zuordnungen verwalten.</p></div></div>
    <details class="settings-section oauth-admin-card ${oauthLocked?'is-locked':''}">
      <summary class="oauth-admin-summary">
        <div class="oauth-admin-head">
          <div class="oauth-admin-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 2.8 20 6v5.2c0 4.8-3.2 8.3-8 10-4.8-1.7-8-5.2-8-10V6l8-3.2Z"/><circle cx="12" cy="10" r="2.2"/><path d="M12 12.2v4"/></svg></div>
          <div class="oauth-admin-title"><span>AUTHENTIFIZIERUNG</span><h2>Single Sign-On</h2><p>OAuth 2.0 und OpenID Connect für einen externen Identity Provider.</p></div>
          <div class="oauth-admin-state"><span class="oauth-status ${oauth.enabled?'is-active':'is-inactive'}"><i></i>${oauth.enabled?'Aktiv':'Inaktiv'}</span><span class="oauth-source-badge">${oauthLocked?'Config-Datei':'Admin UI'}</span></div>
          <span class="oauth-collapse-icon" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="m5 7.5 5 5 5-5"/></svg></span>
        </div>
      </summary>
      <div class="oauth-admin-body">
      ${oauthLocked?`<div class="oauth-lock-notice"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="3"/><path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10"/></svg><div><b>Konfiguration schreibgeschützt</b><span>Die Werte kommen aus <code>${escapeHtml(oauth.config_path)}</code>. Änderungen erfolgen in der Datei und werden nach einem Container-Neustart übernommen.</span></div></div>`:''}
      <div class="admin-form oauth-admin-form">
        <label class="oauth-toggle-row">
          <span class="oauth-toggle-copy"><b>SSO-Anmeldung</b><small>${oauth.enabled?'Der externe Login wird auf der Anmeldeseite angeboten.':'Die lokale Anmeldung bleibt aktiv; SSO ist derzeit ausgeblendet.'}</small></span>
          <span class="oauth-toggle-control"><input type="checkbox" id="oauth-enabled" ${oauth.enabled?'checked':''} ${ro}><span aria-hidden="true"></span></span>
        </label>

        <div class="oauth-form-group">
          <div class="oauth-form-group-head"><span>01</span><div><b>Identity Provider</b><small>Bezeichnung und Zugangsdaten der registrierten Anwendung.</small></div></div>
          <div class="admin-form-grid oauth-provider-grid">
            <label class="oauth-field"><span>Button-Beschriftung</span><input id="oauth-provider-name" value="${escapeHtml(oauth.provider_name||'')}" placeholder="z. B. Mit Authentik anmelden" ${ro}></label>
            <label class="oauth-field"><span>Client-ID</span><input id="oauth-client-id" value="${escapeHtml(oauth.client_id||'')}" autocomplete="off" ${ro}></label>
            <label class="oauth-field"><span>Client-Secret</span><input id="oauth-client-secret" type="password" autocomplete="new-password" placeholder="${oauth.client_secret_set?'••••••••  ·  leer lassen zum Beibehalten':'Client-Secret'}" ${ro}></label>
            <label class="oauth-field oauth-field-wide"><span>Discovery-URL <em>empfohlen</em></span><input id="oauth-discovery-url" value="${escapeHtml(oauth.discovery_url||'')}" placeholder="https://idp.example.com/.well-known/openid-configuration" ${ro}></label>
          </div>
        </div>

        <div class="oauth-form-group">
          <div class="oauth-form-group-head"><span>02</span><div><b>Manuelle Endpunkte</b><small>Nur erforderlich, wenn der Provider keine Discovery-URL bereitstellt.</small></div></div>
          <div class="admin-form-grid oauth-endpoint-grid">
            <label class="oauth-field"><span>Autorisierung</span><input id="oauth-authorize-url" value="${escapeHtml(oauth.authorize_url||'')}" placeholder="https://…/authorize" ${ro}></label>
            <label class="oauth-field"><span>Token</span><input id="oauth-token-url" value="${escapeHtml(oauth.token_url||'')}" placeholder="https://…/token" ${ro}></label>
            <label class="oauth-field"><span>Userinfo</span><input id="oauth-userinfo-url" value="${escapeHtml(oauth.userinfo_url||'')}" placeholder="https://…/userinfo" ${ro}></label>
          </div>
        </div>

        <div class="oauth-form-group">
          <div class="oauth-form-group-head"><span>03</span><div><b>Identität und Konten</b><small>Scopes, Claims und Verhalten bei der ersten Anmeldung.</small></div></div>
          <div class="admin-form-grid oauth-claims-grid">
            <label class="oauth-field oauth-field-wide"><span>Scopes</span><input id="oauth-scopes" value="${escapeHtml(oauth.scopes||'')}" placeholder="openid email profile" ${ro}></label>
            <label class="oauth-field"><span>Benutzername-Claim</span><input id="oauth-username-claim" value="${escapeHtml(oauth.username_claim||'')}" ${ro}></label>
            <label class="oauth-field"><span>E-Mail-Claim</span><input id="oauth-email-claim" value="${escapeHtml(oauth.email_claim||'')}" ${ro}></label>
            <label class="oauth-field"><span>Subjekt-ID-Claim</span><input id="oauth-subject-claim" value="${escapeHtml(oauth.subject_claim||'')}" ${ro}></label>
            <label class="oauth-field oauth-field-wide"><span>Konto-Zuordnung</span><select id="oauth-account-matching" class="select-control" ${ro}>
              <option value="manual" ${oauth.account_matching==='manual'?'selected':''}>Nur manuelle Verknüpfung</option>
              <option value="email" ${oauth.account_matching==='email'?'selected':''}>Automatisch über identische E-Mail-Adresse verknüpfen</option>
              <option value="auto_provision" ${oauth.account_matching==='auto_provision'?'selected':''}>Neue Konten automatisch anlegen</option>
            </select></label>
          </div>
        </div>

        <div class="oauth-admin-actions"><span>Die Anmeldung mit Benutzername und Passwort bleibt zusätzlich verfügbar.</span>${oauthLocked?'':`<button class="primary-button" id="oauth-save"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>SSO-Einstellungen speichern</button>`}</div>
      </div>
      </div>
    </details>
    <div id="admin-ebay-slot"></div>
    <section class="settings-section admin-settings-card">
      <div class="user-settings-card-head admin-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.4"/><path d="M2.8 20c.5-3.6 3-5.6 6.2-5.6s5.700 2 6.200 5.600M16 5.200a3.200 3.200 0 0 1 0 6M18.200 14.800c1.800.700 2.800 2.400 3 5.200"/></svg></span><div><span class="eyebrow">ZUGANG</span><h2>Benutzer</h2><p>Konten anlegen, Rollen vergeben, Passwörter zurücksetzen.</p></div></div>
      <div class="admin-table">${users.map(u=>`<div class="admin-row" data-user-row="${u.id}">
        <div class="admin-row-head"><b>${escapeHtml(u.display_name)}${u.is_self?' <span class="muted">(du)</span>':''}</b><span class="muted">${escapeHtml(u.username)}${u.email?` · ${escapeHtml(u.email)}`:''} · ${u.copies} Karten · ${u.decks} Decks</span></div>
        <div class="admin-status"><span class="admin-status-badge status-${u.role==='admin'?'ok':'none'}">${u.role==='admin'?'Admin':'Benutzer'}</span><span class="muted">${u.oauth_linked?'SSO verknüpft':'kein SSO'}${u.password_set?'':' · kein Passwort'}</span></div>
        <div class="admin-row-actions">
          <button class="secondary-button" data-edit-user="${u.id}">Bearbeiten</button>
          ${u.is_self?'':`<button class="icon-button" data-delete-user="${u.id}" data-user-name="${escapeHtml(u.username)}" title="Konto löschen">✕</button>`}
        </div>
        <div class="admin-config-editor hidden" data-user-editor="${u.id}">
          <div class="admin-form-grid admin-user-fields">
            <label>Anzeigename<input data-user-field="display_name" value="${escapeHtml(u.display_name)}"></label>
            <label>Benutzername<input data-user-field="username" value="${escapeHtml(u.username)}"></label>
            <label>E-Mail<input data-user-field="email" type="email" value="${escapeHtml(u.email)}"></label>
            <label>Rolle<select data-user-field="role" class="select-control"><option value="user" ${u.role==='user'?'selected':''}>Benutzer</option><option value="admin" ${u.role==='admin'?'selected':''}>Admin</option></select></label>
            <label>Neues Passwort<input data-user-field="password" type="password" autocomplete="new-password" placeholder="leer lassen = unverändert"></label>
            ${u.oauth_linked?`<label class="radio-label"><input type="checkbox" data-user-field="unlink_oauth"> SSO-Verknüpfung lösen</label>`:''}
          </div>
          <button class="primary-button" data-save-user="${u.id}">Speichern</button>
        </div>
      </div>`).join('')}</div>
      <details class="admin-add"><summary>+ Neues Konto anlegen</summary>
        <div class="admin-form admin-user-fields">
          <label>Benutzername<input id="admin-new-user-name" autocomplete="off" placeholder="3–32 Zeichen"></label>
          <label>Anzeigename<input id="admin-new-user-display" placeholder="optional"></label>
          <label>E-Mail<input id="admin-new-user-email" type="email" placeholder="optional, für SSO-Zuordnung"></label>
          <label>Passwort<input id="admin-new-user-password" type="password" autocomplete="new-password" placeholder="mindestens 8 Zeichen"></label>
          <label>Rolle<select id="admin-new-user-role" class="select-control"><option value="user">Benutzer</option><option value="admin">Admin</option></select></label>
          <button class="primary-button" id="admin-create-user">Konto anlegen</button>
        </div>
      </details>
    </section>
    <section class="settings-section admin-settings-card">
      <div class="user-settings-card-head admin-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m12 3 8 4-8 4-8-4 8-4Z"/><path d="m4 11 8 4 8-4M4 15l8 4 8-4"/></svg></span><div><span class="eyebrow">KATALOG</span><h2>TCGs</h2><p>Spiele, Cardbacks und Preisquellen verwalten.</p></div><button class="primary-button admin-price-source-button" type="button" id="admin-price-source-open"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="8" r="3"/><circle cx="16" cy="16" r="3"/><path d="M10.5 9.5 13.5 14.5M15.5 5.5l3 3M5.5 15.5l3 3"/></svg>Preisquellen</button></div>
      <div class="admin-table">${games.map(g=>`<div class="admin-row admin-game-row" data-game-row="${g.id}">
        <div class="admin-row-head"><b>${escapeHtml(g.name)}</b><span class="muted">${g.id} · ${g.languages.join('/')}</span></div>
        <label>Card-Back<input type="file" accept="image/jpeg" data-card-back="${g.id}"></label>
      </div>`).join('')}</div>
      <details class="admin-add"><summary>+ Neues TCG anlegen</summary>
        <div class="admin-form">
          <label>Name<input id="admin-new-game-name" placeholder="z.B. Mein neues TCG"></label>
          <label>Kurzname<input id="admin-new-game-short" placeholder="z.B. MTCG"></label>
          <label>Sprachen (Komma-getrennt)<input id="admin-new-game-langs" placeholder="EN,DE" value="EN"></label>
          <label>Akzentfarbe<input id="admin-new-game-accent" type="color" value="#6366f1"></label>
          <button class="primary-button" id="admin-create-game">TCG anlegen</button>
        </div>
      </details>
    </section>
    <section class="settings-section admin-settings-card">
      <div class="user-settings-card-head admin-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m8 7-4 5 4 5M16 7l4 5-4 5M14 4l-4 16"/></svg></span><div><span class="eyebrow">DATENQUELLEN</span><h2>Katalog-Provider</h2><p>Import-Code, Laufstatus und Sicherheitsgrenzen verwalten.</p></div></div>
      <div class="admin-table">${providers.map(p=>`<div class="admin-row" data-provider-row="${p.id}">
        <div class="admin-row-head"><b>${escapeHtml(p.label)}</b><span class="muted">${p.game_id} · Timeout ${p.timeout_seconds}s</span></div>
        <div class="admin-status"><span class="admin-status-badge status-${p.last_status||'none'}">${p.last_status||'noch nicht gelaufen'}</span><span class="muted">${p.last_run_at?date(p.last_run_at):'–'}</span></div>
        ${p.last_error?`<div class="admin-error">${escapeHtml(p.last_error)}</div>`:''}
        <div class="admin-row-actions">
          <button class="secondary-button" data-run-provider="${p.id}">Jetzt importieren</button>
          <button class="secondary-button" data-edit-provider="${p.id}">Code bearbeiten</button>
          <button class="icon-button" data-delete-provider="${p.id}" title="Löschen">✕</button>
        </div>
        <div class="admin-config-editor hidden" data-config-editor="${p.id}">
          <div class="admin-form-grid">
            <label>Min. Sets (Sicherheitsgrenze)<input type="number" min="0" data-provider-field="minimum_sets" value="${p.minimum_sets}"></label>
            <label>Min. Karten (Sicherheitsgrenze)<input type="number" min="0" data-provider-field="minimum_cards" value="${p.minimum_cards}"></label>
            <label>Zeitlimit (Sekunden)<input type="number" min="10" data-provider-field="timeout_seconds" value="${p.timeout_seconds}"></label>
          </div>
          <textarea class="admin-code-editor" data-provider-code rows="20" spellcheck="false">${escapeHtml(p.code||'')}</textarea>
          <button class="primary-button" data-save-code="${p.id}">Code speichern</button>
        </div>
      </div>`).join('')}</div>
      <details class="admin-add"><summary>+ Neuen Provider anlegen</summary>
        <div class="admin-form">
          <label>TCG<select id="admin-new-provider-game" class="select-control">${games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('')}</select></label>
          <label>Label<input id="admin-new-provider-label" placeholder="z.B. Mein TCG"></label>
          <div class="admin-form-grid">
            <label>Min. Sets (Sicherheitsgrenze)<input type="number" min="0" id="admin-new-provider-minsets" value="0"></label>
            <label>Min. Karten (Sicherheitsgrenze)<input type="number" min="0" id="admin-new-provider-mincards" value="0"></label>
            <label>Zeitlimit (Sekunden)<input type="number" min="10" id="admin-new-provider-timeout" value="300"></label>
          </div>
          <label>Code<textarea class="admin-code-editor" id="admin-new-provider-code" rows="16" spellcheck="false" placeholder="from catalog_provider_contract import empty_catalog, fetch, put_identity, slug

def fetch_catalog() -&gt; dict:
    catalog = empty_catalog()
    # ... Sets/Karten/Printings/Varianten in catalog eintragen ...
    return catalog"></textarea></label>
          <button class="primary-button" id="admin-create-provider">Provider anlegen</button>
        </div>
      </details>
    </section>
    <section class="settings-section admin-settings-card">
      <div class="user-settings-card-head admin-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="5" y="3" width="12" height="16" rx="2"/><path d="M9 21h9a2 2 0 0 0 2-2V7M8 11h6M11 8v6"/></svg></span><div><span class="eyebrow">SONDEREINTRÄGE</span><h2>Manuelle Karten</h2><p>Direkte Einträge bleiben unabhängig von Katalog-Synchronisierungen erhalten.</p></div></div>
      <label>TCG<select id="admin-manual-game" class="select-control">${games.map(g=>`<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('')}</select></label>
      <div class="admin-table" id="admin-manual-cards-list"></div>
      <details class="admin-add"><summary>+ Karte manuell hinzufügen</summary>
        <div class="admin-form">
          <label>Vorhandenes Set<select id="admin-manual-set" class="select-control"><option value="">– neues Set unten anlegen –</option></select></label>
          <label>Neuer Set-Code<input id="admin-manual-new-set-code" placeholder="nur falls kein Set gewählt"></label>
          <label>Neuer Set-Name<input id="admin-manual-new-set-name"></label>
          <label>Kartenname<input id="admin-manual-name"></label>
          <label>Kartenschlüssel (optional, sonst aus Name)<input id="admin-manual-key"></label>
          <label>Regeltext<textarea id="admin-manual-rules" rows="3"></textarea></label>
          <label>Kartentyp<input id="admin-manual-type" placeholder="z.B. Character"></label>
          <label>Sammlernummer<input id="admin-manual-number"></label>
          <label>Sprache<input id="admin-manual-language" value="EN"></label>
          <label>Seltenheit<input id="admin-manual-rarity"></label>
          <label>Finish<input id="admin-manual-finish" value="Normal" placeholder="Normal, Foil, ..."></label>
          <label class="checkbox-row"><input type="checkbox" id="admin-manual-parallel"> Parallel / Alternative Art</label>
          <label>Bild-URL<input id="admin-manual-image" placeholder="https://..."></label>
          <button class="primary-button" id="admin-manual-create">Karte speichern</button>
        </div>
      </details>
    </section>`;
  renderAdminEbay();
  $('#oauth-save',content)?.addEventListener('click',async()=>{
    const payload={
      enabled:$('#oauth-enabled').checked,
      provider_name:$('#oauth-provider-name').value.trim(),
      client_id:$('#oauth-client-id').value.trim(),
      client_secret:$('#oauth-client-secret').value,
      discovery_url:$('#oauth-discovery-url').value.trim(),
      authorize_url:$('#oauth-authorize-url').value.trim(),
      token_url:$('#oauth-token-url').value.trim(),
      userinfo_url:$('#oauth-userinfo-url').value.trim(),
      scopes:$('#oauth-scopes').value.trim(),
      username_claim:$('#oauth-username-claim').value.trim(),
      email_claim:$('#oauth-email-claim').value.trim(),
      subject_claim:$('#oauth-subject-claim').value.trim(),
      account_matching:$('#oauth-account-matching').value,
    };
    try{await post('/api/admin/oauth',payload);toast('SSO-Einstellungen gespeichert');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $('#admin-price-source-open',content).onclick=()=>openAdminPriceSources(games);
  $$('[data-card-back]',content).forEach(input=>input.onchange=async e=>{
    const file=e.target.files[0]; if(!file)return;
    const form=new FormData(); form.append('file',file);
    const response=await fetch(`/api/admin/games/${input.dataset.cardBack}/card-back`,{method:'POST',body:form});
    if(response.ok)toast('Card-Back hochgeladen'); else toast('Upload fehlgeschlagen');
  });
  $$('[data-edit-user]',content).forEach(button=>button.onclick=()=>$(`[data-user-editor="${button.dataset.editUser}"]`,content).classList.toggle('hidden'));
  $$('[data-save-user]',content).forEach(button=>button.onclick=async()=>{
    const editor=$(`[data-user-editor="${button.dataset.saveUser}"]`,content),payload={};
    $$('[data-user-field]',editor).forEach(field=>{
      if(field.type==='checkbox'){if(field.checked)payload[field.dataset.userField]=true}
      else if(field.dataset.userField!=='password'||field.value)payload[field.dataset.userField]=field.value;
    });
    try{await api(`/api/admin/users/${button.dataset.saveUser}`,{method:'PATCH',body:JSON.stringify(payload)});toast('Konto gespeichert');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $$('[data-delete-user]',content).forEach(button=>button.onclick=async()=>{
    if(!confirm(`Konto „${button.dataset.userName}“ mit seiner Sammlung, seinen Decks und Listen endgültig löschen?`))return;
    try{await api(`/api/admin/users/${button.dataset.deleteUser}`,{method:'DELETE'});toast('Konto gelöscht');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $('#admin-create-user').onclick=async()=>{
    try{
      await post('/api/admin/users',{username:$('#admin-new-user-name').value,display_name:$('#admin-new-user-display').value,email:$('#admin-new-user-email').value,password:$('#admin-new-user-password').value,role:$('#admin-new-user-role').value});
      toast('Konto angelegt');renderAdmin();
    }catch(error){toast(error.message)}
  };
  $('#admin-create-game').onclick=async()=>{
    const name=$('#admin-new-game-name').value.trim();
    if(!name){toast('Name ist erforderlich');return}
    const languages=$('#admin-new-game-langs').value.split(',').map(x=>x.trim()).filter(Boolean);
    try{
      await post('/api/admin/games',{name,short_name:$('#admin-new-game-short').value.trim()||name,languages,accent:$('#admin-new-game-accent').value});
      toast('TCG angelegt'); renderAdmin();
    }catch(error){toast(error.message)}
  };
  $$('[data-run-provider]',content).forEach(button=>button.onclick=async()=>{
    button.disabled=true; button.textContent='Läuft …';
    try{
      const result=await post(`/api/admin/providers/${button.dataset.runProvider}/run`,{});
      toast(result.status==='ok'?'Import erfolgreich':`Import fehlgeschlagen: ${result.error||'unbekannter Fehler'}`);
    }catch(error){toast(error.message)}
    renderAdmin();
  });
  $$('[data-edit-provider]',content).forEach(button=>button.onclick=()=>{
    $(`[data-config-editor="${button.dataset.editProvider}"]`,content).classList.toggle('hidden');
  });
  $$('[data-save-code]',content).forEach(button=>button.onclick=async()=>{
    const editor=$(`[data-config-editor="${button.dataset.saveCode}"]`,content);
    const payload={
      code:$('[data-provider-code]',editor).value,
      minimum_sets:Number($('[data-provider-field="minimum_sets"]',editor).value)||0,
      minimum_cards:Number($('[data-provider-field="minimum_cards"]',editor).value)||0,
      timeout_seconds:Number($('[data-provider-field="timeout_seconds"]',editor).value)||300,
    };
    try{await api(`/api/admin/providers/${button.dataset.saveCode}`,{method:'PATCH',body:JSON.stringify(payload)});toast('Code gespeichert');renderAdmin()}
    catch(error){toast(error.message)}
  });
  $$('[data-delete-provider]',content).forEach(button=>button.onclick=async()=>{
    await api(`/api/admin/providers/${button.dataset.deleteProvider}`,{method:'DELETE'});
    toast('Provider gelöscht'); renderAdmin();
  });
  $('#admin-create-provider').onclick=async()=>{
    const payload={
      game_id:$('#admin-new-provider-game').value, label:$('#admin-new-provider-label').value.trim()||undefined,
      code:$('#admin-new-provider-code').value,
      minimum_sets:Number($('#admin-new-provider-minsets').value)||0,
      minimum_cards:Number($('#admin-new-provider-mincards').value)||0,
      timeout_seconds:Number($('#admin-new-provider-timeout').value)||300,
    };
    try{
      await post('/api/admin/providers',payload);
      toast('Provider angelegt'); renderAdmin();
    }catch(error){toast(error.message)}
  };
  $('#admin-manual-game').onchange=e=>loadManualCardsSection(e.target.value);
  $('#admin-manual-create').onclick=async()=>{
    const name=$('#admin-manual-name').value.trim();
    if(!name){toast('Kartenname ist erforderlich');return}
    const gameId=$('#admin-manual-game').value;
    const payload={
      canonical_name:name, key:$('#admin-manual-key').value.trim()||undefined,
      set_id:$('#admin-manual-set').value||undefined,
      new_set_code:$('#admin-manual-new-set-code').value.trim()||undefined,
      new_set_name:$('#admin-manual-new-set-name').value.trim()||undefined,
      rules_text:$('#admin-manual-rules').value.trim(), card_type:$('#admin-manual-type').value.trim()||undefined,
      collector_number:$('#admin-manual-number').value.trim()||undefined, language:$('#admin-manual-language').value.trim()||'EN',
      rarity:$('#admin-manual-rarity').value.trim()||undefined, finish:$('#admin-manual-finish').value.trim()||'Normal',
      is_parallel:$('#admin-manual-parallel').checked, image_url:$('#admin-manual-image').value.trim()||undefined,
    };
    try{
      await post(`/api/admin/games/${gameId}/manual-cards`,payload);
      toast('Karte gespeichert');
      $('#admin-manual-name').value='';$('#admin-manual-key').value='';$('#admin-manual-rules').value='';$('#admin-manual-number').value='';$('#admin-manual-rarity').value='';$('#admin-manual-image').value='';$('#admin-manual-parallel').checked=false;
      loadManualCardsSection(gameId);
    }catch(error){toast(error.message)}
  };
  loadManualCardsSection(games[0]?.id);
}

async function loadManualCardsSection(gameId){
  if(!gameId)return;
  $('#admin-manual-game').value=gameId;
  const setSelect=$('#admin-manual-set');
  const sets=await api(`/api/games/${gameId}/sets`);
  setSelect.innerHTML=`<option value="">– neues Set unten anlegen –</option>${sets.map(s=>`<option value="${s.id}">${escapeHtml(s.code)} · ${escapeHtml(s.name)}</option>`).join('')}`;
  await renderManualCards(gameId);
}

async function renderManualCards(gameId){
  const list=$('#admin-manual-cards-list');
  const cards=await api(`/api/admin/games/${gameId}/manual-cards`);
  list.innerHTML=cards.length?cards.map(c=>`<div class="admin-row" data-manual-card="${c.id}">
      <div class="admin-row-head"><b>${escapeHtml(c.canonical_name)}</b><span class="muted">${escapeHtml(c.id)}</span></div>
      <div class="muted">${c.printings.map(p=>`${escapeHtml(p.collector_number)} · ${escapeHtml(p.language)} · ${escapeHtml(p.rarity)} (${p.variants.map(v=>escapeHtml(gameId==='lorcana'?lorcanaFinishLabel(v.finish,p.rarity):v.finish)).join(', ')})`).join(' / ')||'keine Printings'}</div>
      <button class="icon-button" data-delete-manual-card="${c.id}" title="Löschen">✕</button>
    </div>`).join(''):'<p class="muted">Noch keine manuellen Karten für dieses TCG.</p>';
  $$('[data-delete-manual-card]',list).forEach(button=>button.onclick=async()=>{
    await api(`/api/admin/games/${gameId}/manual-cards/${button.dataset.deleteManualCard}`,{method:'DELETE'});
    toast('Karte gelöscht'); renderManualCards(gameId);
  });
}
