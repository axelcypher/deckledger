// None_HighGloss -- shader_graphs["127"] "CardVarnishRegular5". Keys verified against
// CardVarnishRegular.
import { f, v4, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/varnishRegular.frag'),
  roles: {
    varnishSurface: { asset: 'VarnishSurface' },
    varnishShine: { asset: 'VarnishShine' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    f('_VarnishBevelStrength', 'uVarnishBevelStrength', 0.2),
    f('_VarnishDarkenStrength', 'uVarnishDarkenStrength', 0.3),
    f('_VarnishHighlightStrength', 'uVarnishHighlightStrength', 0.65),
    v4('_LightColor', 'uLightColor', [0.98, 0.91, 0.80, 1]),
  ],
};
