import os
import sys
from datetime import datetime, date, timedelta

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from models import db, Equipment, Position, DailyZone  # noqa: E402
from tests.utils import login  # noqa: E402


@pytest.fixture(name="make_app")
def make_app_fixture(base_make_app):
    return base_make_app


def test_index_shows_last_seen_from_positions(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        # Ensure last_position is None, but positions exist
        eq.last_position = None
        ts = datetime(2023, 1, 1, 12, 0, 0)
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=0.0,
                longitude=0.0,
                timestamp=ts,
            )
        )
        db.session.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    row = soup.select_one("table tbody tr")
    assert row is not None
    cells = row.find_all("td")
    # Dernière position is the 2nd column
    assert cells[1].text.strip().startswith("2023-01-01 12:00:00")


def test_index_uses_computed_total_hectares(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        # Two separate days, 1 ha each (100m x 100m squares in arbitrary units)
        day1 = date(2023, 1, 1)
        day2 = date(2023, 1, 2)
        db.session.add(
            DailyZone(
                equipment_id=eq.id,
                date=day1,
                surface_ha=1.0,
                polygon_wkt="POLYGON((0 0,100 0,100 100,0 100,0 0))",
            )
        )
        db.session.add(
            DailyZone(
                equipment_id=eq.id,
                date=day2,
                surface_ha=1.0,
                polygon_wkt="POLYGON((0 0,100 0,100 100,0 100,0 0))",
            )
        )
        # Force a stale stored total so we can verify computed value is used
        eq.total_hectares = 0.0
        db.session.commit()

    resp = client.get("/")
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    row = soup.select_one("table tbody tr")
    assert row is not None
    cells = row.find_all("td")
    # Ha traités is the 3rd column
    # Expect 2.00 (two days x 1 ha)
    assert cells[2].text.strip() in {"2.0", "2.00"}

