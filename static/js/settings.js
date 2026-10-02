// A user's settings and account.

function closeAccountPasswordDialog(){
  $('#account-password-modal')?.remove();
  document.documentElement.style.overflow='';
  document.body.style.overflow='';
}

function openAccountPasswordDialog(user){
  closeAccountPasswordDialog();
  const modal=document.createElement('div');
  modal.id='account-password-modal';
  modal.className='overlay account-password-overlay';
  modal.setAttribute('role','dialog');
  modal.setAttribute('aria-modal','true');
  modal.setAttribute('aria-labelledby','account-password-dialog-title');
  modal.innerHTML=`<div class="utility-dialog account-password-dialog">
    <div class="dialog-head account-password-dialog-head"><div><span class="eyebrow">SICHERHEIT</span><h2 id="account-password-dialog-title">${user.password_set?'Passwort ändern':'Passwort festlegen'}</h2><p>${user.password_set?'Bestätige zuerst dein aktuelles Passwort.':'Lege ein Passwort für die lokale Anmeldung fest.'}</p></div><button class="close-button" type="button" data-password-dialog-close aria-label="Dialog schließen">×</button></div>
    <div class="account-password-fields">
      ${user.password_set?`<label><span>Aktuelles Passwort</span><input id="account-current-password" type="password" autocomplete="current-password"></label>`:''}
      <label><span>Neues Passwort</span><input id="account-new-password" type="password" autocomplete="new-password"></label>
      <label><span>Neues Passwort bestätigen</span><input id="account-confirm-password" type="password" autocomplete="new-password"></label>
    </div>
    <div class="dialog-actions account-password-actions"><button class="secondary-button" type="button" data-password-dialog-close>Abbrechen</button><span class="spacer"></span><button class="primary-button" type="button" id="account-password-save">${user.password_set?'Passwort ändern':'Passwort festlegen'}</button></div>
  </div>`;
  document.body.append(modal);
  document.documentElement.style.overflow='hidden';
  document.body.style.overflow='hidden';
  $$('[data-password-dialog-close]',modal).forEach(button=>button.onclick=closeAccountPasswordDialog);
  modal.onmousedown=event=>{if(event.target===modal)closeAccountPasswordDialog()};
  $('#account-password-save',modal).onclick=async event=>{
    const newPassword=$('#account-new-password',modal).value,confirmPassword=$('#account-confirm-password',modal).value;
    if(newPassword!==confirmPassword){toast('Die Passwörter stimmen nicht überein.');return}
    const button=event.currentTarget;
    button.disabled=true;
    try{
      await post('/api/account/password',{new_password:newPassword,current_password:$('#account-current-password',modal)?.value||''});
      state.boot.user.password_set=true;
      closeAccountPasswordDialog();
      renderSettings();
      toast('Passwort gespeichert');
    }catch(error){button.disabled=false;toast(error.message)}
  };
  requestAnimationFrame(()=>$(user.password_set?'#account-current-password':'#account-new-password',modal)?.focus());
}

