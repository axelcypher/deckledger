// The heart's list menu: holding a heart (or right-clicking it) opens the card's watchlists to put
// it on any of them, or on a new one. A short tap keeps doing what it always did. Works for every
// heart on the page -- tiles, the card dialog -- without their own code knowing about it.

const WATCH_MENU_HOLD_MS=450;
const watchMenu={node:null,button:null,variantId:null,identityId:null,timer:null,start:null,suppressClick:false,changed:false};

// Which card a heart belongs to: the tile it sits on, else the card dialog it is part of.
function heartCard(button){
  const tile=button.closest('.card-tile');
  if(tile)return {variantId:tile.dataset.variant,identityId:tile.dataset.identity};
  if(button.closest('#card-dialog')&&state.modalVariant)return {variantId:state.modalVariant.id,identityId:state.modalCard?.id};
  return null;
}

function paintHeart(button,active){
  button.classList.toggle('active',active);
  button.setAttribute('aria-pressed',String(active));
  button.setAttribute('aria-label',active?'Von der Watchlist entfernen':'Zur Watchlist hinzufügen');
  const icon=$('svg',button);
  if(icon)icon.outerHTML=watchlistIcon(active);else button.innerHTML=watchlistIcon(active);
}

// Everything that shows whether this card is watched: grid tiles, the open dialog, the counters.
function applyWatchState(variantId,identityId,active){
  if(identityId)syncWatchlistIcon(identityId,variantId,active);
  if(state.modalVariant?.id===variantId){
    state.modalVariant.watchlisted=active;
    $$('#card-dialog .watchlist-action').forEach(button=>paintHeart(button,active));
  }
  refreshWatchCount();
}

function closeWatchMenu(){
  clearTimeout(watchMenu.timer);
  if(!watchMenu.node)return;
  watchMenu.node.remove();watchMenu.node=null;
  watchMenu.button?.classList.remove('watch-menu-open');
  // The watchlist page lists the cards themselves; it is redrawn once, when the menu closes.
  if(watchMenu.changed&&state.route==='watchlist')renderWatchlist(true);
  watchMenu.changed=false;watchMenu.button=null;
}

function placeWatchMenu(){
  const menu=watchMenu.node,button=watchMenu.button;if(!menu||!button)return;
  const anchor=button.getBoundingClientRect(),box=menu.getBoundingClientRect(),gap=8;
  let left=Math.min(innerWidth-box.width-gap,Math.max(gap,anchor.left+anchor.width/2-box.width/2));
  let top=anchor.bottom+gap;
  if(top+box.height>innerHeight-gap)top=Math.max(gap,anchor.top-box.height-gap);
  menu.style.left=`${left}px`;menu.style.top=`${top}px`;
}

async function openWatchMenu(button){
  const target=heartCard(button);if(!target)return;
  closeWatchMenu();
  Object.assign(watchMenu,{button,...target,changed:false});
  button.classList.add('watch-menu-open');
  const menu=watchMenu.node=document.createElement('div');
  menu.className='watch-menu';menu.setAttribute('role','menu');menu.setAttribute('aria-label','Auf Watchlist speichern');
  menu.innerHTML='<div class="watch-menu-title">Auf Watchlist speichern</div><div class="watch-menu-loading">Listen werden geladen …</div>';
  document.body.append(menu);placeWatchMenu();
  let data;
  try{data=await api(`/api/watchlists/membership?variant_id=${encodeURIComponent(target.variantId)}`)}
  catch(error){if(watchMenu.node===menu){closeWatchMenu();toast(error.message)}return}
  if(watchMenu.node!==menu)return;
  renderWatchMenu(data);
}

