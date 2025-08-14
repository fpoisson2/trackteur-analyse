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
        assert eq.include_in_analysis is True

    token = get_csrf(client, "/admin")
    client.post(
        "/admin",
        data={
            f"icon_{eq_id}": "",
            "csrf_token": token,
        },
    )

    with app.app_context():
        assert db.session.get(Equipment, eq_id).include_in_analysis is False

    token = get_csrf(client, "/admin")
    client.post(
        "/admin",
        data={
            f"include_{eq_id}": "1",
            f"icon_{eq_id}": "car",
            "csrf_token": token,
        },
    )

    with app.app_context():
        eq = db.session.get(Equipment, eq_id)
        assert eq.include_in_analysis is True
        assert eq.marker_icon == "car"
