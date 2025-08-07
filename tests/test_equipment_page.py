import os
import sys
import json
import re
from datetime import date, timedelta, datetime
from pathlib import Path

from pytest import approx

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User, Equipment, Position, Track  # noqa: E402
from models import DailyZone, Config  # noqa: E402
import zone  # noqa: E402


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

        today = date.today()
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        prev_year = today - timedelta(days=365)
        yesterday = today - timedelta(days=1)
        dz1 = DailyZone(
            equipment_id=eq.id,
            date=today,
            surface_ha=1.0,
            polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        dz2 = DailyZone(
            equipment_id=eq.id,
            date=today,
            surface_ha=1.0,
            polygon_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))",
        )
        dz_yesterday = DailyZone(
            equipment_id=eq.id,
            date=yesterday,
            surface_ha=1.0,
            polygon_wkt='POLYGON((2 0,3 0,3 1,2 1,2 0))',
        )
        dz_prev_month = DailyZone(
            equipment_id=eq.id,
            date=prev_month,
            surface_ha=1.0,
            polygon_wkt='POLYGON((2 2,3 2,3 3,2 3,2 2))',
        )
        dz_prev_year = DailyZone(
            equipment_id=eq.id,
            date=prev_year,
            surface_ha=1.0,
            polygon_wkt='POLYGON((4 0,5 0,5 1,4 1,4 0))',
        )
        db.session.add_all(
            [dz1, dz2, dz_yesterday, dz_prev_month, dz_prev_year]
        )
        for i in range(3):
            db.session.add(
                Position(
                    equipment_id=eq.id,
                    latitude=0.0,
                    longitude=0.0,
                    timestamp=today,
                )
            )
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=0.5,
                longitude=2.5,
                timestamp=yesterday,
            )
        )
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=2.5,
                longitude=2.5,
                timestamp=prev_month,
            )
        )
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=4.0,
                longitude=0.0,
                timestamp=prev_year,
            )
        )
        nozone_day = today - timedelta(days=2)
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=6.0,
                longitude=0.0,
                timestamp=nozone_day,
            )
        )
        db.session.commit()
    return app


def login(client):
    return client.post(
        "/login",
        data={"username": "admin", "password": "pass"},
    )


def get_js_array(html: str, var_name: str):
    match = re.search(rf"const {var_name} = (\[.*?\]);", html)
    assert match, f"{var_name} not found"
    return json.loads(match.group(1))


def test_header_has_clickable_logo_and_no_buttons():
    app = make_app()
    client = app.test_client()
    login(client)

    from flask import url_for

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    with app.test_request_context():
        index_url = url_for("index")

    html = resp.data.decode()
    assert "Retour" not in html
    assert "Déconnexion" not in html

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    logo_link = soup.find("a", href=index_url)
    assert logo_link is not None
    assert logo_link.find("img", alt="Trackteur Analyse") is not None


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
    assert html.find('id="map-container"') < html.find('id="zones-table"')


def test_equipment_defaults_to_last_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert f'value="{today.isoformat()}"' in html


def test_multi_pass_zone_included():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        url = (
            f"/equipment/{eq.id}?year={today.year}&month={today.month}"
            f"&day={today.day}"
        )
        resp = client.get(url)
    html = resp.data.decode()
    assert '<td>2</td>' in html


def test_day_menu_excludes_days_without_zones():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        nz = date.today() - timedelta(days=2)
        resp = client.get(
            f"/equipment/{eq.id}?year={nz.year}&month={nz.month}"
        )
    html = resp.data.decode()
    dates = get_js_array(html, "availableDates")
    assert nz.isoformat() not in dates
    assert "onDayCreate" in html
    assert ".flatpickr-day.no-data" in html
    assert "flatpickr-disabled" in html
    assert "!availableDates.includes(start)" in html
    assert "!availableDates.includes(end)" in html


def test_equipment_page_has_calendar_control_without_arrows():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert 'id="open-calendar"' in html
    assert 'id="prev-day"' not in html
    assert 'id="next-day"' not in html


