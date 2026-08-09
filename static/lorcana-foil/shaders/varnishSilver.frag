// CardVarnishSilver -- Silver_HighGloss. NOT CardVarnishRegular + a generic gloss: a dedicated
// shader graph (shader_graphs path_id 131, distinct from CardVarnishRegular5's 127) that adds a
// silver BACKGROUND reflection (across the whole card, via uMotifMask) underneath the same
// HighGloss top-layer pass -- confirmed by _HighlightBackgroundStrength existing on both
// materials but at 0.0 on Regular vs 0.3 on Silver, and CardVarnishSilver alone having a real
// _MotifMask slot (8.21). Effect-layer-only output (see defaultHolo.frag's header comment).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;
uniform sampler2D uTopLayerMask;
uniform bool uHasTopLayerMask;

uniform sampler2D uVarnishSurface;
uniform sampler2D uVarnishShine;
uniform sampler2D uSilverShine;

uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;
uniform float uVarnishBevelStrength;
uniform float uVarnishDarkenStrength;
uniform float uVarnishHighlightStrength;
uniform float uHighlightBackgroundStrength;
uniform vec4 uLightColor;

void main() {
  vec3 result = vec3(0.0);
  float alpha = 0.0;

  // Background silver reflection -- same diagonal-shine technique as silver.frag, scaled by
  // _HighlightBackgroundStrength (the property this whole shader exists to add over
  // CardVarnishRegular5) and confined to the card's own foil mask.
  if (uHighlightBackgroundStrength > 0.001) {
    float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;
    vec2 centered = vUv - 0.5;
    float tiltLen = length(uTilt);
    vec2 tiltDir = tiltLen > 0.0001 ? uTilt / tiltLen : vec2(0.7071, 0.7071);
    float diag = dot(centered, tiltDir) + 0.5 + dot(uTilt, vec2(0.3));
    vec3 backgroundShine = texture2D(uSilverShine, vec2(fract(diag), 0.5)).rgb;
    result += backgroundShine * uHighlightBackgroundStrength;
    alpha = max(alpha, cardAlpha * clamp(luminance(backgroundShine) * uHighlightBackgroundStrength, 0.0, 1.0));
  }

  if (uHasTopLayerMask) {
    float topMask = readMask(uTopLayerMask, vUv);
    if (topMask > 0.001) {
      vec2 distortUv = applyTilt(vUv, uTilt * 0.5, uDeviceRotationDegrees);
      vec2 distortion = (texture2D(uVarnishSurface, distortUv).rg - 0.5) * (0.02 + uVarnishBevelStrength * 0.1);
      float streak = directionalHighlight(vUv + distortion, uTilt, 0.06 + uVarnishBevelStrength * 0.1);
      vec3 shineSample = texture2D(uVarnishShine, vec2(streak, 0.5)).rgb;
      vec3 specular = texture2D(uSilverShine, distortUv).rgb;

      vec3 glossColor = mix(uLightColor.rgb * (1.0 - uVarnishDarkenStrength), uLightColor.rgb, streak);
      glossColor += (shineSample + specular * 0.5) * streak;
      vec3 topColor = glossColor * uVarnishHighlightStrength;
      float topAlpha = topMask * clamp(streak * uVarnishHighlightStrength, 0.0, 1.0);

      result = mix(result, topColor, topAlpha);
      alpha = max(alpha, topAlpha);
    }
  }

  gl_FragColor = vec4(result * alpha, alpha);
}
