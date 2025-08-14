from datetime import datetime
import json
import re

import zone
from models import db, Equipment, Position, Track, DailyZone
from tests.utils import login


def get_js_array(html: str, var_name: str):
    match = re.search(rf"const {var_name} = (\[.*?\]);", html)
    assert match, f"{var_name} not found"
    return json.loads(match.group(1))


def test_initial_bounds_fallback_to_last_position(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        eq.id_traccar = 0
        eq.osmand_id = "osm-1"
        DailyZone.query.delete()
        Track.query.delete()
        Position.query.delete()
        db.session.commit()
        ts = datetime(2024, 1, 1, 12, 0, 0)
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=45.0,
                longitude=3.0,
                timestamp=ts,
            )
        )
        eq.last_position = ts
        db.session.commit()
        zone._AGG_CACHE.clear()
        eq_id = eq.id

    resp = client.get(f"/equipment/{eq_id}")
    html = resp.data.decode()
    bounds = get_js_array(html, "initialBounds")
    assert bounds[0] < 3.0 < bounds[2]
    assert bounds[1] < 45.0 < bounds[3]
    assert "Aucune donnée disponible" not in html
