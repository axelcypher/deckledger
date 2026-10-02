// Browsing: a game's sets, a set's cards, all cards, card tiles and the progressive grid.

const SET_GROUP_ORDER=['Booster','Decks','Promos','Quests','Sammlungen','Produkte','Zubehör'];
function setGroup(set){
  const type=(set.set_type||'').toLowerCase();
  if(/booster|expansion/.test(type))return 'Booster';
  if(/starter|\bdecks?\b/.test(type))return 'Decks';
  if(/promo/.test(type))return 'Promos';
  if(/quest/.test(type))return 'Quests';
  if(/collection/.test(type))return 'Sammlungen';
  if(/product/.test(type))return 'Produkte';
  if(/accessory/.test(type))return 'Zubehör';
  return set.set_type||'Weitere Sets';
}
function groupedSets(sets){
  const visible=state.setType==='all'?sets:sets.filter(set=>setGroup(set)===state.setType);
  const groups=visible.reduce((all,set)=>{const group=setGroup(set);(all[group]??=[]).push(set);return all},{});
  const rank=group=>{const index=SET_GROUP_ORDER.indexOf(group);return index<0?SET_GROUP_ORDER.length:index};
  return Object.entries(groups).sort(([a],[b])=>rank(a)-rank(b)||a.localeCompare(b,'de')).map(([group,items])=>[
    group,
    items.sort((a,b)=>compareSetRelease(a,b,state.setDirection))
  ]);
}
function latestRelease(set){const values=(set.release_dates||[]).filter(Boolean);return values.length?values.sort().at(-1):set.release_date||null}
function compareSetRelease(a,b,direction='desc'){
  const ad=Date.parse(latestRelease(a))||0,bd=Date.parse(latestRelease(b))||0;
  if(Boolean(ad)!==Boolean(bd))return ad?-1:1;
  const factor=direction==='desc'?-1:1;
  if(ad!==bd)return (ad<bd?-1:1)*factor;
  const code=a.code.localeCompare(b.code,'de',{numeric:true,sensitivity:'base'});
  return code*factor;
}
function sortedSets(sets){
  const visible=state.setType==='all'?sets:sets.filter(set=>setGroup(set)===state.setType);
  const sorters={
    date:(a,b)=>compareSetRelease(a,b,state.setDirection),
    code:(a,b)=>a.code.localeCompare(b.code,'de',{numeric:true}),
    name:(a,b)=>a.name.localeCompare(b.name,'de'),
    value:(a,b)=>a.value-b.value,
    completion:(a,b)=>a.base_completion-b.base_completion,
  };
  const sorter=sorters[state.setSort]||sorters.date;
  return [...visible].sort((a,b)=>['type','date'].includes(state.setSort)?sorter(a,b):sorter(a,b)*(state.setDirection==='desc'?-1:1));
}

