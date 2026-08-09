// Silver_HighGloss -- shader_graphs["131"] "CardVarnishSilver" (a distinct shader graph from
// CardVarnishRegular5, not a reuse -- see varnishSilver.frag's header). Keys verified against
// CardVarnishSilver.
import { f, v4, fragLoader } from './_shared.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/varnishSilver.frag'),
  roles: {
    varnishSurface: { asset: 'VarnishSurface' },
    varnishShine: { asset: 'VarnishShine' },
    silverShine: { asset: 'SilverShine' },
  },
  uniformSpec: [
    f('_VarnishBevelStrength', 'uVarnishBevelStrength', 0.2),
    f('_VarnishDarkenStrength', 'uVarnishDarkenStrength', 0.3),
    f('_VarnishHighlightStrength', 'uVarnishHighlightStrength', 0.65),
    f('_HighlightBackgroundStrength', 'uHighlightBackgroundStrength', 0.3),
    v4('_LightColor', 'uLightColor', [0.98, 0.91, 0.80, 1]),
  ],
};
