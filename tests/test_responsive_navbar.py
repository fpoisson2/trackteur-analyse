from app import create_app  # noqa: E402
from models import db, User, Equipment, Config
from tests.utils import login


def make_app():
    app = create_app(start_scheduler=False, run_initial_analysis=False)
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
        db.session.add(Equipment(id_traccar=1, name="tractor"))
        db.session.commit()
    return app


def test_index_navbar_responsive():
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    toggler = soup.select_one("button.navbar-toggler")
    assert toggler is not None
    assert toggler.get("data-bs-target") == "#navbarNav"
    nav_container = soup.select_one("div#navbarNav")
    assert nav_container is not None
    assert "navbar-collapse" in nav_container.get("class", [])
