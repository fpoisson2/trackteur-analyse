import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import requests  # type: ignore[import-untyped]  # noqa: E402
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
        sim = SimCard(iccid="123", device_id="456", provider=prov, equipment=eq)
        db.session.add(sim)
        db.session.commit()
        eqid = eq.id
    class RespGet:
        def json(self):
            return {"data": {"status": "LIVE"}}
    class RespPost:
        ok = True
    monkeypatch.setattr(requests, "get", lambda *a, **k: RespGet())
    monkeypatch.setattr(requests, "post", lambda *a, **k: RespPost())
    resp = client.get("/sim/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data[0]["connected"] is True
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
        def json(self):
            return {
                "data": [
                    {"id": 1, "name": "Dev1", "links": [{"iccid": "111"}]},
                    {"id": 2, "name": "Dev2", "links": [{"iccid": "222"}]},
                ]
            }
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    resp = client.get(f"/providers/{pid}/sims")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data[0]["value"] == "1:111"
