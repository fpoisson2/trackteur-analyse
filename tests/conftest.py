import os
import warnings

# Silence joblib serial-mode warning emitted in this environment as early as possible
warnings.filterwarnings(
    "ignore",
    message=r".*joblib will operate in serial mode.*",
    category=UserWarning,
)

# Silence SQLAlchemy LegacyAPIWarning about Query.get
from sqlalchemy.exc import LegacyAPIWarning  # type: ignore
warnings.filterwarnings(
    "ignore",
    message=r".*Query.get\(\) method is considered legacy.*",
    category=LegacyAPIWarning,
)
import pytest
from app import create_app
from models import db, User, Config, Equipment


@pytest.fixture
def make_app():
    original = os.environ.get("SKIP_INITIAL_ANALYSIS")
    os.environ["SKIP_INITIAL_ANALYSIS"] = "1"

    def _make_app():
        app = create_app(start_scheduler=False, run_initial_analysis=False)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        with app.app_context():
            db.drop_all()
            db.create_all()
            admin = User(username="admin", is_admin=True)
            admin.set_password("pass")
            db.session.add(admin)
            db.session.add(
                Config(traccar_url="http://example.com", traccar_token="dummy")
            )
            db.session.add(Equipment(id_traccar=1, name="eq"))
            db.session.commit()
        return app

    try:
        yield _make_app
    finally:
        if original is None:
            os.environ.pop("SKIP_INITIAL_ANALYSIS", None)
        else:
            os.environ["SKIP_INITIAL_ANALYSIS"] = original




@pytest.fixture
def base_make_app(make_app):
    return make_app
