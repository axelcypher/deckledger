// SeaWave_None / SeaWave_HighGloss / SeaWave_MatteHotFoil / Lore_MetallicHotFoil / Magma_None /
// Magma_MetallicHotFoil / Magma_SnowHotFoil / Magma_ChromeRainbowHotFoil / CalendarWave_None --
// shader_graphs["129"] "CardFoilLore", "der wichtigste generische Shader". 9 of the 22
// combinations, all sharing this one compiled program (see lore.frag).
//
// Keys were checked per-material (not assumed shared) before being listed here -- several are
// genuinely absent on some of the 9 (e.g. CardMagmaChromeRainbowHotFoilMaterial has no
// _Inkwash_Compactness/_MotifColorWeight/_RainbowStrength/_FoilMidToneFactor at all) and fall
// back to the documented default below via presetResolver's fallback mechanism -- that's a
// real, flagged approximation for those specific fields on that specific material, not a
// silent guess presented as JSON-sourced.
import { f, v2, v4, boolf, fragLoader } from './_shared.js';
import { hasKeyword } from '../../presetResolver.js';

export default {
  loadSource: fragLoader('/static/lorcana-foil/shaders/lore.frag'),
  roles: {
    pattern: { slot: '_FoilPattern' },
    gradient: { slot: '_FoilColorGradient' },
    silverShine: { asset: 'SilverShine' },
    varnishSurface: { asset: 'VarnishSurface' },
    // VarnishShine on the plain/HighGloss variants, VarnishShineBroad on the HotFoil variants
    // (confirmed per-material -- CardFoilSeaWave/CalendarWave/SeaWaveHighGloss reference
    // "VarnishShine"; CardLoreMetallicHotFoil/CardMagma* reference "VarnishShineBroad").
    varnishShine: { assets: ['VarnishShine', 'VarnishShineBroad'] },
    glitter: { asset: 'GlitterPattern' }, // absent on CalendarWave/SeaWaveMatteHotFoil -- optional
    goldGradient: { asset: 'RainbowGradientGold' }, // ChromeRainbowHotFoil only -- optional
  },
  uniformSpec: [
    v2('_FoilTiling', 'uFoilTiling', [1, 1]),
    v2('_FoilOffset', 'uFoilOffset', [0, 0]),
    v2('_FoilMorphOffset', 'uFoilMorphOffset', [0, 0]),
    f('_FoilMorphSpeed', 'uFoilMorphSpeed', 1.0),
    f('_FoilHighlightStrength', 'uFoilHighlightStrength', 5.0),
    f('_FoilMidToneFactor', 'uFoilMidToneFactor', 1.0),
    f('_FoilMidToneStrength', 'uFoilMidToneStrength', 0.4),
    f('_BrightHighlightsReduction', 'uBrightHighlightsReduction', 0.0),
    f('_Inkwash_Strength', 'uInkwashStrength', 0.65),
    f('_RainbowStrength', 'uRainbowStrength', 1.0),
    f('_MotifColorWeight', 'uMotifColorWeight', 0.5),
    boolf('_FOILOFFSETSRAINBOW', 'uFoilOffsetsRainbow', 0),
    v2('_VarnishDistortionTiling', 'uVarnishDistortionTiling', [0.25, 0.5]),
    f('_VarnishBevelStrength', 'uVarnishBevelStrength', 0.12),
    f('_VarnishDarkenStrength', 'uVarnishDarkenStrength', 0.225),
    f('_VarnishDistortionStrength', 'uVarnishDistortionStrength', 0.04),
    f('_VarnishHighlightStrength', 'uVarnishHighlightStrength', 0.5),
    f('_VarnishOutlineStrength', 'uVarnishOutlineStrength', 0.8),
    v4('_VarnishLightColor', 'uVarnishLightColor', [1, 0.98, 0.68, 1]),
    f('_HotFoilBrightness', 'uHotFoilBrightness', 0.5),
    f('_HotFoilContrast', 'uHotFoilContrast', 0.5),
    f('_GlitterSize', 'uGlitterSize', 1.1),
    v4('_MetallicInkColor', 'uMetallicInkColor', [0.6, 0.6, 0.6, 1]),
  ],
  deriveUniforms(material) {
    const varnishType = hasKeyword(material, '_VARNISHTYPE_HIGHGLOSS') ? 1
      : hasKeyword(material, '_VARNISHTYPE_HOTFOIL') ? 2
      : 0;
    const hotFoilSurface = hasKeyword(material, '_HOTFOILSURFACE_COLORRAINBOW') ? 3
      : hasKeyword(material, '_HOTFOILSURFACE_SNOW') ? 2
      : hasKeyword(material, '_HOTFOILSURFACE_METALLIC') ? 1
      : 0;
    return { uVarnishType: varnishType, uHotFoilSurface: hotFoilSurface };
  },
};
