from datetime import datetime

import pytest

from models import db, Equipment, Position
from tests.utils import login


@pytest.mark.usefixtures("base_make_app")
def test_equipment_status_updates(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        ts1 = datetime(2023, 1, 1, 10, 0, 0)
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=0.0,
                longitude=0.0,
                timestamp=ts1,
            )
        )
        eq.last_position = ts1
        db.session.commit()

    r1 = client.get("/equipment_status")
    assert r1.status_code == 200
    data1 = r1.get_json()
    assert data1[0]["last_seen"].startswith("2023-01-01 10:00:00")

    with app.app_context():
        eq = Equipment.query.first()
        ts2 = datetime(2023, 1, 1, 11, 0, 0)
        db.session.add(
            Position(
                equipment_id=eq.id,
                latitude=1.0,
                longitude=1.0,
                timestamp=ts2,
            )
        )
        eq.last_position = ts2
        db.session.commit()

    r2 = client.get("/equipment_status")
    assert r2.status_code == 200
    data2 = r2.get_json()
    assert data2[0]["last_seen"].startswith("2023-01-01 11:00:00")
