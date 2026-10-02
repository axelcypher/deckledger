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
