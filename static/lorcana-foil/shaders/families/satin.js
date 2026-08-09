// Satin_None / Satin_HighGloss -- shader_graphs["135"] "CardFoilSatin". Keys verified against
// both CardFoilSatin and CardFoilSatinHighGloss.
import { f, v2, v4, fragLoader } from './_shared.js';
import { hasKeyword } from '../../presetResolver.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/satin.frag'),
  roles: {
    satinNoise: { asset: 'SatinFoilNoise' },
    // Satin_None references RainbowGradientSatin; Satin_HighGloss references
    // RainbowGradientSatinWide -- never both on the same material, so trying each in order
    // always resolves to the one this specific material actually has.
    gradient: { assets: ['RainbowGradientSatin', 'RainbowGradientSatinWide'] },
    silverShine: { asset: 'SilverShine' },
    varnishSurface: { asset: 'VarnishSurface' },
    varnishShine: { asset: 'VarnishShine' },
  },
  uniformSpec: [
    v2('_FoilDisplacementTiling', 'uFoilDisplacementTiling', [1, 1]),
    f('_FoilDisplacementStrength', 'uFoilDisplacementStrength', 0.1),
    f('_MotifToRainbowRatio', 'uMotifToRainbowRatio', 0.5),
    f('_LerpMotifMultipliedMaskToRegularMask', 'uLerpMotifMultipliedMaskToRegularMask', 0.5),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
    f('_VarnishBevelStrength', 'uVarnishBevelStrength', 0.12),
    f('_VarnishDarkenStrength', 'uVarnishDarkenStrength', 0.225),
    f('_VarnishHighlightStrength', 'uVarnishHighlightStrength', 0.5),
    f('_VarnishOutlineStrength', 'uVarnishOutlineStrength', 0.8),
    v4('_VarnishLightColor', 'uVarnishLightColor', [1, 0.98, 0.68, 1]),
  ],
  deriveUniforms(material) {
    return { uVarnishType: hasKeyword(material, '_VARNISHTYPE_HIGHGLOSS') ? 1 : 0 };
  },
};
