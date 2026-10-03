// What happens around a sheet: the inbox of new comments on its posts and of other people's
// posts that name its cards, the posts linked to a sheet, and the communities that are searched.
// The server does the reading (deckledger/watcher.py); nothing here talks to Reddit.

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

// Which side of a post a card stands on (see watcher.py post_sides).
const MATCH_SIDES={wants:'gesucht',has:'angeboten',mentions:'erwähnt'};

function inboxItemHtml(item){
  const link=safeLink(item.url)||safeLink(item.post_url),found=item.kind==='post';
  const matches=item.matches.map(match=>`<span class="inbox-match ${match.certain?'':'maybe'}" title="${match.certain?(found?'Im Post genannt':'Im Kommentar genannt'):'Vielleicht gemeint – der Name passt auf mehrere Karten'}">${escapeHtml(match.name)}${match.finish&&match.finish!=='Normal'?` · ${escapeHtml(match.finish)}`:''}${match.certain?'':' ?'}${MATCH_SIDES[match.side]?`<i>${MATCH_SIDES[match.side]}</i>`:''}</span>`).join('');
  return `<article class="inbox-item ${found?'is-find':''}" data-inbox-item="${item.id}" data-inbox-kind="${escapeHtml(item.kind)}">
    <header>${found?'<em class="inbox-kind">Fund</em>':''}<b>u/${escapeHtml(item.author)}</b><span>${escapeHtml(timeAgo(item.posted_at))}${item.community?` · r/${escapeHtml(item.community)}`:''}</span>
      <button type="button" class="inbox-sheet" data-inbox-sheet="${item.sheet_id}" title="Sheet öffnen">${escapeHtml(item.sheet_name)}</button></header>
    ${found?`<h4>${escapeHtml(item.title)}</h4>`:''}
    <p>${escapeHtml(item.body)}</p>
    ${matches?`<div class="inbox-matches">${matches}</div>`:''}
    <footer>${link?`<a class="secondary-button" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Auf Reddit öffnen ↗</a>`:''}<button type="button" class="secondary-button" data-inbox-deal="${item.id}">Vorgang anlegen</button><button type="button" class="secondary-button" data-inbox-done="${item.id}">Erledigt</button></footer>
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
  const comments=data.items.filter(item=>item.kind!=='post').length,found=data.items.length-comments;
  const summary=[comments?`${comments} neue${comments===1?'r Kommentar':' Kommentare'}`:'',found?`${found} ${found===1?'Fund':'Funde'} in fremden Posts`:''].filter(Boolean).join(' · ');
  box.innerHTML=`<div class="sheet-section-head"><b>Eingang</b><span>${summary}</span><button type="button" class="secondary-button" id="inbox-all-done">Alle erledigt</button></div>
    <div class="inbox-list">${data.items.map(inboxItemHtml).join('')}</div>`;
  $$('[data-inbox-done]',box).forEach(button=>button.onclick=async()=>{
    try{await api(`/api/inbox/${button.dataset.inboxDone}`,{method:'PATCH',body:JSON.stringify({state:'done'})});renderInbox()}catch(error){toast(error.message)}
  });
  $$('[data-inbox-deal]',box).forEach(button=>button.onclick=()=>dealFromInbox(data.items.find(item=>item.id===Number(button.dataset.inboxDeal))));
  $$('[data-inbox-sheet]',box).forEach(button=>button.onclick=()=>{state.sheetId=Number(button.dataset.inboxSheet);renderSheets()});
  $('#inbox-all-done').onclick=async()=>{try{await post('/api/inbox/done',{});renderInbox()}catch(error){toast(error.message)}};
}

// The posts a sheet was published as, in the sheet editor.
async function renderSheetPosts(){
  const box=$('#sheet-posts'),sheetId=state.sheetId;if(!box||!sheetId)return;
  let posts;
  try{posts=await api(`/api/trade-sheets/${sheetId}/posts`)}catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(!$('#sheet-posts')||state.sheetId!==sheetId)return;
  box.innerHTML=`<div class="sheet-section-head"><b>Posts zu diesem Sheet</b><span>Neue Kommentare landen im Eingang – einmal, auch wenn der Post mit mehreren Sheets verknüpft ist.</span></div>
    <form class="sheet-post-form" id="sheet-post-form"><input id="sheet-post-url" type="url" inputmode="url" autocomplete="off" placeholder="Link zum Reddit-Post einfügen"><button class="secondary-button" type="submit">Verknüpfen</button></form>
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

// Whether other people's posts are searched for this sheet's cards, and in which of the game's
// communities. The list of communities itself is a setting of the account (renderCommunities).
async function renderSheetScout(){
  const box=$('#sheet-scout'),sheetId=state.sheetId,sheet=sheetView.payload?.sheet;if(!box||!sheet)return;
  let communities;
  try{communities=await api(`/api/communities?game_id=${encodeURIComponent(sheet.game_id)}`)}catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(!$('#sheet-scout')||state.sheetId!==sheetId)return;
  const chosen=String(sheet.scout_communities||'').split(',').filter(Boolean),on=Boolean(sheet.scout);
  const looksFor=sheetIsWanted(sheet.kind)?'Posts, in denen jemand eine dieser Karten anbietet':'Posts, in denen jemand eine dieser Karten sucht';
  const save=async change=>{
    try{sheetView.payload.sheet=(await api(`/api/trade-sheets/${sheetId}`,{method:'PATCH',body:JSON.stringify(change)})).sheet;renderSheetScout()}catch(error){toast(error.message)}
  };
  box.innerHTML=`<div class="sheet-section-head"><b>Fremde Posts</b><span>${communities.length?`${looksFor} landen im Eingang.`:'Noch keine Subreddits eingetragen.'}</span>
      ${communities.length?`<button type="button" class="switch" id="sheet-scout-switch" role="switch" aria-checked="${on}" aria-label="In fremden Posts suchen"><span></span></button>`:''}</div>
    ${communities.length
      ?`<div class="community-chips ${on?'':'is-off'}">${communities.map(item=>{const active=!chosen.length||chosen.includes(item.name);return `<button type="button" class="community-chip ${active?'active':''}" data-scout-community="${escapeHtml(item.name)}" aria-pressed="${active}" ${on?'':'disabled'}>r/${escapeHtml(item.name)}</button>`}).join('')}</div>`
      :`<p class="muted sheet-post-hint">Trage unter <button type="button" class="text-link" id="sheet-scout-settings">Einstellungen → Reddit</button> ein, in welchen Subreddits gesucht werden soll.</p>`}`;
  if($('#sheet-scout-settings'))$('#sheet-scout-settings').onclick=()=>routeTo('settings');
  if($('#sheet-scout-switch'))$('#sheet-scout-switch').onclick=()=>save({scout:!on});
  $$('[data-scout-community]',box).forEach(button=>button.onclick=()=>{
    // No choice stored means every community of the game, also those added later.
    const all=communities.map(item=>item.name),now=new Set(chosen.length?chosen:all),name=button.dataset.scoutCommunity;
    if(now.has(name))now.delete(name);else now.add(name);
    const picked=all.filter(item=>now.has(item));
    if(!picked.length){toast('Mindestens ein Subreddit – oder die Suche für dieses Sheet ausschalten.');return}
    save({scout_communities:picked.length===all.length?[]:picked});
  });
}

// The communities searched for the active game, in the settings.
async function renderCommunities(){
  const box=$('#reddit-communities'),gameId=state.activeGameId;if(!box)return;
  let communities;
  try{communities=await api(`/api/communities?game_id=${encodeURIComponent(gameId)}`)}catch(error){box.innerHTML=`<div class="deck-zone-empty">${escapeHtml(error.message)}</div>`;return}
  if(!$('#reddit-communities')||state.activeGameId!==gameId)return;
  const game=state.boot.games.find(item=>item.id===gameId);
  box.innerHTML=`<div class="settings-field"><span>Subreddits für ${escapeHtml(game?.short_name||gameId)}</span></div>
    <p class="muted sheet-post-hint">Hier sucht DeckLedger in neuen Posts nach den Karten deiner Sheets: für Angebote, wer sie sucht; für Gesuche, wer sie anbietet. Die Liste gilt für das oben gewählte TCG.</p>
    <div class="community-list">${communities.map(item=>`<div class="community-row" data-community="${item.id}">
        <div class="sheet-post-copy"><b>r/${escapeHtml(item.name)}</b><small>${item.last_checked_at?`geprüft ${escapeHtml(timeAgo(item.last_checked_at))}`:'noch nicht geprüft'}</small>${item.last_error?`<small class="sheet-warning">${escapeHtml(item.last_error)}</small>`:''}</div>
        <button type="button" class="secondary-button" data-community-check="${item.id}">Jetzt prüfen</button>
        <button type="button" class="sheet-remove" data-community-remove="${item.id}" title="r/${escapeHtml(item.name)} entfernen" aria-label="r/${escapeHtml(item.name)} entfernen">×</button>
      </div>`).join('')}</div>
    <form class="sheet-post-form" id="reddit-community-form"><input id="reddit-community-name" autocomplete="off" spellcheck="false" placeholder="Subreddit hinzufügen, z. B. r/vcardtrades"><button class="secondary-button" type="submit">Hinzufügen</button></form>`;
  $('#reddit-community-form').onsubmit=async event=>{
    event.preventDefault();
    try{const added=await post('/api/communities',{game_id:gameId,name:$('#reddit-community-name').value});toast(`r/${added.name} wird durchsucht`);renderCommunities()}catch(error){toast(error.message)}
  };
  $$('[data-community-check]',box).forEach(button=>button.onclick=async()=>{
    button.disabled=true;
    try{const result=await post(`/api/communities/${button.dataset.communityCheck}/check`,{});toast(result.new?`${result.new} ${result.new===1?'Fund':'Funde'}`:'Nichts Neues für deine Sheets');refreshInboxCount()}
    catch(error){toast(error.message)}
    renderCommunities();
  });
  $$('[data-community-remove]',box).forEach(button=>button.onclick=async()=>{
    try{await api(`/api/communities/${button.dataset.communityRemove}`,{method:'DELETE'});renderCommunities()}catch(error){toast(error.message)}
  });
}
