// Glitter_None -- shader_graphs["133"] "CardFoilGlitter". Keys verified against CardFoilGlitter.
import { f, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/glitter.frag'),
  roles: {
    glitter: { asset: 'GlitterPattern' },
    gradient: { asset: 'RainbowGradientTempestGlitter' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    f('_GlitterSize', 'uGlitterSize', 1.1),
    f('_RainbowToMotifWeight', 'uRainbowToMotifWeight', 0.5),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
  ],
};
