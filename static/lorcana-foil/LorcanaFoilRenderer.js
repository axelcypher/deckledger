// Single shared WebGL context for the whole app -- one <canvas>, one gl context, one compiled
// program PER SHADER FAMILY (cached, reused across every card), one texture object per loaded
// image (also cached by URL). attach()/detach() reposition + rebind this one canvas onto
// whichever card is currently being interacted with; nothing here ever creates a second
// WebGLRenderingContext ("Nur EIN WebGL-Kontext" / "Nicht new WebGLRenderer() pro Karte").
//
// Uniform-binding contract: each shader family module (./shaders/families/*.js) exports a
// UNIFORM_SPEC describing exactly which JSON preset keys it reads and what GLSL type/uniform
// name they map to (see families/_shared.js for the spec shape). bindUniformSpec() below is the
// one generic binder every family goes through -- the TYPE for each entry is declared
// explicitly per family (not inferred), so there's no risk of a vec2/vec4 GL type mismatch from
// guessing Unity's serialized Vector4-for-everything shape.

import { loadPresets, getMaterial, getShaderFamily, resolveMaterialName, effectTextureUrl, findTextureByAssetName, getNamedSlot, floatValue, vec2Value, colorValue, hexToVec4 } from './presetResolver.js';
import { SHADER_FAMILIES } from './shaders/families/index.js';

const COMMON_GLSL_URL = '/static/lorcana-foil/shaders/common.glsl';
const VERTEX_URL = '/static/lorcana-foil/shaders/fullscreen.vert';

let sourceCache = null; // {common, vertex} -- fetched once, reused for every program compile
async function loadSharedSources() {
  if (!sourceCache) {
    const [common, vertex] = await Promise.all([
      fetch(COMMON_GLSL_URL).then(r => r.text()),
      fetch(VERTEX_URL).then(r => r.text()),
    ]);
    sourceCache = { common, vertex };
  }
  return sourceCache;
}

