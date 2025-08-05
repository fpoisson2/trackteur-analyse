(function () {
  const mql = window.matchMedia('(max-width: 768px)');
  if (!mql.matches) return;
  const sheetEl = document.querySelector('[data-sheet="equipment"]');
  if (!sheetEl) return;
  const content = sheetEl.querySelector('[data-sheet-content]');
  if (!content) return;

  let startY = 0;
  let startX = 0;
  let startScrollTop = 0;
  let dragging = false;
  let currentY = 0;
  let lastY = 0;
  let lastTime = 0;

  function onPointerDown(e) {
    if (!e.isPrimary) return;
    startY = e.clientY;
    startX = e.clientX;
    startScrollTop = content.scrollTop;
    dragging = false;
    currentY = 0;
    lastY = startY;
    lastTime = e.timeStamp;
    sheetEl.style.transition = 'none';
    try {
      sheetEl.setPointerCapture(e.pointerId);
    } catch (err) {
      /* ignore */
    }
  }

  function onPointerMove(e) {
    if (!e.isPrimary) return;
    const dy = e.clientY - startY;
    const dx = e.clientX - startX;
    if (!dragging) {
      if (startScrollTop > 0) return;
      if (dy > 6 && Math.abs(dy) > Math.abs(dx)) {
        dragging = true;
      } else {
        return;
      }
    }
    e.preventDefault();
    currentY = Math.min(Math.max(dy, 0), window.innerHeight * 0.6);
    sheetEl.style.transform = `translateY(${currentY}px)`;
    lastY = e.clientY;
    lastTime = e.timeStamp;
  }

  function finishDrag(e) {
    if (!e.isPrimary) return;
    try {
      sheetEl.releasePointerCapture(e.pointerId);
    } catch (err) {
      /* ignore */
    }
    sheetEl.style.transition = '';
    if (!dragging) {
      sheetEl.style.transform = '';
      sheetEl.setAttribute('data-open', 'true');
      return;
    }
    const dt = e.timeStamp - lastTime || 1;
    const vy = (e.clientY - lastY) / dt;
    const shouldClose = currentY > 120 || vy > 0.35;
    sheetEl.style.transform = '';
    if (shouldClose) {
      if (typeof window.closeEquipmentSheet === 'function') {
        window.closeEquipmentSheet();
      } else {
        const btn = document.querySelector('[data-close-sheet="equipment"], #close-equipment, [aria-label="Fermer"]');
        if (btn) {
          btn.click();
        } else {
          sheetEl.setAttribute('data-open', 'false');
        }
      }
    } else {
      sheetEl.setAttribute('data-open', 'true');
    }
    dragging = false;
  }

  sheetEl.addEventListener('pointerdown', onPointerDown, { passive: true });
  sheetEl.addEventListener('pointermove', onPointerMove, { passive: false });
  sheetEl.addEventListener('pointerup', finishDrag, { passive: true });
  sheetEl.addEventListener('pointercancel', finishDrag, { passive: true });
})();
