from models import db, Equipment
from tests.utils import login, get_csrf


def test_toggle_analysis(make_app):
    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment.query.first()
        eq_id = eq.id
        assert eq.include_in_analysis is True

    token = get_csrf(client, "/admin")
    # Disable inclusion by not sending the checkbox value
    client.post(f"/admin/toggle_analysis/{eq_id}", data={"csrf_token": token})

    with app.app_context():
        assert db.session.get(Equipment, eq_id).include_in_analysis is False

    token = get_csrf(client, "/admin")
    # Enable inclusion by sending checkbox value
    client.post(
        f"/admin/toggle_analysis/{eq_id}",
        data={"csrf_token": token, "include": "1"},
    )

    with app.app_context():
        assert db.session.get(Equipment, eq_id).include_in_analysis is True
