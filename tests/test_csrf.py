import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tests.utils import login, get_csrf  # noqa: E402


def test_post_without_csrf_returns_400(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.post("/admin/equipment", data={"base_url": "http://new.com"})
    assert resp.status_code == 400


def test_logout_requires_csrf(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    # Missing token should return 400
    resp = client.post("/logout")
    assert resp.status_code == 400

    token = get_csrf(client, "/")
    resp = client.post("/logout", data={"csrf_token": token})
    assert resp.status_code in (200, 302, 303)
