(function () {
  const mql = window.matchMedia('(max-width: 768px)');
  const sheet = document.querySelector('[data-sheet="equipment"]');
  if (!sheet) return;
  const handle = sheet.querySelector('.drag-handle') || sheet;
  const content = sheet.querySelector('[data-sheet-content]');

  let startY = 0;
  let startX = 0;
  let baseOffset = 0;
  let maxOffset = 0;
  let dragging = false;
  let lastY = 0;
  let lastTime = 0;
  let velocity = 0;
  let initialized = false;

  function computeMaxOffset() {
    const handleHeight = handle.offsetHeight || 0;
    const peek = 40; // keep part of the sheet visible when closed
    maxOffset = sheet.offsetHeight - handleHeight - 8 - peek;
  }

  function applyOffset(y) {
    sheet.style.transform = `translateY(${y}px)`;
  }

  function snap(open) {
    sheet.style.transition = 'transform 0.25s ease-out';
    applyOffset(open ? 0 : maxOffset);
    sheet.setAttribute('data-open', open ? 'true' : 'false');
    setTimeout(() => {
      sheet.style.transition = '';
    }, 250);
  }

  function onPointerDown(e) {
    if (!e.isPrimary) return;
    if (
      content &&
      sheet.getAttribute('data-open') === 'true' &&
      content.scrollTop > 0 &&
      !handle.contains(e.target)
    ) {
      return; // allow normal scrolling
    }
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startX = e.clientX;
    baseOffset = sheet.getAttribute('data-open') === 'false' ? maxOffset : 0;
    lastY = startY;
    lastTime = e.timeStamp;
    velocity = 0;
    sheet.style.transition = 'none';
    try {
      if (e.target && e.target.setPointerCapture) {
        e.target.setPointerCapture(e.pointerId);
      } else if (sheet.setPointerCapture) {
        sheet.setPointerCapture(e.pointerId);
      }
    } catch (err) {
      /* ignore */
    }
  }

  function onPointerMove(e) {
    if (!dragging) return;
    const dy = e.clientY - startY;
    const dx = e.clientX - startX;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dy) < 10) {
      dragging = false;
      return;
    }
    e.preventDefault();
    const offset = Math.min(Math.max(baseOffset + dy, 0), maxOffset);
    applyOffset(offset);
    const now = e.timeStamp;
    velocity = (e.clientY - lastY) / (now - lastTime || 1);
    lastY = e.clientY;
    lastTime = now;
  }

  function onPointerUp(e) {
    if (!dragging) return;
    dragging = false;
    try {
      if (e.target && e.target.releasePointerCapture) {
        e.target.releasePointerCapture(e.pointerId);
      } else if (sheet.releasePointerCapture) {
        sheet.releasePointerCapture(e.pointerId);
      }
    } catch (err) {
      /* ignore */
    }
    const match = sheet.style.transform.match(/-?\d+(?:\.\d+)?/);
    const current = match ? parseFloat(match[0]) : 0;
    const threshold = maxOffset * 0.3;
    const shouldOpen =
      velocity < -0.2 || (velocity <= 0.2 && current < threshold);
    snap(shouldOpen);
  }

  function setup() {
    if (initialized) return;
    computeMaxOffset();
    snap(false);
    sheet.addEventListener('pointerdown', onPointerDown, { passive: false });
    sheet.addEventListener('pointermove', onPointerMove, { passive: false });
    sheet.addEventListener('pointerup', onPointerUp, { passive: true });
    sheet.addEventListener('pointercancel', onPointerUp, { passive: true });
    window.addEventListener('resize', () => {
      const open = sheet.getAttribute('data-open') === 'true';
      computeMaxOffset();
      applyOffset(open ? 0 : maxOffset);
    });
    initialized = true;
  }

  function teardown() {
    if (!initialized) return;
    sheet.style.transform = '';
    sheet.setAttribute('data-open', 'true');
    sheet.removeEventListener('pointerdown', onPointerDown);
    sheet.removeEventListener('pointermove', onPointerMove);
    sheet.removeEventListener('pointerup', onPointerUp);
    sheet.removeEventListener('pointercancel', onPointerUp);
    initialized = false;
  }

  mql.addEventListener('change', (e) => {
    if (e.matches) {
      setup();
    } else {
      teardown();
    }
  });

  if (mql.matches) {
    setup();
  }

  window.openEquipmentSheet = () => {
    if (!initialized && mql.matches) setup();
    snap(true);
  };

  window.closeEquipmentSheet = () => {
    if (initialized) snap(false);
  };
})();
