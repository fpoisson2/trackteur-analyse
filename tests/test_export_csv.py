import os
import sys
from datetime import datetime, date, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from models import db, Equipment, Position  # noqa: E402
from tests.utils import login  # noqa: E402
import zone  # noqa: E402


def test_export_csv_osmand(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        # Create an OsmAnd-backed equipment with stored positions
        eq = Equipment(id_traccar=0, name="osm", osmand_id="dev1")
        db.session.add(eq)
        db.session.flush()
        t0 = datetime.combine(date.today(), datetime.min.time())
        db.session.add(Position(equipment_id=eq.id, latitude=1.0, longitude=2.0, timestamp=t0, battery_level=80))
        db.session.add(Position(equipment_id=eq.id, latitude=1.1, longitude=2.1, timestamp=t0 + timedelta(hours=1)))
        db.session.commit()
        eqid = eq.id

    resp = client.get(f"/equipment/{eqid}/export.csv?start={date.today().isoformat()}&end={date.today().isoformat()}")
    assert resp.status_code == 200
    assert resp.mimetype.startswith("text/csv")
    text = resp.data.decode()
    lines = [line for line in text.strip().splitlines() if line]
    # header + 2 rows
    assert lines[0].split(',') == ["latitude", "longitude", "timestamp", "battery_level"]
    assert len(lines) == 3
    assert ",80" in lines[1] or lines[1].endswith(",80")


def test_export_csv_traccar(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.filter(Equipment.id_traccar != 0).first()
        assert eq is not None
        eqid = eq.id

    # Mock fetch_positions to return a couple of positions with battery
    def fake_fetch(device_id, frm, to):
        return [
            {
                "latitude": 10.0,
                "longitude": 20.0,
                "deviceTime": (datetime.utcnow().isoformat() + "Z"),
                "attributes": {"batteryLevel": 55},
            },
            {
                "latitude": 11.0,
                "longitude": 21.0,
                "deviceTime": (datetime.utcnow().isoformat() + "Z"),
                "attributes": {},
            },
        ]

    monkeypatch.setattr(zone, "fetch_positions", fake_fetch)

    today = date.today().isoformat()
    resp = client.get(f"/equipment/{eqid}/export.csv?start={today}&end={today}")
    assert resp.status_code == 200
    text = resp.data.decode()
    lines = [line for line in text.strip().splitlines() if line]
    assert lines[0].split(',') == ["latitude", "longitude", "timestamp", "battery_level"]
    # header + 2 rows
    assert len(lines) == 3
    assert ",55" in lines[1] or lines[1].endswith(",55")
