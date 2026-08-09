// CardFoilTempest -- Tempest_None. Same property signature as DefaultHoloFoil
// (_Inkwash_Compactness/_Inkwash_Strength/_RainbowStrength/_Offset/_Tiling/_Tilt) but a
// genuinely distinct shader graph (shader_graphs["136"] vs. "137") -- kept as its own file per
// the 10-family list rather than folded into defaultHolo.frag. Distinguishing trait per the
// brief (8.7): hard silver light PEAKS on top of the pattern, not just an ambient specular.
// Effect-layer-only output (see defaultHolo.frag's header comment).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;

uniform sampler2D uPattern;   // TempestPatterns
uniform sampler2D uGradient;  // RainbowGradientTempestGlitter
uniform sampler2D uSilverShine;

uniform vec2 uTiling;
uniform vec2 uOffset;
uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;

uniform float uInkwashStrength;
uniform float uInkwashCompactness;
uniform float uRainbowStrength;

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  vec2 patternUv = applyTilingOffset(vUv, uTiling, uOffset);
  patternUv = applyTilt(patternUv, uTilt, uDeviceRotationDegrees);
  float structureRaw = texture2D(uPattern, patternUv).r;
  float structure = clamp((structureRaw - 0.5) * uInkwashCompactness + 0.5, 0.0, 1.0);

  vec3 rainbow = sampleGradient(uGradient, structure);

  // Hard peaks: a steep power curve turns the ambient shine into sharp, sparse highlights
  // instead of a soft ambient specular (the trait the brief specifically calls out for
  // Tempest -- "SilverShine für harte Lichtpeaks addieren").
  float shineRaw = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).r;
  float peak = pow(shineRaw, 6.0) * 8.0;

  vec3 foil = (rainbow * structure + vec3(peak)) * uInkwashStrength * uRainbowStrength;
  float alpha = cardAlpha * clamp((structure * uInkwashStrength) + peak, 0.0, 1.0);
  gl_FragColor = vec4(foil * alpha, alpha);
}