def test_calendar_shows_with_tracks_only():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        DailyZone.query.delete()
        Track.query.delete()
        db.session.commit()
        tr = Track(
            equipment_id=eq.id,
            start_time=datetime.combine(
                date.today(), datetime.min.time()
            ),
            end_time=(
                datetime.combine(date.today(), datetime.min.time())
                + timedelta(hours=1)
            ),
            line_wkt="LINESTRING(0 0,1 1)",
        )
        db.session.add(tr)
        db.session.commit()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    dates = get_js_array(html, "availableDates")
    assert date.today().isoformat() in dates
    assert 'Aucune donnée disponible' not in html
    assert 'id="date-display"' in html


def test_single_day_request_with_tracks():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        DailyZone.query.delete()
        Track.query.delete()
        db.session.commit()
        d = date.today()
        tr = Track(
            equipment_id=eq.id,
            start_time=datetime.combine(d, datetime.min.time()),
            end_time=(
                datetime.combine(d, datetime.min.time()) + timedelta(hours=1)
            ),
            line_wkt="LINESTRING(0 0,1 1)",
        )
        db.session.add(tr)
        db.session.commit()
        url = f"/equipment/{eq.id}?year={d.year}&month={d.month}&day={d.day}"
        resp = client.get(url)
    assert resp.status_code == 200
    html = resp.data.decode()
    assert f'value="{d.isoformat()}"' in html


def test_tracks_and_points_geojson():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        Position.query.delete()
        db.session.commit()
        track = Track(
            equipment_id=eq.id,
            start_time=date.today(),
            end_time=date.today(),
            line_wkt="LINESTRING(0 0,1 1)",
        )
        db.session.add(track)
        db.session.flush()
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=0,
                longitude=0,
                timestamp=date.today(),
                track_id=track.id,
            )
        )
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=1,
                longitude=1,
                timestamp=date.today(),
                track_id=track.id,
            )
        )
        db.session.commit()
        eqid = eq.id

    resp = client.get(f"/equipment/{eqid}/points.geojson")
    data = resp.get_json()
    assert data["features"] == []
    resp = client.get(f"/equipment/{eqid}/points.geojson?all=1")
    data = resp.get_json()
    assert len(data["features"]) == 2
    resp = client.get(f"/equipment/{eqid}/tracks.geojson")
    data = resp.get_json()
    assert len(data["features"]) == 1
    assert data["features"][0]["geometry"]["type"] == "LineString"


