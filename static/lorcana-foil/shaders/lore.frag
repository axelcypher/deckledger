// CardFoilLore -- "der wichtigste generische Shader": shared kernel for SeaWave (+HighGloss,
// +MatteHotFoil), Lore (+MetallicHotFoil), Magma (+MetallicHotFoil/+SnowHotFoil/
// +ChromeRainbowHotFoil) and CalendarWave -- 9 of the 22 supported combinations. Per-material
// identity is entirely: which pattern/gradient texture is bound (families/lore.js roles) +
// which VARNISHTYPE/HOTFOILSURFACE keyword was active on the original material (passed in as
// uVarnishType/uHotFoilSurface, resolved from valid_keywords in JS, not branched per-shader).
//
// Base foil (8.11): patternUV = uv*_FoilTiling + _FoilOffset, morph-shifted by tilt/_FoilMorph*
// -> sample _FoilPattern -> derive a gradient coordinate from pattern+angle -> sample
// _FoilColorGradient -> shape via _FoilHighlightStrength/_FoilMidToneFactor/
// _FoilMidToneStrength/_BrightHighlightsReduction/_Inkwash_Strength.
// Top layer (8.12/8.13/8.15/8.16-8.19), only inside uTopLayerMask:
//   VARNISHTYPE_HIGHGLOSS   -> VarnishSurface distortion + directional VarnishShine highlight.
//   VARNISHTYPE_HOTFOIL     -> a shared "hot foil response" (angle-driven highlight remapped by
//                              _HotFoilBrightness/_HotFoilContrast), tinted per HOTFOILSURFACE:
//                              METALLIC   -> _MetallicHotFoilColor/_MetallicInkColor tint.
//                              SNOW       -> desaturated + GlitterPattern micro-highlights.
//                              COLORRAINBOW -> remapped through the Gold rainbow gradient.
//                              (no keyword, e.g. SeaWaveMatteHotFoil) -> the base response
//                              un-tinted, at that material's own (deliberately low, per the
//                              brief) contrast/bevel preset values -- same code path, just
//                              different JSON numbers, not a fourth branch.
// hot_foil_color/second_hot_foil_color from the Ravensburger API override the material's own
// static _HotFoilColor/_SecondHotFoilColor (LorcanaFoilRenderer.render() already does this
// substitution before uHotFoilColor/uSecondHotFoilColor reach this shader).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;
uniform sampler2D uTopLayerMask;
uniform bool uHasTopLayerMask;
uniform sampler2D uSecondTopLayerMask;
uniform bool uHasSecondTopLayerMask;

uniform sampler2D uPattern;         // _FoilPattern
uniform sampler2D uGradient;        // _FoilColorGradient
uniform sampler2D uSilverShine;
uniform sampler2D uVarnishSurface;
uniform sampler2D uVarnishShine;    // VarnishShine or VarnishShineBroad, see families/lore.js
uniform sampler2D uGlitter;         // GlitterPattern, optional (dummy-bound when absent)
uniform sampler2D uGoldGradient;    // RainbowGradientGold, ChromeRainbowHotFoil only

uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;

uniform vec2 uFoilTiling;
uniform vec2 uFoilOffset;
uniform vec2 uFoilMorphOffset;
uniform float uFoilMorphSpeed;
uniform float uFoilHighlightStrength;
uniform float uFoilMidToneFactor;
uniform float uFoilMidToneStrength;
uniform float uBrightHighlightsReduction;
uniform float uInkwashStrength;
uniform float uRainbowStrength;
uniform bool uFoilOffsetsRainbow;

uniform float uVarnishType;      // 0 none, 1 highgloss, 2 hotfoil
uniform float uHotFoilSurface;   // 0 matte/none, 1 metallic, 2 snow, 3 colorrainbow
uniform vec2 uVarnishDistortionTiling;
uniform float uVarnishBevelStrength;
uniform float uVarnishDarkenStrength;
uniform float uVarnishDistortionStrength;
uniform float uVarnishHighlightStrength;
uniform float uVarnishOutlineStrength;
uniform vec4 uVarnishLightColor;
uniform float uHotFoilBrightness;
uniform float uHotFoilContrast;
uniform float uGlitterSize;
uniform vec4 uHotFoilColor;
uniform vec4 uSecondHotFoilColor;
uniform vec4 uMetallicInkColor;

