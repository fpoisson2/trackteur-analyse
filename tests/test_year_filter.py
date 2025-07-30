import os
import sys
from datetime import date

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Equipment, DailyZone, Config  # noqa: E402


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
            Config(traccar_url="http://example.com", traccar_token="dummy")
        )
        eq = Equipment(id_traccar=1, name="tractor")
        db.session.add(eq)
        db.session.commit()
        db.session.add_all([
            DailyZone(
                equipment_id=eq.id,
                date=date(2023, 1, 1),
                surface_ha=1.0,
                polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
            ),
            DailyZone(
                equipment_id=eq.id,
                date=date(2024, 1, 1),
                surface_ha=2.0,
                polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
            ),
        ])
        db.session.commit()
    return app


def login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "pass"},
    )


def test_year_filter_on_index():
    app = make_app()
    client = app.test_client()
    login(client)

    resp = client.get("/?year=2023")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "1.0" in html
    assert "2.0" not in html

    resp = client.get("/?year=2024")
    html = resp.data.decode()
    assert "2.0" in html
