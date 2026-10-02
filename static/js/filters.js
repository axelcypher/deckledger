// Filter bars and their controls, including what each game filters by.

function bindBrowserFilters(prefix,render){
  const target=prefix==='watch'?state.watchFilters:state.collectionFilters;let timer;
  $(`#${prefix}-q`).oninput=e=>{clearTimeout(timer);target.q=e.target.value;timer=setTimeout(()=>reRenderPreservingFocus(`#${prefix}-q`,render),250)};
  const mappings=prefix==='watch'?['set','language','finish','sort']:['set','language','rarity','finish','mode','sort'];
  mappings.forEach(key=>{const control=$(`#${prefix}-${key}`);if(control)control.onchange=e=>{target[key==='set'?'set_id':key]=e.target.value;render()}});
}

function bindCatalogFilterControls(filters,render,root=content){
  $$('[data-card-filter]',root).forEach(button=>button.onclick=()=>{
    const key=button.dataset.cardFilter,value=button.dataset.value,current=filters[key]||[];
    filters[key]=current.includes(value)?current.filter(item=>item!==value):[...current,value];render();
  });
  $$('[data-card-single-filter]',root).forEach(button=>button.onclick=()=>{
    const key=button.dataset.cardSingleFilter,value=button.dataset.value;
    filters[key]=filters[key]===value?'':value;render();
  });
}

