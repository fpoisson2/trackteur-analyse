import json
from datetime import datetime

import pytest

from models import db, Equipment, Position
from tests.utils import login


@pytest.mark.usefixtures("base_make_app")
def test_index_source_badge_and_last_geojson(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        # First equipment is created by fixture; ensure it looks like Traccar
        eq = Equipment.query.first()
        eq.id_traccar = eq.id_traccar or 1
        eq.osmand_id = None
        # Add a last position
        ts = datetime(2023, 1, 1, 15, 0, 0)
        db.session.add(Position(equipment_id=eq.id, latitude=1.0, longitude=2.0, timestamp=ts))
        db.session.commit()

        # Create a direct OsmAnd device
        osm = Equipment(id_traccar=0, name="OsmAnd Dev", osmand_id="osm-1")
        db.session.add(osm)
        db.session.flush()  # ensure osm.id is available
        db.session.add(Position(equipment_id=osm.id, latitude=3.0, longitude=4.0, timestamp=ts))
        db.session.commit()

    # Check index badges
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "Traccar" in html
    assert "OsmAnd" in html

    # Check last.geojson endpoint
    with app.app_context():
        eq_id = Equipment.query.filter(Equipment.osmand_id.is_(None)).first().id
    r2 = client.get(f"/equipment/{eq_id}/last.geojson")
    assert r2.status_code == 200
    data = r2.get_json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1
    geom = data["features"][0]["geometry"]
    assert geom["type"] == "Point"
    assert geom["coordinates"] == [2.0, 1.0]


@pytest.mark.usefixtures("base_make_app")
def test_osmand_bulk_devices_json(make_app):
    app = make_app()
    client = app.test_client()
    payload = {
        "devices": [
            {
                "device_id": "bulk-1",
                "locations": [
                    {"coords": {"latitude": 10.0, "longitude": 11.0}, "timestamp": "2024-01-01T00:00:00Z"},
                    {"coords": {"latitude": 10.1, "longitude": 11.1}, "timestamp": "2024-01-01T00:01:00Z"},
                ],
            },
            {
                "device_id": "bulk-2",
                "locations": [
                    {"coords": {"latitude": 20.0, "longitude": 21.0}, "timestamp": "2024-01-01T00:00:00Z"}
                ],
            },
        ]
    }
    resp = client.post("/osmand", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 200
    with app.app_context():
        eq1 = Equipment.query.filter_by(osmand_id="bulk-1").first()
        eq2 = Equipment.query.filter_by(osmand_id="bulk-2").first()
        assert eq1 is not None and eq2 is not None
        c1 = Position.query.filter_by(equipment_id=eq1.id).count()
        c2 = Position.query.filter_by(equipment_id=eq2.id).count()
        assert c1 == 2
        assert c2 == 1
