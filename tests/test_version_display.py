from tests.utils import login

import app as app_module


def test_version_shown_in_navbar(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "get_current_version", lambda: "2030.01.4")
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "v2030.01.4" in resp.get_data(as_text=True)