const OP_COLOR_FILTERS=[['Red','#c42536'],['Green','#168b64'],['Blue','#297eb5'],['Purple','#9749a3'],['Black','#24292f'],['Yellow','#e6d93b']];
const OP_TYPE_FILTERS=[['Leader','Leader'],['Stage','Stage'],['Character','Character'],['Event','Event'],['DON!!','DON']];
const OP_ATTRIBUTE_FILTERS=[['Slash','Slasher','#297eb5'],['Strike','Strike','#e6d93b'],['Special','Special','#9749a3'],['Ranged','Ranged','#c42536'],['Wisdom','Wisdom','#168b64']];
function opColorIcon(color,rotation){
  return `<span class="op-color-glyph" style="--op-color:${color};--op-rotation:${rotation}deg"><img src="/op-filter-icon/color.svg?v=2" alt="" aria-hidden="true"></span>`;
}
function opFilterPanel(filters){
  const selected=(key,value)=>(filters[key]||[]).includes(String(value));
  const pills=(items,key)=>items.map(([value,label])=>`<button type="button" class="op-filter-chip ${selected(key,value)?'active':''}" data-op-filter="${key}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('');
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${pills(OP_TYPE_FILTERS,'types')}</div></div>
    <div class="op-filter-group op-costs"><span>Kosten</span><div>${Array.from({length:10},(_,index)=>index+1).map(cost=>`<button type="button" class="op-image-filter ${selected('costs',cost)?'active':''}" style="--op-cost-icon:url('/op-filter-icon/cost-${cost}.png?v=3')" data-op-filter="costs" data-value="${cost}" aria-label="Kosten ${cost}" title="Kosten ${cost}"><img src="/op-filter-icon/cost-${cost}.png?v=3" alt="${cost}"></button>`).join('')}</div></div>
    <div class="op-filter-group op-colors"><span>Farbe</span><div>${OP_COLOR_FILTERS.map(([name,color],index)=>`<button type="button" class="op-color-filter ${selected('colors',name)?'active':''}" data-op-filter="colors" data-value="${name}" aria-label="${name}" title="${name}">${opColorIcon(color,index*60)}</button>`).join('')}</div></div>
    <div class="op-filter-group op-attributes"><span>Attribut</span><div>${OP_ATTRIBUTE_FILTERS.map(([value,label,color])=>{const iconUrl=`/op-filter-icon/attribute-${value.toLowerCase()}.svg?v=2`;return `<button type="button" class="op-image-filter ${selected('attributes',value)?'active':''}" data-op-filter="attributes" data-value="${value}" aria-label="${label}" title="${label}"><span class="op-attribute-glyph" style="--op-attribute-color:${color};--op-attribute-icon:url('${iconUrl}')"><img src="${iconUrl}" alt="${label}"></span></button>`}).join('')}</div></div>`;
}

const LORCANA_TYPE_FILTERS=[['Character','Charakter'],['Action','Aktion'],['Item','Gegenstand'],['Location','Ort']];
const LORCANA_INK_FILTERS=[['Amber','Bernstein'],['Amethyst','Amethyst'],['Emerald','Smaragd'],['Ruby','Rubin'],['Sapphire','Saphir'],['Steel','Stahl']];
// Keys are language-independent -- a click matches both the EN and DE printed rarity label
// (see LORCANA_RARITY_KEYS server-side), so the icon filter works the same in any language view.
const LORCANA_RARITY_FILTERS=[
  ['common','Gewöhnlich / Common','common.svg'],['uncommon','Ungewöhnlich / Uncommon','uncommon.svg'],['rare','Selten / Rare','rare.svg'],
  ['super-rare','Episch / Super Rare','super_rare.svg'],['legendary','Legendär / Legendary','legendary.svg'],['epic','Mythisch / Epic','epic.png'],
  ['enchanted','Verzaubert / Enchanted','enchanted.png'],['iconic','Ikonisch / Iconic','iconic.png'],['special','Speziell / Special','promo.png'],
];
// LorcanaJSON's special-edition finishes (Lava, Magma, CalendarWave, ...) are internal pattern
// codenames, not something a player recognizes -- show the card's own rarity instead. Silver is
// Lorcana's standard foil finish and always reads as "Foil".
const LORCANA_PLAIN_FINISHES=new Set(['Normal','Satin']);
const COLLECTION_CONDITIONS=['Mint','Near Mint','Excellent','Good','Light Played','Played','Poor'];
function lorcanaFinishLabel(finish,rarity){
  if(finish==='Silver')return 'Foil';
  if(LORCANA_PLAIN_FINISHES.has(finish))return finish;
  return rarity||finish;
}
function filterData(scope,key,value,single=false){
  const attribute=scope==='deck'?(single?'data-single-filter':'data-deck-filter'):(single?'data-card-single-filter':'data-card-filter');
  return `${attribute}="${escapeHtml(key)}" data-value="${escapeHtml(value)}"`;
}
function lorcanaRarityPopup(filters,prefix,scope='card'){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  const popupId=`${prefix}-rarity-popup`;
  return `<div class="toolbar-filter-anchor rarity-filter-anchor">
    <button type="button" class="secondary-button" data-filter-toggle="${popupId}" aria-expanded="false">Seltenheit ▾</button>
    <div class="toolbar-filter-popup align-left rarity-popup hidden" id="${popupId}">
      ${LORCANA_RARITY_FILTERS.map(([key,label,file])=>`<button type="button" class="op-image-filter ${active('rarities',key)?'active':''}" ${filterData(scope,'rarities',key)} aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"><img src="/lorcana-filter-icon/${file}?v=1" alt="${escapeHtml(label)}"></button>`).join('')}
    </div>
  </div>`;
}
function lorcanaCardFilterGroups(filters,scope='card'){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `
    <div class="op-filter-group game-costs"><div>${Array.from({length:7},(_,i)=>i+1).map(cost=>`<button type="button" class="op-number-filter lorcana-cost-filter ${active('costs',cost)?'active':''}" ${filterData(scope,'costs',cost)} aria-label="Kosten ${cost===7?'7+':cost}"><img src="/lorcana-filter-icon/cost.png?v=2" alt="" aria-hidden="true"><span>${cost===7?'7+':cost}</span></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inks"><div>${LORCANA_INK_FILTERS.map(([value,label])=>`<button type="button" class="lorcana-color-filter ${active('colors',value)?'active':''}" ${filterData(scope,'colors',value)} title="${label}" aria-label="${label}"><img src="/lorcana-filter-icon/${value.toLowerCase()}.svg?v=1" alt="" aria-hidden="true"></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inkability"><div><button type="button" class="lorcana-icon-filter ${filters.inkwell==='true'?'active':''}" ${filterData(scope,'inkwell','true',true)} title="Tintbar" aria-label="Tintbar"><img src="/lorcana-filter-icon/inkable.png?v=2" alt="" aria-hidden="true"></button><button type="button" class="lorcana-icon-filter ${filters.inkwell==='false'?'active':''}" ${filterData(scope,'inkwell','false',true)} title="Nicht tintbar" aria-label="Nicht tintbar"><img src="/lorcana-filter-icon/uninkable.png?v=1" alt="" aria-hidden="true"></button></div></div>`;
}
function lorcanaCardFilterBar(filters,prefix,scope='card'){
  return `${lorcanaRarityPopup(filters,prefix,scope)}${lorcanaCardFilterGroups(filters,scope)}`;
}
function lorcanaAnsichtExtras(filters){
  return `<label>Ausführung<div class="segmented"><button type="button" data-card-mode="finish" data-value="normal" class="${filters.finish==='normal'?'active':''}">Normal</button><button type="button" data-card-mode="finish" data-value="foil" class="${filters.finish==='foil'?'active':''}">Foil</button></div></label>
    <label>Foil-Besitz<div class="segmented"><button type="button" data-card-mode="foilMode" data-value="" class="${filters.foilMode===''?'active':''}">Alle</button><button type="button" data-card-mode="foilMode" data-value="owned" class="${filters.foilMode==='owned'?'active':''}">Im Besitz</button><button type="button" data-card-mode="foilMode" data-value="missing" class="${filters.foilMode==='missing'?'active':''}">Fehlend</button></div></label>`;
}
function gameRarityPopup(rarityOptions,filters,prefix,scope='card'){
  const popupId=`${prefix}-rarity-popup`;
  return `<div class="toolbar-filter-anchor rarity-filter-anchor">
    <button type="button" class="secondary-button" data-filter-toggle="${popupId}" aria-expanded="false">Seltenheit ▾</button>
    <div class="toolbar-filter-popup align-left rarity-popup hidden" id="${popupId}">
      ${rarityOptions.map(r=>`<button type="button" class="op-filter-chip ${filters.rarity===r?'active':''}" ${filterData(scope,'rarity',r,true)}>${escapeHtml(r)}</button>`).join('')}
    </div>
  </div>`;
}
function opCardFilterGroups(filters,scope='card'){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `
    <div class="op-filter-group op-costs"><div>${Array.from({length:10},(_,i)=>i+1).map(cost=>`<button type="button" class="op-image-filter ${active('costs',cost)?'active':''}" style="--op-cost-icon:url('/op-filter-icon/cost-${cost}.png?v=3')" ${filterData(scope,'costs',cost)} aria-label="Kosten ${cost}" title="Kosten ${cost}"><img src="/op-filter-icon/cost-${cost}.png?v=3" alt="${cost}"></button>`).join('')}</div></div>
    <div class="op-filter-group op-colors"><div>${OP_COLOR_FILTERS.map(([name,color],index)=>`<button type="button" class="op-color-filter ${active('colors',name)?'active':''}" ${filterData(scope,'colors',name)} aria-label="${name}" title="${name}">${opColorIcon(color,index*60)}</button>`).join('')}</div></div>`;
}
function opCardFilterBar(filters,prefix,rarityOptions,scope='card'){
  return `${gameRarityPopup(rarityOptions,filters,prefix,scope)}${opCardFilterGroups(filters,scope)}`;
}
const HOLOLIVE_COLOR_FILTERS=[['White','#c9ccd1'],['Green','#168b64'],['Red','#c42536'],['Blue','#297eb5'],['Purple','#9749a3'],['Yellow','#e6d93b']];
function hololiveCardFilterGroups(filters,scope='card'){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `
    <div class="op-filter-group op-colors"><div>${HOLOLIVE_COLOR_FILTERS.map(([name,color],index)=>`<button type="button" class="op-color-filter ${active('colors',name)?'active':''}" ${filterData(scope,'colors',name)} aria-label="${name}" title="${name}">${opColorIcon(color,index*60)}</button>`).join('')}</div></div>`;
}
function hololiveCardFilterBar(filters,prefix,rarityOptions,scope='card'){
  return `${gameRarityPopup(rarityOptions,filters,prefix,scope)}${hololiveCardFilterGroups(filters,scope)}`;
}
const VCARD_ELEMENT_FILTERS=[['Fire','#e2572b'],['Water','#2f8fd8'],['Grass','#3aa65a'],['Electric','#e9c530'],['Platinum','#b9c2cc'],['Divine','#f1e2a6'],['Chaos','#7a3fb4']];
const VCARD_TYPE_FILTERS=[['VT','VT'],['Mascot','Mascot'],['Support','Support'],['World','World']];
const VCARD_POWER_LEVELS=[8,9,10];
function vcardCardFilterGroups(filters,scope='card'){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `
    <div class="op-filter-group op-types"><div>${VCARD_POWER_LEVELS.map(level=>`<button type="button" class="op-filter-chip ${active('costs',level)?'active':''}" ${filterData(scope,'costs',level)} aria-label="Power Level ${level}" title="Power Level ${level}">PL${level}</button>`).join('')}</div></div>
    <div class="op-filter-group op-colors"><div>${VCARD_ELEMENT_FILTERS.map(([name,color],index)=>`<button type="button" class="op-color-filter ${active('colors',name)?'active':''}" ${filterData(scope,'colors',name)} aria-label="${name}" title="${name}">${opColorIcon(color,index*51)}</button>`).join('')}</div></div>`;
}
function vcardCardFilterBar(filters,prefix,rarityOptions,scope='card'){
  return `${gameRarityPopup(rarityOptions,filters,prefix,scope)}${vcardCardFilterGroups(filters,scope)}`;
}
function catalogGameFilterBar(game,filters,prefix,rarityOptions,scope='card'){
  if(game.id==='lorcana')return lorcanaCardFilterBar(filters,prefix,scope);
  if(game.id==='one-piece')return opCardFilterBar(filters,prefix,rarityOptions,scope);
  if(game.id==='hololive')return hololiveCardFilterBar(filters,prefix,rarityOptions,scope);
  if(game.id==='vcard')return vcardCardFilterBar(filters,prefix,rarityOptions,scope);
  return gameRarityPopup(rarityOptions,filters,prefix,scope);
}
function setAbbreviation(name){
  return name.replace(/[^\p{L}\s]/gu,'').split(/\s+/).filter(Boolean).map(w=>w[0]).join('').toUpperCase();
}
function setFilterLabel(set){
  const abbreviation=/^\d+$/.test(String(set.code||''))?setAbbreviation(set.name||''):String(set.code||'');
  const lorcanaNumber=String(set.id||'').match(/^lorcana-(\d+)$/)?.[1];
  if(lorcanaNumber)return `${lorcanaNumber} ${abbreviation}`.trim();
  return `${set.code||''} ${setAbbreviation(set.name||'')}`.trim();
}
function setSwitcherPopup(prefix,setOptions,currentSetId,currentLabel){
  const popupId=`${prefix}-set-popup`;
  return `<div class="toolbar-filter-anchor set-switch-control">
    <button type="button" class="secondary-button set-switch-trigger" data-filter-toggle="${popupId}" aria-expanded="false">${escapeHtml(currentLabel)} ▾</button>
    <div class="toolbar-filter-popup align-left set-switch-popup hidden" id="${popupId}">
      <button type="button" class="set-switch-option ${currentSetId==='__all__'?'active':''}" data-set-switch="__all__">Alle Karten</button>
      ${setOptions.map(item=>`<button type="button" class="set-switch-option ${item.id===currentSetId?'active':''}" data-set-switch="${item.id}">${escapeHtml(item.code)} · ${escapeHtml(item.name)}</button>`).join('')}
    </div>
  </div>`;
}
const HOLOLIVE_KIND_FILTERS=[['oshi','Oshi'],['holomem','Holomem'],['buzz','Buzz'],['support','Support'],['cheer','Cheer']];
const HOLOLIVE_BLOOM_FILTERS=[['Debut','Debut'],['1st','1st'],['2nd','2nd'],['Spot','Spot']];
function deckFilterPills(items,key,filters){
  const active=value=>(filters[key]||[]).includes(String(value));
  return items.map(([value,label])=>`<button type="button" class="op-filter-chip ${active(value)?'active':''}" data-deck-filter="${key}" data-value="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('');
}
function lorcanaFilterPanel(filters){
  const active=(key,value)=>(filters[key]||[]).includes(String(value));
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${deckFilterPills(LORCANA_TYPE_FILTERS,'types',filters)}</div></div>
    <div class="op-filter-group game-costs"><span>Kosten</span><div>${Array.from({length:7},(_,i)=>i+1).map(cost=>`<button type="button" class="op-number-filter lorcana-cost-filter ${active('costs',cost)?'active':''}" data-deck-filter="costs" data-value="${cost}" aria-label="Kosten ${cost===7?'7+':cost}"><img src="/lorcana-filter-icon/cost.png?v=2" alt="" aria-hidden="true"><span>${cost===7?'7+':cost}</span></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inks"><span>Tintenfarbe</span><div>${LORCANA_INK_FILTERS.map(([value,label])=>`<button type="button" class="lorcana-color-filter ${active('colors',value)?'active':''}" data-deck-filter="colors" data-value="${value}" title="${label}" aria-label="${label}"><img src="/lorcana-filter-icon/${value.toLowerCase()}.svg?v=1" alt="" aria-hidden="true"></button>`).join('')}</div></div>
    <div class="op-filter-group lorcana-inkability"><span>Tintbarkeit</span><div><button type="button" class="lorcana-icon-filter ${filters.inkwell==='true'?'active':''}" data-single-filter="inkwell" data-value="true" title="Tintbar" aria-label="Tintbar"><img src="/lorcana-filter-icon/inkable.png?v=2" alt="" aria-hidden="true"></button><button type="button" class="lorcana-icon-filter ${filters.inkwell==='false'?'active':''}" data-single-filter="inkwell" data-value="false" title="Nicht tintbar" aria-label="Nicht tintbar"><img src="/lorcana-filter-icon/uninkable.png?v=1" alt="" aria-hidden="true"></button></div></div>`;
}
function hololiveFilterPanel(filters){
  return `<div class="op-filter-group op-types"><span>Kartentyp</span><div>${deckFilterPills(HOLOLIVE_KIND_FILTERS,'kinds',filters)}</div></div>
    <div class="op-filter-group"><span>Bloom-Level</span><div>${deckFilterPills(HOLOLIVE_BLOOM_FILTERS,'bloomLevels',filters)}</div></div>`;
}
