// Shared vertex shader for every foil material -- the WebGL canvas renders one textured quad
// covering the card, all the actual per-material work happens in the fragment shader. Compiled
// once, reused across every program (LorcanaFoilRenderer.js links each fragment shader against
// this same vertex shader instead of recompiling a copy per family).
attribute vec2 aPosition;
attribute vec2 aUv;
varying vec2 vUv;
void main() {
  vUv = aUv;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
