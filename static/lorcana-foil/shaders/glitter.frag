// CardFoilGlitter -- Glitter_None. No JS particle system ("Kein zufälliges JS-Partikelsystem
// verwenden") -- multiple samples of the real GlitterPattern texture at slightly different
// tilt-shifted offsets (8.6), combined into narrow peaks, colored from the gradient, plus a
// silver specular. Effect-layer-only output (see defaultHolo.frag's header comment).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;

uniform sampler2D uGlitter;
uniform sampler2D uGradient; // RainbowGradientTempestGlitter
uniform sampler2D uSilverShine;

uniform vec2 uTilt;
uniform float uDeviceRotationDegrees;
uniform float uGlitterSize;
uniform float uRainbowToMotifWeight;
uniform float uInkwashStrength;
uniform float uRainbowStrength;

float glitterSample(vec2 uv, vec2 offset) {
  return texture2D(uGlitter, uv * uGlitterSize + offset).r;
}

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  // Three samples of the SAME real texture, each nudged by a different fraction of the tilt
  // vector -- combining them (instead of reading it once) is what gives the sparkle its
  // "different facets catch the light at different angles" read as the tilt changes, without
  // synthesizing anything procedurally.
  float s1 = glitterSample(vUv, uTilt * 0.10);
  float s2 = glitterSample(vUv, uTilt * -0.17 + vec2(0.37, 0.11));
  float s3 = glitterSample(vUv, uTilt * 0.23 + vec2(0.71, 0.53));
  float combined = s1 * s2 * s3;
  // Narrow the result into sparse, bright peaks rather than a broad grey wash.
  float peak = pow(combined, 3.0) * 24.0;

  float gradientT = fract(peak + uDeviceRotationDegrees / 360.0);
  vec3 peakColor = sampleGradient(uGradient, gradientT);
  vec3 shine = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).rgb;

  vec3 foil = mix(peakColor, shine, 0.4) * clamp(peak, 0.0, 1.0) * uRainbowToMotifWeight * uRainbowStrength;
  float alpha = cardAlpha * clamp(peak * uInkwashStrength, 0.0, 1.0);
  gl_FragColor = vec4(foil * alpha, alpha);
}
