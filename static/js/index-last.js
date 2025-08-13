document.addEventListener('DOMContentLoaded', () => {
  const cells = document.querySelectorAll('[data-last-position]');
  cells.forEach((cell) => {
    const equipmentId = cell.dataset.equipmentId;
    const deltaCell = document.querySelector(
      `[data-last-delta][data-equipment-id="${equipmentId}"]`,
    );

    let lastTs = null;

    const existing = cell.textContent.trim();
    if (existing && existing !== '–') {
      lastTs = Date.parse(`${existing.replace(' ', 'T')}Z`);
    }

    function formatLocal(dt) {
      const pad = (n) => String(n).padStart(2, '0');
      return (
        `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}` +
        ` ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`
      );
    }

    function updateDelta() {
      if (!deltaCell || !lastTs) {
        return;
      }
      const delta = Math.max(0, Date.now() - lastTs);
      const days = Math.floor(delta / 86400000);
      const hours = Math.floor((delta % 86400000) / 3600000);
      const minutes = Math.floor((delta % 3600000) / 60000);
      deltaCell.textContent = `${days} j ${hours} h ${minutes} min`;
    }

    async function fetchLast() {
      try {
        const resp = await fetch(`/equipment/${equipmentId}/last.geojson`);
        const data = await resp.json();
        if (data.features && data.features.length > 0) {
          const ts = data.features[0].properties.timestamp;
          const dt = new Date(ts);
          lastTs = dt.getTime();
          cell.textContent = formatLocal(dt);
          updateDelta();
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch last position', err);
      }
    }

    fetchLast();
    setInterval(fetchLast, 60000);
    updateDelta();
    setInterval(updateDelta, 60000);
  });
});