function renderWatchMenu(data){
  const menu=watchMenu.node;if(!menu)return;
  menu.innerHTML=`<div class="watch-menu-title">Auf Watchlist speichern</div>
    <div class="watch-menu-lists">${data.lists.map(list=>`<button type="button" role="menuitemcheckbox" aria-checked="${list.contains}" class="watch-menu-item ${list.contains?'is-on':''}" data-watch-menu-list="${list.id}"><i aria-hidden="true">${list.contains?'✓':''}</i><span>${escapeHtml(list.name)}</span><small>${list.count}</small></button>`).join('')}</div>
    <form class="watch-menu-new" id="watch-menu-new"><input id="watch-menu-new-name" maxlength="80" placeholder="Neue Watchlist …" aria-label="Name der neuen Watchlist" autocomplete="off"><button type="submit" class="secondary-button" aria-label="Watchlist anlegen">＋</button></form>`;
  placeWatchMenu();
  $$('[data-watch-menu-list]',menu).forEach(item=>item.onclick=async()=>{
    const list=data.lists.find(entry=>entry.id===Number(item.dataset.watchMenuList)),wanted=!list.contains;
    item.disabled=true;
    try{
      const result=await post('/api/watchlist',{variant_id:watchMenu.variantId,list_id:list.id,active:wanted});
      list.contains=result.active;list.count+=result.active?1:-1;watchMenu.changed=true;
      applyWatchState(watchMenu.variantId,watchMenu.identityId,result.watchlisted);
      toast(result.active?`Auf „${list.name}“ gespeichert`:`Von „${list.name}“ entfernt`,null,null,'watch-menu');
      renderWatchMenu(data);
    }catch(error){item.disabled=false;toast(error.message)}
  });
  $('#watch-menu-new',menu).onsubmit=async event=>{
    event.preventDefault();
    const name=$('#watch-menu-new-name',menu).value.trim();if(!name)return;
    try{
      const created=await post('/api/watchlists',{game_id:data.game_id,name,variant_id:watchMenu.variantId});
      data.lists.push({id:created.id,name:created.name,is_default:0,count:1,contains:true});watchMenu.changed=true;
      if(state.activeWatchlists&&data.game_id===state.activeGameId)state.activeWatchlists=[...state.activeWatchlists,{...created,is_default:0}];
      applyWatchState(watchMenu.variantId,watchMenu.identityId,true);
      toast(`Watchlist „${created.name}“ angelegt`,null,null,'watch-menu');
      renderWatchMenu(data);
    }catch(error){toast(error.message)}
  };
}

// Holding: a press that stays put for a moment opens the menu, and the click that follows the
// release is swallowed so the heart does not also toggle.
document.addEventListener('pointerdown',event=>{
  if(watchMenu.node&&!event.target.closest('.watch-menu'))closeWatchMenu();
  const button=event.target.closest('.watchlist-action');
  if(!button||event.button>0)return;
  clearTimeout(watchMenu.timer);
  watchMenu.start={x:event.clientX,y:event.clientY};
  watchMenu.timer=setTimeout(()=>{
    watchMenu.suppressClick=true;
    setTimeout(()=>{watchMenu.suppressClick=false},800);
    navigator.vibrate?.(12);
    openWatchMenu(button);
  },WATCH_MENU_HOLD_MS);
});
document.addEventListener('pointermove',event=>{
  if(watchMenu.start&&Math.hypot(event.clientX-watchMenu.start.x,event.clientY-watchMenu.start.y)>10){clearTimeout(watchMenu.timer);watchMenu.start=null}
});
['pointerup','pointercancel'].forEach(type=>document.addEventListener(type,()=>{clearTimeout(watchMenu.timer);watchMenu.start=null}));
document.addEventListener('click',event=>{
  if(!watchMenu.suppressClick||!event.target.closest('.watchlist-action'))return;
  watchMenu.suppressClick=false;event.preventDefault();event.stopPropagation();
},true);
// Right-click on a desktop; touch browsers fire it on a long press as well.
document.addEventListener('contextmenu',event=>{
  const button=event.target.closest('.watchlist-action');if(!button)return;
  event.preventDefault();
  if(watchMenu.button!==button||!watchMenu.node){clearTimeout(watchMenu.timer);watchMenu.suppressClick=true;setTimeout(()=>{watchMenu.suppressClick=false},800);openWatchMenu(button)}
});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&watchMenu.node){event.stopPropagation();closeWatchMenu()}},true);
addEventListener('resize',closeWatchMenu);
addEventListener('scroll',event=>{if(watchMenu.node&&!watchMenu.node.contains(event.target))closeWatchMenu()},true);
