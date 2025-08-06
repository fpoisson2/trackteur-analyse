import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Config, Equipment  # noqa: E402
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
            Config(traccar_url="http://example.com", traccar_token="dummy")
        )
        db.session.add(Equipment(id_traccar=1, name="eq"))
        db.session.commit()
    return app


def login(client):
    data = {"username": "admin", "password": "pass"}
    return client.post("/login", data=data)


def test_admin_updates_server_url(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    devices = [{"id": 1, "name": "eq"}]
    monkeypatch.setattr(zone, "fetch_devices", lambda: devices)
    resp = client.post(
        "/admin",
        data={
            "base_url": "http://new.com",
            "token_global": "tok",
            "equip_ids": ["1"],
            "eps_meters": "30",
            "min_surface": "0.2",
            "alpha_shape": "0.05",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.traccar_url == "http://new.com"
        assert cfg.traccar_token == "tok"
        assert cfg.eps_meters == 30
        assert cfg.min_surface_ha == 0.2
        assert cfg.alpha == 0.05
