import os
import sys
from sqlalchemy import inspect

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import create_app  # noqa: E402
from models import db  # noqa: E402


def test_upgrade_adds_orgid(tmp_path):
    db_path = tmp_path / "legacy.db"
    # Create legacy table without orgid using sqlite3 before SQLAlchemy connects
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE provider ("
        "id INTEGER PRIMARY KEY,"
        "name VARCHAR,"
        "type VARCHAR NOT NULL,"
        "token VARCHAR NOT NULL)"
    )
    conn.commit()
    conn.close()

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    with app.test_client() as client:
        client.get("/setup")
    with app.app_context():
        inspector = inspect(db.engine)
    cols = [c["name"] for c in inspector.get_columns("provider")]
    assert "orgid" in cols


def test_upgrade_adds_sim_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE provider (id INTEGER PRIMARY KEY, name VARCHAR, type VARCHAR NOT NULL, token VARCHAR NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE equipment (id INTEGER PRIMARY KEY, id_traccar INTEGER, name VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE sim_card (id INTEGER PRIMARY KEY, iccid VARCHAR UNIQUE NOT NULL, device_id VARCHAR, provider_id INTEGER NOT NULL, equipment_id INTEGER NOT NULL)"
    )
    conn.commit()
    conn.close()

    app = create_app(start_scheduler=False, run_initial_analysis=False)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    with app.test_client() as client:
        client.get("/setup")
    with app.app_context():
        inspector = inspect(db.engine)
        cols = [c["name"] for c in inspector.get_columns("sim_card")]
        assert "connected" in cols
        assert "last_session" in cols
        assert "status_checked" in cols
