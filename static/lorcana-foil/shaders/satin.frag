// CardFoilSatin -- Satin_None / Satin_HighGloss (same shader graph, differing only in
// VARNISHTYPE keyword + preset values -- confirmed both map to shader_graphs["135"]
// "CardFoilSatin"). Effect-layer-only output (see defaultHolo.frag's header comment).
//
// Base satin (8.5): sample SatinFoilNoise (tiled via _FoilDisplacementTiling) -> use it *
// _FoilDisplacementStrength as a UV/gradient displacement -> fold uTilt's angle into the
// gradient coordinate -> sample RainbowGradientSatin(Wide) -> combine with silver shine,
// weighted by _MotifToRainbowRatio/_LerpMotifMultipliedMaskToRegularMask/_RainbowStrength.
// HighGloss top layer (8.5 "Satin + HighGloss"): same varnish technique as varnishRegular.frag,
// confined to uTopLayerMask, only when uVarnishType (from _VARNISHTYPE_HIGHGLOSS) is active.

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;
uniform sampler2D uTopLayerMask;
uniform bool uHasTopLayerMask;

uniform sampler2D uSatinNoise;
uniform sampler2D uGradient;      // RainbowGradientSatin or RainbowGradientSatinWide
uniform sampler2D uSilverShine;
uniform sampler2D uVarnishSurface;
uniform sampler2D uVarnishShine;

uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;
uniform vec2 uFoilDisplacementTiling;
uniform float uFoilDisplacementStrength;
uniform float uMotifToRainbowRatio;
uniform float uLerpMotifMultipliedMaskToRegularMask;
uniform float uRainbowStrength;

uniform float uVarnishType; // 0 none, 1 highgloss
uniform float uVarnishBevelStrength;
uniform float uVarnishDarkenStrength;
uniform float uVarnishHighlightStrength;
uniform float uVarnishOutlineStrength;
uniform vec4 uVarnishLightColor;

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  vec2 noiseUv = applyTilingOffset(vUv, uFoilDisplacementTiling, vec2(0.0));
  vec2 displacement = (texture2D(uSatinNoise, noiseUv).rg - 0.5) * uFoilDisplacementStrength;
  vec2 gradientUv = applyTilt(vUv + displacement, uTilt, uDeviceRotationDegrees);
  float angleT = fract(uDeviceRotationDegrees / 360.0 + gradientUv.x);
  vec3 rainbow = sampleGradient(uGradient, angleT);
  vec3 shine = texture2D(uSilverShine, gradientUv).rgb;

  // _LerpMotifMultipliedMaskToRegularMask blends between a "mask multiplies the effect
  // directly" response and a flatter, unweighted one -- both variants read from the SAME mask
  // (readMask above), so this only changes how strongly the mask edge is felt, not which mask.
  float maskResponse = mix(1.0, cardAlpha, uLerpMotifMultipliedMaskToRegularMask);
  vec3 foilColor = mix(rainbow, rainbow + shine * 0.5, 0.5) * uRainbowStrength * uMotifToRainbowRatio * maskResponse;
  vec3 result = foilColor;
  float alpha = cardAlpha * clamp(uRainbowStrength * uMotifToRainbowRatio * length(displacement) * 4.0 + luminance(rainbow) * 0.3, 0.0, 1.0);

  if (uHasTopLayerMask && uVarnishType > 0.5) {
    float topMask = readMask(uTopLayerMask, vUv);
    if (topMask > 0.001) {
      vec2 distortUv = applyTilt(vUv, uTilt * 0.5, uDeviceRotationDegrees);
      vec2 distortion = (texture2D(uVarnishSurface, distortUv).rg - 0.5) * (0.02 + uVarnishBevelStrength * 0.1);
      float streak = directionalHighlight(vUv + distortion, uTilt, 0.06 + uVarnishBevelStrength * 0.1);
      vec3 shineSample = texture2D(uVarnishShine, vec2(streak, 0.5)).rgb;
      vec3 glossColor = mix(uVarnishLightColor.rgb * (1.0 - uVarnishDarkenStrength), uVarnishLightColor.rgb, streak);
      glossColor += shineSample * streak;
      vec3 topColor = glossColor * uVarnishHighlightStrength;
      float topAlpha = topMask * clamp(streak * uVarnishOutlineStrength, 0.0, 1.0);

      result = mix(result, topColor, topAlpha);
      alpha = max(alpha, topAlpha);
    }
  }

  gl_FragColor = vec4(result * alpha, alpha);
}
