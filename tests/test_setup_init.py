import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("TRACCAR_AUTH_TOKEN", "dummy")
os.environ.setdefault("TRACCAR_BASE_URL", "http://example.com")

from app import create_app  # noqa: E402
from models import db, User  # noqa: E402


def test_setup_without_db():
    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with app.app_context():
        db.drop_all()
    client = app.test_client()
    resp = client.get("/setup")
    assert resp.status_code == 200
    with app.app_context():
        # Query succeeds because tables were created automatically
        assert User.query.count() == 0


def test_schema_upgrade_adds_pass_count(tmp_path):
    """Old databases are upgraded with the pass_count column."""
    db_file = tmp_path / "old.db"

    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE equipment (\n"
                "id INTEGER PRIMARY KEY,\n"
                "id_traccar INTEGER NOT NULL,\n"
                "name VARCHAR NOT NULL,\n"
                "token_api VARCHAR,\n"
                "last_position DATETIME,\n"
                "total_hectares FLOAT,\n"
                "distance_between_zones FLOAT\n"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE daily_zone (\n"
                "id INTEGER PRIMARY KEY,\n"
                "equipment_id INTEGER NOT NULL,\n"
                "date DATE,\n"
                "surface_ha FLOAT,\n"
                "polygon_wkt TEXT,\n"
                "FOREIGN KEY(equipment_id) REFERENCES equipment(id)\n"
                ")"
            )
        )

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    client = app.test_client()
    client.get("/setup")

    from sqlalchemy import inspect

    with app.app_context():
        insp = inspect(db.engine)
        cols = [c["name"] for c in insp.get_columns("daily_zone")]
    assert "pass_count" in cols


def test_schema_upgrade_adds_tracks(tmp_path):
    """Old databases are upgraded with track table and link."""
    db_file = tmp_path / "old2.db"
    from sqlalchemy import create_engine, text, inspect

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE equipment (\n"
                "id INTEGER PRIMARY KEY,\n"
                "id_traccar INTEGER NOT NULL,\n"
                "name VARCHAR NOT NULL\n"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE position (\n"
                "id INTEGER PRIMARY KEY,\n"
                "equipment_id INTEGER NOT NULL,\n"
                "latitude FLOAT,\n"
                "longitude FLOAT,\n"
                "timestamp DATETIME,\n"
                "FOREIGN KEY(equipment_id) REFERENCES equipment(id)\n"
                ")"
            )
        )

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    client = app.test_client()
    client.get("/setup")

    with app.app_context():
        insp = inspect(db.engine)
        assert "track" in insp.get_table_names()
        cols = [c["name"] for c in insp.get_columns("position")]
    assert "track_id" in cols


def test_schema_upgrade_adds_marker_icon(tmp_path):
    """Old databases are upgraded with marker_icon column."""
    db_file = tmp_path / "old_icon.db"
    from sqlalchemy import create_engine, text, inspect

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE equipment (\n"
                "id INTEGER PRIMARY KEY,\n"
                "id_traccar INTEGER NOT NULL,\n"
                "name VARCHAR NOT NULL,\n"
                "token_api VARCHAR,\n"
                "last_position DATETIME,\n"
                "total_hectares FLOAT,\n"
                "distance_between_zones FLOAT\n"
                ")"
            )
        )

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    client = app.test_client()
    client.get("/setup")

    with app.app_context():
        insp = inspect(db.engine)
        cols = [c["name"] for c in insp.get_columns("equipment")]
    assert "marker_icon" in cols


def test_setup_redirects_when_admin_exists():
    app = create_app(start_scheduler=False, run_initial_analysis=False)
    client = app.test_client()
    with app.app_context():
        admin = User(username="admin", is_admin=True)
        admin.set_password("pw")
        db.session.add(admin)
        db.session.commit()
    resp = client.get("/setup")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_initial_analysis_upgrades_before_processing(tmp_path, monkeypatch):
    """initial_analysis should run after upgrade_db."""
    inst = tmp_path / "inst"
    inst.mkdir()
    db_file = inst / "trackteur.db"

    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE equipment (\n"
                "id INTEGER PRIMARY KEY,\n"
                "id_traccar INTEGER NOT NULL,\n"
                "name VARCHAR NOT NULL,\n"
                "token_api VARCHAR,\n"
                "last_position DATETIME,\n"
                "total_hectares FLOAT,\n"
                "distance_between_zones FLOAT\n"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE daily_zone (\n"
                "id INTEGER PRIMARY KEY,\n"
                "equipment_id INTEGER NOT NULL,\n"
                "date DATE,\n"
                "surface_ha FLOAT,\n"
                "polygon_wkt TEXT,\n"
                "FOREIGN KEY(equipment_id) REFERENCES equipment(id)\n"
                ")"
            )
        )

    import importlib
    from flask import Flask as RealFlask
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "Flask",
        lambda name: RealFlask(name, instance_path=str(inst)),
    )
    monkeypatch.setattr(
        app_module.zone,
        "process_equipment",
        lambda *a, **k: None,
    )

    app = importlib.reload(app_module).create_app(start_scheduler=False)

    with app.app_context():
        from sqlalchemy import inspect

        insp = inspect(db.engine)
        cols = [c["name"] for c in insp.get_columns("daily_zone")]
    assert "pass_count" in cols