function renderSettings(){
  const games=state.boot.games,settings=state.boot.settings||{},defaultLanguages=settings.defaultLanguages||{},banner=settings.homeBanner||{},modes=banner.modes||['newest'],mobileTheme=settings.mobileTheme!=='classic'?'modern':'classic',mobileAppearance=settings.mobileThemeAppearance==='light'?'light':'dark';
  const user=state.boot.user,oauth=state.boot.oauth||{enabled:false};
  content.innerHTML=`<div class="page-head compact-page-head user-settings-page-head"><div><span class="eyebrow">KONTO</span><h1>Einstellungen</h1><p>Passe DeckLedger an deine Sammlung an.</p></div></div>
    <div class="user-settings-layout">
    <section class="settings-section user-settings-card settings-card-profile settings-card-wide">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5"/><path d="M4.8 20c1.3-3.6 4.2-5.5 7.2-5.5s5.9 1.9 7.2 5.5"/></svg></span><div><span class="eyebrow">PROFIL</span><h2>Kontodaten</h2><p>Anzeigename, Benutzername und E-Mail-Adresse dieses Kontos.</p></div></div>
      <div class="settings-grid settings-grid-account">
        <label class="settings-field"><span>Anzeigename</span><input id="account-display-name" value="${escapeHtml(user.display_name)}"></label>
        <label class="settings-field"><span>Benutzername</span><input id="account-username" value="${escapeHtml(user.username)}"></label>
        <label class="settings-field"><span>E-Mail</span><input id="account-email" type="email" placeholder="name@example.com" value="${escapeHtml(user.email||'')}"></label>
      </div>
      <div class="user-settings-actions account-settings-actions"><span class="password-settings-status"><i></i><b>${user.password_set?'Lokales Passwort aktiv':'Kein lokales Passwort'}</b></span><button class="primary-button" type="button" id="account-password-open">${user.password_set?'Passwort ändern':'Passwort festlegen'}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button><button class="primary-button" id="account-save"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>Kontodaten speichern</button></div>
    </section>
    ${oauth.enabled?`<section class="settings-section user-settings-card settings-card-sso settings-card-wide">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 2.8 20 6v5.2c0 4.8-3.2 8.3-8 10-4.8-1.7-8-5.2-8-10V6l8-3.2Z"/><circle cx="12" cy="10" r="2.2"/><path d="M12 12.2v4"/></svg></span><div><span class="eyebrow">ANMELDUNG</span><h2>Single Sign-On</h2><p>${user.oauth_linked?`Dieses Konto ist mit ${escapeHtml(oauth.provider_name)} verbunden.`:`Verknüpfe dein Konto mit ${escapeHtml(oauth.provider_name)}.`}</p></div><span class="user-settings-status ${user.oauth_linked?'is-connected':''}"><i></i>${user.oauth_linked?'Verbunden':'Nicht verbunden'}</span></div>
      <div class="user-settings-actions">${user.oauth_linked?`<button class="secondary-button" id="account-oauth-unlink">Verbindung trennen</button>`
        :`<a class="primary-button" href="/oauth/login">Mit ${escapeHtml(oauth.provider_name)} verbinden</a>`}</div>
    </section>`:''}
    <section class="settings-section user-settings-card settings-card-appearance">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9c0-1-.8-1.5-1.7-1.2a4 4 0 0 1-5.1-5.1C14.5 4.8 14 4 13 4l-1-.1Z"/><circle cx="7.5" cy="12" r=".7"/><circle cx="10" cy="17" r=".7"/></svg></span><div><span class="eyebrow">OBERFLÄCHE</span><h2>Darstellung</h2><p>Wähle Layout und Farbgebung der Oberfläche.</p></div></div>
      <div class="appearance-settings-grid">
        <div class="segmented mobile-theme-toggle"><button type="button" data-mobile-theme="modern" class="${mobileTheme==='modern'?'active':''}">Modern</button><button type="button" data-mobile-theme="classic" class="${mobileTheme==='classic'?'active':''}">Klassisch</button></div>
        ${mobileTheme==='modern'?`<div class="segmented mobile-appearance-toggle"><button type="button" data-mobile-appearance="dark" class="${mobileAppearance==='dark'?'active':''}">Dunkel</button><button type="button" data-mobile-appearance="light" class="${mobileAppearance==='light'?'active':''}">Hell</button></div>`:''}
      </div>
    </section>
    <section class="settings-section user-settings-card settings-card-language">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3.5 12h17M12 3c2.2 2.4 3.4 5.4 3.4 9S14.2 18.6 12 21c-2.2-2.4-3.4-5.4-3.4-9S9.8 5.4 12 3Z"/></svg></span><div><span class="eyebrow">KARTENDATEN</span><h2>Standardsprache je Spiel</h2><p>Vorauswahl für Sammlung, Deckbuilder und Import.</p></div></div>
      <div class="settings-grid settings-language-grid">${games.map(g=>`<label class="settings-field settings-language-field"><span class="settings-language-game"><i><img src="/game-logo/${g.id}" alt=""></i><b>${escapeHtml(g.name)}</b></span><select data-lang-game="${g.id}" class="select-control">${g.languages.map(l=>`<option value="${l}" ${(defaultLanguages[g.id]||g.languages[0])===l?'selected':''}>${l}</option>`).join('')}</select></label>`).join('')}</div>
    </section>
    <section class="settings-section user-settings-card settings-card-highlights settings-card-wide">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m12 3 2.1 5.4L20 9l-4.5 3.8L17 19l-5-3.2L7 19l1.5-6.2L4 9l5.9-.6L12 3Z"/></svg></span><div><span class="eyebrow">STARTSEITE</span><h2>Highlights</h2><p>Bestimme die Karten im endlosen Reel des aktiven TCGs.</p></div></div>
      <div class="settings-checklist highlight-settings-list">
        <label class="checkbox-row highlight-setting"><span><b>Neueste Karten</b><small>Die 20 zuletzt hinzugefügten Karten</small></span><input type="checkbox" data-banner-mode="newest" ${modes.includes('newest')?'checked':''}><i aria-hidden="true"></i></label>
        <label class="checkbox-row highlight-setting"><span><b>Wertvollste Karten</b><small>Die 20 Karten mit dem höchsten Wert</small></span><input type="checkbox" data-banner-mode="value" ${modes.includes('value')?'checked':''}><i aria-hidden="true"></i></label>
      </div>
      <p class="muted settings-hint">Aktive Listen werden zu einer gemeinsamen, duplikatfreien Spur zusammengeführt.</p>
      <div class="settings-checklist highlight-settings-list">
        <label class="checkbox-row highlight-setting"><span><b>Immer bewegen</b><small>${matchMedia('(prefers-reduced-motion: reduce)').matches?'Auf diesem Gerät sind Animationen in den Systemeinstellungen reduziert: Das Reel steht still und lässt sich von Hand scrollen. Hiermit läuft es trotzdem.':'Das Reel läuft auch dann, wenn ein Gerät Animationen reduziert (Systemeinstellung, Energiesparmodus, Remote-Sitzung).'}</small></span><input type="checkbox" id="banner-always-moving" ${banner.alwaysMoving?'checked':''}><i aria-hidden="true"></i></label>
      </div>
    </section>
    <section class="settings-section user-settings-card settings-card-offline settings-card-wide" id="offline-save-card">
      <div class="user-settings-card-head"><span class="user-settings-card-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 4v11m0 0-4-4m4 4 4-4"/><path d="M5 19h14"/></svg></span><div><span class="eyebrow">UNTERWEGS</span><h2>Offline verfügbar machen</h2><p>Speichert Sammlung, Watchlists, Kartendetails und Kartenbilder auf diesem Gerät, damit sie auch ohne Verbindung zum Server da sind.</p></div></div>
      <div id="offline-save-body"></div>
    </section>
    </div>`;
  renderOfflineSave();
  $('#account-save',content).onclick=async()=>{
    const payload={display_name:$('#account-display-name').value.trim(),username:$('#account-username').value.trim(),email:$('#account-email').value.trim()};
    try{
      const updated=await api('/api/account',{method:'PATCH',body:JSON.stringify(payload)});
      state.boot.user={...state.boot.user,...updated};
      $('#user-name').textContent=updated.display_name; $('#user-avatar').textContent=initials(updated.display_name);
      toast('Kontodaten gespeichert');
    }catch(error){toast(error.message)}
  };
  $('#account-password-open',content).onclick=()=>openAccountPasswordDialog(user);
  $('#account-oauth-unlink',content)?.addEventListener('click',async()=>{
    try{
      await post('/api/account/oauth/unlink',{});
      state.boot.user.oauth_linked=false;
      toast('SSO-Verbindung getrennt');
      renderSettings();
    }catch(error){toast(error.message)}
  });
  $$('[data-mobile-theme]',content).forEach(b=>b.onclick=async()=>{
    const value=b.dataset.mobileTheme;
    await post('/api/settings',{mobileTheme:value});
    state.boot.settings.mobileTheme=value;
    document.body.classList.toggle('mobile-modern',value!=='classic');
    renderSettings();
    toast('Darstellung gespeichert');
  });
  $$('[data-mobile-appearance]',content).forEach(b=>b.onclick=async()=>{
    const value=b.dataset.mobileAppearance;
    await post('/api/settings',{mobileThemeAppearance:value});
    state.boot.settings.mobileThemeAppearance=value;
    document.body.classList.toggle('mobile-light',value==='light');
    renderSettings();
    toast('Darstellung gespeichert');
  });
  $$('[data-lang-game]',content).forEach(select=>select.onchange=async e=>{
    const updated={...(state.boot.settings.defaultLanguages||{}),[select.dataset.langGame]:e.target.value};
    await post('/api/settings',{defaultLanguages:updated});
    state.boot.settings.defaultLanguages=updated;
    toast('Standardsprache gespeichert');
  });
  $$('[data-banner-mode]',content).forEach(cb=>cb.onchange=async e=>{
    const current=state.boot.settings.homeBanner||{};
    let next=[...(current.modes||['newest'])];
    if(e.target.checked){if(!next.includes(cb.dataset.bannerMode))next.push(cb.dataset.bannerMode)}
    else{next=next.filter(m=>m!==cb.dataset.bannerMode);if(!next.length){e.target.checked=true;toast('Mindestens eine Kartenliste muss aktiv sein.');return}}
    const {excludedGames:ignored,...rest}=current;
    const updated={...rest,modes:next};
    await post('/api/settings',{homeBanner:updated});
    state.boot.settings.homeBanner=updated;
    toast('Banner-Einstellung gespeichert');
  });
  $('#banner-always-moving',content).onchange=async event=>{
    const updated={...(state.boot.settings.homeBanner||{}),alwaysMoving:event.target.checked};
    await post('/api/settings',{homeBanner:updated});
    state.boot.settings.homeBanner=updated;
    toast('Banner-Einstellung gespeichert');
  };
}

