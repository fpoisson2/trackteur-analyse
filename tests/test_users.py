import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import create_app  # noqa: E402
from models import db, User, Equipment  # noqa: E402
import zone  # noqa: E402

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")


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
        db.session.commit()
    return app


def login(client, username="admin", password="pass"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
    )


def test_non_admin_cannot_access_users():
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


def test_non_admin_cannot_access_admin_page():
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


def test_non_admin_cannot_access_initdb():
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    resp = client.get("/initdb")
    assert resp.status_code == 302


def test_admin_add_and_delete_user():
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.post(
        "/users",
        data={
            "action": "add",
            "username": "bob",
            "password": "secret",
            "role": "read",
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username="bob").first()
        assert user is not None
        uid = user.id
    client.post("/users", data={"action": "delete", "user_id": str(uid)})
    with app.app_context():
        assert User.query.filter_by(username="bob").first() is None


def test_password_reset():
    app = make_app()
    with app.app_context():
        u = User(username="temp", is_admin=False)
        u.set_password("old")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    login(client)
    client.post(
        "/users",
        data={"action": "reset", "user_id": str(uid), "password": "new"},
    )
    with app.app_context():
        user = User.query.get(uid)
        assert user.check_password("new")


def test_non_admin_cannot_reanalyze():
    app = make_app()
    with app.app_context():
        u = User(username="reader", is_admin=False)
        u.set_password("pwd")
        db.session.add(u)
        db.session.commit()
    client = app.test_client()
    login(client, "reader", "pwd")
    resp = client.post("/reanalyze_all")
    assert resp.status_code == 302


def test_admin_can_trigger_reanalyze(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        db.session.add(Equipment(id_traccar=1, name="eq"))
        db.session.commit()

    called = []

    def fake_process(eq, base, db, since=None):
        called.append(eq.id_traccar)

    monkeypatch.setattr(zone, "process_equipment", fake_process)

    resp = client.post("/reanalyze_all")
    assert resp.status_code == 302
    assert called == [1]
