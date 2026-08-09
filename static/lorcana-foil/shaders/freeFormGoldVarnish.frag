// CardFoilFreeFormGoldVarnish -- FreeForm2_RainbowHotFoil (8.10). Base FreeForm2 pass (identical
// math to freeForm.frag) plus a gold/rainbow hot-foil top layer confined to uTopLayerMask:
// GradientGold as the base distribution, RainbowGradientGold laid over it angle-dependently.
// hot_foil_color/second_hot_foil_color (API, already substituted into uHotFoilColor/
// uSecondHotFoilColor by LorcanaFoilRenderer before this shader runs) tint the result.
// Effect-layer-only output (see defaultHolo.frag's header comment).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;
uniform sampler2D uTopLayerMask;
uniform bool uHasTopLayerMask;
uniform sampler2D uSecondTopLayerMask;
uniform bool uHasSecondTopLayerMask;

uniform sampler2D uPattern;      // FreeForm2Pattern
uniform sampler2D uGradient;     // RainbowGradientFreeForm
uniform sampler2D uSilverShine;
uniform sampler2D uGoldBase;     // GradientGold
uniform sampler2D uGoldGradient; // RainbowGradientGold

uniform vec2 uTiling;
uniform vec2 uOffset;
uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;

uniform float uFoilStrength;
uniform float uInkwashStrength;
uniform float uRainbowStrength;
uniform vec4 uHotFoilColor;
uniform vec4 uSecondHotFoilColor;

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  vec2 patternUv = applyTilingOffset(vUv, uTiling, uOffset);
  patternUv = applyTilt(patternUv, uTilt, uDeviceRotationDegrees);
  float pattern = texture2D(uPattern, patternUv).r;
  float angleT = fract(pattern + uDeviceRotationDegrees / 360.0);
  vec3 rainbow = sampleGradient(uGradient, angleT);
  vec3 shine = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).rgb;

  vec3 result = (rainbow + shine * 0.4) * uFoilStrength * uInkwashStrength * uRainbowStrength;
  float alpha = cardAlpha * clamp(pattern * uFoilStrength * uInkwashStrength, 0.0, 1.0);

  if (uHasTopLayerMask) {
    float topMask = readMask(uTopLayerMask, vUv);
    if (topMask > 0.001) {
      vec2 goldUv = applyTilt(vUv, uTilt, uDeviceRotationDegrees);
      float goldBase = texture2D(uGoldBase, vec2(goldUv.x, 0.5)).r;
      float goldAngle = fract(goldBase + uDeviceRotationDegrees / 360.0);
      vec3 goldRainbow = sampleGradient(uGoldGradient, goldAngle);
      vec3 goldColor = mix(goldRainbow, uHotFoilColor.rgb, 0.5) * (0.5 + goldBase * 0.5);
      float goldAlpha = clamp(goldBase * 1.4, 0.0, 1.0);

      result = mix(result, goldColor, topMask * goldAlpha);
      alpha = max(alpha, cardAlpha * topMask * goldAlpha);

      if (uHasSecondTopLayerMask) {
        float secondMask = readMask(uSecondTopLayerMask, vUv);
        if (secondMask > 0.001) {
          vec3 secondColor = mix(goldRainbow, uSecondHotFoilColor.rgb, 0.5) * (0.5 + goldBase * 0.5);
          result = mix(result, secondColor, secondMask * goldAlpha);
          alpha = max(alpha, cardAlpha * secondMask * goldAlpha);
        }
      }
    }
  }

  gl_FragColor = vec4(result * alpha, alpha);
}
