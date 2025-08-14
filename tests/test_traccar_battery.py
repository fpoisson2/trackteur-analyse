import pytest
import zone
from models import Equipment


@pytest.mark.usefixtures("base_make_app")
def test_polling_updates_battery_from_traccar(make_app, monkeypatch):
    app = make_app()

    def fake_fetch(device_id, start, end):
        return [
            {
                "deviceTime": "2024-01-01T00:00:00Z",
                "latitude": 1.0,
                "longitude": 2.0,
                "attributes": {"batteryLevel": 77},
            }
        ]

    monkeypatch.setattr(zone, "fetch_positions", fake_fetch)
    app.poll_latest_positions()
    with app.app_context():
        eq = Equipment.query.filter_by(id_traccar=1).first()
        assert eq.battery_level == 77
