// Shared WebGL1/GLSL ES 1.0 utilities used by every ported shader family. GLSL has no
// #include, so this file is prepended (string concatenation) onto every fragment shader's
// source at compile time -- see LorcanaFoilRenderer.js's compileProgram(). Kept as real .glsl
// so it reads/diffs like actual shader code, not a JS template-string blob.

precision highp float;

// ---- tiling / offset ----
// Direct port of Unity's own UV transform convention (every ported material's _Tiling/_Offset
// or _FoilTiling/_FoilOffset color-slot pair): uv * tiling + offset. Nothing fancier -- this is
// intentionally the same one-liner Unity's ShaderGraph "Tiling And Offset" node compiles down to.
vec2 applyTilingOffset(vec2 uv, vec2 tiling, vec2 offset) {
  return uv * tiling + offset;
}

// ---- tilt ----
// Rotates uv around its own center by `rotationDegrees`, then nudges it along `tilt` -- the
// same two inputs the original app derives from DeviceRotation/DeviceTilt (KEYWORD_TILT mode),
// fed here from the emulated mouse-tilt instead (see FoilInteractionController.js). `tilt` is
// expected in roughly [-1,1] per axis, matching the nx/ny the brief defines from pointer
// position; the 0.5 scale keeps the pattern shift subtle (a full 1.0 unit shift would slide the
// pattern clean off itself) -- this scale is a UV-space integration constant for turning a
// [-1,1] pointer range into a sane pattern-shift range, not a borrowed/estimated MATERIAL
// value, so it stays here rather than being pulled from the JSON.
vec2 applyTilt(vec2 uv, vec2 tilt, float rotationDegrees) {
  float rad = radians(rotationDegrees);
  float c = cos(rad);
  float s = sin(rad);
  vec2 centered = uv - 0.5;
  vec2 rotated = vec2(centered.x * c - centered.y * s, centered.x * s + centered.y * c);
  return rotated + 0.5 + tilt * 0.5;
}

// ---- masks ----
// Alpha masks are just luminance here (the extracted/proxied mask JPGs are greyscale already,
// per app.py's foil-mask/foil-layer-mask routes) -- red channel is enough, avoids assuming a
// specific channel-packing convention beyond "grey means grey".
float readMask(sampler2D mask, vec2 uv) {
  return texture2D(mask, uv).r;
}

// ---- blending ----
// Unity's ShaderGraph "Blend" node, Screen mode: 1 - (1-base)*(1-blend). Used wherever a
// material's own compiled shader graph would have used a Screen blend node (kept as its own
// utility per the brief's common-shader-aufbau list, not inlined ad hoc per shader).
vec3 screenBlend(vec3 base, vec3 foil) {
  return vec3(1.0) - (vec3(1.0) - base) * (vec3(1.0) - foil);
}

// ---- gradients ----
// Every "RainbowGradient*"/"GradientGold" asset is a 1D strip (see the extracted textures --
// e.g. RainbowGradientLore is 1024x32): sampling along its horizontal axis at v=0.5 reads the
// gradient at position t regardless of the strip's exact height.
vec3 sampleGradient(sampler2D gradient, float t) {
  return texture2D(gradient, vec2(clamp(t, 0.0, 1.0), 0.5)).rgb;
}

// ---- contrast / brightness remap ----
// Standard pivot-at-0.5 contrast (v-0.5)*contrast+0.5, then a brightness add -- matches how
// Unity ShaderGraph's Contrast+brightness combo nodes read (each ported *_HotFoilContrast/
// *_HotFoilBrightness, *_MetallicContrast/*_MetallicBrightness pair in the JSON feeds this).
float remapContrast(float v, float contrast, float brightness) {
  return clamp((v - 0.5) * contrast + 0.5 + brightness, 0.0, 1.0);
}

// ---- misc shared helpers (not in the brief's explicit list, but needed by >1 family below) ----

// Distance-from-center-ish falloff used by the varnish/glare style highlights (directed light
// streak that's strongest where the tilt vector points).
float directionalHighlight(vec2 uv, vec2 tiltDir, float width) {
  vec2 centered = uv - 0.5;
  float len = length(tiltDir);
  vec2 dir = len > 0.0001 ? tiltDir / len : vec2(0.0, 1.0);
  float d = dot(centered, dir);
  return exp(-(d * d) / max(width, 0.0001));
}

float luminance(vec3 c) {
  return dot(c, vec3(0.2126, 0.7152, 0.0722));
}
