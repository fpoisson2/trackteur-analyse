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
    os.environ["SKIP_INITIAL_ANALYSIS"] = "1"
    app = create_app()
    os.environ.pop("SKIP_INITIAL_ANALYSIS", None)
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

    os.environ["SKIP_INITIAL_ANALYSIS"] = "1"
    app = create_app()
    os.environ.pop("SKIP_INITIAL_ANALYSIS", None)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_file}"
    client = app.test_client()
    client.get("/setup")

    from sqlalchemy import inspect

    with app.app_context():
        insp = inspect(db.engine)
        cols = [c["name"] for c in insp.get_columns("daily_zone")]
    assert "pass_count" in cols
