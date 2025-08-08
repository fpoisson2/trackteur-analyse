import os
import sys
import types
from datetime import datetime, date as dt_date

import pytest
from shapely.geometry import Polygon, Point, LineString

# S'assurer que le dossier racine est dans sys.path pour l'import de zone
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import zone  # noqa: E402

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")


class DummyResponse:
    def __init__(self, json_data=None, status_code=200, text="", content=b""):
        self._json = json_data
        self.status_code = status_code
        self.text = text
        self.content = (
            content if content else (b"" if json_data is None else b"1")
        )

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise zone.requests.exceptions.HTTPError(response=self)


# ---------- _determine_period ----------


def test_determine_period_year_month_day():
    start, end = zone._determine_period(year=2024, month=5, day=2)
    assert start == dt_date(2024, 5, 2)
    assert end == dt_date(2024, 5, 2)


def test_determine_period_start_end():
    start_date = dt_date(2024, 5, 1)
    end_date = dt_date(2024, 5, 2)
    start, end = zone._determine_period(start=start_date, end=end_date)
    assert start == start_date
    assert end == end_date


def test_determine_period_year_month():
    start, end = zone._determine_period(year=2024, month=2)
    assert start == dt_date(2024, 2, 1)
    assert end == dt_date(2024, 2, 29)


def test_determine_period_year_only():
    start, end = zone._determine_period(year=2024)
    assert start == dt_date(2024, 1, 1)
    assert end == dt_date(2024, 12, 31)


# ---------- fetch_devices ----------

def test_fetch_devices_success(monkeypatch):
    called = {}

    def fake_get(url, headers):
        called["url"] = url
        return DummyResponse(json_data=[{"id": 1, "name": "tractor"}])

    monkeypatch.setattr(zone.requests, "get", fake_get)
    devices = zone.fetch_devices()
    assert devices == [{"id": 1, "name": "tractor"}]
    assert called["url"].endswith("/api/devices")


def test_fetch_devices_filtered(monkeypatch):
    monkeypatch.setenv("TRACCAR_DEVICE_NAME", "tractor2")

    def fake_get(url, headers):
        return DummyResponse(
            json_data=[
                {"id": 1, "name": "tractor"},
                {"id": 2, "name": "tractor2"},
            ]
        )

    monkeypatch.setattr(zone.requests, "get", fake_get)
    devices = zone.fetch_devices()
    assert devices == [{"id": 2, "name": "tractor2"}]


def test_fetch_devices_http_error(monkeypatch):
    def fake_get(url, headers):
        return DummyResponse(status_code=404)

    monkeypatch.setattr(zone.requests, "get", fake_get)
    with pytest.raises(zone.requests.exceptions.HTTPError):
        zone.fetch_devices()


# ---------- fetch_positions ----------

def test_fetch_positions_success(monkeypatch):
    resp_data = [
        {"latitude": 0, "longitude": 0, "deviceTime": "2023-01-01T00:00:00Z"}
    ]

    def fake_get(url, headers, params):
        return DummyResponse(json_data=resp_data)

    monkeypatch.setattr(zone.requests, "get", fake_get)
    result = zone.fetch_positions(1, datetime.utcnow(), datetime.utcnow())
    assert result == resp_data


def test_fetch_positions_404(monkeypatch):
    def fake_get(url, headers, params):
        return DummyResponse(status_code=404)

    monkeypatch.setattr(zone.requests, "get", fake_get)
    result = zone.fetch_positions(1, datetime.utcnow(), datetime.utcnow())
    assert result == []


# ---------- add_joggle ----------

def test_add_joggle_changes_points():
    pts = [(0.0, 0.0), (1.0, 1.0)]
    new_pts = zone.add_joggle(pts, noise_scale=0.1)
    assert len(new_pts) == len(pts)
    assert new_pts != pts


# ---------- cluster_positions ----------

def test_cluster_positions_returns_zones():
    now = datetime.utcnow().strftime("%Y-%m-%d")
    positions = []
    for i in range(3):
        positions.append(
            {
                "latitude": 0,
                "longitude": 0,
                "deviceTime": f"{now}T00:00:0{i}Z",
            }
        )
    for i in range(3):
        positions.append(
            {
                "latitude": 0.002,
                "longitude": 0.002,
                "deviceTime": f"{now}T00:00:1{i}Z",
            }
        )
    # Rendre la détection plus permissive
    old_params = zone._analysis_params
    zone._analysis_params = lambda: (25, 0, 0.02)
    try:
        zones, noise = zone.cluster_positions(positions)
    finally:
        zone._analysis_params = old_params
    assert zones
    assert not noise
    assert all("geometry" in z and "dates" in z for z in zones)


