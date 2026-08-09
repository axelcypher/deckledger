// Tiny shorthand for building a family's uniformSpec entries (consumed by
// LorcanaFoilRenderer.bindUniformSpec) + the fetch-once .frag loader every family uses.
// jsonKey must be the REAL key from lorcana_foil_original_presets.json (verified present on
// that family's materials before being referenced here -- see each family module's own
// comment for which materials were checked).

export const f = (jsonKey, glslName, fallback = 0) => ({ jsonKey, glslName, type: 'float', fallback });
export const v2 = (jsonKey, glslName, fallback = [0, 0]) => ({ jsonKey, glslName, type: 'vec2', fallback });
export const v4 = (jsonKey, glslName, fallback = [1, 1, 1, 1]) => ({ jsonKey, glslName, type: 'vec4', fallback });
export const boolf = (jsonKey, glslName, fallback = 0) => ({ jsonKey, glslName, type: 'bool', fallback });

export function fragLoader(url) {
  let cached = null;
  return () => cached || (cached = fetch(url).then(r => {
    if (!r.ok) throw new Error(`shader source ${url} HTTP ${r.status}`);
    return r.text();
  }));
}
