from unittest.mock import patch

from models import db, Config
from tests.utils import login


def test_casic_ephemeris_uses_configured_url_and_token(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        cfg = Config.query.first()
        cfg.ephemeris_url = "https://example.com/{year}/{doy:03d}/{yy:02d}n/brdc{doy:03d}0.{yy:02d}n.gz"
        cfg.ephemeris_token = "abc123"
        db.session.commit()

    with patch("casic.build_casic_ephemeris", return_value=["beef"]) as mock_build:
        resp = client.get("/casic_ephemeris?year=2024&doy=12")
        assert resp.status_code == 200
        # Validate the route forwarded config to casic builder
        called_args, called_kwargs = mock_build.call_args
        assert called_args[0] == 2024
        assert called_args[1] == 12
        assert called_kwargs.get("url_template").startswith("https://example.com/")
        assert called_kwargs.get("token") == "abc123"


def test_casic_ephemeris_hour_template_forwarding(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        cfg = Config.query.first()
        cfg.ephemeris_url = "https://cddis.nasa.gov/archive/gnss/data/hourly/{year}/{doy:03d}/hour{doy:03d}{hour}.{yy:02d}n.gz"
        cfg.ephemeris_token = "tok"
        db.session.commit()

    with patch("casic.build_casic_ephemeris", return_value=["cafe"]) as mock_build:
        resp = client.get("/casic_ephemeris?year=2025&doy=231&hour=10")
        assert resp.status_code == 200
        called_args, called_kwargs = mock_build.call_args
        assert called_args[0] == 2025
        assert called_args[1] == 231
        assert called_kwargs.get("hour") == 10
        # The route should pass through the configured template and token
        assert called_kwargs.get("url_template").startswith("https://cddis.nasa.gov/")
        assert called_kwargs.get("token") == "tok"
