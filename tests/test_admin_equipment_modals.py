"""Tests for the admin equipment page modals."""

import pytest

from models import Equipment, db
from tests.utils import login


@pytest.mark.usefixtures("base_make_app")
def test_admin_equipment_add_modal_and_info(make_app):
    """Ensure add and info modals are present on the admin equipment page."""

    app = make_app()
    client = app.test_client()
    login(client)

    with app.app_context():
        eq = Equipment(name="OsmDev", osmand_id="osm-1", id_traccar=0)
        db.session.add(eq)
        db.session.commit()
        eq_id = eq.id

    resp = client.get("/admin/equipment")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-bs-target="#add-osmand-modal"' in html
    assert 'id="add-osmand-modal"' in html
    assert f'data-bs-target="#osmand-info-{eq_id}"' in html
    assert f'id="osmand-info-{eq_id}"' in html
