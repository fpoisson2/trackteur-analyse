import zone
from models import db, Equipment
from tests.utils import login, get_csrf


def test_toggle_analysis(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    monkeypatch.setattr(zone, "fetch_devices", lambda: [])

    with app.app_context():
        eq = Equipment.query.first()
        eq_id = eq.id
        form_id = f"t{eq.id_traccar}"
        assert eq.include_in_analysis is True

    token = get_csrf(client, "/admin/equipment")
    client.post(
        "/admin/equipment",
        data={
            f"type_{form_id}": "tractor",
            f"include_{form_id}": "0",
            f"follow_{form_id}": "1",
            "csrf_token": token,
        },
    )

    with app.app_context():
        assert db.session.get(Equipment, eq_id).include_in_analysis is False

    token = get_csrf(client, "/admin/equipment")
    client.post(
        "/admin/equipment",
        data={
            f"include_{form_id}": "1",
            f"type_{form_id}": "car",
            f"follow_{form_id}": "1",
            "csrf_token": token,
        },
    )

    with app.app_context():
        eq = db.session.get(Equipment, eq_id)
        assert eq.include_in_analysis is True
        assert eq.marker_icon == "car"
