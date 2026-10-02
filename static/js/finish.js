// How a card's finish looks: image URLs, foil classes, the official Lorcana foil layers.

const artUrl = (variantId, size='thumb') => `/art/${encodeURIComponent(variantId)}.svg?v=5${size==='thumb'?'&size=thumb':''}`;
// Luminance mask derived server-side from THIS card's own art (app.py: /foil-mask/<id>.webp,
// cached_foil_mask) -- used to confine the mobile card-modal's foil/prismatic/aurora shimmer
// to the card's own non-black regions instead of washing over the whole rectangle.
const foilMaskUrl = variantId => `/foil-mask/${encodeURIComponent(variantId)}.webp?v=2`;
function finishPresentation(variant={}){
  const finish=String(variant.finish||'').trim();
  const descriptor=`${finish} ${variant.variant_code||''} ${variant.rarity||''}`.toLowerCase();
  const gameId=String(variant.game_id||state.activeGameId||'').toLowerCase();
  // "verzaubert" = German "Enchanted" (Lorcana ships EN+DE in this dataset) -- without it, a
  // DE-language Enchanted print (rarity="Verzaubert") fell through to the parallel/is_parallel
  // check below and got classified as finish-prismatic (Tier 2) instead of finish-aurora
  // (Tier 3), even though it's the exact same card/rarity as its EN counterpart.
  const premiumNamed=/manga|enchanted|verzaubert|iconic|epic|signature|signed/.test(descriptor);
  const premiumCode=/(?:^|[\s_-])(our|osr|sec|sp|ur|sy)(?:$|[\s_-])/i.test(descriptor);
  if(premiumNamed||premiumCode){
    return {effect:'finish-aurora'};
  }
  // Foil/silver check runs before the parallel/is_parallel check on purpose: Lorcana's
  // Silver finish sets is_parallel=1 in the data (it IS technically a parallel print in
  // that game's model), so checking is_parallel first would misclassify every Silver
  // card as "prismatic" instead of "foil" -- which is exactly what was happening.
  const hololiveBaseFoil=gameId==='hololive'&&/^(s|sr)$/i.test(finish);
  if(hololiveBaseFoil||/foil|silver|satin|holo|rainbow|etched|textured|gold/.test(descriptor)){
    return {effect:'finish-foil'};
  }
  if(/parallel|alternate|alt art/.test(descriptor)||Number(variant.is_parallel)===1){
    return {effect:'finish-prismatic'};
  }
  return {effect:'finish-normal'};
}
function finishThumb(variant,src,alt='',className=''){
  const visual=finishPresentation(variant);
  // Callers pass differently-shaped variant objects (.id from physicalVariants, .variant_id
  // from search results) -- same fallback pattern already used at every artUrl() call site.
  const maskUrl=foilMaskUrl(variant.id||variant.variant_id);
  return `<span class="card-finish-frame finish-thumb ${visual.effect} ${className}" style="--foil-mask:url('${maskUrl}')"><img loading="lazy" src="${src}" alt="${escapeHtml(alt)}"><div class="foil-fx foil-fx-a" aria-hidden="true"></div><div class="foil-fx foil-fx-b" aria-hidden="true"></div><div class="foil-fx foil-fx-c" aria-hidden="true"></div></span>`;
}

