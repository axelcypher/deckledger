// Maps the 10 real shader graph names (presetResolver.getShaderFamily() -- the last path
// segment of a material's `shader.name`, e.g. "Shader Graphs/CardFoilLore" -> "CardFoilLore")
// to their ported WebGL family module. Exactly the 10 entries in
// lorcana_foil_original_presets.json's own `shader_graphs` map -- nothing invented, nothing
// missing (see verify_shader_families.py-style checks: every one of the 22
// supported_combinations resolves to a materialName -> shader family present here).
import varnishRegular from './varnishRegular.js';
import varnishSilver from './varnishSilver.js';
import silver from './silver.js';
import lore from './lore.js';
import defaultHoloFoil from './defaultHoloFoil.js';
import satin from './satin.js';
import glitter from './glitter.js';
import tempest from './tempest.js';
import freeForm from './freeForm.js';
import freeFormGoldVarnish from './freeFormGoldVarnish.js';

export const SHADER_FAMILIES = {
  CardVarnishRegular5: varnishRegular,
  CardVarnishSilver: varnishSilver,
  CardFoilSilver5: silver,
  CardFoilLore: lore,
  DefaultHoloFoil: defaultHoloFoil,
  CardFoilSatin: satin,
  CardFoilGlitter: glitter,
  CardFoilTempest: tempest,
  CardFoilFreeForm: freeForm,
  CardFoilFreeFormGoldVarnish: freeFormGoldVarnish,
};
