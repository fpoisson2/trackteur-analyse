import gzip
import json
import logging

import pytest

from models import Equipment, Position
from tests.utils import login, get_csrf


@pytest.mark.usefixtures("base_make_app")
def test_osmand_get_query_creates_position(make_app):
    app = make_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        client = app.test_client()
        resp = client.get(
            "/osmand",
            query_string={
                "id": "dev123",
                "lat": "48.1",
                "lon": "2.3",
                "timestamp": "1609459200000",  # 2021-01-01T00:00:00Z
            },
        )
        assert resp.status_code == 200
        eq = Equipment.query.filter_by(osmand_id="dev123").first()
        assert eq is not None
        pos = Position.query.filter_by(equipment_id=eq.id).first()
        assert pos is not None
        assert abs(pos.latitude - 48.1) < 1e-9
        assert abs(pos.longitude - 2.3) < 1e-9


@pytest.mark.usefixtures("base_make_app")
def test_osmand_json_creates_position(make_app):
    app = make_app()
    with app.app_context():
        client = app.test_client()
        payload = {
            "location": {
                "timestamp": "2023-01-01T00:00:00.000Z",
                "coords": {
                    "latitude": 45.0,
                    "longitude": 3.0,
                    "accuracy": 5,
                },
                "is_moving": False,
            },
            "device_id": "osdev-42",
        }
        resp = client.post(
            "/osmand",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        eq = Equipment.query.filter_by(osmand_id="osdev-42").first()
        assert eq is not None
        pos = (
            Position.query.filter_by(equipment_id=eq.id)
            .order_by(Position.timestamp.desc())
            .first()
        )
        assert pos is not None
        assert abs(pos.latitude - 45.0) < 1e-9
        assert abs(pos.longitude - 3.0) < 1e-9


@pytest.mark.usefixtures("base_make_app")
def test_osmand_gzip_json_creates_positions(make_app):
    app = make_app()
    with app.app_context():
        client = app.test_client()
        payload = {
            "device_id": "gz-1",
            "locations": [
                {
                    "coords": {"latitude": 1.0, "longitude": 2.0},
                    "timestamp": "2024-01-01T00:00:00Z",
                },
                {
                    "coords": {"latitude": 1.1, "longitude": 2.1},
                    "timestamp": "2024-01-01T00:01:00Z",
                },
            ],
        }
        body = gzip.compress(json.dumps(payload).encode("utf-8"))
        resp = client.post(
            "/osmand",
            data=body,
            content_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        eq = Equipment.query.filter_by(osmand_id="gz-1").first()
        assert eq is not None
        cnt = Position.query.filter_by(equipment_id=eq.id).count()
        assert cnt == 2


@pytest.mark.usefixtures("base_make_app")
def test_osmand_json_with_battery_updates_equipment(make_app):
    app = make_app()
    with app.app_context():
        client = app.test_client()
        payload = {
            "location": {
                "timestamp": "2024-01-01T00:00:00Z",
                "coords": {"latitude": 10.0, "longitude": 20.0},
                "battery": 88,
            },
            "device_id": "bat-1",
        }
        resp = client.post(
            "/osmand",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        eq = Equipment.query.filter_by(osmand_id="bat-1").first()
        assert eq is not None
        assert eq.battery_level == 88


@pytest.mark.usefixtures("base_make_app")
def test_osmand_json_logs_battery_level(make_app, caplog):
    app = make_app()
    with app.app_context():
        client = app.test_client()
        payload = {
            "location": {
                "timestamp": "2024-01-01T00:00:00Z",
                "coords": {"latitude": 10.0, "longitude": 20.0},
                "battery": 55,
            },
            "device_id": "log-1",
        }
        with caplog.at_level(logging.INFO):
            resp = client.post(
                "/osmand",
                data=json.dumps(payload),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert any(
            "Device log-1 battery at 55%" in r.getMessage()
            for r in caplog.records
        )


@pytest.mark.usefixtures("base_make_app")
def test_admin_add_osmand_device(make_app):
    app = make_app()
    client = app.test_client()
    # login
    login(client)
    token = get_csrf(client, "/admin")
    resp = client.post(
        "/admin/add_osmand",
        data={
            "csrf_token": token,
            "osmand_name": "Tracteur OsmAnd",
            "osmand_id": "unit-99",
            "osmand_token": "secret",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        eq = Equipment.query.filter_by(osmand_id="unit-99").first()
        assert eq is not None
        assert eq.name == "Tracteur OsmAnd"
        assert eq.token_api == "secret"
