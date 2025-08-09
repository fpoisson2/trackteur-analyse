import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tests.utils import login  # noqa: E402


def test_post_without_csrf_returns_400(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.post("/admin", data={"base_url": "http://new.com"})
    assert resp.status_code == 400
