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
    monkeypatch.setattr(
        app_module, "get_latest_version", lambda include_prerelease=False: "2025.08.1"
    )
    monkeypatch.setattr(
        app_module,
        "get_available_versions",
        lambda include_prerelease=False: ["2025.08.1", "2025.08.0"],
    )
    monkeypatch.setattr(
        app_module, "get_release_notes_url", lambda tag: f"https://example.com/{tag}"
    )
    resp = client.get("/admin/update")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert "2025.08.0" in data
    assert "2025.08.1" in data
    assert "https://example.com/2025.08.1" in data


def test_admin_update_post(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    versions = iter(["2025.08.0", "2025.08.0", "2025.08.1"])
    monkeypatch.setattr(
        app_module, "get_current_version", lambda: next(versions)
    )
    monkeypatch.setattr(
        app_module, "get_latest_version", lambda include_prerelease=False: "2025.08.1"
    )
    monkeypatch.setattr(
        app_module,
        "get_available_versions",
        lambda include_prerelease=False: ["2025.08.1", "2025.08.0"],
    )
    monkeypatch.setattr(
        app_module, "get_release_notes_url", lambda tag: f"https://example.com/{tag}"
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


def test_beta_mode_shows_prerelease(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(app_module, "get_current_version", lambda: "2025.08.0")

    def fake_latest(include_prerelease=False):
        return "2025.08.1b1" if include_prerelease else "2025.08.0"

    def fake_versions(include_prerelease=False):
        return ["2025.08.1b1", "2025.08.0"] if include_prerelease else ["2025.08.0"]

    monkeypatch.setattr(app_module, "get_latest_version", fake_latest)
    monkeypatch.setattr(app_module, "get_available_versions", fake_versions)
    monkeypatch.setattr(
        app_module, "get_release_notes_url", lambda tag: f"https://example.com/{tag}"
    )
    resp = client.get("/admin/update")
    assert "2025.08.1b1" not in resp.get_data(as_text=True)

    token = get_csrf(client, "/admin/update")
    resp = client.post(
        "/admin/update",
        data={"csrf_token": token, "version": "2025.08.0", "include_prerelease": "y"},
    )
    assert resp.status_code == 200
    resp = client.get("/admin/update")
    assert "2025.08.1b1" in resp.get_data(as_text=True)


def test_update_modal_shown(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setenv("CHECK_UPDATES", "1")
    monkeypatch.setattr(app_module, "get_current_version", lambda: "2025.08.0")
    monkeypatch.setattr(
        app_module, "get_latest_version", lambda include_prerelease=False: "2025.08.1"
    )
    monkeypatch.setattr(
        app_module, "get_release_notes_url", lambda tag: f"https://example.com/{tag}"
    )
    resp = client.get("/")
    data = resp.get_data(as_text=True)
    assert "updateModal" in data
    assert "2025.08.1" in data
