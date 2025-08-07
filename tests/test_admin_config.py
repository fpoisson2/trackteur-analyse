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
import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402
import threading  # noqa: E402


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


def test_upgrade_db_adds_config_columns():
    db_path = Path("instance/trackteur.db")
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE config (id INTEGER PRIMARY KEY, traccar_url TEXT, "
        "traccar_token TEXT)"
    )
    conn.execute(
        "INSERT INTO config (traccar_url, traccar_token) VALUES "
        "('http://old', 'tok')"
    )
    conn.commit()
    conn.close()

    os.environ["SKIP_INITIAL_ANALYSIS"] = "1"
    app = create_app()
    os.environ.pop("SKIP_INITIAL_ANALYSIS", None)
    client = app.test_client()
    client.get("/setup")
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.eps_meters == 25.0
        assert cfg.min_surface_ha == 0.1
        assert cfg.alpha == 0.02
    db_path.unlink()


def test_admin_handles_fetch_error(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    def fake_fetch_devices():
        raise zone.requests.exceptions.HTTPError("401")

    monkeypatch.setattr(zone, "fetch_devices", fake_fetch_devices)
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert (
        "Impossible de récupérer les équipements"
        in resp.get_data(as_text=True)
    )


def test_admin_page_has_status_poll(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    resp = client.get("/admin")
    html = resp.get_data(as_text=True)
    assert "credentials: 'same-origin'" in html


def test_reanalyze_saves_params(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    devices = [{"id": 1, "name": "eq"}]
    monkeypatch.setattr(zone, "fetch_devices", lambda: devices)
    called = []

    def fake_process(eq, since=None):
        called.append(eq.id_traccar)

    monkeypatch.setattr(zone, "process_equipment", fake_process)

    class InstantThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(threading, "Thread", InstantThread)

    resp = client.post(
        "/reanalyze_all",
        data={
            "base_url": "http://new.com",
            "token_global": "tok",
            "equip_ids": ["1"],
            "eps_meters": "40",
            "min_surface": "0.3",
            "alpha_shape": "0.07",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.traccar_url == "http://new.com"
        assert cfg.traccar_token == "tok"
        assert cfg.eps_meters == 40
        assert cfg.min_surface_ha == 0.3
        assert cfg.alpha == 0.07
    assert called == [1]
    status = client.get("/analysis_status")
    assert status.json == {
        "running": False,
        "current": 1,
        "total": 1,
        "equipment": "",
    }
