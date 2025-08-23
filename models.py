from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Config(db.Model):  # type: ignore[name-defined]
    """Paramètres de connexion Traccar et d'analyse des zones."""

    id = db.Column(db.Integer, primary_key=True)
    traccar_url = db.Column(db.String, nullable=False)
    traccar_token = db.Column(db.String, nullable=False)
    eps_meters = db.Column(db.Float, default=25.0)
    min_surface_ha = db.Column(db.Float, default=0.1)
    alpha = db.Column(db.Float, default=0.02)
    analysis_hour = db.Column(db.Integer, default=2)


class Equipment(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    id_traccar = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String, nullable=False)
    token_api = db.Column(db.String, nullable=True)
    # Optional identifier for direct OsmAnd ingestion (string, can be IMEI or custom)
    osmand_id = db.Column(db.String, unique=True, nullable=True)
    # Whether this equipment is included in zone analysis (True) or only tracked (False)
    include_in_analysis = db.Column(db.Boolean, default=True)
    marker_icon = db.Column(db.String, nullable=True, default='tractor')
    last_position = db.Column(db.DateTime)
    total_hectares = db.Column(db.Float, default=0.0)
    # Surface unique cumulée (zones dédupliquées entre jours)
    relative_hectares = db.Column(db.Float, default=0.0)
    distance_between_zones = db.Column(db.Float, default=0.0)
    battery_level = db.Column(db.Float, nullable=True)

    positions = db.relationship('Position', backref='equipment', lazy=True)
    daily_zones = db.relationship('DailyZone', backref='equipment', lazy=True)
    tracks = db.relationship('Track', backref='equipment', lazy=True)


class Provider(db.Model):  # type: ignore[name-defined]
    """Fournisseur de cartes SIM (ex: Hologram)."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False, default="hologram")
    token = db.Column(db.String, nullable=False)
    orgid = db.Column(db.String, nullable=True)

    sims = db.relationship("SimCard", backref="provider", lazy=True)


class SimCard(db.Model):  # type: ignore[name-defined]
    """Carte SIM associée à un équipement."""

    id = db.Column(db.Integer, primary_key=True)
    iccid = db.Column(db.String, unique=True, nullable=False)
    device_id = db.Column(db.String, nullable=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('provider.id'), nullable=False)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )

    equipment = db.relationship(
        'Equipment', backref=db.backref('sim_card', uselist=False)
    )


class Track(db.Model):  # type: ignore[name-defined]
    """Segment de trajet entre deux zones."""

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    line_wkt = db.Column(db.Text)
    positions = db.relationship('Position', backref='track', lazy=True)


class Position(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    track_id = db.Column(db.Integer, db.ForeignKey('track.id'), nullable=True)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    timestamp = db.Column(db.DateTime)
    # Battery level percentage at this position (0-100), if provided
    battery_level = db.Column(db.Float, nullable=True)


class DailyZone(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    date = db.Column(db.Date)
    surface_ha = db.Column(db.Float)
    polygon_wkt = db.Column(db.Text)
    pass_count = db.Column(db.Integer, default=1)