class LorcanaFoilRenderer {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'lorcana-foil-canvas';
    this.canvas.setAttribute('aria-hidden', 'true');
    this.gl = this.canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true, preserveDrawingBuffer: false })
      || this.canvas.getContext('experimental-webgl', { alpha: true, premultipliedAlpha: true });
    this.supported = Boolean(this.gl);
    this.programs = new Map(); // family name -> {program, uniforms:Map, attribs:{}}
    this.textures = new Map(); // url -> WebGLTexture (or a pending Promise while loading)
    this.presets = null; // resolved once via loadPresets()
    this.quadBuffer = null;
    this.currentCard = null; // the DOM element the canvas is currently attached to
    this.dummyTexture = null; // 1x1 transparent -- see _getDummyTexture()
    if (this.supported) this._setupQuad();
  }

  // A role a given material doesn't reference (e.g. no GlitterPattern on CalendarWave) must
  // still bind SOMETHING to that role's sampler uniform -- texture units are shared GL state
  // across draw calls, so leaving one unbound risks sampling whatever a PREVIOUS card's draw
  // happened to leave on that unit. One shared 1x1 transparent black texture, bound wherever a
  // role can't be resolved for the current material.
  _getDummyTexture() {
    if (this.dummyTexture) return this.dummyTexture;
    const gl = this.gl;
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    this.dummyTexture = tex;
    return tex;
  }

  _setupQuad() {
    const gl = this.gl;
    // one static fullscreen-quad (in clip space, [-1,1]) -- every card reuses this same buffer,
    // only the fragment shader's uniforms/textures change between cards.
    const verts = new Float32Array([
      -1, -1, 0, 0,
       1, -1, 1, 0,
      -1,  1, 0, 1,
       1,  1, 1, 1,
    ]);
    this.quadBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
  }

  async _ensurePresets() {
    if (!this.presets) this.presets = await loadPresets();
    return this.presets;
  }

  async _compileProgram(familyName) {
    if (this.programs.has(familyName)) return this.programs.get(familyName);
    const family = SHADER_FAMILIES[familyName];
    if (!family) return null;
    const gl = this.gl;
    const { common, vertex } = await loadSharedSources();
    const fragSource = await family.loadSource();
    const vs = this._compileShader(gl.VERTEX_SHADER, vertex);
    const fs = this._compileShader(gl.FRAGMENT_SHADER, common + '\n' + fragSource);
    if (!vs || !fs) return null;
    const program = gl.createProgram();
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.bindAttribLocation(program, 0, 'aPosition');
    gl.bindAttribLocation(program, 1, 'aUv');
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.error(`[LorcanaFoilRenderer] link failed for ${familyName}:`, gl.getProgramInfoLog(program));
      return null;
    }
    const entry = { program, family };
    this.programs.set(familyName, entry);
    return entry;
  }

  _compileShader(type, source) {
    const gl = this.gl;
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      console.error('[LorcanaFoilRenderer] shader compile failed:', gl.getShaderInfoLog(shader), '\n--- source ---\n', source);
      return null;
    }
    return shader;
  }

  // Loads (or returns the cached) WebGLTexture for a URL. Card-specific textures (art + the
  // three official masks) are fetched fresh per card -- effect textures (the 26 shared assets)
  // are fetched once ever and reused across every card/session ("Statische Effekttexturen
  // global cachen").
  async _getTexture(url) {
    if (!url) return null;
    const cached = this.textures.get(url);
    if (cached) return cached;
    const promise = new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        const gl = this.gl;
        const tex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
        const pow2 = (n) => (n & (n - 1)) === 0;
        if (pow2(img.width) && pow2(img.height)) {
          gl.generateMipmap(gl.TEXTURE_2D);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        } else {
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        }
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        resolve(tex);
      };
      img.onerror = () => reject(new Error(`texture load failed: ${url}`));
      img.src = url;
    });
    this.textures.set(url, promise);
    const tex = await promise;
    this.textures.set(url, tex); // replace the in-flight promise with the resolved texture
    return tex;
  }

  // Resolves (foilType, topLayerType) -> {materialName, material, familyName} or null if this
  // combination isn't in supported_combinations OR maps to a family we haven't ported --
  // callers (FoilInteractionController) fall back to the CSS effect in either case.
  async resolve(foilType, topLayerType) {
    const presets = await this._ensurePresets();
    const materialName = resolveMaterialName(presets, foilType, topLayerType);
    if (!materialName) return null;
    const material = getMaterial(presets, materialName);
    if (!material) return null;
    const familyName = getShaderFamily(material);
    if (!SHADER_FAMILIES[familyName]) return null;
    return { materialName, material, familyName, presets };
  }

  // Attaches the shared canvas onto `cardElement` (absolutely positioned, sized to its
  // bounding rect -- see FoilInteractionController for the actual DOM insertion/resize logic)
  // and prepares it to render `input` (a LorcanaFoilInput-shaped object: cardImageUrl,
  // foilType, foilMaskUrl, topLayer, topLayerMaskUrl, secondTopLayerMaskUrl, hotFoilColor,
  // secondHotFoilColor). Returns true if a WebGL material was actually resolved+prepared
  // (caller should fade the canvas in), false otherwise (caller stays on CSS).
  async prepare(input) {
    if (!this.supported) return false;
    const resolved = await this.resolve(input.foilType || null, input.topLayer || null);
    if (!resolved) return false;
    const programEntry = await this._compileProgram(resolved.familyName);
    if (!programEntry) return false;

    const [motifTex, motifMaskTex, topMaskTex, secondTopMaskTex] = await Promise.all([
      this._getTexture(input.cardImageUrl).catch(() => null),
      this._getTexture(input.foilMaskUrl).catch(() => null),
      this._getTexture(input.topLayerMaskUrl).catch(() => null),
      this._getTexture(input.secondTopLayerMaskUrl).catch(() => null),
    ]);
    if (!motifTex) return false; // no card art at all -- nothing to render

    this.active = {
      programEntry, material: resolved.material, input,
      motifTex, motifMaskTex, topMaskTex, secondTopMaskTex,
      roleTextures: await this._loadRoleTextures(resolved.material, programEntry.family),
    };
    return true;
  }

  // Each family declares ROLES, not fixed asset names -- e.g. "pattern"/"gradient" resolve to
  // a DIFFERENT concrete texture per material (Lava's pattern is InkwashMask, VerticalWave's is
  // VertWaveTexture, both read through the exact same compiled .frag), and even a stable-
  // sounding role like "varnishShine" resolves to VarnishShine on some materials and
  // VarnishShineBroad on others (see families/*.js roles for the real, JSON-confirmed mapping
  // per family). Resolving by role instead of hardcoding one asset name per family is what lets
  // ONE compiled program serve every material that shares its shader graph.
  async _loadRoleTextures(material, family) {
    // Every role's texture is independent of every other -- load them all IN PARALLEL
    // (Promise.all), not one `await` per role in a loop. A texture-heavy family (Lore has 7
    // roles) awaiting each fetch+decode sequentially could take several times longer than the
    // slowest single one for no reason, which is exactly the kind of latency "Kartentexturen
    // beim Hover nur binden/preloaden" (11) is trying to avoid.
    const entries = await Promise.all(Object.entries(family.roles || {}).map(async ([role, roleDef]) => {
      let slot = null;
      if (roleDef.slot) slot = getNamedSlot(material, roleDef.slot);
      if ((!slot || !slot.textureName) && roleDef.asset) slot = findTextureByAssetName(material, roleDef.asset);
      if ((!slot || !slot.textureName) && roleDef.assets) {
        for (const name of roleDef.assets) {
          slot = findTextureByAssetName(material, name);
          if (slot) break;
        }
      }
      if (!slot || !slot.textureName) {
        // Not every material uses every role this family's shader declares (e.g. CalendarWave
        // has no glitter contribution) -- bind the dummy texture rather than leaving this
        // uniform's texture unit pointing at stale state from a previous card's draw.
        return [role, { tex: this._getDummyTexture(), scale: [1, 1], offset: [0, 0], assetName: null }];
      }
      const tex = await this._getTexture(effectTextureUrl(slot.textureName)).catch(() => this._getDummyTexture());
      return [role, { tex, scale: slot.scale, offset: slot.offset, assetName: slot.textureName }];
    }));
    return Object.fromEntries(entries);
  }

  // Repaints the currently-prepared card. `tiltVec` = [nx, ny] emulated device tilt,
  // `rotationDegrees` = atan2-derived pointer angle -- both computed by
  // FoilInteractionController from the live pointer position, TILT mode only (never
  // auto-scrolling time -- "CSS übernimmt Idle/Time").
  render(tiltVec, rotationDegrees) {
    if (!this.supported || !this.active) return;
    const gl = this.gl;
    const { programEntry, material, input, motifTex, motifMaskTex, topMaskTex, secondTopMaskTex, roleTextures } = this.active;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); // premultiplied-alpha compositing over the DOM card

    gl.useProgram(programEntry.program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuffer);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 16, 8);

    let unit = 0;
    const bindTex = (name, tex) => {
      const loc = gl.getUniformLocation(programEntry.program, name);
      if (!loc || !tex) return;
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.uniform1i(loc, unit);
      unit++;
    };
    const setFloat = (name, value) => {
      const loc = gl.getUniformLocation(programEntry.program, name);
      if (loc) gl.uniform1f(loc, value);
    };
    const setVec2 = (name, value) => {
      const loc = gl.getUniformLocation(programEntry.program, name);
      if (loc) gl.uniform2fv(loc, value);
    };
    const setVec4 = (name, value) => {
      const loc = gl.getUniformLocation(programEntry.program, name);
      if (loc) gl.uniform4fv(loc, value);
    };
    const setBool = (name, value) => setFloat(name, value ? 1 : 0);

    bindTex('uMotif', motifTex);
    bindTex('uMotifMask', motifMaskTex);
    setBool('uHasMotifMask', Boolean(motifMaskTex));
    bindTex('uTopLayerMask', topMaskTex);
    setBool('uHasTopLayerMask', Boolean(topMaskTex));
    bindTex('uSecondTopLayerMask', secondTopMaskTex);
    setBool('uHasSecondTopLayerMask', Boolean(secondTopMaskTex));

    for (const [role, entry] of Object.entries(roleTextures)) {
      const capRole = role.charAt(0).toUpperCase() + role.slice(1);
      bindTex(`u${capRole}`, entry.tex);
      setVec2(`u${capRole}Scale`, entry.scale);
      setVec2(`u${capRole}Offset`, entry.offset);
    }

    setVec2('uTilt', tiltVec);
    setFloat('uDeviceRotationDegrees', rotationDegrees);

    // API colors (hot_foil_color/second_hot_foil_color) override the material preset's own
    // static _HotFoilColor/_SecondHotFoilColor -- "API-Farben überschreiben die Default-Farben
    // des Material-Presets."
    const hotFoilOverride = hexToVec4(input.hotFoilColor);
    const secondHotFoilOverride = hexToVec4(input.secondHotFoilColor);
    setVec4('uHotFoilColor', hotFoilOverride || colorValue(material, '_HotFoilColor'));
    setVec4('uSecondHotFoilColor', secondHotFoilOverride || colorValue(material, '_SecondHotFoilColor'));

    bindUniformSpec({ gl, program: programEntry.program, material, setFloat, setVec2, setVec4 }, programEntry.family.uniformSpec || []);
    // Keyword-derived uniforms (e.g. which VARNISHTYPE/HOTFOILSURFACE branch was active on the
    // original material) -- NOT JSON float/color values, so they go through their own hook
    // rather than bindUniformSpec's jsonKey-lookup path. See families/lore.js for the one
    // family that currently uses this.
    if (programEntry.family.deriveUniforms) {
      for (const [glslName, value] of Object.entries(programEntry.family.deriveUniforms(material))) {
        setFloat(glslName, value);
      }
    }

    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  resize(width, height, dpr) {
    const w = Math.max(1, Math.round(width * dpr));
    const h = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== w) this.canvas.width = w;
    if (this.canvas.height !== h) this.canvas.height = h;
  }

  detachDom() {
    if (this.canvas.parentElement) this.canvas.parentElement.removeChild(this.canvas);
    this.active = null;
    this.currentCard = null;
  }
}

// One instance for the entire app -- imported by FoilInteractionController, never constructed
// per-card. See module docstring.
export const sharedRenderer = new LorcanaFoilRenderer();

function bindUniformSpec(ctx, spec) {
  for (const entry of spec) {
    const { jsonKey, glslName, type, fallback } = entry;
    if (type === 'float') ctx.setFloat(glslName, floatValue(ctx.material, jsonKey, fallback ?? 0));
    else if (type === 'vec2') ctx.setVec2(glslName, vec2Value(ctx.material, jsonKey, fallback ?? [0, 0]));
    else if (type === 'vec4') ctx.setVec4(glslName, colorValue(ctx.material, jsonKey, fallback ?? [1, 1, 1, 1]));
    else if (type === 'bool') ctx.setFloat(glslName, floatValue(ctx.material, jsonKey, fallback ?? 0) ? 1 : 0);
  }
}
