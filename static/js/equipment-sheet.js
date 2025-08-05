 (function () {
  const mql = window.matchMedia('(max-width: 768px)');
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
  let initialized = false;

  function getClosedPosition() {
    return sheetEl.offsetHeight - 48; // ~3rem
  }

  function setInitialState() {
    if (sheetEl.getAttribute('data-open') === 'false') {
      sheetEl.style.transform = `translateY(${getClosedPosition()}px)`;
    } else {
      sheetEl.style.transform = '';
    }
  }

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
    let matrix;
    try {
      const Matrix = window.DOMMatrix || window.WebKitCSSMatrix;
      matrix = new Matrix(style.transform);
    } catch (err) {
      matrix = { m42: 0 };
    }
    initialTranslateY = matrix.m42 || 0;
    if (sheetEl.getAttribute('data-open') === 'false') {
      initialTranslateY = getClosedPosition();
    }

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
      const isScrolledToTop = startScrollTop <= 0;
      const isVerticalGesture = Math.abs(dy) > 6 && Math.abs(dy) > Math.abs(dx);
      const isClosed = sheetEl.getAttribute('data-open') === 'false';

      if (isVerticalGesture) {
        if (isClosed && dy < 0) {
          dragging = true;
        } else if (!isClosed && isScrolledToTop && dy > 0) {
          dragging = true;
        } else if (!isClosed && isScrolledToTop && dy < 0) {
          return;
        }
      }

      if (!dragging) {
        return;
      }
    }

    e.preventDefault();

    const now = e.timeStamp;
    velocityY = (e.clientY - lastY) / (now - lastTime || 1);
    lastY = e.clientY;
    lastTime = now;

    const newTranslateY = Math.min(
      Math.max(initialTranslateY + dy, 0),
      getClosedPosition()
    );

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

    sheetEl.style.transition = 'transform 0.2s ease-out';

    if (!dragging) {
      sheetEl.style.transform = '';
      return;
    }

    const closedPosition = getClosedPosition();
    const isClosed = sheetEl.getAttribute('data-open') === 'false';
    const threshold = closedPosition * (isClosed ? 0.7 : 0.3);
    const shouldClose = currentY > threshold || velocityY > 0.35;

    const targetPosition = shouldClose ? closedPosition : 0;
    const targetTransform = shouldClose
      ? `translateY(${targetPosition}px)`
      : 'translateY(0px)';

    requestAnimationFrame(() => {
      sheetEl.style.transform = targetTransform;
    });

    let finalized = false;
    function finalize() {
      if (finalized) return;
      finalized = true;
      if (shouldClose) {
        sheetEl.setAttribute('data-open', 'false');
        if (typeof window.closeEquipmentSheet === 'function') {
          window.closeEquipmentSheet();
        } else {
          const btn = sheetEl.querySelector(
            '[data-close-sheet="equipment"], #close-equipment, [aria-label="Fermer"]'
          );
          if (btn) {
            btn.click();
          }
        }
      } else {
        sheetEl.setAttribute('data-open', 'true');
      }
      requestAnimationFrame(() => {
        setInitialState();
      });
    }
    sheetEl.addEventListener('transitionend', finalize, { once: true });
    setTimeout(finalize, 250);

    dragging = false;
  }
  function setup() {
    if (initialized) return;
    setInitialState();
    window.addEventListener('resize', setInitialState);
    sheetEl.addEventListener('pointerdown', onPointerDown, { passive: true });
    sheetEl.addEventListener('pointermove', onPointerMove, { passive: false });
    sheetEl.addEventListener('pointerup', finishDrag, { passive: true });
    sheetEl.addEventListener('pointercancel', finishDrag, { passive: true });
    initialized = true;
  }

  function teardown() {
    if (!initialized) return;
    sheetEl.style.transform = '';
    sheetEl.setAttribute('data-open', 'true');
    window.removeEventListener('resize', setInitialState);
    sheetEl.removeEventListener('pointerdown', onPointerDown);
    sheetEl.removeEventListener('pointermove', onPointerMove);
    sheetEl.removeEventListener('pointerup', finishDrag);
    sheetEl.removeEventListener('pointercancel', finishDrag);
    initialized = false;
  }

  function handleModeChange(e) {
    if (e.matches) {
      setup();
    } else {
      teardown();
    }
  }

  mql.addEventListener('change', handleModeChange);
  if (mql.matches) {
    setup();
  } else {
    teardown();
  }

  window.openEquipmentSheet = function () {
    if (!initialized && mql.matches) {
      setup();
    }
    sheetEl.style.transition = '';
    sheetEl.style.transform = 'translateY(0px)';
    sheetEl.setAttribute('data-open', 'true');
    setTimeout(() => {
      sheetEl.style.transform = '';
    }, 220);
  };
})();
