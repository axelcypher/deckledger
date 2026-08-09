// CardVarnishRegular5 -- None_HighGloss (a HighGloss top layer with no underlying foil_type at
// all). Renders ONLY within uTopLayerMask (8.20) -- no base foil pass, unlike every other
// family here. Effect-layer-only output (see defaultHolo.frag's header comment).

varying vec2 vUv;

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
uniform vec4 uLightColor;

void main() {
  vec3 result = vec3(0.0);
  float alpha = 0.0;

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

      result = glossColor * uVarnishHighlightStrength;
      alpha = topMask * clamp(streak * uVarnishHighlightStrength, 0.0, 1.0);
    }
  }

  gl_FragColor = vec4(result * alpha, alpha);
}
