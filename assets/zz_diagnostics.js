/* Theia diagnostics: sends JavaScript errors and a small, image-free snapshot
 * of what is on screen (item positions, sizes, load state) to the local
 * server, which appends them to logs/client_diagnostics.jsonl. This lets the
 * developer debug the preview without screen sharing. No photos, URLs of
 * listings, or personal data are sent. Disable with ?nodiag in the URL.
 */
(function () {
  if (location.search.indexOf('nodiag') !== -1) return;
  let last = '';
  function send(kind, payload) {
    try {
      const body = JSON.stringify({ kind: kind, payload: payload });
      if (body.length > 20000) return;
      fetch('/client-log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body, keepalive: true });
    } catch (e) { /* never break the app */ }
  }
  window.addEventListener('error', function (e) {
    send('error', { message: String(e.message), source: String(e.filename || '').split('/').pop(), line: e.lineno, col: e.colno });
  });
  window.addEventListener('unhandledrejection', function (e) {
    send('promise', { message: String(e.reason && (e.reason.stack || e.reason.message) || e.reason).slice(0, 1500) });
  });
  const origError = console.error;
  console.error = function () {
    try { send('console', { message: Array.prototype.map.call(arguments, function (a) { if (a && a.stack) return String(a.stack); if (a && typeof a === 'object') { try { return JSON.stringify(a).slice(0, 800); } catch (e) { return String(a); } } return String(a); }).join(' ').slice(0, 1500) }); } catch (e) { /* ignore */ }
    return origError.apply(console, arguments);
  };

  function rect(el, ref) {
    const r = el.getBoundingClientRect(), b = ref.getBoundingClientRect();
    return { x: Math.round(r.left - b.left), y: Math.round(r.top - b.top), w: Math.round(r.width), h: Math.round(r.height) };
  }
  function snapshot() {
    const mode = (document.querySelector('#view-mode input:checked') || {}).value || null;
    const stage = document.getElementById('room-stage');
    const wrap = document.getElementById('preview-wrap');
    const snap = { mode: mode, wrapClass: wrap ? wrap.className : null, stage: null, overlays: [], scene: null };
    if (stage) {
      snap.stage = Object.assign(rect(stage, stage), { display: getComputedStyle(stage).display, visible: stage.offsetParent !== null });
      stage.querySelectorAll('.item-overlay').forEach(function (o) {
        const img = o.querySelector('img');
        const cs = getComputedStyle(o);
        snap.overlays.push({ rect: rect(o, stage), style: { left: o.style.left, top: o.style.top, width: o.style.width, transform: o.style.transform }, widthM: o.dataset.widthM, depthM: o.dataset.depthM, hasCamera: !!stage.dataset.camera, display: cs.display, opacity: cs.opacity, visibility: cs.visibility, img: img ? { complete: img.complete, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight, rendered: rect(img, stage) } : null });
      });
    }
    const container = document.getElementById('scene3d');
    if (container) snap.scene3dDisplay = getComputedStyle(container).display;
    snap.sceneDataItems = window.__theiaSceneItems === undefined ? 'never-called' : window.__theiaSceneItems;
    snap.threeLoaded = !!(window.TheiaScene && window.TheiaScene.debug && window.TheiaScene.debug());
    try {
      const st = window.TheiaScene && window.TheiaScene.debug();
      if (st && st.data) {
        const items = [];
        st.items.forEach(function (entry, id) {
          const pos = entry.group.position;
          const p = pos.clone(); p.y += entry.dims.h / 2;
          const ndc = p.project(st.camera);
          const card = entry.cards.find(function (c) { return c.visible; });
          items.push({ pos: [pos.x, pos.y, pos.z].map(function (v) { return +v.toFixed(2); }), ndc: [ndc.x, ndc.y, ndc.z].map(function (v) { return +v.toFixed(2); }), dims: entry.dims, cards: entry.cards.length, visibleCard: !!card, texLoaded: card ? !!(card.material.uniforms.map.value && card.material.uniforms.map.value.image) : null, fits: entry.fits });
        });
        const cam = st.camera.position;
        snap.scene = { items: items, camera: [cam.x, cam.y, cam.z].map(function (v) { return +v.toFixed(2); }), floor: st.floor.map(function (p) { return [+p.x.toFixed(2), +p.y.toFixed(2)]; }), canvas: [st.renderer.domElement.width, st.renderer.domElement.height], dataItems: (st.data.items || []).length };
      }
    } catch (e) { snap.sceneError = String(e); }
    const key = JSON.stringify(snap);
    if (key !== last) { last = key; send('snapshot', snap); }
  }
  setInterval(snapshot, 4000);
})();
