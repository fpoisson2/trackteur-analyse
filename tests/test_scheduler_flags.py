import os
import sys
import threading

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import app  # noqa: E402


def test_no_scheduler_or_initial_analysis(monkeypatch):
    before = {t.name for t in threading.enumerate() if "APScheduler" in t.name}

    start_called = {"called": False}

    def fake_start(self):
        start_called["called"] = True

    monkeypatch.setattr(app.BackgroundScheduler, "start", fake_start)

    process_called = {"count": 0}

    def fake_process(*a, **k):
        process_called["count"] += 1

    monkeypatch.setattr(app.zone, "process_equipment", fake_process)

    app.create_app(start_scheduler=False, run_initial_analysis=False)

    after = {t.name for t in threading.enumerate() if "APScheduler" in t.name}

    assert start_called["called"] is False
    assert process_called["count"] == 0
    assert after == before
