import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import requests  # type: ignore[import-untyped]  # noqa: E402
from models import db, Provider, SimCard, Equipment  # noqa: E402
from tests.utils import login  # noqa: E402


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
    resp = client.get(f"/sim/{eqid}/request_position")
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
