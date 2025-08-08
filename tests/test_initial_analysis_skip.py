import importlib


def test_initial_analysis_skips_when_zones_exist(tmp_path, monkeypatch):
    """On restart, initial analysis should skip if data exists."""
    # Prepare an instance folder with a pre-populated DB
    inst = tmp_path / "inst"
    inst.mkdir()
    db_file = inst / "trackteur.db"

    from sqlalchemy import create_engine, text
    from datetime import date

    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        # Minimal schema to satisfy queries
        conn.execute(
            text(
                "CREATE TABLE equipment (\n"
                "id INTEGER PRIMARY KEY,\n"
                "id_traccar INTEGER NOT NULL,\n"
                "name VARCHAR NOT NULL,\n"
                "token_api VARCHAR\n"
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
                "pass_count INTEGER DEFAULT 1,\n"
                "FOREIGN KEY(equipment_id) REFERENCES equipment(id)\n"
                ")"
            )
        )
        # Insert equipment and one zone for current year
        conn.execute(
            text(
                "INSERT INTO equipment (id, id_traccar, name) VALUES"
                " (1, 101, 'E1')"
            )
        )
        today = date.today().isoformat()
        conn.execute(
            text(
                "INSERT INTO daily_zone (equipment_id, date, surface_ha,"
                " polygon_wkt, pass_count)\n"
                "VALUES (1, :d, 1.23, 'POLYGON((0 0,1 0,1 1,0 1,0 0))', 1)"
            ),
            {"d": today},
        )

    # Import app with instance_path redirected to our tmp instance
    import app as app_module
    from flask import Flask as RealFlask

    monkeypatch.setattr(
        app_module,
        "Flask",
        lambda name: RealFlask(name, instance_path=str(inst)),
    )

    # If initial analysis tried to run, this would be called; fail fast
    called = {"count": 0}

    def _nope(*a, **k):
        called["count"] += 1
        raise AssertionError("process_equipment should not be called")

    monkeypatch.setattr(app_module.zone, "process_equipment", _nope)

    # Reload so our monkeypatch of Flask applies before create_app code runs
    importlib.reload(app_module).create_app(
        start_scheduler=False, run_initial_analysis=False
    )

    # App created successfully and no processing attempted
    assert called["count"] == 0
