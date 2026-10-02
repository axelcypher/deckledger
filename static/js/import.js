// Import and export dialog.

let importMode='text', importJsonData=null, importJsonExtras={decks:[],watchlists:[]};

async function previewImport(){
  const box=$('#import-preview');box.classList.add('visible');
  const button=$('#preview-import');
  if(importMode==='json'&&!importJsonData){box.innerHTML='<div class="empty-state">Bitte zuerst eine JSON-Backup-Datei wählen.</div>';$('#apply-import').disabled=true;return[]}
  box.innerHTML='<div class="page-loader compact"><span></span><p>Wird geprüft …</p></div>';
  button.disabled=true;
  let rows;
  try{
    rows=importMode==='json'
      ?await post('/api/import/json/preview',{collection:importJsonData,...importJsonExtras})
      :await post('/api/import/preview',{game_id:$('#import-game').value,language:$('#import-language').value,condition:$('#import-condition').value,text:$('#import-text').value});
  }catch(error){
    box.innerHTML=`<div class="empty-state">Vorschau fehlgeschlagen: ${escapeHtml(error.message)}</div>`;
    button.disabled=false;
    return[];
  }
  button.disabled=false;
  // r.message can accompany a genuine match too (not just ambiguous/not_found) -- e.g. a
  // JSON-backup row recovered by matching set/number/language/finish after its catalogue name
  // text had drifted since the backup was made (see parse_json_backup in app.py). Surface it as
  // its own line whenever present so that recovery is visible, not just silently applied.
  box.innerHTML=rows.length?rows.map(r=>r.kind
    ?`<div class="import-row"><span>${r.line}</span><div><b>${escapeHtml(r.original)}</b><small>${escapeHtml(r.message||'')}</small></div><span class="import-status ${r.status}">${r.status==='matched'?'Gefunden':'Fehlt'}</span></div>`
    :`<div class="import-row"><span>${r.line}</span><div><b>${r.match?escapeHtml(r.match.canonical_name):escapeHtml(r.number||r.original)}</b><small>${r.quantity||'–'}× · ${r.language||'–'} · ${r.match?escapeHtml(r.match.game_id==='lorcana'?lorcanaFinishLabel(r.match.finish,r.match.rarity):r.match.finish):escapeHtml(r.message||'Kein Treffer')}${r.match&&r.condition&&r.condition!=='Near Mint'?` · ${escapeHtml(r.condition)}`:''}${r.match&&r.is_graded?` · ${escapeHtml(r.grade_label||'Graded')}`:''}</small>${r.match&&r.message?`<small class="import-row-note">${escapeHtml(r.message)}</small>`:''}</div><span class="import-status ${r.status}">${r.status==='matched'?'Gefunden':r.status==='ambiguous'?'Prüfen':'Fehlt'}</span></div>`).join(''):'<div class="empty-state">Keine Zeilen erkannt.</div>';
  $('#apply-import').disabled=!rows.some(r=>r.status==='matched');return rows;
}

async function previewDeckImport(){
  const box=$('#deck-import-preview');box.classList.add('visible');
  const rows=await post(`/api/decks/${state.deckId}/import/preview`,{text:$('#deck-import-text').value});
  box.innerHTML=rows.length?rows.map(r=>`<div class="import-row"><span>${r.line}</span><div><b>${r.match?escapeHtml(r.match.canonical_name):escapeHtml(r.original)}</b><small>${r.quantity}× · ${r.match?escapeHtml(r.match.set_name):escapeHtml(r.message||'Kein Treffer')}${r.alt_printings?` · +${r.alt_printings} weitere Drucke`:''}</small></div><span class="import-status ${r.status}">${r.status==='matched'?'Gefunden':r.status==='ambiguous'?'Prüfen':'Fehlt'}</span></div>`).join(''):'<div class="empty-state">Keine Zeilen erkannt.</div>';
  $('#deck-apply-import').disabled=!rows.some(r=>r.status==='matched');return rows;
}

function setImportMode(mode){
  importMode=mode;
  $$('.import-mode-toggle button').forEach(b=>b.classList.toggle('active',b.dataset.importMode===mode));
  $('#import-text-fields').classList.toggle('hidden',mode!=='text');
  $('#import-json-fields').classList.toggle('hidden',mode!=='json');
  $('#import-preview').classList.remove('visible');$('#import-preview').innerHTML='';
  $('#apply-import').disabled=true;
}
function setIeMode(mode){
  $$('.importexport-toggle button').forEach(b=>b.classList.toggle('active',b.dataset.ieMode===mode));
  $('#importexport-import-section').classList.toggle('hidden',mode!=='import');
  $('#importexport-export-section').classList.toggle('hidden',mode!=='export');
  $('#importexport-title').textContent=mode==='import'?'Listenimport':'Sammlung exportieren';
  $('#importexport-eyebrow').textContent=mode==='import'?'SAMMLUNG ERWEITERN':'DATENHOHEIT';
}

async function handleImportJsonFile(file){
  const nameLabel=$('#import-json-filename');
  importJsonExtras={decks:[],watchlists:[],trade_sheets:[]};
  if(!file){importJsonData=null;nameLabel.textContent='';return}
  try{
    const parsed=JSON.parse(await file.text());
    importJsonData=Array.isArray(parsed)?parsed:(parsed.collection||[]);
    if(!Array.isArray(parsed))importJsonExtras={decks:parsed.decks||[],watchlists:(parsed.watchlists||[]).filter(list=>list.entries?.length),trade_sheets:(parsed.trade_sheets||[]).filter(sheet=>sheet.cards?.length)};
    // Older backups carry the former fixed sale list among the watchlists; the server restores it as a sheet.
    const saleLists=importJsonExtras.watchlists.filter(list=>'is_sale_list' in list&&(list.is_sale_list||(!list.is_default&&list.name==='Verkaufsliste'))).length,watchCount=importJsonExtras.watchlists.length-saleLists,sheetCount=importJsonExtras.trade_sheets.length+saleLists;
    const extras=[importJsonExtras.decks.length&&`${importJsonExtras.decks.length} Decks`,watchCount&&`${watchCount} Watchlists`,sheetCount&&`${sheetCount} Sheets`].filter(Boolean);
    nameLabel.textContent=`${file.name} · ${importJsonData.length} Einträge${extras.length?` · ${extras.join(' · ')}`:''}`;
  }catch(error){
    importJsonData=null;nameLabel.textContent='';
    toast('Die Datei ist kein gültiges JSON-Backup.');
  }
}
