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
    return client.post(
        "/login", data={"username": "admin", "password": "pass"}
    )


def test_login_rejects_short_username():
    app = make_app()
    client = app.test_client()
    resp = client.post(
        "/login", data={"username": "ab", "password": "pass"}
    )
    assert resp.status_code == 200
    assert "entre 3 et 80" in resp.get_data(as_text=True)


def test_admin_rejects_invalid_url(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    resp = client.post(
        "/admin",
        data={
            "base_url": "invalid",
            "token_global": "tok",
            "eps_meters": "30",
            "min_surface": "0.2",
            "alpha_shape": "0.05",
        },
    )
    assert resp.status_code == 200
    assert "URL serveur invalide" in resp.get_data(as_text=True)


def test_user_add_requires_long_username():
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.post(
        "/users",
        data={
            "action": "add",
            "username": "ab",
            "password": "secret",
            "role": "read",
        },
    )
    assert resp.status_code == 200
    assert "entre 3 et 80" in resp.get_data(as_text=True)
    with app.app_context():
        assert User.query.filter_by(username="ab").first() is None
