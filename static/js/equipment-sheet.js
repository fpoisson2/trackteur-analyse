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

  // Fonction pour calculer la position fermée du volet
  function getClosedPosition() {
    return sheetEl.offsetHeight - 48; // 3rem = 48px approximativement
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
    
    // Récupérer la position actuelle du volet
    const style = window.getComputedStyle(sheetEl);
    const matrix = new DOMMatrixReadOnly(style.transform);
    initialTranslateY = matrix.m42;
    
    // Si le volet est fermé, utiliser la position fermée comme référence
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
      // Conditions pour démarrer le drag
      const isScrolledToTop = startScrollTop <= 0;
      const isVerticalGesture = Math.abs(dy) > 6 && Math.abs(dy) > Math.abs(dx);
      const isClosed = sheetEl.getAttribute('data-open') === 'false';
      
      if (isVerticalGesture) {
        // Si le volet est fermé, permettre l'ouverture vers le haut
        if (isClosed && dy < 0) {
          dragging = true;
        }
        // Si le volet est ouvert et qu'on est en haut du contenu, permettre la fermeture vers le bas
        else if (!isClosed && isScrolledToTop && dy > 0) {
          dragging = true;
        }
        // Si le volet est ouvert et qu'on tire vers le haut depuis le haut, rester ouvert
        else if (!isClosed && isScrolledToTop && dy < 0) {
          return; // Ne pas démarrer le drag, laisser le contenu gérer le scroll
        }
      }
      
      if (!dragging) {
        return;
      }
    }
    
    e.preventDefault();
    
    // Calcul de la vélocité
    const now = e.timeStamp;
    velocityY = (e.clientY - lastY) / (now - lastTime || 1);
    lastY = e.clientY;
    lastTime = now;
    
    // Calcul de la nouvelle position
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
    
    sheetEl.style.transition = '';
    
    if (!dragging) {
      // Si ce n'était pas un drag, restaurer la position normale
      sheetEl.style.transform = '';
      return;
    }
    
    // Déterminer si le volet doit se fermer ou s'ouvrir
    const closedPosition = getClosedPosition();
    const threshold = closedPosition * 0.3; // 30% de la hauteur fermée
    const shouldClose = currentY > threshold || velocityY > 0.35;
    
    const targetPosition = shouldClose ? closedPosition : 0;
    const targetTransform = shouldClose ? `translateY(${targetPosition}px)` : 'translateY(0px)';
    
    // Animer vers la position finale
    requestAnimationFrame(() => {
      sheetEl.style.transform = targetTransform;
    });
    
    // Écouter la fin de la transition
    sheetEl.addEventListener(
      'transitionend',
      () => {
        if (shouldClose) {
          // Fermer le volet
          sheetEl.setAttribute('data-open', 'false');
          // Appeler la fonction de fermeture si elle existe
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
          // Ouvrir le volet
          sheetEl.setAttribute('data-open', 'true');
        }
        
        // Nettoyer les styles inline
        requestAnimationFrame(() => {
          sheetEl.style.transform = '';
        });
      },
      { once: true }
    );
    
    dragging = false;
  }

  // Ajouter les event listeners
  sheetEl.addEventListener('pointerdown', onPointerDown, { passive: true });
  sheetEl.addEventListener('pointermove', onPointerMove, { passive: false });
  sheetEl.addEventListener('pointerup', finishDrag, { passive: true });
  sheetEl.addEventListener('pointercancel', finishDrag, { passive: true });
  
  // Fonction pour ouvrir le volet programmatiquement
  window.openEquipmentSheet = function() {
    sheetEl.style.transition = '';
    sheetEl.style.transform = 'translateY(0px)';
    sheetEl.setAttribute('data-open', 'true');
    setTimeout(() => {
      sheetEl.style.transform = '';
    }, 220);
  };
})();    }
  }

  function onPointerMove(e) {
    if (!e.isPrimary) return;
    const dy = e.clientY - startY;
    const dx = e.clientX - startX;
    if (!dragging) {
      if (startScrollTop > 0 && initialTranslateY === 0) return;
      if (Math.abs(dy) > 6 && Math.abs(dy) > Math.abs(dx)) {
        if (dy < 0 && initialTranslateY === 0) return;
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
    const newTranslateY = Math.min(
      Math.max(initialTranslateY + dy, 0),
      window.innerHeight * 0.6
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
