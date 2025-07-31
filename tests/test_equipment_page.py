import os
import sys
from datetime import date

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Equipment, Position  # noqa: E402
from models import DailyZone, Config  # noqa: E402


def make_app():
    os.environ["SKIP_INITIAL_ANALYSIS"] = "1"
    app = create_app()
    os.environ.pop("SKIP_INITIAL_ANALYSIS", None)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with app.app_context():
        db.drop_all()
        db.create_all()
        admin = User(username="admin", is_admin=True)
        admin.set_password("pass")
        db.session.add(admin)
        db.session.add(
            Config(
                traccar_url="http://example.com",
                traccar_token="dummy",
            )
        )
        eq = Equipment(id_traccar=1, name="tractor")
        db.session.add(eq)
        db.session.commit()

        dz = DailyZone(
            equipment_id=eq.id,
            date=date.today(),
            surface_ha=1.0,
            polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        db.session.add(dz)
        for i in range(3):
            db.session.add(
                Position(
                    equipment_id=eq.id,
                    latitude=0.0,
                    longitude=0.0,
                    timestamp=date.today(),
                )
            )
        db.session.commit()
    return app


def login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "pass"},
    )


def test_equipment_detail_page_loads():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    assert resp.status_code == 200
    assert b'id="map"' in resp.data


def test_zones_geojson_api_returns_features():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
    resp = client.get(
        f"/equipment/{eq.id}/zones.geojson?bbox=0,0,2,2&zoom=12"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["features"]


def test_points_geojson_api_returns_features():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
    resp = client.get(
        f"/equipment/{eq.id}/points.geojson?bbox=-1,-1,1,1"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["features"]