// The "Offline verfügbar machen" card: what is saved on this device, saving it and removing it.
let offlineSaveFullImages=false;
const offlineCardCount=count=>`${count} Karte${count===1?'':'n'}`;
async function renderOfflineSave(message=''){
  const body=$('#offline-save-body');if(!body)return;
  if(!('caches' in window)||!('serviceWorker' in navigator)){body.innerHTML='<p class="muted settings-hint">Dieser Browser kann keine Daten für die Offline-Nutzung speichern.</p>';return}
  if(offlineSave){
    body.innerHTML=`<div class="offline-save-progress"><div class="offline-save-bar"><i id="offline-save-fill"></i></div><span id="offline-save-count"></span></div><div class="user-settings-actions"><button class="secondary-button" id="offline-save-cancel">Abbrechen</button></div>`;
    $('#offline-save-cancel').onclick=cancelOfflineSave;
    paintOfflineSaveProgress(offlineSave.done,offlineSave.total);
    return;
  }
  const info=await offlineSaveInfo(),mine=info&&info.userId===state.boot.user.id;
  let usage='';
  try{const estimate=await navigator.storage?.estimate?.();if(estimate?.usage)usage=` · DeckLedger belegt hier insgesamt ${Math.max(1,Math.round(estimate.usage/1048576))} MB`}catch{}
  if(!$('#offline-save-body'))return;
  const status=mine
    ?`<b>${offlineCardCount(info.cards)} gespeichert</b><small>Stand ${new Intl.DateTimeFormat('de-DE',{dateStyle:'medium',timeStyle:'short'}).format(new Date(info.savedAt))}${info.fullImages?' · mit großen Bildern':''}${info.skipped?` · ${info.skipped} Dateien nicht erreichbar`:''}${usage}</small>`
    :`<b>Noch nichts gespeichert</b><small>Ohne Speichern ist offline nur da, was du zuletzt angesehen hast.</small>`;
  body.innerHTML=`<div class="offline-save-status"><i class="${mine?'is-saved':''}"></i><span>${status}</span></div>
    ${message?`<p class="muted settings-hint">${escapeHtml(message)}</p>`:''}
    <div class="settings-checklist highlight-settings-list offline-save-option"><label class="checkbox-row highlight-setting"><span><b>Auch große Kartenbilder</b><small>Volle Auflösung für die Detailansicht; braucht ein Vielfaches an Speicher. Ohne sie zeigt die Detailansicht offline das Vorschaubild.</small></span><input type="checkbox" id="offline-save-full" ${offlineSaveFullImages?'checked':''}><i aria-hidden="true"></i></label></div>
    <div class="user-settings-actions">${mine?'<button class="secondary-button" id="offline-save-clear">Gespeicherte Daten löschen</button>':''}<button class="primary-button" id="offline-save-start">${mine?'Aktualisieren':'Jetzt speichern'}</button></div>`;
  $('#offline-save-full').onchange=event=>{offlineSaveFullImages=event.target.checked};
  $('#offline-save-start').onclick=startOfflineSave;
  if($('#offline-save-clear'))$('#offline-save-clear').onclick=async()=>{await clearOfflineSave();toast('Offline-Daten gelöscht');renderOfflineSave()};
}
function paintOfflineSaveProgress(done,total){
  const fill=$('#offline-save-fill'),count=$('#offline-save-count');if(!fill||!count)return;
  fill.style.width=`${total?Math.round(done/total*100):0}%`;
  count.textContent=`${done} von ${total} Dateien`;
}
async function startOfflineSave(){
  if(!navigator.onLine||serverUnreachable){toast('Zum Speichern muss der Server erreichbar sein.');return}
  const saving=saveForOffline({fullImages:offlineSaveFullImages,onProgress:paintOfflineSaveProgress});
  renderOfflineSave();
  try{
    const info=await saving;
    toast(`${offlineCardCount(info.cards)} ${info.cards===1?'ist':'sind'} jetzt offline verfügbar.`);
    renderOfflineSave();
  }catch(error){
    renderOfflineSave(error.name==='AbortError'?'Abgebrochen. Was schon gespeichert war, bleibt erhalten.':`Speichern fehlgeschlagen: ${error.message}`);
  }
}
