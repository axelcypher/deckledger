// CardFoilFreeForm -- FreeForm1_None / FreeForm2_None. Same kernel for both (8.8/8.9) --
// "Gleicher Kernel wie FreeForm1. Nur Texture und Preset ändern, keinen zweiten Shader
// schreiben." Per-material identity is which pattern texture is bound (FreeForm1Pattern vs.
// FreeForm2Pattern -- see families/freeForm.js). Effect-layer-only output (see defaultHolo.frag
// header comment).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;

uniform sampler2D uPattern;  // FreeForm1Pattern / FreeForm2Pattern
uniform sampler2D uGradient; // RainbowGradientFreeForm
uniform sampler2D uSilverShine;

uniform vec2 uTiling;
uniform vec2 uOffset;
uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;

uniform float uFoilStrength;
uniform float uInkwashStrength;
uniform float uRainbowStrength;

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  vec2 patternUv = applyTilingOffset(vUv, uTiling, uOffset);
  patternUv = applyTilt(patternUv, uTilt, uDeviceRotationDegrees);
  float pattern = texture2D(uPattern, patternUv).r;

  float angleT = fract(pattern + uDeviceRotationDegrees / 360.0);
  vec3 rainbow = sampleGradient(uGradient, angleT);
  vec3 shine = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).rgb;

  vec3 foil = (rainbow + shine * 0.4) * uFoilStrength * uInkwashStrength * uRainbowStrength;
  float alpha = cardAlpha * clamp(pattern * uFoilStrength * uInkwashStrength, 0.0, 1.0);
  gl_FragColor = vec4(foil * alpha, alpha);
}
