import pytest


def make_app():
    # Local import to avoid side effects at collection
    from app import create_app
    app = create_app(start_scheduler=False, run_initial_analysis=False)
    return app


@pytest.fixture()
def app_ctx(tmp_path):
    app = make_app()
    # Ensure instance path exists and points to a temp dir DB
    app.instance_path = str(tmp_path)
    with app.app_context():
        from models import db, User
        db.create_all()
        # Create an admin user so /casic_ephemeris passes ensure_setup + login_required
        u = User(username="admin", is_admin=True)
        u.set_password("secret")
        db.session.add(u)
        db.session.commit()
        yield app


def test_casic_ephemeris_returns_bin(app_ctx, monkeypatch):
    import casic as cas

    fake = b"\xBA\xCE\x00\x00\x08\x07"  # minimal CASIC-like header
    monkeypatch.setattr(cas, "build_casic_bin_latest", lambda *a, **k: fake)

    client = app_ctx.test_client()
    # Bypass login by setting Flask-Login session keys directly
    with client.session_transaction() as sess:
        from models import User
        from models import db
        u = User.query.first()
        assert u is not None
        sess["_user_id"] = str(u.id)
        sess["_fresh"] = True

    resp = client.get("/casic_ephemeris?year=2024&doy=123")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/octet-stream")
    assert resp.headers.get("Content-Disposition", "").endswith(".bin")
    assert resp.data == fake

