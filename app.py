import os
import logging
import time
import threading
import json
import gzip

import requests  # type: ignore[import-untyped]
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    current_app,
    flash,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from apscheduler.schedulers.background import BackgroundScheduler

from models import (
    db,
    User,
    Equipment,
    Position,
    Config,
    Track,
    DailyZone,
    Provider,
    SimCard,
)
from forms import (
    LoginForm,
    AdminConfigForm,
    AddUserForm,
    ResetPasswordForm,
    DeleteUserForm,
    ProviderForm,
    SimAssociationForm,
)
import zone

from datetime import datetime, date, timedelta, timezone
from typing import Iterable, Any, Optional
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest

reanalysis_progress = {
    "running": False,
    "current": 0,
    "total": 0,
    "equipment": "",
}


def create_app(
    start_scheduler: bool = True, run_initial_analysis: bool = True
):
    app = Flask(__name__)
    csrf = CSRFProtect()
    csrf.init_app(app)
    reanalysis_progress.update(
        {"running": False, "current": 0, "total": 0, "equipment": ""}
    )
    # Configure logging
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=log_level,
            format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
        )
    else:
        logging.getLogger().setLevel(log_level)
    app.logger.setLevel(log_level)
    app.config['SECRET_KEY'] = os.environ.get(
        'FLASK_SECRET_KEY', os.urandom(24)
    )
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        'sqlite:///' + os.path.join(app.instance_path, 'trackteur.db')
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Secure cookies (configurable via env)
    # Activer en prod via SECURE_COOKIES=1
    secure_cookies = os.environ.get('SECURE_COOKIES') == '1'
    app.config['SESSION_COOKIE_SECURE'] = secure_cookies
    app.config['REMEMBER_COOKIE_SECURE'] = secure_cookies
    app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
    app.config['REMEMBER_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
    if secure_cookies:
        app.config['PREFERRED_URL_SCHEME'] = 'https'
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'
    scheduler = BackgroundScheduler()

    # --- Simple login rate limiting (in-memory) ---
    try:
        max_attempts = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "10"))
    except Exception:
        max_attempts = 10
    try:
        window_seconds = int(os.environ.get("LOGIN_WINDOW_SECONDS", "900"))
    except Exception:
        window_seconds = 900
    _login_attempts: dict[str, list[float]] = {}
    _login_lock = threading.Lock()

    def _client_ip() -> str:
        xf = request.headers.get("X-Forwarded-For", "")
        if xf:
            return xf.split(",")[0].strip()
        return request.remote_addr or "unknown"

    def _too_many_attempts(keys: list[str]) -> bool:
        now = time.time()
        with _login_lock:
            # Clean window and count combined attempts across keys
            total = 0
            for k in keys:
                arr = [t for t in _login_attempts.get(k, []) if now - t < window_seconds]
                _login_attempts[k] = arr
                total += len(arr)
            if total >= max_attempts:
                return True
            # Record one attempt on primary key (first)
            primary = keys[0]
            _login_attempts.setdefault(primary, []).append(now)
            return False

    def _reset_attempts(keys: list[str]) -> None:
        with _login_lock:
            for k in keys:
                _login_attempts.pop(k, None)

    def upgrade_db() -> None:
        """Ensure the database schema includes recent columns."""
        from sqlalchemy import inspect, text

        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if "config" in tables:
            config_cols = {c["name"] for c in inspector.get_columns("config")}
            with db.engine.begin() as conn:
                if "eps_meters" not in config_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE config ADD COLUMN eps_meters "
                            "FLOAT DEFAULT 25.0"
                        )
                    )
                if "min_surface_ha" not in config_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE config ADD COLUMN min_surface_ha "
                            "FLOAT DEFAULT 0.1"
                        )
                    )
                if "alpha" not in config_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE config ADD COLUMN alpha "
                            "FLOAT DEFAULT 0.02"
                        )
                    )
                if "analysis_hour" not in config_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE config ADD COLUMN analysis_hour "
                            "INTEGER DEFAULT 2"
                        )
                    )
        if "daily_zone" in tables:
            daily_cols = [
                c["name"] for c in inspector.get_columns("daily_zone")
            ]
            if "pass_count" not in daily_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE daily_zone ADD COLUMN pass_count "
                            "INTEGER DEFAULT 1"
                        )
                    )
        if "track" not in tables:
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE track ("
                        "id INTEGER PRIMARY KEY,"
                        "equipment_id INTEGER NOT NULL,"
                        "start_time DATETIME,"
                        "end_time DATETIME,"
                        "line_wkt TEXT,"
                        "FOREIGN KEY(equipment_id) REFERENCES equipment(id)"
                        ")"
                    )
                )
        if "equipment" in tables:
            equip_cols = [
                c["name"] for c in inspector.get_columns("equipment")
            ]
            if "relative_hectares" not in equip_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE equipment ADD COLUMN "
                            "relative_hectares FLOAT DEFAULT 0.0"
                        )
                    )
            if "osmand_id" not in equip_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE equipment ADD COLUMN osmand_id "
                            "VARCHAR UNIQUE"
                        )
                    )
            if "include_in_analysis" not in equip_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE equipment ADD COLUMN include_in_analysis "
                            "BOOLEAN DEFAULT 1"
                        )
                    )
            if "marker_icon" not in equip_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE equipment ADD COLUMN marker_icon "
                            "VARCHAR DEFAULT 'tractor'"
                        )
                    )
            if "battery_level" not in equip_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE equipment ADD COLUMN battery_level FLOAT"
                        )
                    )
        if "position" in tables:
            pos_cols = [c["name"] for c in inspector.get_columns("position")]
            if "track_id" not in pos_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE position ADD COLUMN track_id "
                            "INTEGER REFERENCES track(id)"
                        )
                    )
            if "battery_level" not in pos_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE position ADD COLUMN battery_level FLOAT"
                        )
                    )
        if "provider" not in tables:
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE provider ("
                        "id INTEGER PRIMARY KEY,"
                        "name VARCHAR NOT NULL,"
                        "type VARCHAR NOT NULL DEFAULT 'hologram',"
                        "token VARCHAR NOT NULL,"
                        "orgid VARCHAR"
                        ")"
                    )
                )
        else:
            provider_cols = {
                c["name"] for c in inspector.get_columns("provider")
            }
            if "orgid" not in provider_cols:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE provider ADD COLUMN orgid VARCHAR"
                        )
                    )
        if "sim_card" not in tables:
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE sim_card ("
                        "id INTEGER PRIMARY KEY,"
                        "iccid VARCHAR UNIQUE NOT NULL,"
                        "device_id VARCHAR,"
                        "provider_id INTEGER NOT NULL REFERENCES provider(id),"
                        "equipment_id INTEGER NOT NULL REFERENCES equipment(id)"
                        ")"
                    )
                )
        else:
            sim_cols = {
                c["name"] for c in inspector.get_columns("sim_card")
            }
            with db.engine.begin() as conn:
                if "connected" not in sim_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE sim_card ADD COLUMN connected BOOLEAN"
                        )
                    )
                if "last_session" not in sim_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE sim_card ADD COLUMN last_session DATETIME"
                        )
                    )
                if "status_checked" not in sim_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE sim_card ADD COLUMN status_checked DATETIME"
                        )
                    )

    if hasattr(app, "before_first_request"):
        @app.before_first_request
        def init_db() -> None:
            """Crée les tables et applique les migrations légères."""
            db.create_all()
            upgrade_db()
    else:
        @app.before_request
        def init_db_once() -> None:
            """Fallback pour Flask 3 sans before_first_request."""
            if not getattr(app, "_db_init", False):
                db.create_all()
                upgrade_db()
                app._db_init = True  # type: ignore[attr-defined]

    @app.before_request
    def ensure_setup():
        allowed = {'setup', 'static'}
        if request.endpoint in allowed:
            return
        if User.query.count() == 0:
            return redirect(url_for('setup'))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return 'CSRF token missing or invalid', 400

    @login_manager.user_loader
    def load_user(user_id):
        """Retrieve a user for Flask-Login without legacy API warnings."""
        return db.session.get(User, int(user_id))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        form = LoginForm()
        error = None
        if request.method == 'POST':
            username_try = request.form.get('username', '') or ''
            keys = [f"ip:{_client_ip()}"]
            if username_try:
                keys.append(f"user:{username_try}")
            if _too_many_attempts(keys):
                return (
                    render_template('login.html', error='Trop de tentatives, réessayez plus tard', form=form),
                    429,
                )
            if form.validate_on_submit():
                user = User.query.filter_by(
                    username=form.username.data
                ).first()
                if user and user.check_password(form.password.data):
                    login_user(user)
                    _reset_attempts(keys)
                    return redirect(url_for('index'))
                error = 'Nom d’utilisateur ou mot de passe incorrect'
            else:
                # Generic error string; field-level messages shown in template
                error = 'Veuillez corriger les erreurs ci-dessous'
        return render_template('login.html', error=error, form=form)

    @app.route('/logout', methods=['POST'])
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        """Assistant de première configuration."""
        # Option de verrouillage complet du setup en production
        if os.environ.get('SETUP_DISABLED') == '1':
            return ('Setup désactivé', 403)
        if User.query.count() == 0:
            if request.method == 'POST':
                username = request.form.get('username')
                password = request.form.get('password')
                if username and password:
                    admin = User(username=username, is_admin=True)
                    admin.set_password(password)
                    db.session.add(admin)
                    db.session.commit()
                    return redirect(url_for('login'))
            return render_template('setup_step1.html')

        return redirect(url_for('login'))

    def save_config(
        form: MultiDict[str, str], rows: Iterable[dict[str, Any]]
    ) -> None:
        """Persist configuration parameters and device options."""

        def norm_decimal(val: str | None) -> str | None:
            if val is None:
                return None
            return val.replace(',', '.').strip()

        token_global = form.get('token_global')
        base_url = form.get('base_url')
        eps = norm_decimal(form.get('eps_meters'))
        min_surface = norm_decimal(form.get('min_surface'))
        alpha = norm_decimal(form.get('alpha_shape'))
        analysis_hour = form.get('analysis_hour')

        cfg = Config.query.first()
        if cfg:
            if base_url:
                cfg.traccar_url = base_url
            if token_global:
                cfg.traccar_token = token_global
            if eps:
                cfg.eps_meters = float(eps)
            if min_surface:
                cfg.min_surface_ha = float(min_surface)
            if alpha:
                cfg.alpha = float(alpha)
            if analysis_hour:
                cfg.analysis_hour = int(analysis_hour)
        else:
            cfg = Config(
                traccar_url=base_url or "",
                traccar_token=token_global or "",
                eps_meters=float(eps) if eps else 25.0,
                min_surface_ha=float(min_surface) if min_surface else 0.1,
                alpha=float(alpha) if alpha else 0.02,
                analysis_hour=int(analysis_hour) if analysis_hour else 2,
            )
            db.session.add(cfg)

        # Mise à jour ou création des équipements Traccar
        for row in rows:
            form_id = row["form_id"]
            type_val = form.get(f"type_{form_id}", row.get("marker_icon", "tractor"))
            include_val = form.get(
                f"include_{form_id}", "1" if row.get("include_in_analysis", True) else "0"
            )
            if row["source"] == "traccar":
                follow_val = form.get(
                    f"follow_{form_id}", "1" if row.get("follow") else "0"
                )
                eq = row.get("eq")
                if follow_val == "1":
                    if not eq:
                        eq = Equipment(id_traccar=row["dev_id"])
                        db.session.add(eq)
                    eq.name = row["name"]
                    if token_global:
                        eq.token_api = token_global
                    eq.marker_icon = type_val
                    eq.include_in_analysis = include_val == "1"
                elif eq:
                    db.session.delete(eq)
            else:
                eq = row.get("eq")
                if eq:
                    eq.marker_icon = type_val
                    eq.include_in_analysis = include_val == "1"

        db.session.commit()

        if analysis_hour:
            job = scheduler.get_job('daily_analysis')
            if job:
                scheduler.reschedule_job(
                    'daily_analysis', trigger='cron', hour=int(analysis_hour)
                )

    def build_rows(devices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Combine Traccar devices and existing equipment for the admin table."""
        rows: list[dict[str, Any]] = []
        existing = Equipment.query.all()
        traccar_map = {e.id_traccar: e for e in existing if e.id_traccar}
        for dev in devices:
            eq = traccar_map.pop(dev['id'], None)
            rows.append(
                {
                    'form_id': f"t{dev['id']}",
                    'dev_id': dev['id'],
                    'name': dev['name'],
                    'source': 'traccar',
                    'eq': eq,
                    'marker_icon': (eq.marker_icon if eq else 'tractor'),
                    'include_in_analysis': (
                        eq.include_in_analysis if eq else True
                    ),
                    'follow': eq is not None,
                }
            )
        # Remaining Traccar equipments not returned by the API
        for eq in traccar_map.values():
            rows.append(
                {
                    'form_id': f"t{eq.id_traccar}",
                    'dev_id': eq.id_traccar,
                    'name': eq.name,
                    'source': 'traccar',
                    'eq': eq,
                    'marker_icon': eq.marker_icon or 'tractor',
                    'include_in_analysis': eq.include_in_analysis,
                    'follow': True,
                }
            )
        # OsmAnd equipments
        for eq in existing:
            if eq.id_traccar == 0 and eq.osmand_id:
                rows.append(
                    {
                        'form_id': f"o{eq.id}",
                        'dev_id': None,
                        'name': eq.name,
                        'source': 'osmand',
                        'eq': eq,
                        'marker_icon': eq.marker_icon or 'tractor',
                        'include_in_analysis': eq.include_in_analysis,
                        'follow': True,
                    }
                )
        return rows

    @app.route('/admin')
    @login_required
    def admin_redirect():
        return redirect(url_for('admin_equipment'))

    @app.route('/admin/equipment', methods=['GET', 'POST'])
    @login_required
    def admin_equipment():
        """Administration des équipements et paramètres par équipement."""
        if not current_user.is_admin:
            return redirect(url_for('index'))

        message = request.args.get('msg')
        error = None
        form = AdminConfigForm()
        try:
            devices = zone.fetch_devices()
        except (OSError, requests.exceptions.HTTPError, requests.exceptions.RequestException) as exc:
            app.logger.error("Device fetch failed: %s", exc)
            devices = []
            error = (
                "Impossible de récupérer les équipements. "
                "Vérifiez le token ou l'URL."
            )

        rows = build_rows(devices)

        if request.method == 'POST':
            if form.validate_on_submit():
                save_config(request.form, rows)
                rows = build_rows(devices)
                message = "Configuration enregistrée !"
            else:
                error = 'Veuillez corriger les erreurs de validation'

        osmand_devices = [
            e for e in Equipment.query.all() if e.id_traccar == 0 and e.osmand_id
        ]

        return render_template(
            'admin_equipment.html',
            equipment_rows=rows,
            osmand_devices=osmand_devices,
            message=message,
            error=error,
            form=form,
        )

    @app.route('/admin/analysis', methods=['GET', 'POST'])
    @login_required
    def admin_analysis():
        """Configurer les paramètres de l'analyse et du clustering."""
        if not current_user.is_admin:
            return redirect(url_for('index'))

        cfg = Config.query.first()
        message = request.args.get('msg')
        error = None
        form = AdminConfigForm()
        if request.method == 'POST':
            if form.validate_on_submit():
                save_config(request.form, [])
                cfg = Config.query.first()
                message = "Configuration enregistrée !"
            else:
                error = 'Veuillez corriger les erreurs de validation'

        if request.method == 'POST' and not form.validate():
            existing_eps = request.form.get('eps_meters', '')
            existing_surface = request.form.get('min_surface', '')
            existing_alpha = request.form.get('alpha_shape', '')
            existing_hour = request.form.get('analysis_hour', '')
        else:
            existing_eps = cfg.eps_meters if cfg else 25.0
            existing_surface = cfg.min_surface_ha if cfg else 0.1
            existing_alpha = cfg.alpha if cfg else 0.02
            existing_hour = cfg.analysis_hour if cfg else 2

        return render_template(
            'admin_analysis.html',
            existing_eps=existing_eps,
            existing_surface=existing_surface,
            existing_alpha=existing_alpha,
            existing_hour=existing_hour,
            message=message,
            error=error,
            form=form,
        )

    @app.route('/admin/traccar', methods=['GET', 'POST'])
    @login_required
    def admin_traccar():
        """Configurer la connexion au serveur Traccar."""
        if not current_user.is_admin:
            return redirect(url_for('index'))

        cfg = Config.query.first()
        message = request.args.get('msg')
        error = None
        form = AdminConfigForm()
        try:
            devices = zone.fetch_devices()
        except (OSError, requests.exceptions.HTTPError, requests.exceptions.RequestException) as exc:
            app.logger.error("Device fetch failed: %s", exc)
            devices = []
            error = (
                "Impossible de récupérer les équipements. "
                "Vérifiez le token ou l'URL."
            )

        rows = build_rows(devices)

        if request.method == 'POST':
            if form.validate_on_submit():
                save_config(request.form, rows)
                cfg = Config.query.first()
                message = "Configuration enregistrée !"
            else:
                error = 'Veuillez corriger les erreurs de validation'

        if request.method == 'POST' and not form.validate():
            existing_token = request.form.get('token_global', '')
            existing_url = request.form.get('base_url', '')
        else:
            existing_token = cfg.traccar_token if cfg else ""
            existing_url = cfg.traccar_url if cfg else ""

        return render_template(
            'admin_traccar.html',
            existing_token=existing_token,
            existing_url=existing_url,
            message=message,
            error=error,
            form=form,
        )

    @app.route('/admin/providers', methods=['GET', 'POST'])
    @login_required
    def admin_providers():
        """Configurer les fournisseurs de cartes SIM."""
        if not current_user.is_admin:
            return redirect(url_for('index'))

        form = ProviderForm()
        provider = Provider.query.first()
        message = None
        error = None

        if request.method == 'POST':
            if form.validate_on_submit():
                if provider:
                    provider.name = form.name.data
                    provider.token = form.token.data
                    provider.orgid = form.orgid.data or None
                else:
                    provider = Provider(
                        name=form.name.data,
                        token=form.token.data,
                        orgid=form.orgid.data or None,
                        type='hologram',
                    )
                    db.session.add(provider)
                db.session.commit()
                message = 'Fournisseur enregistré'
            else:
                error = 'Veuillez corriger les erreurs'

        if provider and request.method == 'GET':
            form.name.data = provider.name
            form.token.data = provider.token
            form.orgid.data = provider.orgid

        return render_template(
            'admin_providers.html',
            form=form,
            message=message,
            error=error,
        )

    @app.route('/admin/add_osmand', methods=['POST'])
    @login_required
    def add_osmand_device():
        if not current_user.is_admin:
            return redirect(url_for('index'))
        name = request.form.get('osmand_name', '').strip()
        devid = request.form.get('osmand_id', '').strip()
        token = request.form.get('osmand_token', '').strip()
        if not name or not devid:
            return redirect(url_for('admin_equipment', msg='Nom et ID requis'))
        existing = Equipment.query.filter_by(osmand_id=devid).first()
        if existing:
            return redirect(url_for('admin_equipment', msg='ID déjà existant'))
        eq = Equipment(id_traccar=0, name=name, osmand_id=devid)
        if token:
            eq.token_api = token
        db.session.add(eq)
        db.session.commit()
        return redirect(url_for('admin_equipment', msg='Appareil OsmAnd ajouté'))

    @app.route('/reanalyze_all', methods=['POST'])
    @login_required
    def reanalyze_all():
        if not current_user.is_admin:
            return redirect(url_for('index'))
        if reanalysis_progress["running"]:
            return redirect(url_for('admin_equipment', msg="Analyse déjà en cours"))
        if request.form:
            try:
                devices = zone.fetch_devices()
            except (OSError, requests.exceptions.HTTPError, requests.exceptions.RequestException):
                return redirect(
                    url_for(
                        'admin_equipment',
                        msg="Erreur lors de la récupération des équipements",
                    )
                )
            rows = build_rows(devices)
            save_config(request.form, rows)

        equipment_ids = [
            e.id for e in Equipment.query.all()
            if getattr(e, 'include_in_analysis', True)
        ]
        reanalysis_progress.update(
            {
                "running": True,
                "current": 0,
                "total": len(equipment_ids),
                "equipment": "",
            }
        )

        def run() -> None:
            with app.app_context():
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                start_of_year = datetime(now.year, 1, 1)
                for idx, equipment_id in enumerate(equipment_ids, start=1):
                    eq = db.session.get(Equipment, equipment_id)
                    if not eq:
                        continue
                    reanalysis_progress["equipment"] = eq.name
                    # Skip excluded equipments
                    if hasattr(eq, 'include_in_analysis') and not (eq.include_in_analysis or False):
                        reanalysis_progress["current"] = idx
                        continue
                    # Use Traccar fetch or local positions depending on equipment
                    if getattr(eq, 'id_traccar', None):
                        try:
                            zone.process_equipment(eq, since=start_of_year)
                        except Exception as exc:
                            app.logger.exception("process_equipment failed: %s", exc)
                    else:
                        try:
                            zone.recalculate_hectares_from_positions(eq.id, since_date=start_of_year)
                        except Exception as exc:
                            app.logger.exception("recalculate failed: %s", exc)
                    db.session.commit()
                    reanalysis_progress["current"] = idx
                reanalysis_progress["running"] = False
                reanalysis_progress["equipment"] = ""

        threading.Thread(target=run, daemon=True).start()
        return redirect(
            url_for('admin_equipment', msg="Analyse relancée en arrière-plan")
        )

    @app.route('/analysis_status')
    @login_required
    def analysis_status():
        if not current_user.is_admin:
            return jsonify({"running": False}), 403
        resp = jsonify(reanalysis_progress)
        resp.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.route('/users', methods=['GET', 'POST'])
    @login_required
    def users():
        if not current_user.is_admin:
            return redirect(url_for('index'))

        message = None
        add_form = AddUserForm()
        reset_form = ResetPasswordForm()
        delete_form = DeleteUserForm()
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                if add_form.validate_on_submit():
                    username = add_form.username.data
                    password = add_form.password.data
                    role = request.form.get('role')
                    if role not in ('read', 'admin'):
                        message = "Rôle invalide"
                    elif User.query.filter_by(username=username).first():
                        message = "Utilisateur déjà existant"
                    else:
                        user = User(
                            username=username, is_admin=(role == 'admin')
                        )
                        user.set_password(password)
                        db.session.add(user)
                        db.session.commit()
                        message = "Utilisateur ajouté"
                else:
                    message = "Veuillez corriger le formulaire d’ajout"
            elif action == 'reset':
                if reset_form.validate_on_submit():
                    uid = reset_form.user_id.data
                    password = reset_form.password.data
                    user = db.session.get(User, int(uid)) if uid else None
                    if user:
                        user.set_password(password)
                        db.session.commit()
                        message = "Mot de passe réinitialisé"
                else:
                    message = "Mot de passe invalide (min 3 caractères)"
            elif action == 'delete':
                if delete_form.validate_on_submit():
                    uid = delete_form.user_id.data
                    user = db.session.get(User, int(uid)) if uid else None
                    if user and user != current_user:
                        db.session.delete(user)
                        db.session.commit()
                        message = "Utilisateur supprimé"

        users = User.query.all()
        return render_template(
            'users.html', users=users, message=message,
            add_form=add_form, reset_form=reset_form, delete_form=delete_form
        )

    # -------------------- OsmAnd ingest endpoint --------------------
    def _parse_timestamp(val: str | int | float) -> datetime:
        if isinstance(val, (int, float)):
            # seconds or milliseconds
            ts = float(val)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts)
        s = str(val).strip()
        # epoch
        if s.isdigit():
            ts = float(s)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts)
        # ISO8601
        try:
            if s.endswith('Z'):
                s = s.replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            pass
        # Fallback format yyyy-MM-dd HH:mm:ss
        try:
            return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except Exception:
            raise BadRequest('Invalid timestamp format')

    def _ensure_equipment(device_id: str) -> Equipment:
        eq = Equipment.query.filter_by(osmand_id=device_id).first()
        if eq:
            return eq
        # Create new equipment tracked via OsmAnd; use id_traccar=0
        name = f"Device {device_id}"
        eq = Equipment(id_traccar=0, name=name, osmand_id=device_id)
        db.session.add(eq)
        db.session.commit()
        return eq

    def _auth_ok(eq: Equipment) -> bool:
        token = request.args.get('token') or request.headers.get('X-Token')
        if not token:
            auth = request.headers.get('Authorization', '')
            if auth.lower().startswith('bearer '):
                token = auth.split(' ', 1)[1].strip()
        if eq.token_api:
            return token == eq.token_api
        # No token configured: accept
        return True

    @csrf.exempt
    @app.route('/osmand', methods=['GET', 'POST'])
    def osmand_ingest():
        # Accept OsmAnd-like payloads: query params or JSON for a single device
        def ingest_one(device_id: str, locs: list[dict]) -> None:
            eq = _ensure_equipment(str(device_id))
            if not _auth_ok(eq):
                raise BadRequest('Unauthorized')
            latest_ts = None
            for entry in locs:
                # Normalize structure from JSON payload
                if 'coords' in entry:
                    lat = entry['coords'].get('latitude')
                    lon = entry['coords'].get('longitude')
                else:
                    lat = entry.get('latitude')
                    lon = entry.get('longitude')
                if lat is None or lon is None:
                    continue
                ts_val = entry.get('timestamp') or entry.get('time')
                batt_val = entry.get('battery') or entry.get('batt')
                if isinstance(batt_val, dict):
                    batt_val = batt_val.get('level')
                try:
                    ts = _parse_timestamp(ts_val) if ts_val is not None else datetime.utcnow()
                except BadRequest:
                    continue
                ts_naive = ts.replace(tzinfo=None)
                existing = Position.query.filter_by(
                    equipment_id=eq.id,
                    latitude=float(lat),
                    longitude=float(lon),
                    timestamp=ts_naive,
                ).first()
                if not existing:
                    p_obj = Position(
                        equipment_id=eq.id,
                        latitude=float(lat),
                        longitude=float(lon),
                        timestamp=ts_naive,
                    )
                    # Persist per-point battery level when provided
                    if batt_val is not None:
                        try:
                            b = float(batt_val)
                            if b <= 1:
                                b *= 100
                            p_obj.battery_level = b
                        except (TypeError, ValueError):
                            pass
                    db.session.add(p_obj)
                if latest_ts is None or ts_naive > latest_ts:
                    latest_ts = ts_naive
                if batt_val is not None:
                    try:
                        batt_float = float(batt_val)
                        if batt_float <= 1:
                            batt_float *= 100
                        eq.battery_level = batt_float
                        app.logger.info(
                            "Device %s battery at %.0f%%",
                            device_id,
                            eq.battery_level,
                        )
                    except (TypeError, ValueError):
                        app.logger.info(
                            "Ignoring invalid battery level %r for device %s",
                            batt_val,
                            device_id,
                        )
            if latest_ts is not None:
                eq.last_position = latest_ts

        raw = request.get_data() or b""
        if request.headers.get('Content-Encoding') == 'gzip':
            try:
                raw = gzip.decompress(raw)
            except OSError:
                raise BadRequest('Invalid gzip payload')
        data = None
        if raw:
            try:
                data = json.loads(raw.decode('utf-8'))
            except Exception:
                data = None
        if isinstance(data, dict):
            if isinstance(data.get('devices'), list):
                return ("Multiple devices not supported", 400)
            device_id = data.get('device_id') or data.get('deviceid') or data.get('id')
            locations: list[dict] = []
            if 'locations' in data and isinstance(data['locations'], list):
                locations = list(data['locations'])
            elif 'location' in data and isinstance(data['location'], dict):
                locations = [data['location']]
            # If battery is provided at top-level, propagate it to entries
            if 'battery' in data and locations:
                for entry in locations:
                    entry.setdefault('battery', data['battery'])
            if not device_id:
                return ("Missing device id", 400)
            if not locations:
                return ("No locations", 400)
            ingest_one(str(device_id), locations)
            db.session.commit()
            return ("OK", 200)
        # Query/form encoding (single fix)
        form = request.values
        device_id = form.get('deviceid') or form.get('id')
        locations: list[dict] = []
        if form.get('lat') and form.get('lon'):
            ts = form.get('timestamp') or form.get('time')
            locations.append({
                'coords': {
                    'latitude': float(form.get('lat')),
                    'longitude': float(form.get('lon')),
                },
                'timestamp': ts or datetime.utcnow().isoformat() + 'Z',
                'battery': form.get('battery') or form.get('batt'),
            })
        elif form.get('location'):
            try:
                lat_s, lon_s = form.get('location').split(',', 1)
                ts = form.get('timestamp') or form.get('time')
                locations.append({
                    'coords': {
                        'latitude': float(lat_s.strip()),
                        'longitude': float(lon_s.strip()),
                    },
                    'timestamp': ts or datetime.utcnow().isoformat() + 'Z',
                    'battery': form.get('battery') or form.get('batt'),
                })
            except Exception:
                raise BadRequest('Invalid location parameter')
        if not device_id:
            return ("Missing device id", 400)
        if not locations:
            return ("No locations", 400)
        ingest_one(str(device_id), locations)
        db.session.commit()
        return ("OK", 200)

    def get_equipment_data() -> list[dict[str, Any]]:
        equipments = Equipment.query.all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        equipment_data: list[dict[str, Any]] = []
        for eq in equipments:
            last_dt = eq.last_position
            if last_dt is None:
                last_pos = (
                    Position.query.filter_by(equipment_id=eq.id)
                    .order_by(Position.timestamp.desc())
                    .first()
                )
                if last_pos:
                    last_dt = last_pos.timestamp

            if last_dt:
                last = last_dt.strftime('%Y-%m-%d %H:%M:%S')
                delta = now - last_dt
                delta_seconds = delta.total_seconds()
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                delta_str = f"{delta.days} j {hours} h {minutes} min"
            else:
                last = None
                delta_seconds = None
                delta_str = "–"

            distance_km = (eq.distance_between_zones or 0) / 1000
            total_hectares = eq.total_hectares or zone.calculate_total_hectares(eq.id)
            rel_hectares = getattr(eq, "relative_hectares", 0.0) or 0.0
            ratio_eff = (total_hectares / distance_km) if distance_km else 0.0

            if getattr(eq, 'osmand_id', None) and (getattr(eq, 'id_traccar', 0) == 0):
                source = 'osmand'
            else:
                source = 'traccar'

            sim = SimCard.query.filter_by(equipment_id=eq.id).first()
            connected = sim.connected if sim else None
            last_session_str = (
                sim.last_session.strftime('%Y-%m-%d %H:%M:%S')
                if sim and sim.last_session
                else None
            )
            equipment_data.append({
                "id": eq.id,
                "name": eq.name,
                "source": source,
                "include_in_analysis": getattr(eq, 'include_in_analysis', True),
                "icon": eq.marker_icon or 'tractor',
                "last_seen": last,
                "total_hectares": round(total_hectares or 0, 2),
                "relative_hectares": round(rel_hectares, 2),
                "distance_km": round(distance_km, 2),
                "delta_seconds": delta_seconds,
                "ratio_eff": ratio_eff,
                "delta_str": delta_str,
                "battery_level": eq.battery_level,
                "sim_present": sim is not None,
                "sim_device_id": sim.device_id if sim else None,
                "sim_connected": connected,
                "sim_last_session": last_session_str,
            })

        def normalize(values, value, invert=False):
            clean = [v for v in values if v is not None]
            if not clean or value is None:
                return 0.0
            vmin = min(clean)
            vmax = max(clean)
            if vmax == vmin:
                return 1.0
            if invert:
                return (vmax - value) / (vmax - vmin)
            return (value - vmin) / (vmax - vmin)

        times = [
            d["delta_seconds"]
            for d in equipment_data
            if d["delta_seconds"] is not None
        ]
        totals = [d["total_hectares"] for d in equipment_data]
        uniques = [d["relative_hectares"] for d in equipment_data]
        distances = [d["distance_km"] for d in equipment_data]
        ratios = [d["ratio_eff"] for d in equipment_data]

        for d in equipment_data:
            n_time = normalize(times, d["delta_seconds"], invert=True)
            n_total = normalize(totals, d["total_hectares"])
            n_unique = normalize(uniques, d["relative_hectares"])
            n_dist = normalize(distances, d["distance_km"])
            n_ratio = normalize(ratios, d["ratio_eff"])
            d["score"] = round(
                0.3 * n_time
                + 0.3 * n_total
                + 0.2 * n_unique
                + 0.1 * n_dist
                + 0.1 * n_ratio,
                3,
            )

        equipment_data.sort(key=lambda x: x["score"], reverse=True)
        for idx, d in enumerate(equipment_data, start=1):
            d["rank"] = idx

        return equipment_data

    @app.route('/')
    @login_required
    def index():
        equipment_data = get_equipment_data()
        providers = Provider.query.all()
        sim_form = SimAssociationForm()
        sim_form.provider.choices = [(p.id, p.name) for p in providers]
        return render_template(
            'index.html',
            equipment_data=equipment_data,
            sim_form=sim_form,
        )

    @app.route('/equipment_status')
    @login_required
    def equipment_status():
        return jsonify(get_equipment_data())

    def _hologram_device_status(
        token: str, device_id: str
    ) -> tuple[bool, Optional[datetime]]:
        """Retourne l'état de connexion et la dernière session."""
        try:
            url = f"https://dashboard.hologram.io/api/1/devices/{device_id}"
            app.logger.info("Hologram GET %s", url)
            resp = requests.get(url, auth=("apikey", token), timeout=10)
            app.logger.info(
                "Hologram response %s: %s", resp.status_code, resp.text
            )
            data = resp.json().get("data", {})
            links = data.get("links", {}).get("cellular", [])
            last_connect = links[0].get("last_connect_time") if links else None
            last_session_str = (
                data.get("lastsession", {}).get("session_end")
            )
            last_session = None
            if last_session_str and last_session_str != "0000-00-00 00:00:00":
                try:
                    last_session = datetime.strptime(
                        last_session_str, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    last_session = None
            connected = False
            if last_connect:
                try:
                    dt = datetime.strptime(last_connect, "%Y-%m-%d %H:%M:%S")
                    connected = datetime.utcnow() - dt < timedelta(hours=1)
                except ValueError:
                    connected = False
            return connected, last_session
        except Exception:
            return False, None

    def _hologram_send_sms(token: str, device_id: str, body: str) -> bool:
        """Envoie un SMS via l'API Hologram."""
        try:
            url = "https://dashboard.hologram.io/api/1/sms/incoming"
            payload = {"deviceid": device_id, "body": body}
            app.logger.info(
                "Hologram POST %s payload=%s", url, payload
            )
            resp = requests.post(
                url, auth=("apikey", token), json=payload, timeout=10
            )
            app.logger.info(
                "Hologram response %s: %s", resp.status_code, resp.text
            )
            return resp.ok
        except Exception:
            return False

    def _update_sim_status(sim: SimCard) -> None:
        if sim.provider.type == 'hologram' and sim.device_id:
            connected, last_session = _hologram_device_status(
                sim.provider.token, sim.device_id
            )
            sim.connected = connected
            sim.last_session = last_session
        else:
            sim.connected = False
            sim.last_session = None
        sim.status_checked = datetime.utcnow()

    @app.route('/providers/<int:prov_id>/sims')
    @login_required
    def list_provider_sims(prov_id: int):
        """Retourne la liste des SIM disponibles chez le fournisseur."""
        provider = Provider.query.get_or_404(prov_id)
        if provider.type != 'hologram':
            return jsonify([])
        params: dict[str, str] = {"limit": "1000"}
        if provider.orgid:
            params["orgid"] = provider.orgid
        app.logger.info(
            "Fetching SIM list for provider %s (org %s)",
            provider.name,
            provider.orgid or "",
        )
        try:
            url = "https://dashboard.hologram.io/api/1/devices"
            app.logger.info("Hologram GET %s params=%s", url, params)
            resp = requests.get(
                url,
                auth=("apikey", provider.token),
                params=params,
                timeout=10,
            )
            app.logger.info(
                "Hologram response %s: %s", resp.status_code, resp.text
            )
            resp.raise_for_status()
            payload = resp.json()
            if not payload.get("success"):
                app.logger.error(
                    "Hologram device fetch error: %s", payload.get("error")
                )
                return jsonify([]), 502
            data = payload.get("data", [])
            app.logger.info("Received %s devices from Hologram", len(data))
        except Exception as e:
            app.logger.exception("Hologram SIM fetch failed: %s", e)
            return jsonify([]), 500
        sims = []
        for device in data:
            dev_id = device.get("id")
            name = device.get("name") or str(dev_id)
            links = device.get("links", {}).get("cellular", [])
            for link in links:
                iccid = link.get("sim")
                if iccid and dev_id:
                    sims.append(
                        {
                            "value": f"{dev_id}:{iccid}",
                            "label": f"{name} ({iccid})",
                        }
                    )
        return jsonify(sims)

    @app.route('/sim/status')
    @login_required
    def sim_status_all():
        """Retourne l'état de connexion des SIM."""
        sims = SimCard.query.all()
        result = []
        updated = False
        now = datetime.utcnow()
        for sim in sims:
            if (
                sim.status_checked is None
                or now - sim.status_checked > timedelta(hours=1)
            ):
                _update_sim_status(sim)
                updated = True
            result.append(
                {
                    "id": sim.equipment_id,
                    "connected": sim.connected,
                    "last_session": sim.last_session.strftime("%Y-%m-%d %H:%M:%S")
                    if sim.last_session
                    else None,
                }
            )
        if updated:
            db.session.commit()
        return jsonify(result)

    @app.route('/sim/associate', methods=['POST'])
    @login_required
    def associate_sim():
        form = SimAssociationForm()
        providers = Provider.query.all()
        form.provider.choices = [(p.id, p.name) for p in providers]
        if form.validate_on_submit():
            device_id, iccid = form.sim.data.split(':', 1)
            eq_id = int(form.equipment_id.data)
            current_app.logger.info(
                "Associating SIM %s (device %s) to equipment %s via provider %s",
                iccid,
                device_id,
                eq_id,
                form.provider.data,
            )
            sim = SimCard(
                iccid=iccid,
                device_id=device_id,
                provider_id=form.provider.data,
                equipment_id=eq_id,
            )
            db.session.add(sim)
            db.session.commit()
            _update_sim_status(sim)
            db.session.commit()
            flash("Carte SIM associée", "success")
        else:
            current_app.logger.warning("SIM association failed: %s", form.errors)
            flash("Échec de l'association de la SIM", "danger")
        return redirect(url_for('index'))

    @app.route('/sim/<int:eq_id>/request_position', methods=['POST'])
    @login_required
    def request_position(eq_id: int):
        sim = SimCard.query.filter_by(equipment_id=eq_id).first()
        if not sim:
            return jsonify({"success": False}), 404
        ok = False
        if sim.provider.type == 'hologram' and sim.device_id:
            ok = _hologram_send_sms(sim.provider.token, sim.device_id, 'POSITION')
        return jsonify({"success": ok})

    @app.route('/sim/<int:eq_id>/dissociate', methods=['POST'])
    @login_required
    def dissociate_sim(eq_id: int):
        sim = SimCard.query.filter_by(equipment_id=eq_id).first()
        if not sim:
            return jsonify({"success": False}), 404
        db.session.delete(sim)
        db.session.commit()
        return jsonify({"success": True})

    @app.route('/equipment/<int:equipment_id>/last.geojson')
    @login_required
    def equipment_last_geojson(equipment_id):
        from flask import abort
        eq = db.session.get(Equipment, equipment_id)
        if not eq:
            abort(404)
        # Find latest position for this equipment
        pos = (
            Position.query.filter_by(equipment_id=equipment_id)
            .order_by(Position.timestamp.desc())
            .first()
        )
        if not pos:
            return {"type": "FeatureCollection", "features": []}
        if getattr(eq, 'osmand_id', None) and (getattr(eq, 'id_traccar', 0) == 0):
            source = 'osmand'
        else:
            source = 'traccar'
        feature = {
            'type': 'Feature',
            'id': str(pos.id),
            'properties': {
                'timestamp': pos.timestamp.isoformat(),
                'source': source,
                'equipment': eq.name,
                'icon': getattr(eq, 'marker_icon', 'tractor'),
            },
            'geometry': {
                'type': 'Point',
                'coordinates': [pos.longitude, pos.latitude],
            },
        }
        return {'type': 'FeatureCollection', 'features': [feature]}

    @app.route('/equipment/<int:equipment_id>')
    @login_required
    def equipment_detail(equipment_id):
        from flask import abort
        eq = db.session.get(Equipment, equipment_id)
        if not eq:
            abort(404)
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        start_date = date.fromisoformat(start_str) if start_str else None
        end_date = date.fromisoformat(end_str) if end_str else None
        show_all = request.args.get('show') == 'all'

        if DailyZone.query.filter_by(equipment_id=equipment_id).count():
            agg_all = zone.get_aggregated_zones(equipment_id)
        else:
            agg_all = []
        dates = {
            date.fromisoformat(d)
            for z in agg_all
            for d in z.get("dates", [])
        }

        all_tracks = Track.query.filter_by(equipment_id=equipment_id).all()
        track_dates = set()
        for t in all_tracks:
            current = t.start_time.date()
            last_day = t.end_time.date()
            while current <= last_day:
                track_dates.add(current)
                current += timedelta(days=1)
        dates.update(track_dates)

        last_position = (
            Position.query.filter_by(equipment_id=equipment_id)
            .order_by(Position.timestamp.desc())
            .first()
        )
        if last_position:
            dates.add(last_position.timestamp.date())

        # Include days that have raw GPS points (useful when there are no
        # tracks/zones, e.g., OsmAnd-only data). We query distinct date(ts).
        try:
            rows = (
                db.session.query(db.func.date(Position.timestamp))
                .filter(Position.equipment_id == equipment_id)
                .distinct()
                .all()
            )
            for (dt_val,) in rows:
                # SQLite returns string YYYY-MM-DD; other backends may return date
                if isinstance(dt_val, date):
                    dates.add(dt_val)
                else:
                    try:
                        dates.add(date.fromisoformat(str(dt_val)))
                    except Exception:
                        pass
        except Exception:
            # Fallback: ignore if aggregation fails; not critical
            pass

        has_tracks = bool(all_tracks)

        if (
            not show_all
            and start_date is None
            and end_date is None
            and year is None
            and month is None
            and day is None
            and dates
        ):
            last = max(dates)
            start_date = end_date = last

        if show_all or (
            start_date is None
            and end_date is None
            and year is None
            and month is None
            and day is None
        ):
            agg_period = agg_all
        else:
            agg_period = zone.get_aggregated_zones(
                equipment_id,
                year=year,
                month=month,
                day=day,
                start=start_date,
                end=end_date,
            )

        zones: list = []
        zone_bounds = {}
        grouped: dict = {}
        for idx, z in enumerate(agg_period):
            ids_set = set(z.get("ids", []))
            full_idx = next(
                (
                    i
                    for i, full in enumerate(agg_all)
                    if ids_set == set(full.get("ids", []))
                ),
                None,
            )
            if full_idx is None:
                full_idx = next(
                    (
                        i
                        for i, full in enumerate(agg_all)
                        if ids_set <= set(full.get("ids", []))
                    ),
                    idx,
                )
            info = grouped.setdefault(full_idx, {"dates": [], "surface": 0.0})
            info["dates"].extend(z.get("dates", []))
            info["surface"] += z["geometry"].area / 1e4
            zone_bounds[full_idx] = zone.geom_bounds(
                agg_all[full_idx]["geometry"]
            )

        for idx, info in grouped.items():
            zones.append(
                {
                    "id": idx,
                    "dates": ", ".join(sorted(set(info["dates"]))),
                    "pass_count": len(set(info["dates"])),
                    "surface_ha": info["surface"],
                }
            )

        from shapely.ops import unary_union
        from shapely import wkt

        zone_union = (
            unary_union([z["geometry"] for z in agg_period])
            if agg_period
            else None
        )
        bounds = (
            zone.geom_bounds(zone_union) if zone_union is not None else None
        )

        track_query = Track.query.filter_by(equipment_id=equipment_id)
        filter_start = start_date
        filter_end = end_date
        if (
            filter_start is None
            and filter_end is None
            and year
            and month
            and day
        ):
            d = date(year, month, day)
            filter_start = filter_end = d
        if filter_start is not None:
            start_dt = datetime.combine(filter_start, datetime.min.time())
            track_query = track_query.filter(Track.end_time >= start_dt)
        if filter_end is not None:
            end_dt = datetime.combine(
                filter_end + timedelta(days=1), datetime.min.time()
            )
            track_query = track_query.filter(Track.start_time < end_dt)
        tracks = [
            wkt.loads(t.line_wkt) for t in track_query.all() if t.line_wkt
        ]

        # Compute days that have tracks within the selected period
        track_days_in_period = set()
        for t in Track.query.filter_by(equipment_id=equipment_id).all():
            if not t.start_time or not t.end_time:
                continue
            # If a filter is active, keep only overlapping days
            if filter_start is not None and t.end_time.date() < filter_start:
                continue
            if filter_end is not None and t.start_time.date() > filter_end:
                continue
            cur = t.start_time.date()
            last = t.end_time.date()
            while cur <= last:
                if (
                    (filter_start is None or cur >= filter_start)
                    and (filter_end is None or cur <= filter_end)
                ):
                    track_days_in_period.add(cur)
                cur += timedelta(days=1)
        if tracks:
            track_union = unary_union(tracks)
            tb = track_union.bounds
            if bounds:
                bounds = (
                    min(bounds[0], tb[0]),
                    min(bounds[1], tb[1]),
                    max(bounds[2], tb[2]),
                    max(bounds[3], tb[3]),
                )
            else:
                bounds = tb

        last = last_position
        has_last_position = last is not None

        # If no zones/tracks bounds, try to derive bounds from GPS points
        if bounds is None:
            # Build a positions query constrained to the selected period
            pos_query = Position.query.filter_by(equipment_id=equipment_id)
            if filter_start is not None:
                pos_query = pos_query.filter(Position.timestamp >= datetime.combine(filter_start, datetime.min.time()))
            if filter_end is not None:
                pos_query = pos_query.filter(Position.timestamp < datetime.combine(filter_end + timedelta(days=1), datetime.min.time()))
            # Compute min/max extents from filtered points
            pts = pos_query.all()
            if pts:
                lons = [p.longitude for p in pts]
                lats = [p.latitude for p in pts]
                min_lon, max_lon = min(lons), max(lons)
                min_lat, max_lat = min(lats), max(lats)
            else:
                min_lon = min_lat = max_lon = max_lat = None
            if (
                min_lon is not None and min_lat is not None
                and max_lon is not None and max_lat is not None
            ):
                if min_lon == max_lon and min_lat == max_lat:
                    delta = 0.0005
                    bounds = (
                        min_lon - delta,
                        min_lat - delta,
                        max_lon + delta,
                        max_lat + delta,
                    )
                else:
                    bounds = (min_lon, min_lat, max_lon, max_lat)

        # Final fallback: last position small envelope
        if bounds is None and last:
            delta = 0.0005
            bounds = (
                last.longitude - delta,
                last.latitude - delta,
                last.longitude + delta,
                last.latitude + delta,
            )

        sorted_dates = sorted(dates)
        available_dates = [d.isoformat() for d in sorted_dates]
        # Determine if there are points in selected period
        has_points_in_period = False
        try:
            pq = Position.query.filter_by(equipment_id=equipment_id)
            if filter_start is not None:
                pq = pq.filter(Position.timestamp >= datetime.combine(filter_start, datetime.min.time()))
            if filter_end is not None:
                pq = pq.filter(Position.timestamp < datetime.combine(filter_end + timedelta(days=1), datetime.min.time()))
            has_points_in_period = pq.limit(1).count() > 0
        except Exception:
            pass

        has_data = bool(zones or has_tracks or has_last_position or has_points_in_period)

        # Add explicit rows for days that have tracks but no computed zones
        # in the selected period (or the auto-selected single day).
        period_zone_dates = set()
        for z in agg_period:
            for dstr in z.get("dates", []):
                try:
                    period_zone_dates.add(date.fromisoformat(dstr))
                except Exception:
                    continue
        missing_days = sorted(track_days_in_period - period_zone_dates)
        for d in missing_days:
            zones.append(
                {
                    "id": f"nozone:{d.isoformat()}",
                    "dates": d.isoformat(),
                    "pass_count": 0,
                    "surface_ha": 0.0,
                    "no_zone": True,
                }
            )

        date_value = ""
        if start_date and end_date:
            if start_date == end_date:
                date_value = start_date.isoformat()
            else:
                date_value = (
                    f"{start_date.isoformat()} to {end_date.isoformat()}"
                )
        elif year and month and day:
            date_value = date(year, month, day).isoformat()

        # Default to showing points if they are the only data in the period
        show_points_default = has_points_in_period and not zones and not tracks

        return render_template(
            'equipment.html',
            equipment=eq,
            zones=zones,
            bounds=bounds,
            zone_bounds=zone_bounds,
            available_dates=available_dates,
            year=year,
            month=month,
            day=day,
            start=start_date.isoformat() if start_date else None,
            end=end_date.isoformat() if end_date else None,
            date_value=date_value,
            show_all=show_all,
            has_tracks=has_tracks,
            has_data=has_data,
            show_points_default=show_points_default,
        )

    @app.route('/equipment/<int:equipment_id>/zones.geojson')
    @login_required
    def equipment_zones_geojson(equipment_id):
        from flask import abort
        if not db.session.get(Equipment, equipment_id):
            abort(404)
        bbox = request.args.get('bbox')
        zoom = int(request.args.get('zoom', 12))
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        start = date.fromisoformat(start_str) if start_str else None
        end = date.fromisoformat(end_str) if end_str else None
        agg_all = zone.get_aggregated_zones(equipment_id)
        agg = zone.get_aggregated_zones(
            equipment_id,
            year=year,
            month=month,
            day=day,
            start=start,
            end=end,
        )

        from shapely.geometry import box
        from shapely.ops import transform as shp_transform

        bbox_geom = None
        if bbox:
            west, south, east, north = [float(x) for x in bbox.split(',')]
            bbox_geom = shp_transform(
                zone._to_webmerc,
                box(west, south, east, north)
            )

        features = []
        for idx, z in enumerate(agg):
            geom = z["geometry"]
            if bbox_geom and not geom.intersects(bbox_geom):
                continue
            if bbox_geom:
                geom = geom.intersection(bbox_geom)
            geom = zone.simplify_for_zoom(geom, zoom)
            geom_wgs = shp_transform(zone._transformer, geom)
            ids_set = set(z.get("ids", []))
            full_idx = next(
                (
                    i
                    for i, full in enumerate(agg_all)
                    if ids_set == set(full.get("ids", []))
                ),
                None,
            )
            if full_idx is None:
                full_idx = next(
                    (
                        i
                        for i, full in enumerate(agg_all)
                        if ids_set <= set(full.get("ids", []))
                    ),
                    idx,
                )
            zid = str(full_idx)
            features.append(
                {
                    "type": "Feature",
                    "id": zid,
                    "properties": {
                        "id": zid,
                        "dates": z["dates"],
                        "dz_ids": z.get("ids", []),
                        "count": len(z["dates"]),
                        "surface_ha": round(geom.area / 1e4, 2),
                    },
                    "geometry": geom_wgs.__geo_interface__,
                }
            )

        return {'type': 'FeatureCollection', 'features': features}

    @app.route('/equipment/<int:equipment_id>/points.geojson')
    @login_required
    def equipment_points_geojson(equipment_id):
        """Return a random sample of GPS points for the current map view."""
        from flask import abort
        if not db.session.get(Equipment, equipment_id):
            abort(404)
        bbox = request.args.get('bbox')
        try:
            limit = int(request.args.get('limit', 5000))
        except Exception:
            limit = 5000
        limit = max(0, min(limit, 10000))
        include_all = request.args.get('all') == '1'
        west = south = east = north = None
        if bbox:
            west, south, east, north = [float(x) for x in bbox.split(',')]
        query = Position.query.filter_by(equipment_id=equipment_id)
        if not include_all:
            query = query.filter(Position.track_id.is_(None))
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        if start_str or end_str:
            start_d = date.fromisoformat(start_str) if start_str else None
            end_d = date.fromisoformat(end_str) if end_str else None
            if start_d:
                start_dt = datetime.combine(start_d, datetime.min.time())
                query = query.filter(Position.timestamp >= start_dt)
            if end_d:
                end_dt = datetime.combine(
                    end_d + timedelta(days=1), datetime.min.time()
                )
                query = query.filter(Position.timestamp < end_dt)
        elif year is not None:
            start_dt = datetime(year, 1, 1)
            end_dt = datetime(year + 1, 1, 1)
            if month is not None:
                start_dt = datetime(year, month, 1)
                end_dt = (
                    datetime(year + 1, 1, 1)
                    if month == 12
                    else datetime(year, month + 1, 1)
                )
                if day is not None:
                    start_dt = datetime(year, month, day)
                    end_dt = start_dt + timedelta(days=1)
            query = query.filter(
                Position.timestamp >= start_dt, Position.timestamp < end_dt
            )
        if bbox:
            query = query.filter(
                Position.longitude >= west,
                Position.longitude <= east,
                Position.latitude >= south,
                Position.latitude <= north,
            )
        if include_all:
            points = query.all()
        else:
            points = query.order_by(db.func.random()).limit(limit).all()
        features = []
        for p in points:
            features.append({
                'type': 'Feature',
                'id': str(p.id),
                'properties': {
                    'timestamp': p.timestamp.isoformat(),
                    'battery_level': (
                        int(p.battery_level) if p.battery_level is not None else None
                    ),
                },
                'geometry': {
                    'type': 'Point',
                    'coordinates': [p.longitude, p.latitude],
                },
            })

        return {'type': 'FeatureCollection', 'features': features}

    @app.route('/equipment/<int:equipment_id>/tracks.geojson')
    @login_required
    def equipment_tracks_geojson(equipment_id):
        from flask import abort
        eq = db.session.get(Equipment, equipment_id)
        if not eq:
            abort(404)
        bbox = request.args.get('bbox')
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        from shapely import wkt
        from shapely.geometry import box
        bbox_geom = None
        if bbox:
            west, south, east, north = [float(x) for x in bbox.split(',')]
            bbox_geom = box(west, south, east, north)
        features = []
        query = Track.query.filter_by(equipment_id=equipment_id)
        if start_str or end_str:
            start_d = date.fromisoformat(start_str) if start_str else None
            end_d = date.fromisoformat(end_str) if end_str else None
            if start_d is not None:
                start_dt = datetime.combine(start_d, datetime.min.time())
                query = query.filter(Track.end_time >= start_dt)
            if end_d is not None:
                end_dt = datetime.combine(
                    end_d + timedelta(days=1), datetime.min.time()
                )
                query = query.filter(Track.start_time < end_dt)
        elif year is not None:
            start_dt = datetime(year, 1, 1)
            end_dt = datetime(year + 1, 1, 1)
            if month is not None:
                start_dt = datetime(year, month, 1)
                end_dt = (
                    datetime(year + 1, 1, 1)
                    if month == 12
                    else datetime(year, month + 1, 1)
                )
                if day is not None:
                    start_dt = datetime(year, month, day)
                    end_dt = start_dt + timedelta(days=1)
            query = query.filter(
                Track.start_time < end_dt, Track.end_time >= start_dt
            )
        tracks = query.all()
        for t in tracks:
            geom = wkt.loads(t.line_wkt)
            if bbox_geom and not geom.intersects(bbox_geom):
                continue
            features.append({
                'type': 'Feature',
                'id': str(t.id),
                'properties': {
                    'start_time': (
                        t.start_time.isoformat() if t.start_time else None
                    ),
                    'end_time': (
                        t.end_time.isoformat() if t.end_time else None
                    ),
                },
                'geometry': geom.__geo_interface__,
            })
        return {'type': 'FeatureCollection', 'features': features}

    # Planification de la tâche quotidienne

    def scheduled_job():
        with app.app_context():
            zone.analyse_quotidienne()

    def poll_latest_positions():
        with app.app_context():
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                window_start = now - timedelta(minutes=2)
                for eq in Equipment.query.all():
                    # Only poll devices backed by Traccar
                    if not getattr(eq, 'id_traccar', None):
                        continue
                    try:
                        positions = zone.fetch_positions(eq.id_traccar, window_start, now)
                    except Exception:
                        app.logger.exception("Live fetch failed for %s", eq.name)
                        continue
                    latest_ts = None
                    for p in positions:
                        try:
                            ts = datetime.fromisoformat(p['deviceTime'].replace('Z', '+00:00')).replace(tzinfo=None)
                        except Exception:
                            continue
                        existing = Position.query.filter_by(
                            equipment_id=eq.id,
                            latitude=p.get('latitude'),
                            longitude=p.get('longitude'),
                            timestamp=ts,
                        ).first()
                        if not existing:
                            batt_val = (p.get('attributes') or {}).get('batteryLevel')
                            if batt_val is None:
                                batt_val = (p.get('attributes') or {}).get('battery')
                            p_obj = Position(
                                equipment_id=eq.id,
                                latitude=p.get('latitude'),
                                longitude=p.get('longitude'),
                                timestamp=ts,
                            )
                            if batt_val is not None:
                                try:
                                    b = float(batt_val)
                                    if b <= 1:
                                        b *= 100
                                    p_obj.battery_level = b
                                except (TypeError, ValueError):
                                    pass
                            db.session.add(p_obj)
                        batt_val = (p.get('attributes') or {}).get('batteryLevel')
                        if batt_val is None:
                            batt_val = (p.get('attributes') or {}).get('battery')
                        if batt_val is not None:
                            try:
                                batt_float = float(batt_val)
                                if batt_float <= 1:
                                    batt_float *= 100
                                eq.battery_level = batt_float
                                app.logger.info(
                                    "Device %s battery at %.0f%%",
                                    eq.name or eq.id_traccar,
                                    eq.battery_level,
                                )
                            except (TypeError, ValueError):
                                app.logger.info(
                                    "Ignoring invalid battery level %r for device %s",
                                    batt_val,
                                    eq.name or eq.id_traccar,
                                )
                        if latest_ts is None or ts > latest_ts:
                            latest_ts = ts
                    if latest_ts is not None:
                        eq.last_position = latest_ts
                db.session.commit()
            except Exception:
                app.logger.exception("Unexpected error during live polling")

    @app.route('/equipment/<int:equipment_id>/export.csv')
    @login_required
    def equipment_export_csv(equipment_id):
        from flask import abort, Response
        import csv
        import io
        eq = db.session.get(Equipment, equipment_id)
        if not eq:
            abort(404)

        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        show_all = request.args.get('show') == 'all'

        start_dt = None
        end_dt = None
        if start_str or end_str:
            start_d = date.fromisoformat(start_str) if start_str else None
            end_d = date.fromisoformat(end_str) if end_str else None
            if start_d is not None:
                start_dt = datetime.combine(start_d, datetime.min.time())
            if end_d is not None:
                end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time())
        elif year is not None:
            start_dt = datetime(year, 1, 1)
            end_dt = datetime(year + 1, 1, 1)
            if month is not None:
                start_dt = datetime(year, month, 1)
                end_dt = (
                    datetime(year + 1, 1, 1)
                    if month == 12
                    else datetime(year, month + 1, 1)
                )
                if day is not None:
                    start_dt = datetime(year, month, day)
                    end_dt = start_dt + timedelta(days=1)
        elif show_all:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            start_dt = datetime(now.year, 1, 1)
            end_dt = now

        # Prepare CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["latitude", "longitude", "timestamp", "battery_level"]) 

        if getattr(eq, 'id_traccar', None):
            # Fetch from Traccar directly to include attributes like battery.
            if start_dt is None or end_dt is None:
                # Fallback to last day if no range specified
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                start_dt = datetime(now.year, now.month, now.day)
                end_dt = start_dt + timedelta(days=1)
            try:
                positions = zone.fetch_positions(eq.id_traccar, start_dt, end_dt)
            except Exception:
                positions = []
            for p in positions:
                lat = p.get('latitude')
                lon = p.get('longitude')
                ts_raw = p.get('deviceTime') or p.get('fixTime')
                try:
                    ts = datetime.fromisoformat((ts_raw or '').replace('Z', '+00:00'))
                except Exception:
                    ts = None
                batt_val = (p.get('attributes') or {}).get('batteryLevel')
                if batt_val is None:
                    batt_val = (p.get('attributes') or {}).get('battery')
                batt = None
                if batt_val is not None:
                    try:
                        batt = float(batt_val)
                        if batt <= 1:
                            batt *= 100
                    except (TypeError, ValueError):
                        batt = None
                if lat is None or lon is None or ts is None:
                    continue
                writer.writerow([lat, lon, ts.isoformat(), batt if batt is not None else ""])            
        else:
            # Export from local DB positions (OsmAnd or stored).
            query = Position.query.filter_by(equipment_id=eq.id)
            if start_dt is not None:
                query = query.filter(Position.timestamp >= start_dt)
            if end_dt is not None:
                query = query.filter(Position.timestamp < end_dt)
            for p in query.order_by(Position.timestamp.asc()).all():
                writer.writerow([
                    p.latitude,
                    p.longitude,
                    p.timestamp.isoformat() if p.timestamp else "",
                    (int(p.battery_level) if p.battery_level is not None else ""),
                ])

        csv_data = output.getvalue()
        output.close()
        filename = f"equipment_{equipment_id}_points.csv"
        resp = Response(csv_data, mimetype='text/csv; charset=utf-8')
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    if start_scheduler and os.environ.get("START_SCHEDULER", "1") != "0":
        with app.app_context():
            # Assurer que la base est prête avant l'analyse initiale
            db.create_all()
            upgrade_db()
            cfg = Config.query.first()
            hour = cfg.analysis_hour if cfg else 2

        scheduler.add_job(
            scheduled_job, trigger='cron', hour=hour, id='daily_analysis'
        )
        scheduler.add_job(
            poll_latest_positions, trigger='interval', minutes=1, id='live_positions'
        )
        scheduler.start()

    setattr(app, "poll_latest_positions", poll_latest_positions)

    def initial_analysis():
        with app.app_context():
            # Ensure DB is usable
            try:
                Equipment.query.all()
            except Exception:
                return
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            start_of_year = datetime(now.year, 1, 1)
            # Skip if we already have zones for this year
            try:
                existing = (
                    DailyZone.query
                    .filter(DailyZone.date >= start_of_year.date())
                    .count()
                )
            except Exception:
                existing = 0
            if existing > 0:
                app.logger.info(
                    "Initial analysis skipped: %d zones already present "
                    "this year",
                    existing,
                )
                return
            for eq in Equipment.query.all():
                if getattr(eq, 'id_traccar', None):
                    zone.process_equipment(eq, since=start_of_year)
                else:
                    zone.recalculate_hectares_from_positions(eq.id, since_date=start_of_year)

    if run_initial_analysis and not os.environ.get("SKIP_INITIAL_ANALYSIS"):
        initial_analysis()

    @app.after_request
    def set_security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        resp.headers.setdefault('Permissions-Policy', 'geolocation=(), camera=(), microphone=()')
        # HSTS uniquement en HTTPS ou si forcé
        if request.is_secure or os.environ.get('FORCE_HTTPS') == '1':
            resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        # CSP permissive mais utile; ajuster au besoin
        csp = (
            "default-src 'self' https: data: blob; "
            "script-src 'self' https: 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' https: 'unsafe-inline'; "
            "img-src 'self' https: data: blob; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'"
        )
        resp.headers.setdefault('Content-Security-Policy', csp)
        return resp

    return app


if __name__ == '__main__':
    app = create_app()
    debug = os.environ.get('FLASK_DEBUG') == '1'
    host = '0.0.0.0'
    app.run(debug=debug, host=host)
