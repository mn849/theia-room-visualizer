/* Theia 3D room viewer.
 *
 * The room photo is reconstructed into a metric 3D room (camera + floor plane +
 * walls, optionally a depth-relief mesh). The view opens at the exact camera
 * pose the photo was taken from, so it looks like the original photo; you can
 * then orbit, pan and zoom, and drag items around the floor or along walls.
 *
 * Items are never generated: each item is the seller's own photo (cutout or
 * untouched) standing in the room at the seller's dimensions. When you orbit,
 * Theia switches to whichever listing photo was taken from the nearest angle;
 * sides that no listing photo shows are drawn as a grey silhouette.
 *
 * Data flows in through window.TheiaScene.update(scene, placements, selected)
 * (called by a Dash clientside callback) and placements flow back out through
 * dash_clientside.set_props('item-placements', ...).
 */
(function () {
  'use strict';
  const PHOTO_HOLD_DEG = 75;      // the photo turns with the item up to this angle, then eases toward you
  const BACK_START_DEG = 115;     // beyond this, no real photo shows that side
  const SNAP_DISTANCE = 0.3;      // metres from a wall to snap flush
  const VIEW_ANGLES = { front: 0, 'front-right': 45, right: 90, back: 180, left: -90, 'front-left': -45 };

  let THREE = null, OrbitControls = null, loading = null, loadFailed = false;
  let state = null;               // live viewer state (renderer, scene, ...)
  let pending = null;             // latest update() arguments
  let lastSceneKey = null;

  function loadThree() {
    if (loading) return loading;
    loading = Promise.all([import('three')])
      .then(function (mods) {
        THREE = mods[0];
        THREE.ColorManagement.enabled = false; // pass photo pixels through untouched
      })
      .catch(function (err) { loadFailed = true; console.warn('Theia 3D viewer unavailable', err); });
    return loading;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function sendPlacements() {
    if (!state || !window.dash_clientside || !window.dash_clientside.set_props) return;
    window.dash_clientside.set_props('item-placements', { data: JSON.parse(JSON.stringify(state.placements)) });
  }

  function sendSelected(id) {
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props('item-select', { value: id });
    }
  }

  // ---------------------------------------------------------------- setup ---
  function ensureViewer(container) {
    if (state && state.container === container && container.isConnected) return state;
    container.replaceChildren();
    const frame = el('div', 'scene3d-frame');
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0xebe9e1, 1);
    frame.appendChild(renderer.domElement);
    const toolbar = el('div', 'scene3d-toolbar');
    const buttons = {};
    const resetBtn = el('button', 'scene3d-btn', 'Reset view');
    resetBtn.type = 'button'; toolbar.appendChild(resetBtn); buttons.reset = resetBtn;
    const badge = el('div', 'scene3d-badge');
    const notice = el('div', 'scene3d-notice');
    const help = el('div', 'scene3d-help', 'Walk through: drag to look around · scroll or W / S to walk · A / D to step sideways · drag an item to move it · Q / E rotate it · hold Alt to stop wall snapping');
    frame.appendChild(toolbar); frame.appendChild(badge); frame.appendChild(notice);
    container.appendChild(frame); container.appendChild(help);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, 4 / 3, 0.05, 80);
    // First-person "walk" state relative to the photo's own camera. Motion is
    // kept within a small radius and angle of where the photo was taken,
    // because that is where a single photo can be reprojected convincingly.
    const controls = { enabled: true, yaw: 0, pitch: 0, offset: null, keys: {} };

    state = {
      container: container, frame: frame, renderer: renderer, scene: scene, camera: camera, controls: controls,
      badge: badge, notice: notice, buttons: buttons, roomGroup: new THREE.Group(), itemGroup: new THREE.Group(),
      textures: new Map(), items: new Map(), placements: {}, selected: null, data: null, mode: 'photo',
      photoCam: null, walls: [], floor: [], ceiling: 2.5, tween: null, drag: null, raycaster: new THREE.Raycaster(),
    };
    scene.add(state.roomGroup); scene.add(state.itemGroup);

    buttons.reset.addEventListener('click', function () { goPhotoView(true); });
    wirePointer(renderer.domElement);
    window.addEventListener('keydown', onKey);
    window.addEventListener('keydown', function (e) { if (state.hover && WALK_KEYS[e.key]) { state.controls.keys[e.key] = true; e.preventDefault(); } });
    window.addEventListener('keyup', function (e) { delete state.controls.keys[e.key]; });
    renderer.domElement.addEventListener('pointerenter', function () { state.hover = true; });
    renderer.domElement.addEventListener('pointerleave', function () { state.hover = false; state.controls.keys = {}; });
    renderer.domElement.addEventListener('wheel', function (e) {
      if (!state.data) return;
      e.preventDefault();
      walk(-e.deltaY * 0.0025, 0);
    }, { passive: false });
    new ResizeObserver(resize).observe(container);
    renderer.setAnimationLoop(tick);
    return state;
  }

  const WALK_KEYS = { w: [1, 0], W: [1, 0], ArrowUp: [1, 0], s: [-1, 0], S: [-1, 0], ArrowDown: [-1, 0], a: [0, -1], A: [0, -1], ArrowLeft: [0, -1], d: [0, 1], D: [0, 1], ArrowRight: [0, 1] };
  const MAX_WALK_M = 2.2, MAX_YAW = THREE_DEG(55), MAX_PITCH = THREE_DEG(25);
  function THREE_DEG(d) { return d * Math.PI / 180; }

  function setMode(mode) { state.mode = mode; }

  // Move the eye on the floor plane (forward/back along the view direction,
  // sideways), staying within MAX_WALK_M of the photo camera and on the floor.
  function walk(forward, side) {
    const c = state.controls, pc = state.photoCam;
    if (!pc) return;
    const yawQ = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), c.yaw);
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(pc.quaternion).applyQuaternion(yawQ); fwd.y = 0; fwd.normalize();
    const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
    const next = c.offset.clone().add(fwd.multiplyScalar(forward)).add(right.multiplyScalar(side));
    if (next.length() > MAX_WALK_M) next.setLength(MAX_WALK_M);
    const world = pc.position.clone().add(next);
    if (!pointInFloor(world.x, world.z)) return;
    c.offset.copy(next);
  }

  function applyWalk() {
    const c = state.controls, pc = state.photoCam;
    Object.keys(c.keys).forEach(function (k) { const v = WALK_KEYS[k]; if (v) walk(v[0] * 0.035, v[1] * 0.03); });
    const yawQ = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), c.yaw);
    const pitchQ = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), c.pitch);
    state.camera.quaternion.copy(yawQ).multiply(pc.quaternion).multiply(pitchQ);
    state.camera.position.copy(pc.position).add(c.offset);
  }

  function resize() {
    if (!state || !state.data) return;
    const cam = state.data.room.scene.camera;
    const aspect = cam.width / cam.height;
    const maxW = state.container.clientWidth || 600;
    let w = maxW, h = w / aspect;
    const maxH = Math.max(320, Math.min(680, window.innerHeight * 0.72));
    if (h > maxH) { h = maxH; w = h * aspect; }
    state.frame.style.width = Math.round(w) + 'px';
    state.frame.style.height = Math.round(h) + 'px';
    state.renderer.setSize(Math.round(w), Math.round(h), true);
    state.camera.aspect = aspect;
    state.camera.updateProjectionMatrix();
  }

  // ----------------------------------------------------------- textures ---
  function texture(src) {
    if (state.textures.has(src)) return state.textures.get(src);
    const tex = new THREE.TextureLoader().load(src);
    tex.colorSpace = THREE.NoColorSpace;
    tex.anisotropy = 4;
    state.textures.set(src, tex);
    return tex;
  }

  // ----------------------------------------------------------------- room ---
  function photoCamera(cam) {
    const pc = new THREE.PerspectiveCamera(cam.fov_y, cam.width / cam.height, 0.05, 80);
    pc.position.fromArray(cam.position);
    const basis = new THREE.Matrix4().makeBasis(
      new THREE.Vector3().fromArray(cam.basis.x), new THREE.Vector3().fromArray(cam.basis.y), new THREE.Vector3().fromArray(cam.basis.z));
    pc.quaternion.setFromRotationMatrix(basis);
    pc.updateMatrixWorld(true); pc.updateProjectionMatrix();
    return pc;
  }

  function projectedMaterial(tex, base, useTexture) {
    return new THREE.ShaderMaterial({
      uniforms: {
        map: { value: tex }, useMap: { value: useTexture ? 1 : 0 },
        photoView: { value: state.photoCam.matrixWorldInverse.clone() },
        photoProj: { value: state.photoCam.projectionMatrix.clone() },
        base: { value: new THREE.Color(base) },
      },
      vertexShader: 'varying vec3 vWorld; void main(){ vec4 w = modelMatrix * vec4(position,1.0); vWorld = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }',
      fragmentShader: [
        'uniform sampler2D map; uniform int useMap; uniform mat4 photoView; uniform mat4 photoProj; uniform vec3 base; varying vec3 vWorld;',
        'void main(){',
        '  vec4 c = photoProj * photoView * vec4(vWorld, 1.0);',
        '  vec3 colour = base;',
        '  if (useMap == 1 && c.w > 0.0) {',
        '    vec2 uv = c.xy / c.w * 0.5 + 0.5;',
        '    if (uv.x >= 0.0 && uv.x <= 1.0 && uv.y >= 0.0 && uv.y <= 1.0) colour = texture2D(map, uv).rgb;',
        '  }',
        '  gl_FragColor = vec4(colour, 1.0);',
        '}'].join('\n'),
      side: THREE.FrontSide,
    });
  }

  function quad(a, b, c, d) { // four Vector3, counter-clockwise when seen from the front
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute([a, b, c, a, c, d].flatMap(function (v) { return [v.x, v.y, v.z]; }), 3));
    g.computeVertexNormals();
    return g;
  }

  function buildRoom(room) {
    const s = room.scene;
    state.roomGroup.clear();
    state.photoCam = photoCamera(s.camera);
    state.ceiling = s.ceiling_m;
    state.floor = s.floor.map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    const tex = texture(room.src);
    const hasDepth = !!room.depth;
    const centre = state.floor.reduce(function (acc, p) { return acc.add(p); }, new THREE.Vector2()).multiplyScalar(1 / state.floor.length);

    // Floor and ceiling (two triangles each). Winding chosen to face inward.
    const f = state.floor.map(function (p) { return new THREE.Vector3(p.x, 0, p.y); });
    let floorGeom = quad(f[0], f[3], f[2], f[1]);
    if (floorGeom.attributes.normal.getY(0) < 0) floorGeom = quad(f[0], f[1], f[2], f[3]);
    state.roomGroup.add(new THREE.Mesh(floorGeom, projectedMaterial(tex, 0xcfc8bb, !hasDepth)));
    const c = f.map(function (v) { return new THREE.Vector3(v.x, state.ceiling, v.z); });
    let ceilGeom = quad(c[0], c[1], c[2], c[3]);
    if (ceilGeom.attributes.normal.getY(0) > 0) ceilGeom = quad(c[0], c[3], c[2], c[1]);
    state.ceilingMesh = new THREE.Mesh(ceilGeom, projectedMaterial(tex, 0xf1efea, !hasDepth));
    state.roomGroup.add(state.ceilingMesh);

    state.walls = s.walls.map(function (w, i) {
      const a = new THREE.Vector2(w.a[0], w.a[1]), b = new THREE.Vector2(w.b[0], w.b[1]);
      const dir = b.clone().sub(a).normalize();
      let n = new THREE.Vector2(-dir.y, dir.x);
      if (n.dot(centre.clone().sub(a)) < 0) n.negate();
      const A = new THREE.Vector3(a.x, 0, a.y), B = new THREE.Vector3(b.x, 0, b.y);
      const At = A.clone().setY(state.ceiling), Bt = B.clone().setY(state.ceiling);
      let g = quad(A, B, Bt, At);
      const gn = new THREE.Vector3(g.attributes.normal.getX(0), 0, g.attributes.normal.getZ(0));
      if (gn.x * n.x + gn.z * n.y < 0) g = quad(B, A, At, Bt);
      const shade = [0xe6e2d9, 0xdedad0, 0xe9e6de, 0xdcd8ce][i % 4];
      const mesh = new THREE.Mesh(g, projectedMaterial(tex, shade, !hasDepth));
      mesh.userData.wall = { name: w.name, a: a, b: b, n: n };
      state.roomGroup.add(mesh);
      return { name: w.name, a: a, b: b, n: n, mesh: mesh };
    });

    if (hasDepth) state.roomGroup.add(depthMesh(room, tex));
    state.badge.textContent = 'Layout: ' + s.layout_source + ' · camera ' + s.camera_height_m.toFixed(2) + ' m high · ' + (hasDepth ? 'depth relief on' : 'flat room box') + ' · confidence ' + s.confidence;
  }

  function depthMesh(room, tex) {
    const s = room.scene, d = room.depth, cam = s.camera;
    const raw = atob(d.depth_mm_b64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
    const depth = new Uint16Array(bytes.buffer);
    const cols = d.cols, rows = d.rows, f = cam.focal_px;
    const positions = new Float32Array(cols * rows * 3), uvs = new Float32Array(cols * rows * 2);
    const v = new THREE.Vector3();
    for (let r = 0; r < rows; r += 1) {
      for (let k = 0; k < cols; k += 1) {
        const i = r * cols + k;
        const u = (k + 0.5) / cols * cam.width - cam.width / 2;
        const w = (r + 0.5) / rows * cam.height - cam.height / 2;
        const z = depth[i] / 1000;
        v.set(u / f * z, -(w / f) * z, -z).applyMatrix4(state.photoCam.matrixWorld);
        positions.set([v.x, v.y, v.z], i * 3);
        uvs.set([(k + 0.5) / cols, 1 - (r + 0.5) / rows], i * 2);
      }
    }
    const index = [];
    const ok = function (a, b, c) {
      const za = depth[a], zb = depth[b], zc = depth[c];
      return Math.max(za, zb, zc) / Math.max(1, Math.min(za, zb, zc)) < 1.1;
    };
    for (let r = 0; r < rows - 1; r += 1) {
      for (let k = 0; k < cols - 1; k += 1) {
        const a = r * cols + k, b = a + 1, c = a + cols, e = c + 1;
        if (ok(a, c, b)) index.push(a, c, b);
        if (ok(b, c, e)) index.push(b, c, e);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
    g.setIndex(index);
    return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide }));
  }

  // ---------------------------------------------------------------- items ---
  function cardMaterial(tex) {
    return new THREE.ShaderMaterial({
      uniforms: { map: { value: tex }, back: { value: 0 }, highlight: { value: 0 } },
      vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
      fragmentShader: [
        'uniform sampler2D map; uniform float back; uniform float highlight; varying vec2 vUv;',
        'void main(){ vec4 t = texture2D(map, vUv); if (t.a < 0.03) discard;',
        '  vec3 grey = vec3(0.62, 0.63, 0.60);',
        '  vec3 colour = mix(t.rgb, grey, back);',
        '  gl_FragColor = vec4(colour, t.a); }'].join('\n'),
      transparent: true, side: THREE.DoubleSide, depthWrite: true,
    });
  }

  function shadowTexture() {
    if (state.shadowTex) return state.shadowTex;
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 128;
    const ctx = canvas.getContext('2d');
    const g = ctx.createRadialGradient(64, 64, 4, 64, 64, 64);
    g.addColorStop(0, 'rgba(0,0,0,0.55)'); g.addColorStop(0.6, 'rgba(0,0,0,0.25)'); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, 128, 128);
    state.shadowTex = new THREE.CanvasTexture(canvas);
    return state.shadowTex;
  }

  function itemDims(item) {
    const d = item.dims || {};
    const w = Math.max(0.05, d.w || 1), h = Math.max(0.02, d.h || 1);
    return { w: w, h: h, d: Math.max(0.03, d.d || Math.min(w, 0.6)) };
  }

  function buildItem(item) {
    const group = new THREE.Group();
    group.userData.id = item.id;
    const dims = itemDims(item);
    const cards = [];
    const views = (item.views && item.views.length) ? item.views : [{ src: item.src, aspect: item.aspect || 1, angle: 0 }];
    views.forEach(function (view) {
      const mat = cardMaterial(texture(view.src));
      let width, height;
      if (item.pose === 'flat') {
        const scale = Math.min(dims.w / view.aspect, dims.d);
        height = scale; width = scale * view.aspect;
      } else {
        const a = THREE.MathUtils.degToRad(view.angle || 0);
        const extent = Math.abs(dims.w * Math.cos(a)) + Math.abs(dims.d * Math.sin(a));
        height = Math.min(dims.h, extent / view.aspect); width = height * view.aspect;
      }
      const geom = new THREE.PlaneGeometry(width, height);
      if (item.pose === 'flat') geom.rotateX(-Math.PI / 2); else geom.translate(0, height / 2, 0);
      const mesh = new THREE.Mesh(geom, mat);
      mesh.userData.itemId = item.id;
      mesh.userData.angle = view.angle || 0;
      mesh.renderOrder = 2;
      group.add(mesh); cards.push(mesh);
    });
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(dims.w * 1.15, (item.pose === 'flat' ? dims.d : dims.d) * 1.25).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false, opacity: item.pose === 'flat' ? 0 : 0.75 }));
    shadow.position.y = 0.003; shadow.renderOrder = 1;
    if (item.pose !== 'wall') group.add(shadow);
    const outline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-dims.w / 2, 0.006, -dims.d / 2), new THREE.Vector3(dims.w / 2, 0.006, -dims.d / 2),
      new THREE.Vector3(dims.w / 2, 0.006, dims.d / 2), new THREE.Vector3(-dims.w / 2, 0.006, dims.d / 2)]),
      new THREE.LineBasicMaterial({ color: 0x1e5a4c, transparent: true, opacity: 0.9, depthTest: false }));
    outline.visible = false; outline.renderOrder = 3;
    if (item.pose !== 'wall') group.add(outline);
    return { group: group, cards: cards, outline: outline, dims: dims, item: item };
  }

  function wallById(name) { return state.walls.find(function (w) { return w.name === name; }) || state.walls[0]; }
  function yawFromNormal(n) { return Math.atan2(n.x, n.y); }

  // Items always start clearly in view: on open floor in front of the photo
  // camera (about 2.4 m away, closer if the floor ends sooner), facing you.
  // If that spot is close to a wall the item is pushed flush against it.
  function defaultPlacement(entry, index) {
    const item = entry.item, dims = entry.dims;
    const pc = state.photoCam;
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(pc.quaternion); fwd.y = 0; fwd.normalize();
    const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
    const facing = Math.atan2(-fwd.x, -fwd.z);
    if (item.pose === 'wall') {
      state.raycaster.set(pc.position.clone(), fwd.clone());
      const hit = state.raycaster.intersectObjects(state.walls.map(function (w) { return w.mesh; }), false)[0];
      if (hit && hit.distance < 10) {
        const w = hit.object.userData.wall;
        const side = right.clone().multiplyScalar(((index % 3) - 1) * 0.8);
        return { x: hit.point.x + side.x + w.n.x * 0.012, z: hit.point.z + side.z + w.n.y * 0.012, yaw: yawFromNormal(w.n), y: Math.max(0.3, 1.45 - dims.h / 2), wall: w.name };
      }
    }
    const base = new THREE.Vector3(pc.position.x, 0, pc.position.z);
    // Up to 15 items: rows of five (centre first, then alternating left and
    // right), each row a little further back. A spot is only used if it is on
    // the floor AND inside the photo's field of view, so new items are always
    // visible when the 3D view opens.
    const slot = [0, -1, 1, -2, 2][index % 5];
    let p = null;
    for (let spread = 0.75; spread >= 0 && !p; spread -= 0.25) {
      for (let dist = (item.pose === 'flat' ? 2.0 : 2.4) + Math.floor(index / 5) * 0.9; dist >= 1.0; dist -= 0.2) {
        const q = base.clone().add(fwd.clone().multiplyScalar(dist)).add(right.clone().multiplyScalar(slot * spread));
        const candidate = { x: q.x, z: q.z, yaw: facing, y: item.pose === 'wall' ? 1.0 : 0 };
        if (footprintInside(entry, candidate) && inPhotoView(q, dims)) { p = candidate; break; }
      }
    }
    if (!p) {
      const q = base.clone().add(fwd.clone().multiplyScalar(1.6));
      p = { x: q.x, z: q.z, yaw: facing, y: 0 };
    }
    if (item.pose === 'upright') snapToWalls(entry, p);
    return p;
  }

  // Is an item standing at q (floor point) visible from the photo camera?
  function inPhotoView(q, dims) {
    const pc = state.photoCam;
    const pts = [q.clone().setY(0.02), q.clone().setY(Math.min(dims.h, 1.2))];
    return pts.every(function (v) {
      const n = v.clone().project(pc);
      return n.z < 1 && Math.abs(n.x) < 0.9 && Math.abs(n.y) < 0.95;
    });
  }

  // A saved placement is only reused if it is still in front of the camera
  // and on the floor (older sessions could leave items far away).
  function placementUsable(entry, p) {
    if (!p || typeof p.x !== 'number' || typeof p.z !== 'number') return false;
    const pc = state.photoCam;
    const dx = p.x - pc.position.x, dz = p.z - pc.position.z;
    if (Math.hypot(dx, dz) > 7) return false;
    return entry.item.pose === 'wall' || pointInFloor(p.x, p.z);
  }

  function applyPlacement(entry) {
    const p = state.placements[entry.item.id];
    entry.group.position.set(p.x, entry.item.pose === 'wall' ? (p.y || 1) : 0.0, p.z);
    entry.group.rotation.set(0, p.yaw || 0, 0);
    const inside = entry.item.pose === 'wall' || footprintInside(entry, p);
    entry.outline.material.color.setHex(inside ? 0x1e5a4c : 0xc0392b);
    entry.fits = inside;
  }

  function buildItems(items) {
    state.itemGroup.clear();
    state.items.clear();
    items.forEach(function (item, index) {
      const entry = buildItem(item);
      state.items.set(item.id, entry);
      state.itemGroup.add(entry.group);
      const existing = state.placements[item.id];
      if (!existing || existing.pose !== item.pose || !placementUsable(entry, existing)) {
        const keepYaw = existing && existing.x === undefined && typeof existing.yaw === 'number' ? { yaw: existing.yaw } : {};
        state.placements[item.id] = Object.assign(defaultPlacement(entry, index), { pose: item.pose }, keepYaw);
      }
      applyPlacement(entry);
    });
  }

  // ------------------------------------------------------ geometry helpers ---
  function pointInFloor(x, z) {
    const poly = state.floor; let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
      const xi = poly[i].x, zi = poly[i].y, xj = poly[j].x, zj = poly[j].y;
      if (((zi > z) !== (zj > z)) && (x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi)) inside = !inside;
    }
    return inside;
  }

  function footprintCorners(entry, p) {
    const c = Math.cos(p.yaw || 0), s = Math.sin(p.yaw || 0), hw = entry.dims.w / 2;
    const hd = entry.item.pose === 'flat' ? entry.dims.d / 2 : entry.dims.d / 2;
    return [[-hw, -hd], [hw, -hd], [hw, hd], [-hw, hd]].map(function (q) {
      return [p.x + q[0] * c + q[1] * s, p.z - q[0] * s + q[1] * c];
    });
  }

  function footprintInside(entry, p) {
    return footprintCorners(entry, p).every(function (q) { return pointInFloor(q[0], q[1]); });
  }

  function snapToWalls(entry, p) {
    let best = null;
    state.walls.forEach(function (w) {
      const rel = new THREE.Vector2(p.x, p.z).sub(w.a);
      const len = w.b.distanceTo(w.a);
      const t = rel.dot(w.b.clone().sub(w.a).normalize());
      if (t < -0.2 || t > len + 0.2) return;
      const dist = rel.dot(w.n);
      if (dist < entry.dims.d / 2 + SNAP_DISTANCE && (!best || dist < best.dist)) best = { wall: w, dist: dist };
    });
    if (!best) { p.wall = null; return p; }
    const push = entry.dims.d / 2 + 0.015 - best.dist;
    p.x += best.wall.n.x * push; p.z += best.wall.n.y * push;
    p.yaw = yawFromNormal(best.wall.n); p.wall = best.wall.name;
    return p;
  }

  // ------------------------------------------------------- per-frame view ---
  function updateViewDependentCards() {
    const camPos = state.camera.position;
    let backView = false;
    state.items.forEach(function (entry) {
      if (entry.item.pose === 'flat') return;
      const g = entry.group;
      const local = g.worldToLocal(camPos.clone());
      const phi = THREE.MathUtils.radToDeg(Math.atan2(local.x, local.z));
      let best = null, bestDelta = 999;
      entry.cards.forEach(function (card) {
        let delta = phi - card.userData.angle;
        delta = ((delta + 540) % 360) - 180;
        if (Math.abs(delta) < Math.abs(bestDelta)) { bestDelta = delta; best = card; }
      });
      entry.cards.forEach(function (card) { card.visible = card === best; });
      if (!best) return;
      const extra = Math.max(0, Math.abs(bestDelta) - PHOTO_HOLD_DEG) * Math.sign(bestDelta);
      best.rotation.y = THREE.MathUtils.degToRad(best.userData.angle + (entry.item.pose === 'wall' ? 0 : extra));
      const backness = THREE.MathUtils.smoothstep(Math.abs(bestDelta), BACK_START_DEG, 160);
      best.material.uniforms.back.value = backness;
      if (backness > 0.3 && entry.item.id === state.selected) backView = true;
    });
    state.notice.textContent = backView ? 'No listing photo shows this side — drawn as a grey silhouette, not invented.' : '';
    state.notice.style.display = backView ? 'block' : 'none';
  }

  function tick() {
    if (!state || !state.data) return;
    if (state.tween) {
      const t = Math.min(1, (performance.now() - state.tween.start) / state.tween.ms);
      const e = t * t * (3 - 2 * t);
      state.camera.position.lerpVectors(state.tween.fromPos, state.tween.toPos, e);
      state.camera.quaternion.slerpQuaternions(state.tween.fromQ, state.tween.toQ, e);
      if (t >= 1) {
        const done = state.tween.done; state.tween = null;
        if (done) done();
      }
    } else if (state.photoCam) {
      applyWalk();
    }
    updateViewDependentCards();
    state.renderer.render(state.scene, state.camera);
  }

  function animateTo(pos, quat, done) {
    state.tween = { start: performance.now(), ms: 650, fromPos: state.camera.position.clone(), toPos: pos, fromQ: state.camera.quaternion.clone(), toQ: quat, done: done };
  }

  function floorCentre() {
    const c = state.floor.reduce(function (acc, p) { return acc.add(p); }, new THREE.Vector2()).multiplyScalar(1 / state.floor.length);
    return new THREE.Vector3(c.x, 0, c.y);
  }

  function goPhotoView(animate) {
    const pc = state.photoCam;
    const c = state.controls;
    const finish = function () { c.yaw = 0; c.pitch = 0; c.offset = new THREE.Vector3(); };
    if (animate) animateTo(pc.position.clone(), pc.quaternion.clone(), finish); else { finish(); applyWalk(); }
  }

  function selectedPosition() {
    const entry = state.selected && state.items.get(state.selected);
    return entry ? entry.group.position.clone().setY(0) : null;
  }

  // ------------------------------------------------------------ pointer ---
  function ndc(event) {
    const box = state.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2((event.clientX - box.left) / box.width * 2 - 1, -((event.clientY - box.top) / box.height) * 2 + 1);
  }

  function pickItem(event) {
    state.raycaster.setFromCamera(ndc(event), state.camera);
    const cards = [];
    state.items.forEach(function (entry) { entry.cards.forEach(function (c) { if (c.visible) cards.push(c); }); });
    const hit = state.raycaster.intersectObjects(cards, false)[0];
    return hit ? hit.object.userData.itemId : null;
  }

  function floorHit(event, height) {
    state.raycaster.setFromCamera(ndc(event), state.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -(height || 0));
    const p = new THREE.Vector3();
    return state.raycaster.ray.intersectPlane(plane, p) ? p : null;
  }

  function wallHit(event) {
    state.raycaster.setFromCamera(ndc(event), state.camera);
    const hit = state.raycaster.intersectObjects(state.walls.map(function (w) { return w.mesh; }), false)[0];
    return hit ? { point: hit.point, wall: hit.object.userData.wall } : null;
  }

  function select(id, notify) {
    state.selected = id;
    state.items.forEach(function (entry, key) { entry.outline.visible = key === id; });
    if (notify && id) sendSelected(id);
  }

  function wirePointer(canvas) {
    canvas.addEventListener('pointerdown', function (event) {
      if (!state.data || event.button !== 0) return;
      const id = pickItem(event);
      if (!id) return;
      event.preventDefault(); event.stopImmediatePropagation();
      select(id, true);
      const entry = state.items.get(id), p = state.placements[id];
      const start = entry.item.pose === 'wall' ? null : floorHit(event, 0);
      state.drag = { id: id, offset: start ? new THREE.Vector2(p.x - start.x, p.z - start.z) : new THREE.Vector2(), pointerId: event.pointerId };
      canvas.setPointerCapture(event.pointerId);
    }, true);
    // Dragging empty space looks around (like turning your head).
    canvas.addEventListener('pointerdown', function (event) {
      if (!state.data || state.drag || state.tween) return;
      state.look = { x: event.clientX, y: event.clientY, yaw: state.controls.yaw, pitch: state.controls.pitch };
      canvas.setPointerCapture(event.pointerId);
      canvas.style.cursor = 'move';
    });
    canvas.addEventListener('pointermove', function (event) {
      if (state.look && !state.drag) {
        const c = state.controls;
        c.yaw = THREE.MathUtils.clamp(state.look.yaw + (event.clientX - state.look.x) * 0.0045, -MAX_YAW, MAX_YAW);
        c.pitch = THREE.MathUtils.clamp(state.look.pitch + (event.clientY - state.look.y) * 0.0035, -MAX_PITCH, MAX_PITCH);
        return;
      }
      if (!state.drag) { canvas.style.cursor = pickItem(event) ? 'grab' : ''; return; }
      const entry = state.items.get(state.drag.id), p = Object.assign({}, state.placements[state.drag.id]);
      if (entry.item.pose === 'wall') {
        const hit = wallHit(event);
        if (!hit) return;
        const n = hit.wall.n;
        p.x = hit.point.x + n.x * 0.012; p.z = hit.point.z + n.y * 0.012;
        p.y = THREE.MathUtils.clamp(hit.point.y - entry.dims.h / 2, 0, Math.max(0, state.ceiling - entry.dims.h));
        p.yaw = yawFromNormal(n); p.wall = hit.wall.name;
      } else {
        const hit = floorHit(event, 0);
        if (!hit) return;
        p.x = hit.x + state.drag.offset.x; p.z = hit.z + state.drag.offset.y;
        if (!pointInFloor(p.x, p.z)) return;
        if (entry.item.pose !== 'flat' && !event.altKey) snapToWalls(entry, p);
      }
      state.placements[state.drag.id] = p;
      applyPlacement(entry);
      canvas.style.cursor = 'grabbing';
    });
    const end = function (event) {
      if (state.look) {
        state.look = null; canvas.style.cursor = '';
        if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      }
      if (!state.drag) return;
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      state.drag = null;
      canvas.style.cursor = '';
      sendPlacements();
    };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
  }

  function onKey(event) {
    if (!state || !state.selected || !state.data) return;
    if (/input|textarea|select/i.test((event.target && event.target.tagName) || '')) return;
    const key = event.key.toLowerCase();
    if (key !== 'q' && key !== 'e') return;
    const entry = state.items.get(state.selected);
    if (!entry || entry.item.pose === 'wall') return;
    const p = state.placements[state.selected];
    p.yaw = (p.yaw || 0) + THREE.MathUtils.degToRad(key === 'q' ? 15 : -15);
    p.wall = null;
    applyPlacement(entry);
    sendPlacements();
  }

  // ------------------------------------------------------------ public ---
  function render() {
    if (!pending) return;
    const container = document.getElementById('scene3d');
    if (!container) return;
    const args = pending;
    const data = args.scene;
    const stageWrap = document.getElementById('preview-wrap');
    if (args.mode !== '3d') {
      if (stageWrap) stageWrap.classList.remove('is-3d');
      container.style.display = 'none';
      if (state) state.renderer.setAnimationLoop(null);
      return;
    }
    if (!data || !data.room || !data.room.scene) {
      if (stageWrap) stageWrap.classList.remove('is-3d');
      container.style.display = 'none';
      return;
    }
    if (loadFailed) {
      container.style.display = 'block';
      container.textContent = '3D viewer could not load (three.js needs an internet connection). Showing the 2D preview instead.';
      container.className = 'scene3d scene3d-failed';
      return;
    }
    ensureViewer(container);
    container.style.display = 'block';
    state.renderer.setAnimationLoop(tick);
    if (stageWrap) stageWrap.classList.add('is-3d');
    const roomKey = JSON.stringify([data.room.src, data.room.scene.camera, data.room.scene.floor, data.room.scene.ceiling_m, !!data.room.depth]);
    const roomChanged = roomKey !== lastSceneKey;
    state.data = data;
    // Placements coming back from Dash win unless we are mid-drag.
    if (!state.drag) state.placements = JSON.parse(JSON.stringify(args.placements || {}));
    if (roomChanged) {
      lastSceneKey = roomKey;
      buildRoom(data.room);
      resize();
    }
    // Placements belong to one room geometry; start fresh when it changes.
    if (state.placements._room !== data.room.key || state.placements._v !== 2) state.placements = { _room: data.room.key, _v: 2 };
    const itemKey = JSON.stringify((data.items || []).map(function (i) { return [i.id, i.pose, i.dims, i.views]; }));
    if (roomChanged || itemKey !== state.itemKey) {
      state.itemKey = itemKey;
      buildItems(data.items || []);
      sendPlacements();
    } else {
      state.items.forEach(function (entry, id) { if (state.placements[id]) applyPlacement(entry); });
    }
    select(args.selected || null, false);
    if (roomChanged) goPhotoView(false);
  }

  window.TheiaScene = {
    update: function (scene, placements, selected, mode) {
      pending = { scene: scene, placements: placements, selected: selected, mode: mode || '2d' };
      window.__theiaSceneItems = scene && scene.items ? scene.items.length : null;
      if (pending.mode !== '3d' && !THREE) { render(); return; }
      loadThree().then(render);
    },
    snapshot: function () { return state ? state.renderer.domElement.toDataURL('image/png') : null; },
    debug: function () { return state; },
  };
})();
