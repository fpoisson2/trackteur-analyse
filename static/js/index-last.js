document.addEventListener('DOMContentLoaded', () => {
  const cells = document.querySelectorAll('[data-last-position]');
  cells.forEach((cell) => {
    const equipmentId = cell.dataset.equipmentId;
    const deltaCell = document.querySelector(
      `[data-last-delta][data-equipment-id="${equipmentId}"]`,
    );

    async function update() {
      try {
        const resp = await fetch(`/equipment/${equipmentId}/last.geojson`);
        const data = await resp.json();
        if (!data.features || data.features.length === 0) {
          return;
        }
        const ts = data.features[0].properties.timestamp;
        const dt = new Date(ts);
        cell.textContent = dt.toISOString().replace('T', ' ').substring(0, 19);
        const now = new Date();
        const delta = now.getTime() - dt.getTime();
        const days = Math.floor(delta / 86400000);
        const hours = Math.floor((delta % 86400000) / 3600000);
        const minutes = Math.floor((delta % 3600000) / 60000);
        if (deltaCell) {
          deltaCell.textContent = `${days} j ${hours} h ${minutes} min`;
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch last position', err);
      }
    }

    update();
    setInterval(update, 60000);
  });
});

