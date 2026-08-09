// Wires real pointer events to the shared WebGL renderer: hover (mouse) attaches the canvas
// over a card, pointer position drives the emulated device-tilt uniforms every frame, leaving
// fades the canvas back out and detaches it. Exactly one <canvas> ever exists (sharedRenderer,
// see LorcanaFoilRenderer.js) -- this controller only repositions/reprepares it, never
// constructs a second one ("Nur EIN WebGL-Kontext").
//
// Crossfade (5): CSS-Foil-Layer opacity 1->0 while InteractiveCanvas opacity 0->1, ~150-200ms,
// reversed on leave. Reuses the exact same .is-webgl-active class + transition:opacity pattern
// already on foil-fx-a/foil-fx-b (see foil-effects.css) -- toggling one class fades both the
// outgoing CSS layers AND (via the canvas's own opacity transition below) the incoming WebGL
// layer together.
//
// Tilt (6): nx/ny normalized from pointer position within the card's own rect, smoothed (only
// the smoothing constant is a local integration choice -- "nur die Pointer-Glättung darf für
// Desktop angepasst werden, da die App dafür Gyro-Smoothing benutzt"), fed as uTilt/
// uDeviceRotationDegrees. TILT mode only, never auto-scrolling time -- "CSS übernimmt Idle/Time".

import { sharedRenderer } from './LorcanaFoilRenderer.js';

const FADE_MS = 180;
const SMOOTHING = 0.18; // local integration constant, not a JSON preset value -- see header

class FoilInteractionController {
  constructor() {
    this.card = null;
    this.rafId = null;
    this.tilt = [0, 0];
    this.targetTilt = [0, 0];
    this.rotation = 0;
    this.targetRotation = 0;
    this.fadeTimer = null;
    this._tick = this._tick.bind(this);

    const canvas = sharedRenderer.canvas;
    canvas.style.position = 'fixed';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '5';
    canvas.style.opacity = '0';
    canvas.style.transition = `opacity ${FADE_MS}ms ease-out`;

    this._reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  }

  // WebGL support + prefers-reduced-motion gate together -- "Bei prefers-reduced-motion WebGL-
  // Interaktion deaktivierbar machen": reduced-motion users simply never leave the CSS effect,
  // same code path as "no WebGL support" / "no official mask" / "unknown combination" (12).
  get enabled() {
    return sharedRenderer.supported && !this._reducedMotionQuery.matches;
  }

  // Returns true if the WebGL layer actually took over (caller/CSS should treat the card as
  // "webgl active"); false means every fallback condition applies and the card stays on its
  // existing CSS effect untouched -- never a blank card, never a thrown error (12).
  async attach(cardElement, foilInput) {
    if (!this.enabled) return false;
    let prepared = false;
    try {
      prepared = await sharedRenderer.prepare(foilInput);
    } catch {
      prepared = false;
    }
    if (!prepared) return false;

    // A second hover could have started (and finished) while the async prepare() above was
    // in flight, e.g. a fast mouse pass over several cards -- only proceed if we're still the
    // most recently requested card.
    if (this._pendingCard && this._pendingCard !== cardElement) return false;

    if (this.fadeTimer) { clearTimeout(this.fadeTimer); this.fadeTimer = null; }
    this.card = cardElement;
    this.tilt = [0, 0];
    this.targetTilt = [0, 0];
    this.rotation = 0;
    this.targetRotation = 0;
    this._positionCanvas();
    if (!sharedRenderer.canvas.parentElement) document.body.appendChild(sharedRenderer.canvas);
    // Force layout before flipping opacity so the transition actually animates instead of
    // starting from a not-yet-applied style.
    void sharedRenderer.canvas.offsetWidth;
    sharedRenderer.canvas.style.opacity = '1';
    this._startLoop();
    return true;
  }

  // Call before attach() resolves if you need to know "is a hover currently being prepared for
  // this element" (used by the modal wiring to avoid firing a second prepare() for the same
  // card on a stray extra pointerover).
  markPending(cardElement) {
    this._pendingCard = cardElement;
  }

  updatePointer(clientX, clientY) {
    if (!this.card) return;
    const r = this.card.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    // Exactly the brief's own formula (6).
    const nx = ((clientX - r.left) / r.width) * 2 - 1;
    const ny = 1 - ((clientY - r.top) / r.height) * 2;
    this.targetTilt = [nx, ny];
    this.targetRotation = (Math.atan2(ny, nx) * 180) / Math.PI;
    this._positionCanvas(); // the card may have moved/resized (e.g. still mid CSS transition)
  }

  fadeOutAndDetach() {
    if (!this.card) return;
    this._pendingCard = null;
    sharedRenderer.canvas.style.opacity = '0';
    if (this.fadeTimer) clearTimeout(this.fadeTimer);
    // RAF keeps running through the fade (11: "sobald Fade-out abgeschlossen ist" -- not
    // before) so the effect doesn't visibly freeze mid-fade.
    this.fadeTimer = setTimeout(() => {
      this._stopLoop();
      sharedRenderer.detachDom();
      this.card = null;
      this.fadeTimer = null;
    }, FADE_MS);
  }

  _positionCanvas() {
    if (!this.card) return;
    const r = this.card.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2); // 2x is already plenty for a foil layer
    const canvas = sharedRenderer.canvas;
    canvas.style.left = `${r.left}px`;
    canvas.style.top = `${r.top}px`;
    canvas.style.width = `${r.width}px`;
    canvas.style.height = `${r.height}px`;
    canvas.style.borderRadius = getComputedStyle(this.card).borderRadius;
    sharedRenderer.resize(r.width, r.height, dpr);
  }

  _startLoop() {
    if (this.rafId) return;
    this.rafId = requestAnimationFrame(this._tick);
  }

  _stopLoop() {
    if (this.rafId) cancelAnimationFrame(this.rafId);
    this.rafId = null;
  }

  _tick() {
    if (!this.card) { this.rafId = null; return; }
    this.tilt[0] += (this.targetTilt[0] - this.tilt[0]) * SMOOTHING;
    this.tilt[1] += (this.targetTilt[1] - this.tilt[1]) * SMOOTHING;
    // shortest-path angle smoothing so it doesn't spin the long way round crossing +-180deg.
    let delta = this.targetRotation - this.rotation;
    delta = ((delta + 180) % 360 + 360) % 360 - 180;
    this.rotation += delta * SMOOTHING;
    sharedRenderer.render(this.tilt, this.rotation);
    this.rafId = requestAnimationFrame(this._tick);
  }
}

export const foilInteractionController = new FoilInteractionController();
