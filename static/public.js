// The public profile (templates/public.html, deckledger/public_profile.py): a shared collection
// without signing in. Its own small script -- the app's scripts expect a signed-in user. The
// address says what is shown: #<game> for its sets, #<game>/<set> for the cards of a set.

const view=document.getElementById('content');
const slug=view.dataset.slug;
const base=`/u/${encodeURIComponent(slug)}`;
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number=value=>Number(value||0).toLocaleString('de-DE');
const percent=(part,whole)=>whole?Math.round(part/whole*100):0;
const cache=new Map();
let profile=null,cardMode='all';

async function load(url){
  if(!cache.has(url))cache.set(url,fetch(url).then(async response=>{
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||`Fehler ${response.status}`);
    return data;
  }).catch(error=>{cache.delete(url);throw error}));
  return cache.get(url);
}
const go=hash=>{if(location.hash!==hash)location.hash=hash;else render()};

function head(activeGame){
  const game=profile.games.find(item=>item.id===activeGame);
  return `<section class="public-head">
      <div><span class="eyebrow">ÖFFENTLICHE SAMMLUNG</span><h1>${esc(profile.name)}</h1>
        ${game?`<p>${number(game.owned)} von ${number(game.total)} Karten aus ${esc(game.name)} · ${number(game.copies)} Exemplare</p>`:''}</div>
    </section>
    ${profile.games.length>1?`<nav class="segmented public-games" aria-label="TCG">${profile.games.map(item=>`<button type="button" data-game="${esc(item.id)}" class="${item.id===activeGame?'active':''}"><img src="${base}/game-logo/${encodeURIComponent(item.id)}" alt="">${esc(item.short_name)}</button>`).join('')}</nav>`:''}`;
}

function bindHead(){
  view.querySelectorAll('[data-game]').forEach(button=>button.onclick=()=>go(`#${button.dataset.game}`));
}

async function renderGame(gameId){
  const sets=await load(`/api/public/${encodeURIComponent(slug)}/games/${encodeURIComponent(gameId)}/sets`);
  view.innerHTML=`${head(gameId)}
    <div class="toolbar public-toolbar"><h2>Sets</h2></div>
    ${sets.length?`<div class="set-grid public-set-grid">${sets.map(set=>`<article class="set-card" tabindex="0" role="button" data-set="${esc(set.id)}" style="--accent:${esc(set.accent)}">
      <div class="set-art"><img class="set-brand-logo set-brand-logo-${esc(set.game_id)}" loading="lazy" src="${base}/set-logo/${encodeURIComponent(set.id)}?v=${encodeURIComponent(set.visual_version||'')}" alt="${esc(set.name)} Logo"><span class="set-code">${esc(set.code)}</span><span class="set-kind">${esc(set.set_type)}</span></div>
      <div class="set-card-info"><h3>${esc(set.name)}</h3><div class="set-meta">${esc((set.release_dates||[set.release_date]).filter(Boolean).at(-1)||'')}${set.printed_card_count?` · ${set.printed_card_count} nummeriert`:''}</div>
        <div class="public-progress" style="--done:${set.base_completion}%"><i></i></div>
        <div class="set-completion"><span>KARTEN<b>${number(set.owned)} / ${number(set.total)}</b></span><span>BASE<b>${set.base_completion}%</b></span><span>MASTER<b>${set.master_completion}%</b></span></div></div></article>`).join('')}</div>`
      :'<div class="empty-state"><b>Keine Sets</b></div>'}`;
  bindHead();
  view.querySelectorAll('[data-set]').forEach(tile=>{
    tile.onclick=()=>go(`#${gameId}/${tile.dataset.set}`);
    tile.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();tile.click()}};
  });
}

function cardTile(card){
  const owned=card.variants.filter(variant=>variant.quantity>0&&variant.language===card.language);
  const chips=card.variants.filter(variant=>variant.language===card.language).length>1&&owned.length
    ?`<div class="variant-chips">${owned.map(variant=>`<span class="variant-chip">${esc(variant.finish)} ×${variant.quantity}</span>`).join('')}</div>`:'';
  return `<article class="card-tile ${card.quantity?'owned':'missing'}">
    <div class="card-image-wrap"><img loading="lazy" decoding="async" src="${base}/art/${encodeURIComponent(card.variant_id)}?size=thumb" alt="${esc(card.canonical_name)}">
      ${card.quantity?`<span class="owned-pill" title="${card.quantity} im Besitz">×${card.quantity}</span>`:''}</div>
    <div class="card-info"><b>${esc(card.canonical_name)}</b><div class="card-subline"><span>${esc(card.collector_number)} · ${esc(card.rarity)} · ${esc(card.language)}</span></div>${chips}</div></article>`;
}

async function renderSet(gameId,setId){
  const data=await load(`/api/public/${encodeURIComponent(slug)}/sets/${encodeURIComponent(setId)}/cards`);
  const shown=data.cards.filter(card=>cardMode==='all'||(cardMode==='owned')===(card.quantity>0));
  const stats=data.stats;
  view.innerHTML=`${head(gameId)}
    <section class="public-set-head">
      <button type="button" class="secondary-button public-back" data-back>← Sets</button>
      <div><h2><span>${esc(data.set.code)}</span> ${esc(data.set.name)}</h2>
        <p>${number(stats.owned)} von ${number(stats.total)} Karten · ${stats.base}% · Master ${stats.master}%</p></div>
      <div class="deal-filter-chips public-filter">${[['all','Alle'],['owned','Im Besitz'],['missing','Fehlt']].map(([id,label])=>`<button type="button" class="community-chip ${cardMode===id?'active':''}" data-mode="${id}">${label}</button>`).join('')}</div>
    </section>
    <section class="card-grid public-card-grid">${shown.map(cardTile).join('')||'<div class="empty-state"><b>Keine Karten in dieser Ansicht</b></div>'}</section>`;
  bindHead();
  view.querySelector('[data-back]').onclick=()=>go(`#${gameId}`);
  view.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{cardMode=button.dataset.mode;render()});
}

async function render(){
  try{
    profile??=await load(`/api/public/${encodeURIComponent(slug)}`);
    const [gameId,setId]=decodeURIComponent(location.hash.slice(1)).split('/');
    const game=profile.games.find(item=>item.id===gameId)||profile.games[0];
    if(setId&&game.id===gameId)await renderSet(game.id,setId);
    else await renderGame(game.id);
  }catch(error){
    view.innerHTML=`<div class="empty-state public-missing"><b>Die Sammlung lässt sich gerade nicht laden</b><span>${esc(error.message)}</span></div>`;
  }
}

let lastSet='';
window.addEventListener('hashchange',()=>{
  const setPart=location.hash.split('/')[1]||'';
  if(setPart!==lastSet)cardMode='all';
  lastSet=setPart;
  render().then(()=>scrollTo(0,0));
});
render();
