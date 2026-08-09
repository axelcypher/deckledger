// Silver_None -- shader_graphs["128"] "CardFoilSilver5". Keys verified against CardFoilSilver
// (its full float set is just render-state boilerplate + _MotifColorWeight/_RainbowStrength/
// _TimeFactor/_Speed -- no _FoilTiling/_FoilHighlightStrength etc. exist on this material at
// all, confirming it's a genuinely simpler/different shader graph than the Lore family, not
// just a differently-tuned instance of it).
import { f, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/silver.frag'),
  roles: {
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
    f('_MotifColorWeight', 'uMotifColorWeight', 0.5),
  ],
};
