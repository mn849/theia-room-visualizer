(function () {
  function wireDrag() {
    const stage = document.getElementById('room-stage');
    if (!stage) return;
    let floor = null;
    try { floor = JSON.parse(stage.dataset.floorPoints || 'null'); } catch (e) { floor = null; }
    let camera = null;
    try { camera = JSON.parse(stage.dataset.camera || 'null'); } catch (e) { camera = null; }
    const roomImg = stage.querySelector('.room-image');

    // Map a point in the preview to the photo, cast a ray from the recovered
    // camera and hit the floor. Returns the distance along the camera's
    // viewing axis (metres) and the photo-to-preview scale.
    function floorDepthAt(px, py) {
      if (!camera || !roomImg || !roomImg.naturalWidth) return null;
      const box = stage.getBoundingClientRect();
      const iw = camera.width, ih = camera.height;
      const s = Math.max(box.width / iw, box.height / ih);  // object-fit: cover
      const ox = (box.width - iw * s) / 2, oy = (box.height - ih * s) / 2;
      const uc = (px - ox) / s - iw / 2, vc = (py - oy) / s - ih / 2;
      const f = camera.focal_px, b = camera.basis;
      const d = [uc / f, -vc / f, -1];
      const dir = [0, 1, 2].map(function (k) { return b.x[k] * d[0] + b.y[k] * d[1] + b.z[k] * d[2]; });
      if (dir[1] >= -1e-4) return { depth: Infinity, scale: s };
      return { depth: -camera.position[1] / dir[1], scale: s };
    }

    function applyMetricScale(overlay) {
      const widthM = Number(overlay.dataset.widthM);
      if (!camera || !widthM || !roomImg || !roomImg.naturalWidth) return false;
      const box = stage.getBoundingClientRect();
      if (!box.width || !box.height) return false;
      const rect = overlay.getBoundingClientRect();
      const cx = (rect.left + rect.right) / 2 - box.left;
      let bottom = rect.bottom - box.top;
      if (overlay.dataset.default === '1') {
        // First placement: stand the item on the floor ~2.4 m in front of
        // the camera (rows of five step back 0.9 m each).
        const target = 2.4 + Number(overlay.dataset.row || 0) * 0.9;
        for (let y = box.height - 2; y > 0; y -= 3) {
          const hit = floorDepthAt(cx, y);
          if (hit && hit.depth >= target) { bottom = y; break; }
        }
        overlay.dataset.default = '0';
      }
      const hit = floorDepthAt(cx, bottom);
      if (!hit) return false;
      const depth = Math.min(Math.max(hit.depth, 0.6), 25);
      const widthPx = widthM * camera.focal_px / depth * hit.scale;
      overlay.style.width = Math.max(1.5, widthPx / box.width * 100) + '%';
      const h = overlay.offsetHeight;
      const top = Math.max(0, Math.min(box.height - h, bottom - h));
      overlay.style.top = (top / box.height * 100) + '%';
      return true;
    }

    function applyDepthScale(overlay) {
      if (applyMetricScale(overlay)) return;
      const stageBox = stage.getBoundingClientRect();
      if (!stageBox.width || !stageBox.height) return;
      let farY = 0.18, nearY = 0.92;
      if (floor && floor.length === 4) {
        const ys = floor.flat().filter(function (value, index) { return index % 2 === 1; }).map(Number).filter(Number.isFinite);
        if (ys.length === 4) {
          farY = Math.min.apply(null, ys);
          nearY = Math.max.apply(null, ys);
        }
      }
      if (nearY <= farY + 0.02) return;
      const base = Number(overlay.dataset.baseWidth);
      if (!base) return;
      const current = overlay.getBoundingClientRect();
      const bottom = current.bottom - stageBox.top;
      const centerY = (current.top + current.bottom) / 2 - stageBox.top;
      const depth = Math.max(0, Math.min(1, (centerY / stageBox.height - farY) / (nearY - farY)));
      const factor = Math.max(0.42, Math.min(1.75, 0.42 + depth * 1.33));
      overlay.style.width = (base * factor) + '%';
      const updatedHeight = overlay.offsetHeight;
      // Keep the whole item inside the preview (flat items used to slide
      // below the bottom edge and disappear).
      const clampedTop = Math.max(0, Math.min(stageBox.height - updatedHeight, bottom - updatedHeight));
      overlay.style.top = (clampedTop / stageBox.height * 100) + '%';
    }
    stage.querySelectorAll('.item-overlay').forEach(function (overlay) {
      if (overlay.dataset.wired === 'true') return;
      overlay.dataset.wired = 'true';
      const itemImg = overlay.querySelector('img');
      const rescale = function () { applyDepthScale(overlay); };
      if (itemImg && !itemImg.complete) itemImg.addEventListener('load', rescale, { once: true });
      if (roomImg && !roomImg.complete) roomImg.addEventListener('load', rescale, { once: true });
      applyDepthScale(overlay);
      let startX = 0, startY = 0, startLeft = 0, startTop = 0;
      overlay.addEventListener('pointerdown', function (event) {
        event.preventDefault();
        overlay.setPointerCapture(event.pointerId);
        const stageBox = stage.getBoundingClientRect();
        const overlayBox = overlay.getBoundingClientRect();
        startX = event.clientX; startY = event.clientY;
        startLeft = overlayBox.left - stageBox.left; startTop = overlayBox.top - stageBox.top;
      });
      overlay.addEventListener('pointermove', function (event) {
        if (!overlay.hasPointerCapture(event.pointerId)) return;
        const stageBox = stage.getBoundingClientRect();
        const x = Math.max(0, Math.min(stageBox.width - overlay.offsetWidth, startLeft + event.clientX - startX));
        const y = Math.max(0, Math.min(stageBox.height - overlay.offsetHeight, startTop + event.clientY - startY));
        overlay.style.left = (x / stageBox.width * 100) + '%';
        overlay.style.top = (y / stageBox.height * 100) + '%';
        applyDepthScale(overlay);
      });
      overlay.addEventListener('pointerup', function (event) {
        if (overlay.hasPointerCapture(event.pointerId)) overlay.releasePointerCapture(event.pointerId);
        applyDepthScale(overlay);
        savePosition(overlay);
      });
    });
  }
  // Remember where each item was dragged so adding another item (which
  // re-renders the preview) never moves the items already in the room.
  const positions = {};
  function savePosition(overlay) {
    let id = null;
    try { id = JSON.parse(overlay.id).index; } catch (e) { return; }
    positions[id] = { left: overlay.style.left, top: overlay.style.top };
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props('overlay-positions', { data: Object.assign({}, window.__theiaPositions || {}, positions) });
    }
  }
  const observer = new MutationObserver(wireDrag);
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', wireDrag);
})();
