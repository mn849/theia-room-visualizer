(function () {
  function wireCalibration() {
    const image = document.getElementById('calibration-image');
    if (!image) return;
    const reset = document.getElementById('reset-floor');
    const pointLabels = ['Near left', 'Near right', 'Far right', 'Far left'];
    // Draw the floor outline through the placed points so users can see the
    // shape they are defining, not just four isolated markers.
    function drawOutline() {
      const layer = document.getElementById('floor-markers');
      if (!layer) return;
      let svg = layer.querySelector('svg.floor-outline');
      if (!svg) {
        svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'floor-outline');
        svg.setAttribute('viewBox', '0 0 100 100');
        svg.setAttribute('preserveAspectRatio', 'none');
        svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'polygon'));
        layer.prepend(svg);
      }
      const pts = Array.from(layer.querySelectorAll('.floor-marker'))
        .sort(function (a, b) { return a.dataset.index - b.dataset.index; })
        .map(function (m) { return parseFloat(m.style.left) + ',' + parseFloat(m.style.top); });
      const poly = svg.querySelector('polygon');
      poly.setAttribute('points', pts.join(' '));
      poly.setAttribute('class', pts.length === 4 ? 'closed' : 'open');
    }
    function makeMarker(index, x, y) {
      const marker = document.createElement('span');
      marker.className = 'floor-marker';
      marker.dataset.index = String(index);
      marker.title = pointLabels[index - 1] + ' floor corner (drag to adjust)';
      marker.style.left = x + '%'; marker.style.top = y + '%';
      const tag = document.createElement('span');
      tag.className = 'floor-marker-tag';
      tag.textContent = index + ' · ' + pointLabels[index - 1];
      if (y < 12) tag.classList.add('below');
      if (x > 78) tag.classList.add('left');
      marker.appendChild(tag);
      return marker;
    }
    const apply = document.getElementById('apply-floor');
    if (apply && apply.dataset.wired !== 'true') {
      apply.dataset.wired = 'true';
      apply.addEventListener('click', function () { image.dataset.locked = 'true'; });
    }
    function updateStatus() {
      const status = document.getElementById('floor-point-status');
      if (!status) return;
      let count = 0;
      for (let i = 1; i <= 4; i += 1) {
        const x = document.getElementById('corner-' + i + '-x');
        const y = document.getElementById('corner-' + i + '-y');
        if (x && y && x.value !== '' && y.value !== '') count += 1;
      }
      status.textContent = count + ' of 4 points selected';
    }
    function setInputValue(input, value) {
      if (!input) return;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, value);
      input.dispatchEvent(new Event('input', {bubbles:true}));
      input.dispatchEvent(new Event('change', {bubbles:true}));
      input.dispatchEvent(new Event('blur', {bubbles:true}));
    }
    function pointFromEvent(event) {
      const box = image.getBoundingClientRect();
      return {x: Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100)), y: Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100))};
    }
    function wireMarker(marker, index) {
      if (marker.dataset.wired === 'true') return;
      marker.dataset.wired = 'true';
      marker.addEventListener('pointerdown', function (event) {
        if (image.dataset.locked === 'true') return;
        event.preventDefault(); event.stopPropagation(); marker.setPointerCapture(event.pointerId);
      });
      marker.addEventListener('pointermove', function (event) {
        if (!marker.hasPointerCapture(event.pointerId) || image.dataset.locked === 'true') return;
        event.preventDefault(); event.stopPropagation();
        const point = pointFromEvent(event);
        marker.style.left = point.x + '%'; marker.style.top = point.y + '%';
        const tag = marker.querySelector('.floor-marker-tag');
        if (tag) { tag.classList.toggle('below', point.y < 12); tag.classList.toggle('left', point.x > 78); }
        drawOutline();
        setInputValue(document.getElementById('corner-' + index + '-x'), point.x.toFixed(1));
        setInputValue(document.getElementById('corner-' + index + '-y'), point.y.toFixed(1));
      });
      marker.addEventListener('pointerup', function (event) {
        if (marker.hasPointerCapture(event.pointerId)) marker.releasePointerCapture(event.pointerId);
      });
    }
    if (reset && reset.dataset.wired !== 'true') {
      reset.dataset.wired = 'true';
      reset.addEventListener('click', function () {
        const markers = document.getElementById('floor-markers');
        if (markers) markers.replaceChildren();
        image.dataset.locked = 'false';
        for (let i = 1; i <= 4; i += 1) {
          const xInput = document.getElementById('corner-' + i + '-x');
          const yInput = document.getElementById('corner-' + i + '-y');
          [xInput, yInput].forEach(function (input) {
            if (!input) return;
            setInputValue(input, '');
          });
        }
        updateStatus();
      });
    }
    if (image.dataset.wired === 'true') return;
    image.dataset.wired = 'true';
    image.addEventListener('load', function () {
      const markers = document.getElementById('floor-markers');
      if (markers) markers.replaceChildren();
      image.dataset.locked = 'false';
      for (let i = 1; i <= 4; i += 1) {
        const xInput = document.getElementById('corner-' + i + '-x');
        const yInput = document.getElementById('corner-' + i + '-y');
        setInputValue(xInput, '');
        setInputValue(yInput, '');
      }
      updateStatus();
    });
    image.addEventListener('click', function (event) {
      if (!image.src) return;
      const box = image.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const x = Math.max(0, Math.min(100, (event.clientX - box.left) / box.width * 100));
      const y = Math.max(0, Math.min(100, (event.clientY - box.top) / box.height * 100));
      for (let i = 1; i <= 4; i += 1) {
        const xInput = document.getElementById('corner-' + i + '-x');
        const yInput = document.getElementById('corner-' + i + '-y');
        if (xInput && yInput && (!xInput.value || !yInput.value)) {
          [ [xInput, x], [yInput, y] ].forEach(function(pair) {
            const input = pair[0];
            setInputValue(input, pair[1].toFixed(1));
          });
          const marker = makeMarker(i, x, y);
          const markers = document.getElementById('floor-markers');
          if (markers) { markers.appendChild(marker); wireMarker(marker, i); drawOutline(); }
          updateStatus();
          break;
        }
      }
    });
  }
  const observer = new MutationObserver(wireCalibration);
  observer.observe(document.body, {childList:true, subtree:true});
  document.addEventListener('DOMContentLoaded', wireCalibration);
})();
