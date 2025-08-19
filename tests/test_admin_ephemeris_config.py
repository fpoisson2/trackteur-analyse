from models import Config
from tests.utils import login, get_csrf


def test_admin_updates_ephemeris_config(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    token = get_csrf(client, "/admin/ephemeris")
    resp = client.post(
        "/admin/ephemeris",
        data={
            "base_url": "https://ephem.example.com/day",
            "token_global": "secrettok",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 200
    with app.app_context():
        cfg = Config.query.first()
        assert cfg is not None
        assert cfg.ephemeris_url == "https://ephem.example.com/day"
        assert cfg.ephemeris_token == "secrettok"


def test_admin_ephemeris_page_loads(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.get("/admin/ephemeris")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Paramètres éphémérides" in html
