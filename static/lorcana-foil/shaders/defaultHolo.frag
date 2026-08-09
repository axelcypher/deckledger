// DefaultHoloFoil -- shared kernel for Lava / VerticalWave / RainbowPillars (Lava_None,
// VerticalWave_None, RainbowPillars_None). Per-material identity lives entirely in which
// texture is bound to uPattern/uGradient (see families/defaultHoloFoil.js's `roles`) -- this
// shader has no per-material branching itself, matching "nicht 22 separate Shader schreiben".
//
// Ablauf (8.2-8.4): tile/offset the UV -> sample the pattern as an organic distribution,
// phase-shifted by tilt -> turn (structure + tilt) into a 0-1 gradient coordinate -> sample the
// rainbow gradient there -> add a silver specular -> scale everything by
// Inkwash_Strength/Inkwash_Compactness/RainbowStrength -> clip with the official foil mask.
//
// Output is the EFFECT LAYER ONLY (premultiplied RGB + alpha), not the card art recomposited --
// "Der WebGL-Canvas rendert nur die Effekt-Schicht transparent über der DOM-Karte" (7). The real
// card <img> sits beneath the canvas in the DOM; LorcanaFoilRenderer's CSS gives the canvas a
// screen/color-dodge-style mix-blend-mode so the effect brightens/tints that real image instead
// of this shader re-drawing it. uMotif is sampled anyway for the (currently unused, kept for
// future per-family tuning) case a family wants art-luminance-adaptive strength.

varying vec2 vUv;

uniform sampler2D uMotifMask;
uniform bool uHasMotifMask;

uniform sampler2D uPattern;   // InkwashMask / VertWaveTexture / RainbowPillarsPattern
uniform sampler2D uGradient;  // RainbowGradientInkwash / RainbowGradientSeaWave
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
  // _Inkwash_Compactness pulls the organic pattern toward tighter/looser bands -- a
  // contrast-style remap around its own midpoint (no matching *Brightness term exists for this
  // one in the JSON, unlike the Hot/MetallicContrast+Brightness pairs elsewhere).
  float structure = clamp((structureRaw - 0.5) * uInkwashCompactness + 0.5, 0.0, 1.0);

  float gradientT = fract(structure + dot(uTilt, vec2(0.5)));
  vec3 rainbow = sampleGradient(uGradient, gradientT);
  vec3 shine = texture2D(uSilverShine, applyTilt(vUv, uTilt, uDeviceRotationDegrees)).rgb;

  vec3 foil = (rainbow + shine * 0.5) * uInkwashStrength * uRainbowStrength;
  float alpha = cardAlpha * clamp(structure * uInkwashStrength, 0.0, 1.0);
  gl_FragColor = vec4(foil * alpha, alpha);
}
