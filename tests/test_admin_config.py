import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import Config  # noqa: E402
import zone  # noqa: E402
import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402
import threading  # noqa: E402
from tests.utils import login, get_csrf  # noqa: E402


def test_admin_updates_server_url(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    devices = [{"id": 1, "name": "eq"}]
    monkeypatch.setattr(zone, "fetch_devices", lambda: devices)
    token = get_csrf(client, "/admin/traccar")
    resp = client.post(
        "/admin/traccar",
        data={
            "base_url": "http://new.com",
            "token_global": "tok",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.traccar_url == "http://new.com"
        assert cfg.traccar_token == "tok"


def test_admin_updates_analysis_hour(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    token = get_csrf(client, "/admin/analysis")
    resp = client.post(
        "/admin/analysis",
        data={"analysis_hour": "5", "csrf_token": token},
    )
    assert resp.status_code == 200
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.analysis_hour == 5


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

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    client = app.test_client()
    client.get("/setup")
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.eps_meters == 25.0
        assert cfg.min_surface_ha == 0.1
        assert cfg.alpha == 0.02
        assert cfg.analysis_hour == 2
    db_path.unlink()


def test_admin_handles_fetch_error(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    def fake_fetch_devices():
        raise zone.requests.exceptions.HTTPError("401")

    monkeypatch.setattr(zone, "fetch_devices", fake_fetch_devices)
    resp = client.get("/admin/equipment")
    assert resp.status_code == 200
    assert (
        "Impossible de récupérer les équipements"
        in resp.get_data(as_text=True)
    )


def test_admin_page_has_status_poll(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    resp = client.get("/admin/equipment")
    html = resp.get_data(as_text=True)
    assert "credentials: 'same-origin'" in html
    assert 'id="analysis-banner"' in html


def test_reanalyze_saves_params(make_app, monkeypatch):
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

    token = get_csrf(client, "/admin/equipment")
    resp = client.post(
        "/reanalyze_all",
        data={"csrf_token": token},
    )
    assert resp.status_code == 302
    assert called == [1]
    status = client.get("/analysis_status")
    assert status.json == {
        "running": False,
        "current": 1,
        "total": 1,
        "equipment": "",
    }


def test_admin_accepts_decimal_comma(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    token = get_csrf(client, "/admin/analysis")
    resp = client.post(
        "/admin/analysis",
        data={"eps_meters": "40,0", "analysis_hour": "3", "csrf_token": token},
    )
    assert resp.status_code == 200
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.eps_meters == 40.0
        assert cfg.analysis_hour == 3


def test_reanalyze_accepts_decimal_comma(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    devices = [{"id": 1, "name": "eq"}]
    monkeypatch.setattr(zone, "fetch_devices", lambda: devices)

    class InstantThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(threading, "Thread", InstantThread)

    # Empêche tout accès réseau en court-circuitant le traitement
    called = []

    def fake_process(eq, since=None):
        called.append(eq.id_traccar)

    monkeypatch.setattr(zone, "process_equipment", fake_process)

    token = get_csrf(client, "/admin/equipment")
    resp = client.post(
        "/reanalyze_all",
        data={
            "eps_meters": "40,0",
            "min_surface": "0,3",
            "alpha_shape": "0,07",
            "analysis_hour": "4",
            "csrf_token": token,
        },
    )
    assert resp.status_code in (200, 302)
    with app.app_context():
        cfg = Config.query.first()
        assert cfg.eps_meters == 40.0
        assert cfg.min_surface_ha == 0.3
        assert cfg.alpha == 0.07
        assert cfg.analysis_hour == 4
    assert called == [1]
