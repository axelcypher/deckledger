// CardFoilSilver5 -- Silver_None. NOT a rainbow holo: a directional, mostly silver/white
// reflective shine (8.1). No RainbowGradient* asset exists on this material at all (confirmed:
// CardFoilSilver's texture list is just CardMasks + SilverShine) -- deliberately not adding one.
//
// Ablauf: derive a diagonal shine coordinate from uTilt -> sample SilverShine along it -> clip
// by the official foil mask -> apply _RainbowStrength from the preset. Effect-layer-only output
// (see defaultHolo.frag's header comment for the compositing model -- same here).

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;
uniform sampler2D uSilverShine;

uniform vec2 uTilt;
uniform float uRainbowStrength;

void main() {
  float cardAlpha = uHasMotifMask ? readMask(uMotifMask, vUv) : 1.0;

  // A diagonal coordinate along the tilt axis -- SilverShine IS the effect here (a real
  // asset), not a supplementary specular on top of something else.
  vec2 centered = vUv - 0.5;
  float tiltLen = length(uTilt);
  vec2 tiltDir = tiltLen > 0.0001 ? uTilt / tiltLen : vec2(0.7071, 0.7071);
  float diag = dot(centered, tiltDir) + 0.5 + dot(uTilt, vec2(0.3));
  vec3 shine = texture2D(uSilverShine, vec2(fract(diag), 0.5)).rgb;

  vec3 foil = shine * uRainbowStrength;
  float alpha = cardAlpha * clamp(luminance(shine) * uRainbowStrength, 0.0, 1.0);
  gl_FragColor = vec4(foil * alpha, alpha);
}
