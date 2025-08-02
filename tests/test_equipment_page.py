import os
import sys
from datetime import date

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Equipment, Position  # noqa: E402
from models import DailyZone, Config  # noqa: E402


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
            Config(
                traccar_url="http://example.com",
                traccar_token="dummy",
            )
        )
        eq = Equipment(id_traccar=1, name="tractor")
        db.session.add(eq)
        db.session.commit()

        dz1 = DailyZone(
            equipment_id=eq.id,
            date=date.today(),
            surface_ha=1.0,
            polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        dz2 = DailyZone(
            equipment_id=eq.id,
            date=date.today(),
            surface_ha=1.0,
            polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        db.session.add_all([dz1, dz2])
        for i in range(3):
            db.session.add(
                Position(
                    equipment_id=eq.id,
                    latitude=0.0,
                    longitude=0.0,
                    timestamp=date.today(),
                )
            )
        db.session.commit()
    return app


def login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "pass"},
    )


def test_equipment_detail_page_loads():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "map-container" in html
    assert "zones-table" in html
    assert html.index("map-container") < html.index("zones-table")


def test_equipment_page_shows_legend():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "legend" in html


def test_zones_geojson_endpoint():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(
            f"/equipment/{eq.id}/zones.geojson?bbox=-180,-90,180,90&zoom=12"
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["features"]
    assert "surface_ha" in data["features"][0]["properties"]
    assert "dz_ids" in data["features"][0]["properties"]


def test_points_geojson_endpoint():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(
            f"/equipment/{eq.id}/points.geojson?bbox=-180,-90,180,90&limit=2"
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["features"]) <= 2


def test_equipment_page_contains_highlight_zone():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "function highlightZone" in html
    start = html.find("function highlightZone")
    end = html.find("function fetchData")
    snippet = html[start:end] if end != -1 else html[start:]
    assert "fetchData()" in snippet


def test_equipment_page_contains_highlight_rows():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "function highlightRows" in html


def test_map_container_has_touch_action():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "touch-action: none" in html


def test_row_click_uses_instant_zoom():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "animate: false" in html
    assert "fitBounds(bounds, { animate: false" in html
    assert "once('moveend', ensureZoom" in html
    assert "once('zoomend', finish" in html
    assert "fetchData().then" in html


def test_row_click_recenters_when_visible():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "panTo(center, { animate: false" in html


def test_row_click_enforces_min_zoom():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "setZoom(17" in html


def test_highlight_zone_skip_zoom_parameter():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "skipZoom" in html
    assert "highlightZone(zoneId, true)" in html


def test_bounds_check_before_zooming():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "getBounds().contains" in html


def test_zone_rows_have_ids():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert 'data-zone-id="' in html


def test_equipment_table_columns():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "Date(s)" in html
    assert "Passages" in html
    assert "Hectares travaillés" in html


def test_table_shows_aggregated_pass_count():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#zones-table tbody tr")
    assert rows
    cells = rows[0].find_all("td")
    assert cells[1].text.strip() == "2"


def test_fetch_data_uses_token():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "let fetchToken" in html
    assert "token !== fetchToken" in html


def test_zones_loaded_once_on_page_load():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "zonesLoaded" in html
    assert "if (!zonesLoaded)" in html
    assert "zones.geojson?zoom=17" in html