async function renderGame(gameId){
  const stale=renderGuard();
  content.innerHTML='<div class="page-loader"><span></span><p>Sets werden geladen …</p></div>';
  setActiveGame(gameId,false); const game=state.boot.games.find(g=>g.id===gameId); state.game=game;
  const sets=await api(`/api/games/${gameId}/sets`); game.sets=sets;
  if(stale())return;
  const completion=sets.length?Math.round(sets.reduce((a,s)=>a+s.base_completion,0)/sets.length):0;
  const groups=groupedSets(sets), flatSets=sortedSets(sets), availableGroups=[...new Set(sets.map(setGroup))].sort((a,b)=>{const ai=SET_GROUP_ORDER.indexOf(a),bi=SET_GROUP_ORDER.indexOf(b);return (ai<0?99:ai)-(bi<0?99:bi)||a.localeCompare(b,'de')});
  content.innerHTML=`
    <div class="breadcrumbs"><button data-route-back>Übersicht</button><span>›</span><b>${escapeHtml(game.short_name)}</b></div>
    <section class="game-banner" style="--accent:${game.accent}"><span class="game-banner-symbol"><img src="/game-logo/${game.id}" alt=""></span><div><h1>${escapeHtml(game.name)}</h1><p>${sets.length} Sets und Produkte · ${game.languages.join(' & ')}</p></div><div class="banner-value"><b>${money(game.value)}</b><span>SAMMLUNGSWERT · ${completion}% Ø FORTSCHRITT</span></div></section>
    <div class="toolbar set-overview-toolbar"><h2>Sets & Veröffentlichungen</h2><div class="toolbar-spacer"></div><button class="secondary-button all-cards-button" id="show-all-cards">Alle Karten</button><select id="set-type-filter" class="select-control" aria-label="Nach Set-Art filtern"><option value="all">Alle Set-Arten</option>${availableGroups.map(group=>`<option value="${escapeHtml(group)}" ${state.setType===group?'selected':''}>${escapeHtml(group)}</option>`).join('')}</select><select id="set-sort" class="select-control" aria-label="Sets sortieren"><option value="type">Art · gruppiert</option><option value="date">Erscheinungsdatum</option><option value="code">Setcode</option><option value="name">Name</option><option value="value">Sammlungswert</option><option value="completion">Fortschritt</option></select><select id="set-direction" class="select-control" aria-label="Sortierreihenfolge"><option value="desc">Neu → Alt · Nr. ↓</option><option value="asc">Alt → Neu · Nr. ↑</option></select><div class="zoom-control"><span>−</span><input id="set-zoom" type="range" min="2" max="4" value="${state.setZoom}"><span>＋</span></div></div>
    <div class="set-overview-groups">${state.setSort==='type'?(groups.length?groups.map(([group,items])=>setGroupSection(gameId,group,items)).join(''):'<div class="empty-state"><b>Keine Sets dieser Art</b><span>Wähle eine andere Set-Art.</span></div>'):(flatSets.length?`<div class="set-grid" style="--set-cols:${state.setZoom}">${flatSets.map(s=>setTile(s)).join('')}</div>`:'<div class="empty-state"><b>Keine Sets dieser Art</b><span>Wähle eine andere Set-Art.</span></div>')}</div>`;
  mountFilterPanel('sets',['.set-overview-toolbar'],'Sets filtern & sortieren');
  $('#set-sort').value=state.setSort;
  $('#set-direction').value=state.setDirection;
  $('[data-route-back]').onclick=()=>routeTo('dashboard');
  $('#show-all-cards').onclick=()=>routeTo('game-cards',gameId);
  $$('.set-card',content).forEach(el=>{
    el.tabIndex=0;el.setAttribute('role','button');
    el.onclick=()=>routeTo('set',el.dataset.set);
    el.onkeydown=event=>{if(event.target===el&&(event.key==='Enter'||event.key===' ')){event.preventDefault();el.click()}};
  });
  $$('[data-set-group-toggle]',content).forEach(button=>button.onclick=()=>{
    const section=button.closest('.set-group'),group=section.dataset.setGroup,key=`${gameId}:${group}`;
    const collapsed=!section.classList.contains('collapsed');
    state.collapsedSetGroups[key]=collapsed;section.classList.toggle('collapsed',collapsed);
    button.setAttribute('aria-expanded',String(!collapsed));$('.set-group-body',section).hidden=collapsed;
  });
  $('#set-type-filter').onchange=e=>{state.setType=e.target.value;renderGame(gameId)};
  $('#set-sort').onchange=e=>{state.setSort=e.target.value;renderGame(gameId)};
  $('#set-direction').onchange=e=>{state.setDirection=e.target.value;renderGame(gameId)};
  $('#set-zoom').oninput=e=>{state.setZoom=e.target.value;$$('.set-grid',content).forEach(grid=>grid.style.setProperty('--set-cols',state.setZoom));post('/api/settings',{setZoom:Number(state.setZoom)});};
}

function setGroupSection(gameId,group,items){
  const collapsed=Boolean(state.collapsedSetGroups[`${gameId}:${group}`]);
  return `<section class="set-group ${collapsed?'collapsed':''}" data-set-group="${escapeHtml(group)}"><button class="set-group-head" data-set-group-toggle aria-expanded="${!collapsed}"><span class="set-group-chevron">⌄</span><h3>${escapeHtml(group)}</h3><span>${items.length} ${items.length===1?'Set':'Sets'}</span></button><div class="set-group-body" ${collapsed?'hidden':''}><div class="set-grid" style="--set-cols:${state.setZoom}">${items.map(s=>setTile(s)).join('')}</div></div></section>`;
}

function setTile(s){
 return `<article class="set-card" data-set="${s.id}" style="--accent:${s.accent}"><div class="set-art"><img class="set-brand-logo set-brand-logo-${s.game_id}" loading="lazy" src="/set-logo/${encodeURIComponent(s.id)}?v=${encodeURIComponent(s.visual_version||'provider-v1')}" alt="${escapeHtml(s.name)} Logo"><span class="set-code">${escapeHtml(s.code)}</span><span class="set-kind">${escapeHtml(s.set_type)}</span></div><div class="set-card-info"><h3>${escapeHtml(s.name)}</h3><div class="set-meta">${releaseDate(s)} · ${s.printed_card_count ?? '–'} nummeriert</div><div class="badges">${s.classifications.map(c=>`<span class="badge">${escapeHtml(c)}</span>`).join('')}</div><div class="set-completion"><span>BASE<b>${s.base_completion}%</b></span><span>FOIL / PARALLEL<b>${s.foil_completion}%</b></span><span>MASTER<b>${s.master_completion}%</b></span><span>PLAYSET<b>${s.playset_completion}%</b></span><span style="margin-left:auto">WERT<b>${money(s.value)}</b></span></div></div></article>`;
}