def test_tracks_endpoint_triggers_analysis(monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        Track.query.delete()
        db.session.commit()

        called = {"count": 0}

        def fake_process(equipment, since=None):
            called["count"] += 1
            tr = Track(
                equipment_id=equipment.id,
                start_time=date.today(),
                end_time=date.today(),
                line_wkt="LINESTRING(0 0,1 1)",
            )
            db.session.add(tr)
            db.session.commit()

        monkeypatch.setattr(zone, "process_equipment", fake_process)
        eqid = eq.id

    resp = client.get(f"/equipment/{eqid}/tracks.geojson")
    data = resp.get_json()
    assert called["count"] == 1
    assert len(data["features"]) == 1


def test_equipment_page_has_help_button_and_no_legend():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "const legend = L.control" not in html
    assert "button.id = 'legend-btn'" in html
    assert "button.innerHTML = '?'" in html
    assert 'id="legend-popup"' in html


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
    assert "return new Promise" in snippet
    assert "return Promise.resolve()" in snippet


def test_equipment_page_contains_highlight_rows():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "function highlightRows" in html
    start = html.find("function highlightRows")
    end = html.find("function highlightZone")
    snippet = html[start:end] if end != -1 else html[start:]
    assert "highlighted" in snippet
    assert "ids.includes(r.dataset.zoneId)" in snippet
    assert "parseInt" not in snippet


def test_map_container_allows_touch():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find('<div id="map-container"')
    end = html.find('>', start)
    tag = html[start:end]
    assert "touch-action: none" not in tag


def test_equipment_sheet_has_data_attributes_and_script():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert 'data-sheet="equipment"' in html
    assert 'data-sheet-content' in html
    assert 'data-open="false"' in html
    assert 'equipment-sheet.js' in html


def test_row_click_fits_bounds_without_zoom_out():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "map.fitBounds(bounds" in html
    assert "animate: false" in html
    assert "zoomOut" not in html
    assert "autoZoomed" not in html
    assert "panTo(center" not in html
    assert "fetchData().then" in html


def test_row_click_calls_fit_bounds():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "map.fitBounds(bounds" in html


def test_row_click_does_not_zoom_out():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "zoomOut" not in html
    assert "autoZoomed" not in html


def test_row_click_calls_highlight_zone_with_popup():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find("row.addEventListener('click'")
    end = html.find("});", start)
    snippet = html[start:end]
    assert "async () =>" in snippet
    assert "const zoneId = row.dataset.zoneId" in snippet
    assert "openEquipmentSheet()" in snippet
    assert "if (!zonesLoaded)" in snippet
    fd = snippet.index("await fetchData()")
    os = snippet.index("openEquipmentSheet()")
    sz = snippet.index("await selectZone(zoneId)")
    assert fd < os < sz
    assert "parseInt" not in snippet


def test_select_zone_calls_highlight_and_popup():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find("function selectZone")
    end = html.find("function fetchData")
    snippet = html[start:end] if end != -1 else html[start:]
    assert "highlightRows([zoneId])" in snippet
    assert "return highlightZone(zoneId, true)" in snippet
    assert "parseInt" not in snippet


def test_highlight_zone_offsets_for_open_sheet():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find("function highlightZone")
    end = html.find("function selectZone")
    snippet = html[start:end] if end != -1 else html[start:]
    assert "[data-sheet=\"equipment\"]" in snippet
    assert "getAttribute('data-open') === 'true'" in snippet
    assert "paddingBottomRight: [0, offset]" in snippet
    assert "map.panBy([0, offset / 2" in snippet
    assert "map.panBy([0, -offset" not in snippet


def test_rebuild_date_layers_uses_properties_id():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find("function rebuildDateLayers")
    end = html.find("function highlightRows")
    snippet = html[start:end] if end != -1 else html[start:]
    assert "layer.feature.properties.id" in snippet
    assert "layer.feature.id" in snippet


def test_polygon_click_calls_select_zone_without_opening_sheet():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    start = html.find("layer.on('click'")
    end = html.find("});", start)
    snippet = html[start:end]
    assert "feature.properties.id" in snippet
    assert "feature.id" in snippet
    assert "String(" in snippet
    assert "async () =>" in snippet
    assert "await selectZone(zoneId)" in snippet
    assert "openEquipmentSheet()" not in snippet


def test_bounds_check_before_zooming():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert "getBounds().contains" not in html


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
    assert "zones.geojson" in html


def test_equipment_page_has_period_selectors():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")
    html = resp.data.decode()
    assert 'id="date-display"' in html
    assert 'id="open-calendar"' in html


def test_zones_geojson_filters_by_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp = client.get(
            f"/equipment/{eq.id}/zones.geojson?year={today.year}"
            f"&month={today.month}&day={today.day}&zoom=12"
        )
    data = resp.get_json()
    for feat in data["features"]:
        assert all(d == today.isoformat() for d in feat["properties"]["dates"])


def test_zones_geojson_filters_by_range():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        yesterday = today - timedelta(days=1)
        resp = client.get(
            f"/equipment/{eq.id}/zones.geojson?start={yesterday.isoformat()}&"
            f"end={today.isoformat()}&zoom=12",
        )

    data = resp.get_json()
    for feat in data["features"]:
        for d in feat["properties"]["dates"]:
            dd = date.fromisoformat(d)
            assert yesterday <= dd <= today


def test_zones_geojson_range_with_gap():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        resp = client.get(
            f"/equipment/{eq.id}/zones.geojson?start={prev_month.isoformat()}&"
            f"end={today.isoformat()}&zoom=12",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    all_dates = []
    for feat in data["features"]:
        all_dates.extend(feat["properties"]["dates"])
    assert today.isoformat() in all_dates
    assert prev_month.isoformat() in all_dates


def test_zones_geojson_uses_global_ids():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        yesterday = date.today() - timedelta(days=1)
        agg_all = zone.get_aggregated_zones(eq.id)
        full_idx = next(
            i for i, z in enumerate(agg_all) if str(yesterday) in z["dates"]
        )
        resp = client.get(
            f"/equipment/{eq.id}/zones.geojson?start={yesterday.isoformat()}&"
            f"end={yesterday.isoformat()}&zoom=12"
        )
    data = resp.get_json()
    assert data["features"], "no features returned"
    feat = data["features"][0]
    assert feat["id"] == str(full_idx)
    assert feat["properties"]["id"] == str(full_idx)


def test_points_geojson_filters_by_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        prev_year = date.today() - timedelta(days=365)
        resp = client.get(
            f"/equipment/{eq.id}/points.geojson?"
            f"year={prev_year.year}&month={prev_year.month}"
            f"&day={prev_year.day}&limit=100"
        )
    data = resp.get_json()
    assert len(data["features"]) == 1


def test_points_geojson_range_with_gap():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        prev_year = today - timedelta(days=365)
        resp = client.get(
            f"/equipment/{eq.id}/points.geojson?start={prev_year.isoformat()}&"
            f"end={today.isoformat()}"
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["features"]


def test_tracks_geojson_filters_cross_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        Track.query.delete()
        db.session.commit()
        start = (
            datetime.combine(
                date.today() - timedelta(days=1), datetime.min.time()
            )
            + timedelta(hours=23)
        )
        end = (
            datetime.combine(date.today(), datetime.min.time())
            + timedelta(hours=1)
        )
        tr = Track(
            equipment_id=eq.id,
            start_time=start,
            end_time=end,
            line_wkt="LINESTRING(0 0,1 1)",
        )
        db.session.add(tr)
        db.session.commit()
        eqid = eq.id
        today = date.today()
        prev = today - timedelta(days=1)

    resp = client.get(
        f"/equipment/{eqid}/tracks.geojson?"
        f"year={today.year}&month={today.month}&day={today.day}"
    )
    data = resp.get_json()
    assert len(data["features"]) == 1

    resp = client.get(
        f"/equipment/{eqid}/tracks.geojson?"
        f"year={prev.year}&month={prev.month}&day={prev.day}"
    )
    data = resp.get_json()
    assert len(data["features"]) == 1


def test_tracks_geojson_range_with_gap():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        Track.query.delete()
        db.session.commit()
        tr = Track(
            equipment_id=eq.id,
            start_time=datetime.combine(date.today(), datetime.min.time()),
            end_time=(
                datetime.combine(date.today(), datetime.min.time())
                + timedelta(hours=1)
            ),
            line_wkt="LINESTRING(0 0,1 1)",
        )
        db.session.add(tr)
        db.session.commit()
        today = date.today()
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        url = (
            f"/equipment/{eq.id}/tracks.geojson?"
            f"start={prev_month.isoformat()}&end={today.isoformat()}"
        )
        resp = client.get(url)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["features"]


def test_equipment_detail_filters_by_period():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        prev_month = (
            date.today().replace(day=1) - timedelta(days=1)
        ).replace(day=1)
        resp = client.get(
            f"/equipment/{eq.id}?year={prev_month.year}"
            f"&month={prev_month.month}"
        )
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#zones-table tbody tr")
    assert len(rows) == 1
    assert prev_month.isoformat() in rows[0].find_all("td")[0].text


def test_equipment_detail_filters_by_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp = client.get(
            f"/equipment/{eq.id}?year={today.year}&month={today.month}"
            f"&day={today.day}"
        )
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#zones-table tbody tr")
    assert len(rows) == 1
    assert today.isoformat() in rows[0].find_all("td")[0].text


def test_equipment_page_exposes_year_month_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp = client.get(
            f"/equipment/{eq.id}?year={today.year}&month={today.month}"
            f"&day={today.day}"
        )

    html = resp.data.decode()
    assert f"const year = {today.year}" in html
    assert f"const month = {today.month}" in html
    assert f"const day = {today.day}" in html


def test_map_and_table_zones_match_for_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp_page = client.get(
            f"/equipment/{eq.id}?year={today.year}&month={today.month}"
            f"&day={today.day}"
        )
        resp_geo = client.get(
            f"/equipment/{eq.id}/zones.geojson?zoom=17"
            f"&year={today.year}&month={today.month}&day={today.day}"
        )

    html = resp_page.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table_ids = {
        row["data-zone-id"]
        for row in soup.select("#zones-table tbody tr")
    }
    data = resp_geo.get_json()
    feature_ids = {feat["id"] for feat in data["features"]}
    assert table_ids == feature_ids


def test_initial_bounds_reflect_selected_day():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp_all = client.get(f"/equipment/{eq.id}?show=all")
        resp_day = client.get(
            f"/equipment/{eq.id}?year={today.year}"
            f"&month={today.month}&day={today.day}"
        )

    bounds_all = get_js_array(resp_all.data.decode(), "initialBounds")
    bounds_day = get_js_array(resp_day.data.decode(), "initialBounds")

    width_all = bounds_all[2] - bounds_all[0]
    width_day = bounds_day[2] - bounds_day[0]

    assert width_all == approx(5 * width_day, rel=0.1)
    assert bounds_day[0] == approx(bounds_all[0])
    assert bounds_day[1] == approx(bounds_all[1])


def test_equipment_detail_filters_by_range():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        yesterday = today - timedelta(days=1)
        resp = client.get(
            f"/equipment/{eq.id}?start={yesterday.isoformat()}&"
            f"end={today.isoformat()}"
        )

    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#zones-table tbody tr")
    assert len(rows) == 2
    dates = [r.find_all("td")[0].text for r in rows]
    assert any(yesterday.isoformat() in d for d in dates)
    assert any(today.isoformat() in d for d in dates)


def test_equipment_detail_range_with_gap():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        resp = client.get(
            f"/equipment/{eq.id}?start={prev_month.isoformat()}&"
            f"end={today.isoformat()}"
        )

    assert resp.status_code == 200
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("#zones-table tbody tr")
    assert rows
    dates = [r.find_all("td")[0].text for r in rows]
    assert any(prev_month.isoformat() in d for d in dates)
    assert any(today.isoformat() in d for d in dates)


def test_initial_bounds_include_tracks():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        track = Track(
            equipment_id=eq.id,
            start_time=date.today(),
            end_time=date.today(),
            line_wkt="LINESTRING(10 0,11 0)",
        )
        db.session.add(track)
        db.session.commit()
        resp = client.get(f"/equipment/{eq.id}?show=all")

    bounds = get_js_array(resp.data.decode(), "initialBounds")
    assert bounds[2] > 9


def test_overlay_bundle_guard_present():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")

    html = resp.data.decode()
    assert "js/overlay_bundle.js" in html

    project_root = Path(__file__).resolve().parents[1]
    overlay_path = project_root / "static" / "js" / "overlay_bundle.js"
    content = overlay_path.read_text()
    assert "customElements.get('mce-autosize-textarea')" in content


def test_map_click_does_not_open_sheet():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")

    html = resp.data.decode()
    assert html.count("openEquipmentSheet()") == 1


def test_calendar_allows_single_day_selection():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        resp = client.get(f"/equipment/{eq.id}")

    html = resp.data.decode()
    assert "firstDate = null" in html
    assert "instance.setDate([current, current], true)" in html
    assert "picker.clear()" in html


def test_track_and_point_requests_use_day_params():
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        today = date.today()
        resp = client.get(
            f"/equipment/{eq.id}?year={today.year}&month={today.month}"
            f"&day={today.day}"
        )

    html = resp.data.decode()
    assert "trackParams.set('year', year)" in html
    assert "pointParams.set('year', year)" in html
