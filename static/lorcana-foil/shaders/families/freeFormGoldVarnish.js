// FreeForm2_RainbowHotFoil -- shader_graphs["132"] "CardFoilFreeFormGoldVarnish". Keys verified
// against CardFreeForm2RainbowHotFoil.
import { f, v2, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/freeFormGoldVarnish.frag'),
  roles: {
    pattern: { slot: '_Texture' }, // FreeForm2Pattern
    gradient: { asset: 'RainbowGradientFreeForm' },
    silverShine: { asset: 'SilverShine' },
    goldBase: { asset: 'GradientGold' },
    goldGradient: { asset: 'RainbowGradientGold' },
  },
  uniformSpec: [
    v2('_Tiling', 'uTiling', [1, 1]),
    v2('_Offset', 'uOffset', [0, 0]),
    f('_Foil_Strength', 'uFoilStrength', 1.0),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
  ],
};