async function renderSet(setId, preserve=false){
  const stale=renderGuard();
  if(!preserve) content.innerHTML='<div class="page-loader"><span></span><p>Kartenkatalog wird geladen …</p></div>';
  const f=state.cardFilters;
  const params=new URLSearchParams({language:state.language,mode:state.filter,sort:state.sort,q:state.query,rarity:f.rarity,foil:f.foilMode,finish:f.finish,rarities:f.rarities.join(','),costs:f.costs.join(','),colors:f.colors.join(','),inkwell:f.inkwell});
  const data=await api(`/api/sets/${setId}/cards?${params}`);
  if(stale())return;
  state.set=data.set; state.cards=data.cards;
  const s=data.set, st=data.stats; const game=state.boot.games.find(g=>g.id===s.game_id); state.game=game;
  const availableSets=game.sets||await api(`/api/games/${game.id}/sets`);game.sets=availableSets;
  if(stale())return;
  const setOptions=[...availableSets].sort((a,b)=>compareSetRelease(a,b,'desc'));
  const isLorcana=game.id==='lorcana',isOnePiece=game.id==='one-piece',isHololive=game.id==='hololive';
  const foilDisplayActive=isLorcana&&f.finish==='foil';
  const setStats=statPill([
    {label:'Base',value:`${st.base}%`},
    {label:'Foil',value:`${st.foil}%`},
    {label:'Master',value:`${st.master}%`},
    {label:'Playset',value:`${st.playset}%`},
    {label:'Besitz / Fehlt',value:`${st.owned} / ${st.missing}`},
    {label:'Setwert',value:money(st.value)},
  ],{className:'compact-stats',columns:6,mobileColumns:3});
  content.innerHTML=`
    <section class="set-compact-head">
      <div class="set-compact-title"><div class="breadcrumbs"><button class="breadcrumb-back" id="cards-back" title="Zurück zur Setübersicht" aria-label="Zurück zur Setübersicht">←</button><button data-dashboard>Übersicht</button><span>›</span><button data-game-back>${escapeHtml(game.short_name)}</button></div><h1><span>${escapeHtml(s.code)}</span>${escapeHtml(s.name)} <small>${releaseDate(s)} · ${s.printed_card_count} Karten</small></h1></div>
      <div class="compact-badges">${s.classifications.map(c=>`<span class="badge">${escapeHtml(c)}</span>`).join('')}</div>
      ${setStats}
    </section>
    <div class="card-toolbar-sticky">
      <div class="op-catalog-filterbar catalog-filter-mobile">
        ${setSwitcherPopup('set',setOptions,s.id,setFilterLabel(s))}
        ${catalogGameFilterBar(game,f,'set',data.rarities)}
        <div class="toolbar-filter-anchor catalog-settings-control">
          <button type="button" class="icon-button" data-filter-toggle="set-view-popup" aria-expanded="false" aria-label="Ansicht" title="Ansicht">⚙</button>
          <div class="toolbar-filter-popup hidden" id="set-view-popup">
            <label>Sprache<select id="language-filter" class="select-control"><option value="combined">Sprachen kombiniert</option>${s.languages.map(l=>`<option value="${l}" ${state.language===l?'selected':''}>${l}</option>`).join('')}</select></label>
            <label>Sortierung<select id="sort-filter" class="select-control"><option value="number">Nr. · Modulsortierung</option><option value="name">Name A–Z</option><option value="rarity">Seltenheit</option><option value="value">Marktwert</option><option value="quantity">Menge</option><option value="missing">Fehlend zuerst</option></select></label>
            ${isLorcana?lorcanaAnsichtExtras(f):''}
          </div>
        </div>
        <div class="zoom-control card-filter-end"><span>−</span><input id="card-zoom" type="range" min="110" max="320" value="${state.zoom}"><span>＋</span></div>
      </div>
    </div>
    ${tileEditionSwitch(game.id)}
    <section class="card-grid" style="--card-size:${state.zoom}px">${data.cards.length?'':'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe Suche oder Filter an.</span></div>'}</section>`;
  mountTileFeed($('.card-grid',content),[{cards:data.cards}],{key:`set:${setId}`,preserve,render:card=>cardTile(card,foilDisplayActive)});
  state.statsUrl=`/api/sets/${setId}/cards?${params}`;
  state.statsItems=stats=>[['Base',`${stats.base}%`],['Foil',`${stats.foil}%`],['Master',`${stats.master}%`],['Playset',`${stats.playset}%`],['Besitz / Fehlt',`${stats.owned} / ${stats.missing}`],['Setwert',money(stats.value)]];
  mountFilterPanel('set-cards',['.card-toolbar-sticky'],'Karten filtern & sortieren');
  $('#sort-filter').value=state.sort;
  $('[data-dashboard]').onclick=()=>routeTo('dashboard'); $('[data-game-back]').onclick=()=>routeTo('game',game.id);
  $('#cards-back').onclick=()=>routeTo('game',game.id);
  $$('[data-set-switch]').forEach(b=>b.onclick=()=>{const val=b.dataset.setSwitch;val==='__all__'?routeTo('game-cards',game.id):routeTo('set',val)});
  bindCardEvents();
  $('#language-filter').onchange=e=>{state.language=e.target.value;f.rarity='';renderSet(setId,true)};
  $('#rarity-filter')?.addEventListener('change',e=>{f.rarity=e.target.value;renderSet(setId,true)});
  $('#sort-filter').onchange=e=>{state.sort=e.target.value;renderSet(setId,true);post('/api/settings',{[`sort_${game.id}`]:state.sort})};
  $$('[data-card-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardFilter,value=b.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(x=>x!==value):[...current,value];renderSet(setId,true)});
  $$('[data-card-single-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardSingleFilter,value=b.dataset.value;f[key]=f[key]===value?'':value;renderSet(setId,true)});
  $$('[data-card-mode]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardMode,value=b.dataset.value;f[key]=value;renderSet(setId,true)});
  bindTileEditionSwitch(game.id,()=>renderSet(setId,true));
  $('#card-zoom').oninput=e=>{state.zoom=e.target.value;$('.card-grid').style.setProperty('--card-size',`${state.zoom}px`);post('/api/settings',{[`zoom_${game.id}`]:Number(state.zoom)})};
}

async function renderAllCards(gameId,preserve=false){
  const stale=renderGuard();
  if(!preserve)content.innerHTML='<div class="page-loader"><span></span><p>Alle Karten werden zusammengestellt …</p></div>';
  setActiveGame(gameId,false);
  const f=state.cardFilters;
  const game=state.boot.games.find(item=>item.id===gameId),params=new URLSearchParams({language:state.language,mode:state.filter,sort:state.sort,set_order:state.setDirection,q:state.query,rarity:f.rarity,foil:f.foilMode,finish:f.finish,rarities:f.rarities.join(','),costs:f.costs.join(','),colors:f.colors.join(','),inkwell:f.inkwell});
  const [data,availableSets]=await Promise.all([api(`/api/games/${gameId}/cards?${params}`),game.sets?Promise.resolve(game.sets):api(`/api/games/${gameId}/sets`)]);game.sets=availableSets;
  if(stale())return;
  state.game=game;state.cards=data.groups.flatMap(group=>group.cards);
  const setOptions=[...availableSets].sort((a,b)=>compareSetRelease(a,b,'desc'));
  const stats=data.stats;
  const isLorcana=game.id==='lorcana',isOnePiece=game.id==='one-piece',isHololive=game.id==='hololive';
  const foilDisplayActive=isLorcana&&f.finish==='foil';
  const allCardStats=statPill([
    {label:'Base',value:`${stats.base}%`},
    {label:'Foil',value:`${stats.foil}%`},
    {label:'Master',value:`${stats.master}%`},
    {label:'Playset',value:`${stats.playset}%`},
    {label:'Besitz / Fehlt',value:`${stats.owned} / ${stats.missing}`},
    {label:'Gesamtwert',value:money(stats.value)},
  ],{className:'compact-stats',columns:6,mobileColumns:3});
  content.innerHTML=`
    <section class="set-compact-head all-cards-head">
      <div class="set-compact-title"><div class="breadcrumbs"><button class="breadcrumb-back" id="cards-back" title="Zurück zur Setübersicht" aria-label="Zurück zur Setübersicht">←</button><button data-dashboard>Übersicht</button><span>›</span><button data-game-back>${escapeHtml(game.short_name)}</button></div><h1>Alle Karten <small>${stats.total} Karten in ${data.groups.length} Sets</small></h1></div>
      ${allCardStats}
    </section>
    <div class="card-toolbar-sticky">
      <div class="op-catalog-filterbar catalog-filter-mobile">
        <div class="filter-search"><span>⌕</span><input id="all-card-search" value="${escapeHtml(state.query)}" placeholder="Alle Sets durchsuchen"></div>
        ${setSwitcherPopup('all',setOptions,'__all__','Alle Karten')}
        ${catalogGameFilterBar(game,f,'all',data.rarities)}
        <div class="toolbar-filter-anchor catalog-settings-control">
          <button type="button" class="icon-button" data-filter-toggle="all-view-popup" aria-expanded="false" aria-label="Ansicht" title="Ansicht">⚙</button>
          <div class="toolbar-filter-popup hidden" id="all-view-popup">
            <label>Sprache<select id="all-language-filter" class="select-control"><option value="combined">Sprachen kombiniert</option>${game.languages.map(language=>`<option value="${language}" ${state.language===language?'selected':''}>${language}</option>`).join('')}</select></label>
            <label>Sortierung<select id="all-sort-filter" class="select-control"><option value="number">Nr. · Modulsortierung</option><option value="name">Name A–Z</option><option value="rarity">Seltenheit</option><option value="value">Marktwert</option><option value="quantity">Menge</option><option value="missing">Fehlend zuerst</option></select></label>
            <label>Setreihenfolge<select id="all-set-order" class="select-control"><option value="desc">Sets: Neu → Alt · Nr. ↓</option><option value="asc">Sets: Alt → Neu · Nr. ↑</option></select></label>
            ${isLorcana?lorcanaAnsichtExtras(f):''}
          </div>
        </div>
        <div class="zoom-control card-filter-end"><span>−</span><input id="all-card-zoom" type="range" min="110" max="320" value="${state.zoom}"><span>＋</span></div>
      </div>
    </div>
    ${tileEditionSwitch(game.id)}
    <div class="all-card-groups" style="--card-size:${state.zoom}px">${data.groups.length?'':'<div class="empty-state"><b>Keine Karten gefunden</b><span>Passe Suche oder Filter an.</span></div>'}</div>`;
  mountTileFeed($('.all-card-groups',content),data.groups.map(group=>({cards:group.cards,sectionHtml:`<section class="all-card-set"><header class="set-card-divider"><img loading="lazy" src="/set-logo/${encodeURIComponent(group.set.id)}?v=${encodeURIComponent(group.set.visual_version||'provider-v1')}" alt=""><div><span>${escapeHtml(group.set.code)}</span><h2>${escapeHtml(group.set.name)}</h2></div><small>${releaseDate(group.set)} · ${group.cards.length} Karten</small></header><div class="card-grid"></div></section>`})),{key:`all:${gameId}`,preserve,render:card=>cardTile(card,foilDisplayActive)});
  state.statsUrl=`/api/games/${gameId}/cards?${params}`;
  state.statsItems=stats=>[['Base',`${stats.base}%`],['Foil',`${stats.foil}%`],['Master',`${stats.master}%`],['Playset',`${stats.playset}%`],['Besitz / Fehlt',`${stats.owned} / ${stats.missing}`],['Gesamtwert',money(stats.value)]];
  mountFilterPanel('all-cards',['.card-toolbar-sticky'],'Karten filtern & sortieren');
  $('#all-sort-filter').value=state.sort;$('#all-set-order').value=state.setDirection;bindCardEvents();
  $('[data-dashboard]').onclick=()=>routeTo('dashboard');$('[data-game-back]').onclick=()=>routeTo('game',gameId);$('#cards-back').onclick=()=>routeTo('game',gameId);
  $$('[data-set-switch]').forEach(b=>b.onclick=()=>{const val=b.dataset.setSwitch;val==='__all__'?renderAllCards(gameId,true):routeTo('set',val)});
  $('#all-language-filter').onchange=event=>{state.language=event.target.value;f.rarity='';renderAllCards(gameId,true)};
  $('#all-rarity-filter')?.addEventListener('change',event=>{f.rarity=event.target.value;renderAllCards(gameId,true)});
  $('#all-sort-filter').onchange=event=>{state.sort=event.target.value;renderAllCards(gameId,true);post('/api/settings',{[`sort_${game.id}`]:state.sort})};
  $('#all-set-order').onchange=event=>{state.setDirection=event.target.value;renderAllCards(gameId,true)};
  $$('[data-card-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardFilter,value=b.dataset.value,current=f[key]||[];f[key]=current.includes(value)?current.filter(x=>x!==value):[...current,value];renderAllCards(gameId,true)});
  $$('[data-card-single-filter]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardSingleFilter,value=b.dataset.value;f[key]=f[key]===value?'':value;renderAllCards(gameId,true)});
  $$('[data-card-mode]').forEach(b=>b.onclick=()=>{const key=b.dataset.cardMode,value=b.dataset.value;f[key]=value;renderAllCards(gameId,true)});
  bindTileEditionSwitch(gameId,()=>renderAllCards(gameId,true));
  let timer;$('#all-card-search').oninput=event=>{clearTimeout(timer);state.query=event.target.value;timer=setTimeout(()=>reRenderPreservingFocus('#all-card-search',()=>renderAllCards(gameId,true)),280)};
  $('#all-card-zoom').oninput=event=>{state.zoom=event.target.value;$('.all-card-groups',content).style.setProperty('--card-size',`${state.zoom}px`);post('/api/settings',{[`zoom_${game.id}`]:Number(state.zoom)})};
}

const cardCycleRegistry=new Map();
const LORCANA_PREMIUM_TIER={Epic:'Epic',Mythisch:'Epic',Enchanted:'Enchanted',Verzaubert:'Enchanted',Iconic:'Iconic',Ikonisch:'Iconic'};

function lorcanaVariantGroups(languageVariants){
  const normal=languageVariants.filter(x=>x.finish==='Normal');
  const foil=languageVariants.filter(x=>x.finish==='Silver');
  const premium=languageVariants.filter(x=>LORCANA_PREMIUM_TIER[x.rarity]);
  const groups=[];
  if(normal.length)groups.push({tier:'normal',items:normal});
  if(foil.length)groups.push({tier:'foil',items:foil});
  if(premium.length)groups.push({tier:'premium',items:premium,rarityLabel:LORCANA_PREMIUM_TIER[premium[0].rarity]});
  return groups;
}

function lorcanaVariantBadges(languageVariants){
  return lorcanaVariantGroups(languageVariants).map(g=>{
    const qty=g.items.reduce((a,x)=>a+(Number(x.quantity)||0),0);
    return `<span class="variant-badge tier-${g.tier} ${qty?'owned':'missing'}" title="${g.rarityLabel?escapeHtml(g.rarityLabel):''}">×${qty}</span>`;
  }).join('');
}

// Mirrors playset_size() in app.py: the constructed copy limit a full playset is measured against.
// What differs per game (playset size, copy limits, icon) comes with the game from the server
// (deckledger/games); nothing here knows a game by its id.
const gameRules=gameId=>state.boot?.games.find(game=>game.id===gameId)||{};
function playsetSize(gameId){return gameRules(gameId).playset_size||4}

// VCard prints every card in up to four edition/finish combinations; show all of them, base first.
// Games whose cards come in several print runs (VCard: Limited/Unlimited and 1st Edition) get a
// switch for which of them the tiles' quantity buttons count.
function tileEditionSwitch(gameId){
  const editions=gameRules(gameId).tile_editions||[];
  if(editions.length<2)return '';
  const active=editions.find(edition=>edition.id===state.tileEditions[gameId])||editions[0];
  // Its own row above the grid, not inside the filter panel: it is used while entering cards,
  // when that panel is closed.
  return `<div class="tile-edition-row"><span>Auflage</span><div class="segmented tile-edition-switch" role="radiogroup" aria-label="Auflage für die Mengen-Schalter" title="Welche Auflage die Mengen-Schalter der Karten zählen">${editions.map(edition=>`<button type="button" role="radio" aria-checked="${edition===active}" data-tile-edition="${edition.id}" class="${edition===active?'active':''}">${escapeHtml(edition.label)}</button>`).join('')}</div></div>`;
}
function bindTileEditionSwitch(gameId,render){
  $$('[data-tile-edition]',content).forEach(button=>button.onclick=()=>{
    if(state.tileEditions[gameId]===button.dataset.tileEdition)return;
    state.tileEditions={...state.tileEditions,[gameId]:button.dataset.tileEdition};
    post('/api/settings',{tileEditions:state.tileEditions});
    render();
  });
}

function tileChipVariants(variants,gameId){
  if(gameId!=='vcard')return variants.slice(0,3);
  const order=finishFilterOptions(gameId),rank=x=>{const index=order.indexOf(x.finish);return index<0?order.length:index};
  return [...variants].sort((a,b)=>rank(a)-rank(b)).slice(0,4);
}

function watchlistIcon(active=false){
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-9.8-9A5.4 5.4 0 0 1 12 6a5.4 5.4 0 0 1 9.8 5.5c-2.3 4.4-9.8 9-9.8 9Z"${active?' fill="currentColor"':''}/></svg>`;
}

function cardTile(card,foilDisplayActive=false){
  const languageVariants=card.variants.filter(x=>x.language===card.language);
  // When the server switched the representative to the foil printing (foil filter engaged),
  // honor that choice first -- otherwise always default to the Normal finish, as before.
  const v0=languageVariants.find(x=>x.variant_id===card.variant_id)||languageVariants.find(x=>x.finish==='Normal')||languageVariants.find(x=>['standard','normal'].includes(x.variant_code))||languageVariants[0]||card.variants[0];
  const gameId=v0.game_id||state.activeGameId,isLorcana=gameId==='lorcana';
  // Which print run the tile's buttons stand for, and its regular and foil variant of this card
  // (see tile_editions in deckledger/games). A card that does not exist in the chosen run -- or
  // only as a foil -- falls back to what it does exist as.
  const editions=gameRules(gameId).tile_editions||[],chosen=editions.find(edition=>edition.id===state.tileEditions[gameId])||editions[0];
  const pairOf=edition=>({edition,regular:languageVariants.find(x=>x.finish===edition.regular),foil:languageVariants.find(x=>x.finish===edition.foil)});
  const pair=[chosen,...editions].filter(Boolean).map(pairOf).find(item=>item.regular||item.foil);
  const v=editions.length>1&&pair?(pair.regular||pair.foil):v0;
  const foil=(pair&&pair.regular&&pair.foil&&pair.foil!==v&&!foilDisplayActive)?pair.foil:null;
  const visual=finishPresentation(v);
  // Hovering cycles through a card's other printings. For Lorcana specifically,
  // the Silver/foil finish is skipped -- every card has one, so it adds no
  // information -- only genuinely rarer premium tiers (Enchanted etc.) cycle in.
  const premiumVariants=isLorcana?(lorcanaVariantGroups(languageVariants).find(g=>g.tier==='premium')?.items||[]).filter(x=>x!==v):[];
  const cycle=isLorcana?[v,...premiumVariants]:[v,...languageVariants.filter(x=>x!==v)];
  cardCycleRegistry.set(v.variant_id,cycle);
  const badgesHtml=isLorcana?lorcanaVariantBadges(languageVariants):(card.quantity?`<span class="owned-pill">×${card.quantity}</span>`:'');
  const quantityHtml=foil
    ?`<div class="quantity-stack"><div class="quantity-control foil" data-variant="${foil.variant_id}"><button data-delta="-1">−</button><b>${foil.quantity}</b><button data-delta="1">＋</button></div><div class="quantity-control" data-variant="${v.variant_id}"><button data-delta="-1">−</button><b>${v.quantity}</b><button data-delta="1">＋</button></div></div>
      <div class="quick-add-stack"><button class="quick-add foil" data-variant="${foil.variant_id}">＋ 1 ${escapeHtml(pair.edition.foil_label||'Foil')}</button><button class="quick-add" data-variant="${v.variant_id}">＋ 1 hinzufügen</button></div>`
    :`<div class="quantity-control" data-variant="${v.variant_id}"><button data-delta="-1">−</button><b>${v.quantity}</b><button data-delta="1">＋</button></div><button class="quick-add" data-variant="${v.variant_id}">＋ 1 hinzufügen</button>`;
  const imageHtml=cycle.length>1
    ?`<div class="card-flip-stack"><img class="cycle-img front" loading="lazy" decoding="async" src="${artUrl(v.variant_id)}" alt="${escapeHtml(card.canonical_name)}"><img class="cycle-img back" loading="lazy" decoding="async" alt="" aria-hidden="true"></div>`
    :`<img loading="lazy" decoding="async" src="${artUrl(v.variant_id)}" alt="${escapeHtml(card.canonical_name)}">`;
  // A playset (4 copies) gets its own badge per finish -- base and foil count
  // separately, so a 4x foil playset doesn't need 4 base copies too to show.
  const playset=playsetSize(v.game_id||state.activeGameId);
  const ribbons=isLorcana
    ?`${v.quantity>=4?`<span class="playset-badge" title="Playset komplett · 4 Exemplare">✓</span>`:''}${foil&&foil.quantity>=4?`<span class="playset-badge foil" title="Foil-Playset komplett · 4 Exemplare">✓</span>`:''}`
    :(card.quantity>=playset?`<span class="playset-badge" title="Playset komplett · ${playset} Exemplare">✓</span>`:'');
  const playsetHtml=ribbons?`<div class="playset-ribbons">${ribbons}</div>`:'';
  return `<article class="card-tile ${card.quantity?'owned':'missing'}" data-identity="${card.identity_id}" data-variant="${v.variant_id}">
    <div class="card-image-wrap card-finish-frame ${visual.effect}" style="--foil-mask:url('${foilMaskUrl(v.variant_id)}')">${imageHtml}<div class="foil-fx foil-fx-a" aria-hidden="true"></div><div class="foil-fx foil-fx-b" aria-hidden="true"></div><div class="foil-fx foil-fx-c" aria-hidden="true"></div><button class="watchlist-action watchlist-action-icon watch-button ${card.watchlisted?'active':''}" title="Watchlist" aria-label="${card.watchlisted?'Von der Watchlist entfernen':'Zur Watchlist hinzufügen'}" aria-pressed="${Boolean(card.watchlisted)}">${watchlistIcon(card.watchlisted)}</button><div class="variant-badges">${badgesHtml}</div>${quantityHtml}</div>
    ${playsetHtml}
    <div class="card-info"><b>${escapeHtml(card.canonical_name)}</b><div class="card-subline"><span>${escapeHtml(card.collector_number)} · ${escapeHtml(card.rarity)} · ${v.language}${editions.length>1&&pair&&pair.edition.id!=='base'?` · ${escapeHtml(pair.edition.label)}`:''}</span><span class="card-price">${price(v.price)}</span></div>${state.zoom>175?`<div class="variant-chips">${tileChipVariants(languageVariants,v.game_id||state.activeGameId).map(x=>`<span class="variant-chip">${escapeHtml(isLorcana?lorcanaFinishLabel(x.finish,x.rarity):x.finish)}</span>`).join('')}</div>`:''}</div></article>`;
}

// Keeps the heart icon in sync everywhere a card can be watchlist-toggled from. The card modal
// fetches its own separate card object via /api/cards/<id> (not a reference into state.cards),
// so toggling from there used to only re-render the modal itself -- the grid tile underneath,
// and state.cards (which e.g. the "Auf Watchlist" collection filter reads), stayed stale until
// a full re-render. This patches both, given whichever identity/variant id shape the caller has.
function syncWatchlistIcon(identityId,variantId,active){
  const gridCard=state.cards.find(c=>c.identity_id===identityId);
  if(gridCard){
    const variant=(gridCard.variants||[]).find(x=>(x.variant_id||x.id)===variantId);
    if(variant)variant.watchlisted=active;
    gridCard.watchlisted=(gridCard.variants||[]).some(x=>x.watchlisted);
    active=gridCard.watchlisted;
  }
  const btn=$(`.card-tile[data-identity="${identityId}"] .watch-button`,content);
  if(btn){btn.classList.toggle('active',active);btn.innerHTML=watchlistIcon(active);btn.setAttribute('aria-pressed',String(active));btn.setAttribute('aria-label',active?'Von der Watchlist entfernen':'Zur Watchlist hinzufügen')}
}
// ---- Progressive card grids ------------------------------------------------------------
// A game's full card list or a large collection is thousands of tiles. The data arrives in one
// answer either way, but turning all of it into DOM at once is what made opening and re-rendering
// those views slow. Only the first chunk is built; further chunks are appended from the data
// already in memory shortly before the user scrolls to them, so loading more costs a few
// milliseconds and no request.
const TILE_CHUNK=120;
const TILE_FEED_MARGIN=2400; // px below the viewport at which the next chunk is appended
let tileFeed=null;
function mountTileFeed(container,groups,{key,preserve=false,render=cardTile,bind=()=>bindCardEvents()}={}){
  const wanted=preserve&&tileFeed?.key===key?Math.max(TILE_CHUNK,tileFeed.rendered):TILE_CHUNK;
  tileFeed?.observer?.disconnect();
  const feed=tileFeed={key,container,groups,render,bind,groupIndex:0,cardIndex:0,rendered:0,grid:null,sentinel:document.createElement('div')};
  feed.sentinel.className='tile-feed-sentinel';
  container.after(feed.sentinel);
  appendTiles(feed,wanted);
  if(!feed.done&&'IntersectionObserver'in window){
    feed.observer=new IntersectionObserver(()=>fillTileFeed(feed),{rootMargin:`${TILE_FEED_MARGIN}px 0px`});
    feed.observer.observe(feed.sentinel);
  }else if(!feed.done)appendTiles(feed,Infinity);
}
function appendTiles(feed,count){
  let added=0;
  while(added<count&&feed.groupIndex<feed.groups.length){
    const group=feed.groups[feed.groupIndex];
    if(feed.cardIndex===0){
      // A group with its own heading (one set in the all-cards view) brings its own grid.
      if(group.sectionHtml){feed.container.insertAdjacentHTML('beforeend',group.sectionHtml);feed.grid=$('.card-grid',feed.container.lastElementChild)}
      else feed.grid=feed.container;
    }
    const slice=group.cards.slice(feed.cardIndex,feed.cardIndex+(count-added));
    feed.grid.insertAdjacentHTML('beforeend',slice.map(card=>feed.render(card)).join(''));
    feed.cardIndex+=slice.length;added+=slice.length;
    if(feed.cardIndex>=group.cards.length){feed.groupIndex+=1;feed.cardIndex=0}
  }
  feed.rendered+=added;
  if(added)feed.bind();
  if(feed.groupIndex>=feed.groups.length){feed.done=true;feed.observer?.disconnect();feed.sentinel.remove()}
}
function fillTileFeed(feed){
  // The observer only reports a change of state; after a long jump (End key, dragging the
  // scrollbar) one chunk is not enough, so keep appending until the sentinel is out of range.
  while(feed===tileFeed&&!feed.done&&feed.sentinel.getBoundingClientRect().top<innerHeight+TILE_FEED_MARGIN)appendTiles(feed,TILE_CHUNK);
}

function bindCardEvents(watchlistId=null){
  // Only tiles that are new since the last call: the feed above appends chunks to a live grid.
  $$('.card-tile:not([data-bound])',content).forEach(tile=>{
    tile.dataset.bound='1';
    tile.tabIndex=0;tile.setAttribute('role','button');
    tile.onkeydown=event=>{if(event.target===tile&&(event.key==='Enter'||event.key===' ')){event.preventDefault();tile.click()}};
    tile.onclick=e=>{
      if(e.target.closest('.watch-button,.quantity-control,.quick-add,.watch-desired'))return;
      if(state.route==='watchlist'&&state.watchSelectionMode){toggleWatchCardSelection(tile);return}
      openCard(tile.dataset.identity,tile.dataset.variant);
    };
    $('.watch-button',tile).onclick=async e=>{e.stopPropagation();const r=await post('/api/watchlist',{variant_id:tile.dataset.variant,...(watchlistId?{list_id:watchlistId}:{})});syncWatchlistIcon(tile.dataset.identity,tile.dataset.variant,r.active);toast(r.active?'Zur Watchlist hinzugefügt':'Von der Watchlist entfernt');refreshWatchCount();if(state.route==='watchlist')renderWatchlist(true)};
    $$('.quantity-control',tile).forEach(row=>{const variantId=row.dataset.variant||tile.dataset.variant;$$('button',row).forEach(btn=>btn.onclick=e=>{e.stopPropagation();changeQuantity(variantId,Number(btn.dataset.delta))})});
    $$('.quick-add',tile).forEach(btn=>btn.onclick=e=>{e.stopPropagation();changeQuantity(btn.dataset.variant||tile.dataset.variant,1,true)});
    const cycle=cardCycleRegistry.get(tile.dataset.variant);
    const stack=$('.card-flip-stack',tile);
    if(cycle&&cycle.length>1&&stack){
      const frame=$('.card-image-wrap',tile),priceEl=$('.card-price',tile);
      let [frontEl,backEl]=$$('.cycle-img',stack);
      if(!frontEl.classList.contains('front')){[frontEl,backEl]=[backEl,frontEl]}
      let cycleIndex=0,cycleTimer=null;
      const advance=()=>{
        cycleIndex=(cycleIndex+1)%cycle.length;
        const variant=cycle[cycleIndex];
        backEl.src=artUrl(variant.variant_id);
        frontEl.classList.replace('front','back');
        backEl.classList.replace('back','front');
        frame.className=`card-image-wrap card-finish-frame ${finishPresentation(variant).effect}`;
        // className reassignment above only touches the class attribute -- the .foil-fx child
        // divs (and their listeners, none here) survive untouched. Only the mask URL itself
        // needs updating, since cycling can switch to a variant with different card art.
        frame.style.setProperty('--foil-mask',`url('${foilMaskUrl(variant.variant_id)}')`);
        if(priceEl)priceEl.textContent=price(variant.price);
        [frontEl,backEl]=[backEl,frontEl];
      };
      const reset=()=>{
        cycleIndex=0;
        const variant=cycle[0];
        frontEl.src=artUrl(variant.variant_id);
        frontEl.classList.add('front');frontEl.classList.remove('back');
        backEl.classList.add('back');backEl.classList.remove('front');
        frame.className=`card-image-wrap card-finish-frame ${finishPresentation(variant).effect}`;
        frame.style.setProperty('--foil-mask',`url('${foilMaskUrl(variant.variant_id)}')`);
        if(priceEl)priceEl.textContent=price(variant.price);
      };
      tile.addEventListener('mouseenter',()=>{cycleIndex=0;cycleTimer=setInterval(advance,1800)});
      tile.addEventListener('mouseleave',()=>{clearInterval(cycleTimer);reset()});
    }
  });
}

async function refreshCurrentView(){
  if(state.route==='set') await renderSet(state.set.id,true);
  else if(state.route==='game-cards') await renderAllCards(state.activeGameId,true);
  else if(state.route==='collection') await renderCollection(true);
  else if(state.route==='watchlist') await renderWatchlist(true);
  else if(state.route==='decks') await renderDeckbuilder(true);
}
