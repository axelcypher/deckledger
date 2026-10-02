// Changes made without a connection: the outbox and sending it later.

// ---- Offline outbox -------------------------------------------------------
// Only delta-based collection mutations are safe to queue offline: two
// devices independently collecting +1/-1 while disconnected can always be
// replayed in any order without clobbering each other. Absolute-value writes
// (price overrides, "set to X") don't have that property, so those are
// blocked outright while offline instead of silently risking a stomped value
// once reconnected -- see changeCollectionEntry below.
const OFFLINE_DB_NAME='deckledger-offline', OFFLINE_DB_VERSION=1, OFFLINE_STORE='outbox';
function openOfflineDb(){
  return new Promise((resolve,reject)=>{
    const req=indexedDB.open(OFFLINE_DB_NAME,OFFLINE_DB_VERSION);
    req.onupgradeneeded=()=>{req.result.createObjectStore(OFFLINE_STORE,{keyPath:'id',autoIncrement:true})};
    req.onsuccess=()=>resolve(req.result);
    req.onerror=()=>reject(req.error);
  });
}
async function queueOfflineMutation(payload){
  const db=await openOfflineDb();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(OFFLINE_STORE,'readwrite');
    // userId: the outbox lives in the browser, not in an account -- a change queued by one
    // user must never be sent under whoever signs in on this device next.
    tx.objectStore(OFFLINE_STORE).add({payload,createdAt:Date.now(),userId:state.boot?.user?.id??null});
    tx.oncomplete=()=>resolve(); tx.onerror=()=>reject(tx.error);
  });
}
async function listOfflineMutations(){
  const db=await openOfflineDb();
  return new Promise((resolve,reject)=>{
    const req=db.transaction(OFFLINE_STORE,'readonly').objectStore(OFFLINE_STORE).getAll();
    req.onsuccess=()=>resolve(req.result); req.onerror=()=>reject(req.error);
  });
}
async function removeOfflineMutation(id){
  const db=await openOfflineDb();
  return new Promise((resolve,reject)=>{
    const tx=db.transaction(OFFLINE_STORE,'readwrite');
    tx.objectStore(OFFLINE_STORE).delete(id);
    tx.oncomplete=()=>resolve(); tx.onerror=()=>reject(tx.error);
  });
}
async function updateOfflineIndicator(){
  const el=$('#offline-indicator'); if(!el)return;
  let count=0; try{count=(await listOfflineMutations()).filter(item=>item.userId==null||item.userId===state.boot?.user?.id).length}catch{}
  if(!navigator.onLine){
    el.classList.remove('hidden');
    el.textContent=count>0?`Offline · ${count} ausstehende Änderung${count===1?'':'en'}`:'Offline';
  }else if(serverUnreachable){
    el.classList.remove('hidden');
    el.textContent=`Server nicht erreichbar · gespeicherter Stand${count>0?` · ${count} ausstehende Änderung${count===1?'':'en'}`:''}`;
  }else if(count>0){
    el.classList.remove('hidden');
    el.textContent=`Wird synchronisiert … (${count})`;
  }else{
    el.classList.add('hidden');
  }
}
let offlineSyncInProgress=false;
async function syncOfflineQueue(){
  if(offlineSyncInProgress||!navigator.onLine)return;
  offlineSyncInProgress=true;
  let hadItems=false,signedOut=false;
  try{
    const items=await listOfflineMutations();
    // Runs on every page load. With nothing queued there is nothing to announce and nothing to
    // reload -- refreshing the view here fetched everything on the page a second time.
    hadItems=items.length>0;
    for(const item of items){
      if(item.userId!=null&&item.userId!==state.boot?.user?.id)continue; // someone else's, kept for them
      try{
        await post('/api/collection',item.payload);
        await removeOfflineMutation(item.id);
      }catch(error){
        // Still unreachable, or the session ran out while offline: keep this and the rest queued.
        // They are sent after the next sign-in instead of being thrown away.
        signedOut=Boolean(error.isAuthError);
        if(error.isNetworkError||error.isAuthError)break;
        // a real server error (e.g. the variant no longer exists) -- this one can never succeed as-is,
        // so drop it and tell the user rather than blocking every queued change behind it forever
        await removeOfflineMutation(item.id);
        toast(`Offline-Änderung verworfen: ${error.message||'unbekannter Fehler'}`);
      }
    }
  }finally{
    offlineSyncInProgress=false;
    await updateOfflineIndicator();
    if(hadItems&&!signedOut){ // signed out: the page is already on its way to the login form
      if(!(await listOfflineMutations()).some(item=>item.userId==null||item.userId===state.boot?.user?.id))toast('Offline-Änderungen synchronisiert');
      await refreshCurrentView();
      if(state.modalCard)await openCard(state.modalCard.id,state.modalVariant?.id,true);
    }
  }
}
window.addEventListener('online',()=>{updateOfflineIndicator();syncOfflineQueue()});
window.addEventListener('offline',updateOfflineIndicator);

