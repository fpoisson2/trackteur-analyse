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
  let initialTranslateY = 0;
  let lastY = 0;
  let lastTime = 0;
  let velocityY = 0;

  function onPointerDown(e) {
    if (!e.isPrimary) return;
    startY = e.clientY;
    startX = e.clientX;
    startScrollTop = content.scrollTop;
    dragging = false;
    currentY = 0;
    lastY = startY;
    lastTime = e.timeStamp;
    velocityY = 0;
    sheetEl.style.transition = 'none';
    const style = window.getComputedStyle(sheetEl);
    const matrix = new DOMMatrixReadOnly(style.transform);
    initialTranslateY = matrix.m42;
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
        if (dy > 0) e.preventDefault();
        return;
      }
    }
    e.preventDefault();
    const now = e.timeStamp;
    velocityY = (e.clientY - lastY) / (now - lastTime || 1);
    lastY = e.clientY;
    lastTime = now;
    const dyClamped = Math.min(
      Math.max(dy, 0),
      window.innerHeight * 0.6
    );
    const newTranslateY = initialTranslateY + dyClamped;
    sheetEl.style.transform = `translateY(${newTranslateY}px)`;
    currentY = newTranslateY;
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
    const shouldClose = currentY > 120 || velocityY > 0.35;
    const target = shouldClose ? 'calc(100% - 3rem)' : '0px';
    requestAnimationFrame(() => {
      sheetEl.style.transform = `translateY(${target})`;
    });
    sheetEl.addEventListener(
      'transitionend',
      () => {
        if (shouldClose) {
          if (typeof window.closeEquipmentSheet === 'function') {
            window.closeEquipmentSheet();
          } else {
            const btn = sheetEl.querySelector(
              '[data-close-sheet="equipment"], #close-equipment, [aria-label="Fermer"]'
            );
            if (btn) {
              btn.click();
            } else {
              sheetEl.setAttribute('data-open', 'false');
            }
          }
          requestAnimationFrame(() => {
            sheetEl.style.transform = '';
          });
        } else {
          sheetEl.style.transform = '';
          sheetEl.setAttribute('data-open', 'true');
        }
      },
      { once: true }
    );
    dragging = false;
  }

  sheetEl.addEventListener('pointerdown', onPointerDown, { passive: true });
  sheetEl.addEventListener('pointermove', onPointerMove, { passive: false });
  sheetEl.addEventListener('pointerup', finishDrag, { passive: true });
  sheetEl.addEventListener('pointercancel', finishDrag, { passive: true });
})();
