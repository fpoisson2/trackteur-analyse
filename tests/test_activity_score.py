import os
import sys
from datetime import date, datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Equipment, DailyZone, Config  # noqa: E402
import zone  # noqa: E402


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
        now = datetime.utcnow()
        eq1 = Equipment(
            id_traccar=1,
            name="T1",
            total_hectares=10.0,
            distance_between_zones=1000.0,
            last_position=now - timedelta(hours=1),
        )
        eq2 = Equipment(
            id_traccar=2,
            name="T2",
            total_hectares=5.0,
            distance_between_zones=2000.0,
            last_position=now - timedelta(hours=10),
        )
        db.session.add_all([eq1, eq2])
        db.session.commit()
        db.session.add_all([
            DailyZone(
                equipment_id=eq1.id,
                date=date.today(),
                surface_ha=10.0,
                polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
            ),
            DailyZone(
                equipment_id=eq2.id,
                date=date.today(),
                surface_ha=5.0,
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


def test_index_sorted_by_score(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        ids = {e.name: e.id for e in Equipment.query.all()}

    def fake_rel(equipment_id: int) -> float:
        return 9.0 if equipment_id == ids["T1"] else 4.0

    monkeypatch.setattr(zone, "calculate_relative_hectares", fake_rel)

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "🥇" in html
    assert html.index("T1") < html.index("T2")


def test_index_has_mobile_menu():
    app = make_app()
    client = app.test_client()
    login(client)

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "navbar-toggler" in html
    assert 'id="navbar-menu"' in html
