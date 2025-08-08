import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import create_app  # noqa: E402
from models import db, User, Config, Equipment  # noqa: E402
from tests.utils import login  # noqa: E402


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
            Config(traccar_url="http://example.com", traccar_token="tok")
        )
        db.session.add(Equipment(id_traccar=1, name="eq"))
        db.session.commit()
    return app


def test_post_without_csrf_returns_400():
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.post("/admin", data={"base_url": "http://new.com"})
    assert resp.status_code == 400
