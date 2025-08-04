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
    """Paramètres de connexion Traccar."""

    id = db.Column(db.Integer, primary_key=True)
    traccar_url = db.Column(db.String, nullable=False)
    traccar_token = db.Column(db.String, nullable=False)


class Equipment(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    id_traccar = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String, nullable=False)
    token_api = db.Column(db.String, nullable=True)
    last_position = db.Column(db.DateTime)
    total_hectares = db.Column(db.Float, default=0.0)
    distance_between_zones = db.Column(db.Float, default=0.0)

    positions = db.relationship('Position', backref='equipment', lazy=True)
    daily_zones = db.relationship('DailyZone', backref='equipment', lazy=True)
    traces = db.relationship('Trace', backref='equipment', lazy=True)


class Position(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    timestamp = db.Column(db.DateTime)


class DailyZone(db.Model):  # type: ignore[name-defined]
    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    date = db.Column(db.Date)
    surface_ha = db.Column(db.Float)
    polygon_wkt = db.Column(db.Text)
    pass_count = db.Column(db.Integer, default=1)


class Trace(db.Model):  # type: ignore[name-defined]
    """Représente un tracé construit à partir de points GPS."""

    id = db.Column(db.Integer, primary_key=True)
    equipment_id = db.Column(
        db.Integer, db.ForeignKey('equipment.id'), nullable=False
    )
    date = db.Column(db.Date)
    line_wkt = db.Column(db.Text, nullable=False)
