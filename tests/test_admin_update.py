from __future__ import annotations

from tests.utils import get_csrf, login

import app as app_module


def test_admin_update_get(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(
        app_module, "get_current_version", lambda: "2025.08.0"
    )
    monkeypatch.setattr(app_module, "get_latest_version", lambda: "2025.08.1")
    monkeypatch.setattr(
        app_module, "get_available_versions", lambda: ["2025.08.1", "2025.08.0"]
    )
    resp = client.get("/admin/update")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert "2025.08.0" in data
    assert "2025.08.1" in data


def test_admin_update_post(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    versions = iter(["2025.08.0", "2025.08.0", "2025.08.1"])
    monkeypatch.setattr(
        app_module, "get_current_version", lambda: next(versions)
    )
    monkeypatch.setattr(app_module, "get_latest_version", lambda: "2025.08.1")
    monkeypatch.setattr(
        app_module, "get_available_versions", lambda: ["2025.08.1", "2025.08.0"]
    )
    called = {}

    def fake_update(version: str) -> None:
        called["version"] = version

    monkeypatch.setattr(app_module, "perform_update", fake_update)
    token = get_csrf(client, "/admin/update")
    resp = client.post(
        "/admin/update",
        data={"csrf_token": token, "version": "2025.08.1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert called.get("version") == "2025.08.1"
    assert "2025.08.1" in resp.get_data(as_text=True)
