// Loads lorcana_foil_original_presets.json (served at /assets/lorcana/foil-presets.json,
// extracted straight from the shipped Unity player -- see the file's own "source"/"notes"
// fields) and exposes typed-ish accessors over it. No values are hand-transcribed or estimated
// here: every float/color/keyword/texture-slot comes directly from the JSON at load time.
//
// One deliberate departure from a literal "just index by slot key" reading: several texture
// slots in the JSON are auto-generated ShaderGraph node names
// (e.g. "_SampleTexture2D_189abfa7029441b2a872a686f60c1b59_Texture_1_Texture2D") rather than
// clean semantic names -- those hashes are an artifact of THIS specific extraction run, not a
// stable contract worth hand-copying into shader code (the same caution the brief gives for
// numeric values applies to fragile generated strings too). findTextureByAssetName() below
// looks a material's textures up by the ASSET they reference (e.g. "SilverShine",
// "InkwashMask") instead, which is exactly the same data, addressed the durable way. The clean,
// stable slots (_FoilPattern, _FoilColorGradient, _Motif, _MotifMask, _TopLayerMask,
// _SecondTopLayerMask, _MainTex) are used directly by name -- confirmed present under those
// exact keys across the materials inspected while building this.

const PRESETS_URL = '/assets/lorcana/foil-presets.json';
const TEXTURE_BASE_URL = '/assets/lorcana/foil/';

let presetsPromise = null;

export function loadPresets() {
  if (!presetsPromise) {
    presetsPromise = fetch(PRESETS_URL).then(r => {
      if (!r.ok) throw new Error(`foil-presets.json HTTP ${r.status}`);
      return r.json();
    });
  }
  return presetsPromise;
}

// URL for one of the 26 shared, non-card-specific effect textures (rainbow gradients,
// noise/pattern maps, shine/varnish textures) -- extracted from the app's own Unity build, see
// app.py's lorcana_foil_texture_asset route.
export function effectTextureUrl(assetName) {
  return `${TEXTURE_BASE_URL}${encodeURIComponent(assetName)}.png`;
}

// "Silver" + "HighGloss" -> "Silver_HighGloss"; a missing foil/top-layer becomes the literal
// "None" segment, matching supported_combinations' own key convention exactly (verified against
// the JSON: "None_HighGloss", "Silver_None", "Silver_HighGloss", ...).
export function combinationKey(foilType, topLayerType) {
  return `${foilType || 'None'}_${topLayerType || 'None'}`;
}

// The core lookup + the explicitly-required fallback: "Für unbekannte zukünftige Kombinationen
// auf den vorhandenen CSS-Foil zurückfallen" -- returns null (never throws) for anything not in
// supported_combinations, which callers treat as "no WebGL material, stay on the CSS effect".
// Never invents a combination that isn't literally in the JSON.
export function resolveMaterialName(presets, foilType, topLayerType) {
  const key = combinationKey(foilType, topLayerType);
  return presets.supported_combinations?.[key] || null;
}

export function getMaterial(presets, materialName) {
  return presets.materials?.[materialName] || null;
}

export function getShaderFamily(material) {
  // e.g. "Shader Graphs/CardFoilLore" -> "CardFoilLore". Matches one of the 10 shader_graphs
  // entries in the JSON; the caller (LorcanaFoilRenderer) maps this to the corresponding
  // compiled WebGL program.
  const name = material?.shader?.name || '';
  const slash = name.lastIndexOf('/');
  return slash >= 0 ? name.slice(slash + 1) : name;
}

export function hasKeyword(material, keyword) {
  return Boolean(material?.valid_keywords?.includes(keyword));
}

// Plain float uniform, straight passthrough -- returns `fallback` (not a guessed "sensible"
// number) only when the key is genuinely absent from this material's floats, which per the
// brief shouldn't happen for anything this renderer actually reads; the fallback exists purely
// so a missing key degrades to inert (0) instead of throwing mid-render.
export function floatValue(material, key, fallback = 0) {
  const v = material?.floats?.[key];
  return typeof v === 'number' ? v : fallback;
}

// Unity serializes some Vector2s as 4-component vectors -- only .xy is meaningful for the UV
// tiling/offset values this renderer uses (per the brief's own note); z/w are read but ignored.
export function vec2Value(material, key, fallback = [0, 0]) {
  const v = material?.colors?.[key];
  if (Array.isArray(v) && v.length >= 2) return [v[0], v[1]];
  return fallback;
}

export function colorValue(material, key, fallback = [1, 1, 1, 1]) {
  const v = material?.colors?.[key];
  if (Array.isArray(v) && v.length >= 4) return v;
  return fallback;
}

// #rrggbb (from the Ravensburger card API, hot_foil_color/second_hot_foil_color) -> linear
// [0,1] RGBA. API colors OVERRIDE the material preset's own static _HotFoilColor/
// _SecondHotFoilColor when present -- see FoilLayerInput consumers.
export function hexToVec4(hex, alpha = 1) {
  if (!hex) return null;
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255, alpha];
}

// Look up a texture slot by the ASSET it references (e.g. "SilverShine") rather than by its
// (often auto-generated, run-specific) slot key -- see module docstring. Returns
// {slotKey, textureName, scale, offset} or null if this material doesn't reference that asset
// at all (e.g. CardFoilSilver has no VarnishSurface slot -- that's expected, not an error).
export function findTextureByAssetName(material, assetName) {
  const textures = material?.textures || {};
  for (const [slotKey, entry] of Object.entries(textures)) {
    if (entry?.texture?.name === assetName) {
      return { slotKey, textureName: assetName, scale: entry.scale || [1, 1], offset: entry.offset || [0, 0] };
    }
  }
  return null;
}

// The clean, stable slots -- used by name directly (see module docstring for why these are
// trusted as-is while the auto-generated ones aren't).
export function getNamedSlot(material, slotName) {
  const entry = material?.textures?.[slotName];
  if (!entry) return null;
  return { slotKey: slotName, textureName: entry.texture?.name || null, scale: entry.scale || [1, 1], offset: entry.offset || [0, 0] };
}