// ---- Saved for offline use ------------------------------------------------------------------
// The service worker keeps what was looked at, within limits, and drops the oldest entries. That
// is no promise that a whole collection is there when the connection is not. Saving for offline
// use fetches it deliberately -- the views' data, every owned and watched card's details and
// image -- into a cache of its own that is never trimmed. The service worker falls back to it.
const OFFLINE_SAVE_CACHE='deckledger-offline-v1', OFFLINE_SAVE_MARKER='/offline-save/info', OFFLINE_SAVE_PARALLEL=6;
let offlineSave=null;   // {done,total,controller} while a save is running

async function offlineSaveInfo(){
  if(!('caches' in window))return null;
  try{const marker=await (await caches.open(OFFLINE_SAVE_CACHE)).match(OFFLINE_SAVE_MARKER);return marker?await marker.json():null}catch{return null}
}
async function clearOfflineSave(){if('caches' in window)await caches.delete(OFFLINE_SAVE_CACHE)}
// The cache belongs to whoever saved it. Another account signing in on this browser must not be
// shown the previous one's collection when the server cannot be reached.
async function dropForeignOfflineSave(){
  const info=await offlineSaveInfo();
  if(info&&info.userId!==state.boot.user.id)await clearOfflineSave();
}

// Everything the offline views ask for, as lists of URLs: the data first (it names the cards),
// then one details request and the images per card.
async function saveForOffline({fullImages=false,onProgress=()=>{}}={}){
  if(offlineSave)return null;
  const controller=new AbortController(),cache=await caches.open(OFFLINE_SAVE_CACHE),saved=new Set();
  const progress=offlineSave={done:0,total:0,controller};
  const report=()=>onProgress(progress.done,progress.total);
  const keep=async(url,image=false)=>{
    const response=await fetch(url,{signal:controller.signal,headers:image?{}:{'Content-Type':'application/json'}});
    // An answer the service worker took from its own cache because the server did not respond
    // is not a fresh copy, and the stand-in for an image that could not be fetched is no image.
    if(!response.ok||response.headers.get('X-DeckLedger-Stale')==='1')throw new Error(`${url} (${response.status})`);
    if(image&&response.headers.get('X-Image-Source')==='placeholder')throw new Error(`${url} (no image)`);
    await cache.put(url,response.clone());
    saved.add(new URL(url,location.origin).href);
    return image?null:response.json();
  };
  const data=async url=>{progress.total++;report();const result=await keep(url);progress.done++;report();return result};
  let skipped=0;
  try{
    const boot=await data('/api/bootstrap');
    await data('/api/home-banner');
    const identities=new Set(),variants=new Set();
    const collect=cards=>cards.forEach(card=>{identities.add(card.identity_id);variants.add(card.variant_id)});
    for(const game of boot.games){
      const id=encodeURIComponent(game.id);
      await Promise.all([`/api/games/${id}/sets`,`/api/games/${id}/formats`,`/api/decks?game_id=${id}`,`/api/trade-sheets?game_id=${id}`,`/api/home-recent?game_id=${id}`].map(data));
      collect((await data(collectionUrl(game.id,defaultCollectionFilters(game.id)))).cards);
      for(const list of await data(`/api/watchlists?game_id=${game.id}`))collect((await data(watchlistCardsUrl(list.id,defaultWatchFilters(game.id)))).cards);
    }
    const queue=[...[...identities].map(identity=>[`/api/cards/${identity}`,false]),...[...variants].flatMap(variant=>[[artUrl(variant),true],...(fullImages?[[artUrl(variant,'full'),true]]:[])])];
    progress.total+=queue.length;report();
    // A card whose image or details cannot be fetched right now is skipped, not a reason to stop.
    const worker=async()=>{
      for(let item;(item=queue.shift());){
        try{await keep(item[0],item[1])}catch(error){if(controller.signal.aborted)throw error;skipped++}
        progress.done++;report();
      }
    };
    await Promise.all(Array.from({length:OFFLINE_SAVE_PARALLEL},worker));
    // What an earlier save stored and this one no longer needs (cards that left the collection).
    for(const request of await cache.keys())if(!saved.has(request.url))await cache.delete(request);
    const info={userId:boot.user.id,savedAt:new Date().toISOString(),cards:variants.size,files:saved.size,skipped,fullImages};
    await cache.put(OFFLINE_SAVE_MARKER,new Response(JSON.stringify(info),{headers:{'Content-Type':'application/json'}}));
    return info;
  }finally{offlineSave=null}
}
function cancelOfflineSave(){offlineSave?.controller.abort()}
