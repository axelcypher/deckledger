// Tempest_None -- shader_graphs["136"] "CardFoilTempest". Keys verified against CardFoilTempest.
// No clean _Texture/_FoilPattern slot exists on this material (only auto-generated hash slots)
// -- resolved by asset name instead (see presetResolver's findTextureByAssetName).
import { f, v2, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/tempest.frag'),
  roles: {
    pattern: { asset: 'TempestPatterns' },
    gradient: { asset: 'RainbowGradientTempestGlitter' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    v2('_Tiling', 'uTiling', [1, 1]),
    v2('_Offset', 'uOffset', [0, 0]),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_Inkwash_Compactness', 'uInkwashCompactness', 2.0),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
  ],
};