# ---------- aggregate_overlapping_zones ----------

def test_aggregate_overlapping_zones():
    poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly2 = Polygon([(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)])
    daily = [
        {"geometry": poly1, "dates": ["2023-01-01"]},
        {"geometry": poly2, "dates": ["2023-01-02"]},
    ]
    agg = zone.aggregate_overlapping_zones(daily)
    assert any(len(z["dates"]) == 2 for z in agg)


# ---------- _build_map / generate_map_html / generate_map ----------

def test_build_map_and_generate(tmp_path):
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    zones = [{"geometry": poly, "dates": ["2023-01-01"]}]
    fmap = zone._build_map(zones)
    assert fmap is not None
    html = zone.generate_map_html(zones)
    assert "folium-map" in html
    assert "2023-01-01" in html

    out = tmp_path / "map.html"
    zone.generate_map(zones, output=str(out))
    assert out.read_text().strip().startswith("<div")


def test_generate_map_with_raw_points():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    zones = [{"geometry": poly, "dates": ["2023-01-01"]}]
    pts = [Point(0.1, 0.2), Point(0.3, 0.4)]
    html = zone.generate_map_html(zones, raw_points=pts)
    assert "circleMarker" in html


def test_geojson_features_contain_dates():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    zones = [{"geometry": poly, "dates": ["2023-01-01"]}]
    fmap = zone._build_map(zones)
    geo_layers = [
        c
        for c in fmap._children.values()
        if isinstance(c, zone.folium.GeoJson)
    ]
    assert geo_layers
    feature = geo_layers[0].data["features"][0]
    assert feature["properties"]["dates"] == ["2023-01-01"]


# ---------- Helpers for DB ----------

def setup_db():
    from flask import Flask
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    zone.db.init_app(app)
    with app.app_context():
        zone.db.create_all()
        yield app


# ---------- process_equipment ----------

def test_process_equipment(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            positions = [
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:00:00Z",
                },
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:01:00Z",
                },
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:02:00Z",
                },
            ]

            monkeypatch.setattr(
                zone,
                "fetch_positions",
                lambda *a, **k: positions,
            )
            monkeypatch.setattr(
                zone,
                "cluster_positions",
                lambda pos: (
                    [
                        {
                            "geometry": Polygon(
                                [(0, 0), (1, 0), (1, 1), (0, 1)]
                            ),
                            "dates": ["2023-01-01"],
                        }
                    ],
                    {},
                ),
            )
            monkeypatch.setattr(
                zone,
                "aggregate_overlapping_zones",
                lambda z: z,
            )

            zone.process_equipment(eq)

            dz = zone.DailyZone.query.filter_by(equipment_id=eq.id).first()
            assert dz is not None
            assert dz.surface_ha > 0
            assert eq.total_hectares == dz.surface_ha
            assert eq.last_position == datetime(2023, 1, 1, 0, 2, 0)


def test_process_equipment_creates_tracks(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            positions = [
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:00:00Z",
                },
                {
                    "latitude": 0.001,
                    "longitude": 0.001,
                    "deviceTime": "2023-01-01T00:10:00Z",
                },
            ]

            monkeypatch.setattr(
                zone,
                "fetch_positions",
                lambda *a, **k: positions,
            )

            from datetime import datetime as dt

            def fake_cluster(pos):
                return [], {
                    "2023-01-01": [
                        (0, 0, dt(2023, 1, 1, 0, 0, 0)),
                        (0.001, 0.001, dt(2023, 1, 1, 0, 10, 0)),
                    ]
                }

            monkeypatch.setattr(zone, "cluster_positions", fake_cluster)
            monkeypatch.setattr(
                zone, "aggregate_overlapping_zones", lambda z: z
            )

            zone.process_equipment(eq)

            assert zone.Track.query.count() == 1
            track = zone.Track.query.first()
            assert track and track.line_wkt.startswith("LINESTRING")
            positions_db = zone.Position.query.filter_by(
                equipment_id=eq.id
            ).all()
            assert all(p.track_id == track.id for p in positions_db)


