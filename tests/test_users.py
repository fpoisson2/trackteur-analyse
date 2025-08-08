import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from models import db, User  # noqa: E402
import zone  # noqa: E402
import threading  # noqa: E402
from tests.utils import login, get_csrf  # noqa: E402

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")


def test_non_admin_cannot_access_users(make_app):
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    resp = client.get("/users")
    assert resp.status_code == 302


def test_non_admin_cannot_access_admin_page(make_app):
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    resp = client.get("/admin")
    assert resp.status_code == 302


def test_admin_add_and_delete_user(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    token = get_csrf(client, "/users")
    resp = client.post(
        "/users",
        data={
            "action": "add",
            "username": "bob",
            "password": "secret",
            "role": "read",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username="bob").first()
        assert user is not None
        uid = user.id
    token = get_csrf(client, "/users")
    client.post(
        "/users",
        data={"action": "delete", "user_id": str(uid), "csrf_token": token},
    )
    with app.app_context():
        assert User.query.filter_by(username="bob").first() is None


def test_password_reset(make_app):
    app = make_app()
    with app.app_context():
        u = User(username="temp", is_admin=False)
        u.set_password("old")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    login(client)
    token = get_csrf(client, "/users")
    client.post(
        "/users",
        data={
            "action": "reset",
            "user_id": str(uid),
            "password": "new",
            "csrf_token": token,
        },
    )
    with app.app_context():
        user = db.session.get(User, uid)
        assert user.check_password("new")


def test_non_admin_cannot_reanalyze(make_app):
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    token = get_csrf(client, "/login")
    resp = client.post("/reanalyze_all", data={"csrf_token": token})
    assert resp.status_code == 302


def test_admin_can_trigger_reanalyze(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    called = []

    def fake_process(eq, since=None):
        called.append(eq.id_traccar)

    monkeypatch.setattr(zone, "process_equipment", fake_process)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])

    class InstantThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(threading, "Thread", InstantThread)

    token = get_csrf(client, "/admin")
    resp = client.post("/reanalyze_all", data={"csrf_token": token})
    assert resp.status_code == 302
    assert called == [1]
    status = client.get("/analysis_status")
    assert status.json == {
        "running": False,
        "current": 1,
        "total": 1,
        "equipment": "",
    }


def test_admin_can_reanalyze_via_get(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

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

    resp = client.get("/reanalyze_all")
    assert resp.status_code == 302
    assert called == [1]
    status = client.get("/analysis_status")
    assert status.json == {
        "running": False,
        "current": 1,
        "total": 1,
        "equipment": "",
    }


def test_analysis_status_requires_admin(make_app, monkeypatch):
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    resp = client.get("/analysis_status")
    assert resp.status_code == 403


def test_analysis_status_initial(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.get("/analysis_status")
    assert resp.json == {
        "running": False,
        "current": 0,
        "total": 0,
        "equipment": "",
    }


def test_analysis_status_reports_equipment(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    start_evt = threading.Event()
    finish_evt = threading.Event()
    done_evt = threading.Event()

    def fake_process(eq, since=None):
        start_evt.set()
        finish_evt.wait()
        done_evt.set()

    monkeypatch.setattr(zone, "process_equipment", fake_process)

    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    token = get_csrf(client, "/admin")
    resp = client.post("/reanalyze_all", data={"csrf_token": token})
    assert resp.status_code == 302

    assert start_evt.wait(1)
    status_running = client.get("/analysis_status")
    assert status_running.json == {
        "running": True,
        "current": 0,
        "total": 1,
        "equipment": "eq",
    }

    finish_evt.set()
    assert done_evt.wait(1)
    status_done = client.get("/analysis_status")
    assert status_done.json == {
        "running": False,
        "current": 1,
        "total": 1,
        "equipment": "",
    }
    assert (
        status_done.headers["Cache-Control"]
        == "no-store, no-cache, must-revalidate, max-age=0"
    )