vec3 hotFoilResponse(vec2 uv, vec2 tilt, float rotationDegrees, float brightness, float contrast) {
  float highlight = directionalHighlight(uv, tilt, 0.12);
  float shaped = remapContrast(highlight, contrast * 4.0 + 1.0, brightness - 0.5);
  return vec3(shaped);
}

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  vec2 morph = uFoilMorphOffset * sin(uFoilMorphSpeed * (uTilt.x + uTilt.y) * 0.5);
  vec2 patternUv = applyTilingOffset(vUv, uFoilTiling, uFoilOffset) + morph;
  patternUv = applyTilt(patternUv, uTilt, uDeviceRotationDegrees);
  float pattern = texture2D(uPattern, patternUv).r;

  float angleT = (uDeviceRotationDegrees / 360.0);
  float gradientT = uFoilOffsetsRainbow ? fract(pattern + angleT) : fract(pattern);
  vec3 rainbow = sampleGradient(uGradient, gradientT);

  // highlight/midtone shaping: push saturated color into the highlights, a duller tone into
  // the midtones, both scaled by their own preset strengths.
  float highlightMask = smoothstep(0.5, 1.0, pattern) * uFoilHighlightStrength;
  float midtoneMask = (1.0 - abs(pattern - 0.5) * 2.0) * uFoilMidToneStrength * uFoilMidToneFactor;
  vec3 shine = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).rgb;
  vec3 foilColor = rainbow * (highlightMask + midtoneMask) + shine * highlightMask * (1.0 - uBrightHighlightsReduction);
  foilColor *= uInkwashStrength * uRainbowStrength;

  vec3 result = foilColor;
  float alpha = cardAlpha * clamp((highlightMask + midtoneMask) * uInkwashStrength, 0.0, 1.0);

  // ---- top layer (varnish / hot foil), confined to uTopLayerMask ----
  if (uHasTopLayerMask && uVarnishType > 0.5) {
    float topMask = readMask(uTopLayerMask, vUv);
    if (topMask > 0.001) {
      vec3 topColor = vec3(0.0);
      float topAlpha = 0.0;

      if (uVarnishType < 1.5) {
        // HIGHGLOSS: VarnishSurface distortion perturbs where the directional VarnishShine
        // highlight is sampled from, so the streak bends slightly instead of reading as a flat
        // static gradient.
        vec2 distortUv = applyTilingOffset(vUv, uVarnishDistortionTiling, vec2(0.0));
        vec2 distortion = (texture2D(uVarnishSurface, distortUv).rg - 0.5) * uVarnishDistortionStrength;
        float streak = directionalHighlight(vUv + distortion, uTilt, 0.05 + uVarnishBevelStrength * 0.1);
        vec3 shineSample = texture2D(uVarnishShine, vec2(streak, 0.5)).rgb;
        topColor = mix(uVarnishLightColor.rgb * (1.0 - uVarnishDarkenStrength), uVarnishLightColor.rgb, streak) * uVarnishHighlightStrength;
        topColor += shineSample * streak;
        topAlpha = clamp(streak * uVarnishOutlineStrength + uVarnishHighlightStrength * 0.3, 0.0, 1.0);
      } else {
        // HOTFOIL: shared angle-driven response, tinted per HOTFOILSURFACE keyword.
        vec3 response = hotFoilResponse(vUv, uTilt, uDeviceRotationDegrees, uHotFoilBrightness, uHotFoilContrast);
        if (uHotFoilSurface > 2.5) {
          // ChromeRainbowHotFoil: remap the response through the gold/rainbow gradient instead
          // of a flat tint.
          vec3 chrome = sampleGradient(uGoldGradient, response.r);
          topColor = chrome;
        } else if (uHotFoilSurface > 1.5) {
          // SnowHotFoil: pastel/desaturated response + fine glitter micro-highlights.
          vec3 pastel = mix(vec3(response.r), uHotFoilColor.rgb, 0.35);
          float glitter = texture2D(uGlitter, vUv * uGlitterSize).r;
          topColor = pastel + vec3(glitter) * response.r * 0.5;
        } else if (uHotFoilSurface > 0.5) {
          // MetallicHotFoil: tint via _MetallicInkColor / the (possibly API-overridden)
          // _HotFoilColor, response drives a metallic-looking brightness ramp.
          topColor = mix(uMetallicInkColor.rgb, uHotFoilColor.rgb, response.r) * (0.6 + response.r * 0.6);
        } else {
          // Matte / no keyword (e.g. SeaWaveMatteHotFoil): the un-tinted base response at this
          // material's own (deliberately low, per the brief) contrast/brightness values.
          topColor = mix(vec3(0.5), uHotFoilColor.rgb, response.r * 0.6);
        }
        topAlpha = clamp(response.r, 0.0, 1.0);

        if (uHasSecondTopLayerMask) {
          float secondMask = readMask(uSecondTopLayerMask, vUv);
          if (secondMask > 0.001) {
            vec3 secondResponse = hotFoilResponse(vUv, -uTilt, uDeviceRotationDegrees + 90.0, uHotFoilBrightness, uHotFoilContrast);
            vec3 secondColor = mix(vec3(0.5), uSecondHotFoilColor.rgb, secondResponse.r * 0.6);
            topColor = mix(topColor, secondColor, secondMask);
            topAlpha = max(topAlpha, secondMask * secondResponse.r);
          }
        }
      }

      result = mix(result, topColor, topMask * topAlpha);
      alpha = max(alpha, cardAlpha * topMask * topAlpha);
    }
  }

  gl_FragColor = vec4(result * alpha, alpha);
}