// ---- Official Ravensburger foil layers (Lorcana only) ----
// A progressive enhancement layered ON TOP of the existing generic foil-fx system above, never
// a replacement for it -- see app.py's /api/foil-layer-meta + foil-effects.css's own comment
// block for the full pipeline (matching key, caching, routes). Registries below are
// deliberately just presence-markers for now: "die eigentlichen Effekte machen wir dann in
// einem eigenen Schritt" -- this step wires the real official masks + type/color metadata
// through end-to-end and gives every foil_type/foil_top_layer a resolvable preset slot,
// without yet defining what each one actually looks like. An unknown/future type (a new set
// could ship one any day) falls back to the shared __default__ preset instead of throwing.
const FOIL_EFFECTS = {
  Silver: {}, Lava: {}, Satin: {}, Glitter: {}, VerticalWave: {}, Tempest: {},
  FreeForm1: {}, FreeForm2: {}, SeaWave: {}, Lore: {}, Magma: {}, RainbowPillars: {}, CalendarWave: {},
  __default__: {},
};
// Kept as its OWN registry, not merged into FOIL_EFFECTS -- a top layer (hot foil) is a
// separate rendered layer stacked on top of the base foil effect, never a substitute for one
// (a card can carry a foil_type AND a foil_top_layer at once -- e.g. Magma + ChromeRainbowHotFoil
// on The Madrigal Family). MatteHotFoil isn't in the original brief but IS a real value in the
// live feed (confirmed against the actual API response) -- listed explicitly rather than
// silently relying on the fallback for something that isn't actually unknown.
const FOIL_TOP_LAYER_EFFECTS = {
  HighGloss: {}, RainbowHotFoil: {}, MetallicHotFoil: {}, SnowHotFoil: {}, ChromeRainbowHotFoil: {}, MatteHotFoil: {},
  __default__: {},
};
const foilEffectPreset = type => FOIL_EFFECTS[type] || FOIL_EFFECTS.__default__;
const foilTopLayerPreset = type => FOIL_TOP_LAYER_EFFECTS[type] || FOIL_TOP_LAYER_EFFECTS.__default__;

// FoilLayer / FoilTopLayer(xN) -- the official-mask counterpart to foil-fx-a/b/c. Its own
// encapsulated markup+hydration pair, kept separate from the existing card-finish-frame
// rendering rather than folded into it ("bestehende Card-Komponente möglichst nicht umbauen").
// Always rendered as inert, empty <div>s (no mask-image, no background) -- hydrateOfficial-
// FoilLayers() below fills in style values on nodes that already exist rather than the DOM
// structure changing shape once data arrives. Left permanently empty (CSS: no mask set = no
// visual effect) for the normal case of anything without an official match -- non-Lorcana
// games, non-foil finishes, or Lorcana cards/printings Ravensburger's own feed doesn't cover.
function officialFoilLayerMarkup(){
  return `<div class="official-foil-layer" aria-hidden="true"></div>`+
    `<div class="official-foil-top-layer" aria-hidden="true"></div>`+
    `<div class="official-foil-top-layer secondary" aria-hidden="true"></div>`;
}
// Fetches /api/foil-layer-meta/<variantId> and, if real data comes back, wires the official
// mask URLs + type/color parameters onto the three officialFoilLayerMarkup() nodes inside
// `container`. Silently does nothing on any failure/absence (network hiccup, non-Lorcana card,
// no official match for this printing) -- the existing generic foil-fx effect already works
// completely on its own; this only ever adds to it, never gates on it.
async function hydrateOfficialFoilLayers(variantId, container){
  // Reset up front (not just on success) -- a stale meta object from whichever card was
  // hovered/open previously must never survive into a WebGL attach() for THIS card if this
  // fetch fails or comes back empty; see foilInputForModal()'s own variantId re-check too.
  state.modalFoilLayerMeta=null; state.modalFoilLayerMetaVariantId=variantId;
  const base=$('.official-foil-layer',container);
  const top=$('.official-foil-top-layer:not(.secondary)',container);
  const top2=$('.official-foil-top-layer.secondary',container);
  if(!base&&!top&&!top2)return;
  let data;
  try{
    data=await (await fetch(`/api/foil-layer-meta/${encodeURIComponent(variantId)}`)).json();
  }catch{
    return;
  }
  if(!data||!data.available)return;
  // The WebGL interactive layer (FoilInteractionController) reuses this same fetch instead of
  // re-requesting it on every hover -- see foilInputForModal() in wireGlobalEvents.
  state.modalFoilLayerMeta=data;
  if(base&&data.mask_url){
    base.style.setProperty('--official-mask',`url('${data.mask_url}')`);
    base.dataset.foilType=data.foil_type||'';
    base.classList.add('is-active');
  }
  const wireTopLayer=(el,maskUrl,color)=>{
    if(!el||!maskUrl)return;
    el.style.setProperty('--official-mask',`url('${maskUrl}')`);
    if(color)el.style.setProperty('--hot-foil-color',color);
    el.dataset.foilTopLayer=data.foil_top_layer||'';
    el.classList.add('is-active');
  };
  wireTopLayer(top,data.top_layer_mask_url,data.hot_foil_color);
  wireTopLayer(top2,data.second_top_layer_mask_url,data.second_hot_foil_color);
}
