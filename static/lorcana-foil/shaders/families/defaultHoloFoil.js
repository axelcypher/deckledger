// Lava_None, VerticalWave_None, RainbowPillars_None -- shader_graphs["137"] "DefaultHoloFoil".
// Keys verified present on CardFoilLava / CardFoilVertWave / CardRainbowPillarsFoil (all three
// share the identical float/color key set -- confirmed by diffing all three materials'
// `floats`/`colors` dicts before writing this).
import { f, v2, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/defaultHolo.frag'),
  roles: {
    // Slot key confirmed as literally "_Texture" on all three materials (not "_FoilPattern" --
    // that clean name belongs to the Lore family's shader graph, a different one).
    pattern: { slot: '_Texture' },
    gradient: { slot: '_FoilColorGradient' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    v2('_Tiling', 'uTiling', [1, 1]),
    v2('_Offset', 'uOffset', [0, 0]),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_Inkwash_Compactness', 'uInkwashCompactness', 2.0),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
    f('_MotifColorWeight', 'uMotifColorWeight', 0.5),
  ],
};
