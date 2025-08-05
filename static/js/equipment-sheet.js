(function() {
  function setupEquipmentSheet() {
    const sheet = document.getElementById('equipment-sheet');
    if (!sheet) return;
    const handle = sheet.querySelector('.drag-handle');
    const content = document.getElementById('equipment-sheet-content');
    if (!handle || !content) return;

    const handleStyles = getComputedStyle(handle);
    const handleHeight =
      handle.offsetHeight +
      parseFloat(handleStyles.marginTop) +
      parseFloat(handleStyles.marginBottom);
    const collapsed = sheet.offsetHeight - handleHeight;
    let current = collapsed;
    sheet.style.transform = `translateY(${current}px)`;
    const clamp = (v, min, max) => Math.min(Math.max(v, min), max);

    const disableScroll = () => {
      content.style.touchAction = 'none';
      content.style.overflowY = 'hidden';
    };
    const enableScroll = () => {
      content.style.touchAction = 'pan-y';
      content.style.overflowY = 'auto';
    };
    disableScroll();

    let startY = 0;
    let startX = 0;
    let start = 0;
    let startScrollTop = 0;
    let lastY = 0;
    let lastTime = 0;
    let dragging = false;
    let maybeDrag = false;
    let captureEl = null;
    const mediaQuery = window.matchMedia('(max-width: 768px)');

    function onPointerDown(e) {
      if (!mediaQuery.matches || !e.isPrimary) return;
      startY = e.clientY;
      startX = e.clientX;
      start = current;
      startScrollTop = content.scrollTop;
      lastY = e.clientY;
      lastTime = e.timeStamp;
      dragging = false;
      maybeDrag = startScrollTop <= 0;
      sheet.style.transition = 'none';
      if (maybeDrag) {
        captureEl = e.target;
        try {
          captureEl.setPointerCapture(e.pointerId);
        } catch (err) {
          captureEl = sheet;
          captureEl.setPointerCapture(e.pointerId);
        }
        disableScroll();
        e.preventDefault();
      }
    }

    function onPointerMove(e) {
      if (!mediaQuery.matches || !e.isPrimary) return;
      const dy = e.clientY - startY;
      const dx = e.clientX - startX;

      if (!dragging) {
        if (maybeDrag) {
          if (
            Math.abs(dx) > Math.abs(dy) ||
            (start === 0 && dy <= 0) ||
            (start > 0 && dy >= 0)
          ) {
            maybeDrag = false;
            enableScroll();
            if (captureEl) {
              try { captureEl.releasePointerCapture(e.pointerId); } catch (err) {}
              captureEl = null;
            }
            return;
          }
          dragging = true;
        } else {
          return;
        }
      }
      e.preventDefault();
      current = clamp(start + dy, 0, collapsed);
      sheet.style.transform = `translateY(${current}px)`;
      lastY = e.clientY;
      lastTime = e.timeStamp;
    }

    function onPointerUp(e) {
      if (!dragging) {
        if (maybeDrag) {
          enableScroll();
          if (captureEl) {
            try { captureEl.releasePointerCapture(e.pointerId); } catch (err) {}
            captureEl = null;
          }
          maybeDrag = false;
        }
        return;
      }
      const dt = e.timeStamp - lastTime;
      const velocity = dt > 0 ? (e.clientY - lastY) / dt : 0;
      sheet.style.transition = '';
      if (start === 0) {
        if (current > 120 || velocity > 0.25) {
          current = collapsed;
          sheet.classList.remove('open');
          disableScroll();
        } else {
          current = 0;
          sheet.classList.add('open');
          enableScroll();
        }
      } else {
        const traveled = start - current;
        if (traveled > 120 || velocity < -0.25) {
          current = 0;
          sheet.classList.add('open');
          enableScroll();
        } else {
          current = collapsed;
          sheet.classList.remove('open');
          disableScroll();
        }
      }
      sheet.style.transform = `translateY(${current}px)`;
      dragging = false;
      maybeDrag = false;
      if (captureEl) {
        try { captureEl.releasePointerCapture(e.pointerId); } catch (err) {}
        captureEl = null;
      }
    }

    sheet.addEventListener('pointerdown', onPointerDown, { passive: false });
    sheet.addEventListener('pointermove', onPointerMove, { passive: false });
    sheet.addEventListener('pointerup', onPointerUp, { passive: false });
    sheet.addEventListener('pointercancel', onPointerUp, { passive: false });

    handle.addEventListener('click', e => {
      e.stopPropagation();
      if (dragging) return;
      if (current === 0) {
        current = collapsed;
        sheet.classList.remove('open');
        sheet.style.transform = `translateY(${current}px)`;
        disableScroll();
      } else {
        current = 0;
        sheet.classList.add('open');
        sheet.style.transform = `translateY(${current}px)`;
        enableScroll();
      }
    });

    sheet.addEventListener('click', () => {
      if (dragging) return;
      if (!sheet.classList.contains('open')) {
        current = 0;
        sheet.classList.add('open');
        sheet.style.transform = `translateY(${current}px)`;
        enableScroll();
      }
    });
  }

  window.setupEquipmentSheet = setupEquipmentSheet;
})();
