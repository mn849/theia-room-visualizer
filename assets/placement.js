(function () {
  function wireDrag() {
    const stage = document.getElementById('room-stage');
    if (!stage) return;
    let floor = null;
    try { floor = JSON.parse(stage.dataset.floorPoints || 'null'); } catch (e) { floor = null; }
    function applyDepthScale(overlay) {
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
