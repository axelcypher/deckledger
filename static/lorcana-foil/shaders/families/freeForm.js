// FreeForm1_None / FreeForm2_None -- shader_graphs["130"] "CardFoilFreeForm". Keys verified
// against CardFreeForm1Foil and CardFreeForm2Foil (identical key sets).
import { f, v2, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/freeForm.frag'),
  roles: {
    pattern: { slot: '_Texture' }, // FreeForm1Pattern or FreeForm2Pattern, per-material
    gradient: { asset: 'RainbowGradientFreeForm' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    v2('_Tiling', 'uTiling', [1, 1]),
    v2('_Offset', 'uOffset', [0, 0]),
    f('_Foil_Strength', 'uFoilStrength', 1.0),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
  ],
};