def test_process_equipment_updates_last_position_from_history(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            from datetime import datetime as dt

            ts = dt(2023, 1, 1, 0, 0, 0)
            zone.db.session.add(
                zone.Position(
                    equipment_id=eq.id,
                    latitude=0,
                    longitude=0,
                    timestamp=ts,
                )
            )
            zone.db.session.commit()

            monkeypatch.setattr(zone, "fetch_positions", lambda *a, **k: [])
            monkeypatch.setattr(
                zone, "cluster_positions", lambda pos: ([], {})
            )
            monkeypatch.setattr(
                zone, "aggregate_overlapping_zones", lambda z: z
            )

            zone.process_equipment(eq)

            assert eq.last_position == ts


def test_track_includes_zone_endpoints(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            positions = [
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:00:00Z",
                },
                {
                    "latitude": 0.0005,
                    "longitude": 0.0005,
                    "deviceTime": "2023-01-01T00:05:00Z",
                },
                {
                    "latitude": 0.001,
                    "longitude": 0.001,
                    "deviceTime": "2023-01-01T00:10:00Z",
                },
            ]

            monkeypatch.setattr(
                zone,
                "fetch_positions",
                lambda *a, **k: positions,
            )

            from datetime import datetime as dt

            def fake_cluster(pos):
                return [], {
                    "2023-01-01": [
                        (0.0005, 0.0005, dt(2023, 1, 1, 0, 5)),
                    ]
                }

            monkeypatch.setattr(zone, "cluster_positions", fake_cluster)
            monkeypatch.setattr(
                zone, "aggregate_overlapping_zones", lambda z: z
            )

            zone.process_equipment(eq)

            track = zone.Track.query.first()
            from shapely import wkt

            coords = list(wkt.loads(track.line_wkt).coords)
            assert coords[0] == (0.0, 0.0)
            assert coords[1] == (0.0005, 0.0005)
            assert coords[-1] == (0.001, 0.001)


# ---------- track boundary clipping ----------

def test_track_clipped_to_zone_boundaries(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            positions = [
                {
                    "latitude": 0,
                    "longitude": 0,
                    "deviceTime": "2023-01-01T00:00:00Z",
                },
                {
                    "latitude": 0.0003,
                    "longitude": 0.0003,
                    "deviceTime": "2023-01-01T00:05:00Z",
                },
                {
                    "latitude": 0.0007,
                    "longitude": 0.0007,
                    "deviceTime": "2023-01-01T00:07:00Z",
                },
                {
                    "latitude": 0.001,
                    "longitude": 0.001,
                    "deviceTime": "2023-01-01T00:10:00Z",
                },
            ]

            monkeypatch.setattr(
                zone, "fetch_positions", lambda *a, **k: positions
            )

            from datetime import datetime as dt

            zone1 = Point(0, 0).buffer(0.0002)
            zone2 = Point(0.001, 0.001).buffer(0.0002)

            def fake_cluster(pos):
                return (
                    [
                        {"geometry": zone1, "dates": ["2023-01-01"]},
                        {"geometry": zone2, "dates": ["2023-01-01"]},
                    ],
                    {
                        "2023-01-01": [
                            (0.0003, 0.0003, dt(2023, 1, 1, 0, 5)),
                            (0.0007, 0.0007, dt(2023, 1, 1, 0, 7)),
                        ]
                    },
                )

            monkeypatch.setattr(zone, "cluster_positions", fake_cluster)
            monkeypatch.setattr(
                zone, "aggregate_overlapping_zones", lambda z: z
            )

            zone.process_equipment(eq)

            track = zone.Track.query.first()
            from shapely import wkt

            coords = list(wkt.loads(track.line_wkt).coords)
            assert len(coords) == 4

            start_expected = LineString([
                (0, 0),
                (0.0003, 0.0003),
            ]).intersection(zone1.exterior)
            end_expected = LineString([
                (0.001, 0.001),
                (0.0007, 0.0007),
            ]).intersection(zone2.exterior)

            assert coords[0][0] == pytest.approx(start_expected.x)
            assert coords[0][1] == pytest.approx(start_expected.y)
            assert coords[-1][0] == pytest.approx(end_expected.x)
            assert coords[-1][1] == pytest.approx(end_expected.y)


# ---------- recalculate_hectares_from_positions ----------

def test_recalculate_hectares_from_positions(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            zone.db.session.add(
                zone.Position(
                    equipment_id=eq.id,
                    latitude=0,
                    longitude=0,
                    timestamp=datetime(2023, 1, 1),
                )
            )
            zone.db.session.add(
                zone.Position(
                    equipment_id=eq.id,
                    latitude=0,
                    longitude=0,
                    timestamp=datetime(2023, 1, 1, 0, 1),
                )
            )
            zone.db.session.add(
                zone.Position(
                    equipment_id=eq.id,
                    latitude=0,
                    longitude=0,
                    timestamp=datetime(2023, 1, 1, 0, 2),
                )
            )
            zone.db.session.commit()

            monkeypatch.setattr(
                zone,
                "cluster_positions",
                lambda pos: (
                    [
                        {
                            "geometry": Polygon(
                                [(0, 0), (1, 0), (1, 1), (0, 1)]
                            ),
                            "dates": ["2023-01-01"],
                        }
                    ],
                    {},
                ),
            )
            monkeypatch.setattr(
                zone,
                "aggregate_overlapping_zones",
                lambda z: z,
            )

            total = zone.recalculate_hectares_from_positions(eq.id)
            assert total > 0
            assert eq.total_hectares == total


def test_calculate_relative_hectares():
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            poly = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
            zone.db.session.add(
                zone.DailyZone(
                    equipment_id=eq.id,
                    date=datetime(2023, 1, 1).date(),
                    surface_ha=1.0,
                    polygon_wkt=poly.wkt,
                )
            )
            zone.db.session.add(
                zone.DailyZone(
                    equipment_id=eq.id,
                    date=datetime(2023, 1, 2).date(),
                    surface_ha=1.0,
                    polygon_wkt=poly.wkt,
                )
            )
            zone.db.session.commit()

            total = zone.calculate_relative_hectares(eq.id)
            assert abs(total - 1.0) < 1e-6


# ---------- analyse_quotidienne & analyser_equipement ----------

def test_analyse_quotidienne(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq1 = zone.Equipment(id_traccar=1, name="a")
            eq2 = zone.Equipment(id_traccar=2, name="b")
            zone.db.session.add_all([eq1, eq2])
            zone.db.session.commit()

            called = []

            def fake_process(e):
                called.append(e.id_traccar)
            monkeypatch.setattr(zone, "process_equipment", fake_process)

            zone.analyse_quotidienne()
            assert set(called) == {1, 2}


def test_analyser_equipement(monkeypatch):
    called = {}

    def fake_process(eq, since=None):
        called["id"] = eq.id_traccar
        called["since"] = since
    monkeypatch.setattr(zone, "process_equipment", fake_process)
    eq = types.SimpleNamespace(id_traccar=5)
    zone.analyser_equipement(eq, start_date=42)
    assert called == {"id": 5, "since": 42}


def test_distance_between_zones_calculation(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            monkeypatch.setattr(
                zone,
                "fetch_positions",
                lambda *a, **k: [],
            )

            def fake_cluster(pos):
                return (
                    [
                        {
                            "geometry": Polygon(
                                [
                                    (0, 0),
                                    (100, 0),
                                    (100, 100),
                                    (0, 100),
                                ]
                            ),
                            "dates": ["2023-01-01"],
                        },
                        {
                            "geometry": Polygon(
                                [
                                    (1000, 0),
                                    (1100, 0),
                                    (1100, 100),
                                    (1000, 100),
                                ]
                            ),
                            "dates": ["2023-01-02"],
                        },
                    ],
                    {},
                )

            monkeypatch.setattr(
                zone,
                "cluster_positions",
                fake_cluster,
            )
            monkeypatch.setattr(
                zone,
                "aggregate_overlapping_zones",
                lambda z: z,
            )

            zone.process_equipment(eq)

            assert eq.distance_between_zones > 0


def test_zones_split_by_pass_count(monkeypatch):
    for app in setup_db():
        with app.app_context():
            eq = zone.Equipment(id_traccar=1, name="eq1")
            zone.db.session.add(eq)
            zone.db.session.commit()

            poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            poly2 = Polygon([(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)])

            monkeypatch.setattr(zone, "fetch_positions", lambda *a, **k: [])
            monkeypatch.setattr(
                zone,
                "cluster_positions",
                lambda pos: (
                    [
                        {"geometry": poly1, "dates": ["2023-01-01"]},
                        {"geometry": poly2, "dates": ["2023-01-01"]},
                    ],
                    {},
                ),
            )

            zone.process_equipment(eq, since=datetime(2023, 1, 1))

            zones = zone.DailyZone.query.filter_by(equipment_id=eq.id).all()
            counts = sorted(z.pass_count for z in zones)
            assert counts == [1, 1, 2]
