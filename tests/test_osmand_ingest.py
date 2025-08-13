import json
from datetime import datetime

import pytest

from models import db, Equipment, Position
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

