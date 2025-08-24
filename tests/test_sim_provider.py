import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import requests  # type: ignore[import-untyped]  # noqa: E402
from datetime import datetime  # noqa: E402
from models import db, Provider, SimCard, Equipment  # noqa: E402
from tests.utils import login, get_csrf  # noqa: E402


def test_sim_status_and_sms(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        prov = Provider(name="Hologram", token="t")
        db.session.add(prov)
        eq = Equipment.query.first()
        sim = SimCard(
            iccid="123",
            device_id="456",
            provider=prov,
            equipment=eq,
        )
        db.session.add(sim)
        db.session.commit()
        eqid = eq.id

    class RespGet:
        status_code = 200
        text = "{}"

        def json(self):
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            return {
                "data": {
                    "links": {"cellular": [{"last_connect_time": ts}]},
                    "lastsession": {"session_end": ts},
                }
            }

    class RespPost:
        ok = True
        status_code = 200
        text = "{}"
    monkeypatch.setattr(requests, "get", lambda *a, **k: RespGet())
    monkeypatch.setattr(requests, "post", lambda *a, **k: RespPost())
    resp = client.get("/sim/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data[0]["connected"] is True
    assert data[0]["last_session"] is not None
    token = get_csrf(client, "/")
    resp = client.post(
        f"/sim/{eqid}/request_position",
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


def test_list_provider_sims(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        prov = Provider(name="Hologram", token="t")
        db.session.add(prov)
        db.session.commit()
        pid = prov.id

    class Resp:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "name": "Dev1",
                        "links": {"cellular": [{"sim": "111"}]},
                    },
                    {
                        "id": 2,
                        "name": "Dev2",
                        "links": {"cellular": [{"sim": "222"}]},
                    },
                ],
            }
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    resp = client.get(f"/providers/{pid}/sims")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data[0]["value"] == "1:111"


def test_associate_sim_creates_record(make_app, monkeypatch):
    """Posting to /sim/associate should persist the SIM card."""
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        prov = Provider(name="Hologram", token="t")
        db.session.add(prov)
        eq = Equipment.query.first()
        db.session.commit()
        pid = prov.id
        eqid = eq.id
    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            return {
                "data": {
                    "links": {"cellular": [{"last_connect_time": ts}]},
                    "lastsession": {"session_end": ts},
                }
            }

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    token = get_csrf(client, "/")
    resp = client.post(
        "/sim/associate",
        data={
            "equipment_id": eqid,
            "provider": pid,
            "sim": "123:999",
            "csrf_token": token,
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        sim = SimCard.query.filter_by(equipment_id=eqid).first()
        assert sim is not None
        assert sim.iccid == "999"


def test_associate_sim_shows_feedback(make_app, monkeypatch):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        prov = Provider(name="Hologram", token="t")
        db.session.add(prov)
        eq = Equipment.query.first()
        db.session.commit()
        pid = prov.id
        eqid = eq.id

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            return {
                "data": {
                    "links": {"cellular": [{"last_connect_time": ts}]},
                    "lastsession": {"session_end": ts},
                }
            }

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())

    token = get_csrf(client, "/")
    resp = client.post(
        "/sim/associate",
        data={
            "equipment_id": eqid,
            "provider": pid,
            "sim": "123:999",
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Carte SIM associ\xc3\xa9e" in resp.data
    assert b"connect\xc3\xa9" in resp.data


def test_dissociate_sim_removes_record(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    with app.app_context():
        prov = Provider(name="Hologram", token="t")
        db.session.add(prov)
        eq = Equipment.query.first()
        sim = SimCard(
            iccid="123",
            device_id="456",
            provider=prov,
            equipment=eq,
        )
        db.session.add(sim)
        db.session.commit()
        eqid = eq.id
    token = get_csrf(client, "/")
    resp = client.post(
        f"/sim/{eqid}/dissociate",
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    with app.app_context():
        assert SimCard.query.filter_by(equipment_id=eqid).first() is None
