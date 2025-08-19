from unittest.mock import patch

import pytest

import casic

from tests.utils import login


def test_fetch_rinex_brdc_uses_env_token(monkeypatch, tmp_path):
    """Ensure CDDIS_TOKEN env var is forwarded as Authorization header."""

    called = {}

    def fake_get(url, timeout, headers=None):  # type: ignore[no-untyped-def]
        called["headers"] = headers

        class Resp:
            content = b"data"

            def raise_for_status(self):
                pass

        return Resp()

    monkeypatch.setenv("CDDIS_TOKEN", "secret")
    monkeypatch.setattr("casic.requests.get", fake_get)
    out_path = tmp_path / "brdc.gz"
    casic.fetch_rinex_brdc(2024, 1, str(out_path))
    assert called["headers"] == {"Authorization": "Bearer secret"}


@pytest.mark.usefixtures("base_make_app")
def test_casic_ephemeris_endpoint(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    with patch("casic.build_casic_ephemeris", return_value=["deadbeef"]):
        resp = client.get("/casic_ephemeris?year=2024&doy=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"frames": ["deadbeef"]}


@pytest.mark.usefixtures("base_make_app")
def test_casic_ephemeris_download_error(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    with patch(
        "casic.build_casic_ephemeris", side_effect=RuntimeError("boom")
    ):
        resp = client.get("/casic_ephemeris")
    assert resp.status_code == 502
    assert resp.get_json() == {"error": "boom"}
