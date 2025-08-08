import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import create_app  # noqa: E402
from models import db, User, Config  # noqa: E402
from tests.utils import login, get_csrf  # noqa: E402
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
        db.session.commit()
    return app


def test_login_shows_field_errors():
    app = make_app()
    client = app.test_client()
    token = get_csrf(client, "/login")
    resp = client.post(
        "/login",
        data={"username": "ab", "password": "", "csrf_token": token},
    )
    html = resp.get_data(as_text=True)
    assert "Veuillez corriger les erreurs" in html
    assert "Doit faire entre 3 et 64 caractères" in html or "Nom d’utilisateur requis" in html
    assert "Mot de passe requis" in html


def test_admin_invalid_url_validation(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])
    token = get_csrf(client, "/admin")
    resp = client.post(
        "/admin",
        data={
            "base_url": "not a url",
            "token_global": "tok",
            "eps_meters": "10",
            "min_surface": "0.1",
            "alpha_shape": "0.5",
            "analysis_hour": "2",
            "csrf_token": token,
        },
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "URL invalide" in html


def test_users_add_validation_errors():
    app = make_app()
    client = app.test_client()
    login(client)
    token = get_csrf(client, "/users")
    resp = client.post(
        "/users",
        data={
            "action": "add",
            "username": "ab",
            "password": "",
            "role": "read",
            "csrf_token": token,
        },
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Veuillez corriger le formulaire d’ajout" in html or "3–64 caractères" in html


def test_users_reset_validation_error():
    app = make_app()
    with app.app_context():
        u = User(username="u1", is_admin=False)
        u.set_password("old")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    login(client)
    token = get_csrf(client, "/users")
    resp = client.post(
        "/users",
        data={
            "action": "reset",
            "user_id": str(uid),
            "password": "",
            "csrf_token": token,
        },
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Mot de passe invalide" in html

