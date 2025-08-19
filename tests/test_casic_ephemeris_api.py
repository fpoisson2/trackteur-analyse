import pytest
from unittest.mock import patch

from tests.utils import login


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
