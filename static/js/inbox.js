// What happens on the posts a sheet was published as: the inbox of new comments, and the list
// of posts linked to a sheet. The server reads the posts (deckledger/watcher.py); nothing here
// talks to Reddit.

const INBOX_REFRESH_MS=180000;

function setInboxCount(count){
  if(state.boot)state.boot.inbox_new=count;
  $$('[data-inbox-count]').forEach(badge=>{badge.textContent=count;badge.classList.toggle('hidden',!count)});
}
async function refreshInboxCount(){
  try{setInboxCount((await api('/api/inbox')).new)}catch{/* the badge is a convenience; the next round tries again */}
}

function timeAgo(iso){
  const then=Date.parse(iso);if(!then)return '';
  const minutes=Math.max(0,Math.round((Date.now()-then)/60000));
  if(minutes<1)return 'gerade eben';
  if(minutes<60)return `vor ${minutes} Min.`;
  if(minutes<1440)return `vor ${Math.round(minutes/60)} Std.`;
  return `vor ${Math.round(minutes/1440)} Tg.`;
}
// Links come out of a feed; only a plain https address is turned into something clickable.
const safeLink=url=>/^https:\/\/[^\s"'<>]+$/.test(String(url||''))?url:'';

function inboxItemHtml(item){
  const link=safeLink(item.url)||safeLink(item.post_url);
  const matches=item.matches.map(match=>`<span class="inbox-match ${match.certain?'':'maybe'}" title="${match.certain?'Im Kommentar genannt':'Vielleicht gemeint – der Name passt auf mehrere Karten'}">${escapeHtml(match.name)}${match.finish&&match.finish!=='Normal'?` · ${escapeHtml(match.finish)}`:''}${match.certain?'':' ?'}</span>`).join('');
  return `<article class="inbox-item" data-inbox-item="${item.id}">
    <header><b>u/${escapeHtml(item.author)}</b><span>${escapeHtml(timeAgo(item.posted_at))}${item.community?` · r/${escapeHtml(item.community)}`:''}</span>
      <button type="button" class="inbox-sheet" data-inbox-sheet="${item.sheet_id}" title="Sheet öffnen">${escapeHtml(item.sheet_name)}</button></header>
    <p>${escapeHtml(item.body)}</p>
    ${matches?`<div class="inbox-matches">${matches}</div>`:''}
    <footer>${link?`<a class="secondary-button" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Auf Reddit öffnen ↗</a>`:''}<button type="button" class="secondary-button" data-inbox-done="${item.id}">Erledigt</button></footer>
  </article>`;
}

// The inbox above the list of sheets: new comments of the active game's posts, newest first.
async function renderInbox(){
  const box=$('#sheet-inbox');if(!box)return;
  let data;
  try{data=await api(`/api/inbox?game_id=${encodeURIComponent(state.activeGameId)}`)}catch{return}
  if(!$('#sheet-inbox'))return;
  setInboxCount(data.new);
  if(!data.items.length){box.innerHTML='';box.classList.add('hidden');return}
  box.classList.remove('hidden');
  box.innerHTML=`<div class="sheet-section-head"><b>Eingang</b><span>${data.items.length} neue${data.items.length===1?'r Kommentar':' Kommentare'}</span><button type="button" class="secondary-button" id="inbox-all-done">Alle erledigt</button></div>
    <div class="inbox-list">${data.items.map(inboxItemHtml).join('')}</div>`;
  $$('[data-inbox-done]',box).forEach(button=>button.onclick=async()=>{
    try{await api(`/api/inbox/${button.dataset.inboxDone}`,{method:'PATCH',body:JSON.stringify({state:'done'})});renderInbox()}catch(error){toast(error.message)}
  });
  $$('[data-inbox-sheet]',box).forEach(button=>button.onclick=()=>{state.sheetId=Number(button.dataset.inboxSheet);renderSheets()});
  $('#inbox-all-done').onclick=async()=>{try{await post('/api/inbox/done',{});renderInbox()}catch(error){toast(error.message)}};
}

// The posts a sheet was published as, in the sheet editor.
async function renderSheetPosts(){
  const box=$('#sheet-posts'),sheetId=state.sheetId;if(!box||!sheetId)return;
  let posts;
  try{posts=await api(`/api/trade-sheets/${sheetId}/posts`)}catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(!$('#sheet-posts')||state.sheetId!==sheetId)return;
  const named=Boolean(state.boot.settings?.redditUsername);
  box.innerHTML=`<div class="sheet-section-head"><b>Posts zu diesem Sheet</b><span>Neue Kommentare landen im Eingang.</span></div>
    <form class="sheet-post-form" id="sheet-post-form"><input id="sheet-post-url" type="url" inputmode="url" autocomplete="off" placeholder="Link zum Reddit-Post einfügen"><button class="secondary-button" type="submit">Verknüpfen</button></form>
    ${posts.length&&!named?`<p class="muted sheet-post-hint">Trage in den Einstellungen deinen Reddit-Namen ein, sonst zählen deine eigenen Antworten als neue Kommentare.</p>`:''}
    <div class="sheet-post-list">${posts.map(item=>{
      const link=safeLink(item.url),watching=item.status==='watching';
      return `<div class="sheet-post ${watching?'':'is-done'}" data-post="${item.id}">
        <div class="sheet-post-copy"><b>${link?`<a href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title||item.url)}</a>`:escapeHtml(item.title||item.url)}</b>
          <small>${item.community?`r/${escapeHtml(item.community)} · `:''}${watching?'wird beobachtet':'beendet'}${item.last_checked_at?` · geprüft ${escapeHtml(timeAgo(item.last_checked_at))}`:' · noch nicht geprüft'}${item.new?` · <span class="sheet-post-new">${item.new} neu</span>`:''}</small>
          ${item.last_error?`<small class="sheet-warning">${escapeHtml(item.last_error)}</small>`:''}</div>
        <button type="button" class="secondary-button" data-post-check="${item.id}">Jetzt prüfen</button>
        <button type="button" class="secondary-button" data-post-status="${watching?'done':'watching'}" data-post-id="${item.id}">${watching?'Beenden':'Wieder beobachten'}</button>
        <button type="button" class="sheet-remove" data-post-remove="${item.id}" title="Verknüpfung entfernen" aria-label="Verknüpfung entfernen">×</button>
      </div>`}).join('')}</div>`;
  $('#sheet-post-form').onsubmit=async event=>{
    event.preventDefault();
    try{await post(`/api/trade-sheets/${sheetId}/posts`,{url:$('#sheet-post-url').value});toast('Post verknüpft');renderSheetPosts()}catch(error){toast(error.message)}
  };
  $$('[data-post-check]',box).forEach(button=>button.onclick=async()=>{
    button.disabled=true;
    try{const result=await post(`/api/sheet-posts/${button.dataset.postCheck}/check`,{});toast(result.new?`${result.new} neue${result.new===1?'r Kommentar':' Kommentare'}`:'Keine neuen Kommentare');refreshInboxCount()}
    catch(error){toast(error.message)}
    renderSheetPosts();
  });
  $$('[data-post-status]',box).forEach(button=>button.onclick=async()=>{
    try{await api(`/api/sheet-posts/${button.dataset.postId}`,{method:'PATCH',body:JSON.stringify({status:button.dataset.postStatus})});renderSheetPosts()}catch(error){toast(error.message)}
  });
  $$('[data-post-remove]',box).forEach(button=>button.onclick=async()=>{
    if(!confirm('Verknüpfung entfernen? Die Kommentare dieses Posts verschwinden aus dem Eingang.'))return;
    try{await api(`/api/sheet-posts/${button.dataset.postRemove}`,{method:'DELETE'});refreshInboxCount();renderSheetPosts()}catch(error){toast(error.message)}
  });
}
