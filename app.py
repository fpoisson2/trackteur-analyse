import os
import logging
import threading

import requests
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Equipment, Position, Config, Track
import zone

from datetime import datetime, date, timedelta
from typing import Iterable, Any
from werkzeug.datastructures import MultiDict

reanalysis_progress = {
    "running": False,
    "current": 0,
    "total": 0,
    "equipment": "",
}


def create_app():
    app = Flask(__name__)
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
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'

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
                app._db_init = True

    @app.before_request
    def ensure_setup():
        allowed = {'setup', 'static'}
        if request.endpoint in allowed:
            return
        if User.query.count() == 0:
            return redirect(url_for('setup'))
        if Config.query.count() == 0:
            if current_user.is_authenticated or request.endpoint != 'login':
                return redirect(url_for('setup'))
        if Equipment.query.count() == 0:
            if current_user.is_authenticated or request.endpoint != 'login':
                return redirect(url_for('setup'))

    @login_manager.user_loader
    def load_user(user_id):
        """Retrieve a user for Flask-Login without legacy API warnings."""
        return db.session.get(User, int(user_id))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                login_user(user)
                return redirect(url_for('index'))
            error = 'Nom d’utilisateur ou mot de passe incorrect'
        return render_template('login.html', error=error)

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        """Assistant de première configuration."""
        # Détermination de l'étape
        if User.query.count() == 0:
            step = 1
        elif Config.query.count() == 0:
            step = 2
        elif Equipment.query.count() == 0:
            step = 3
        else:
            step = 4

        if step == 1:
            if request.method == 'POST':
                username = request.form.get('username')
                password = request.form.get('password')
                if username and password:
                    admin = User(username=username, is_admin=True)
                    admin.set_password(password)
                    db.session.add(admin)
                    db.session.commit()
                    return redirect(url_for('setup'))
            return render_template('setup_step1.html')

        if step == 2:
            if request.method == 'POST':
                url = request.form.get('base_url')
                token = request.form.get('token')
                if url and token:
                    cfg = Config(traccar_url=url, traccar_token=token)
                    db.session.add(cfg)
                    db.session.commit()
                    return redirect(url_for('setup'))
            return render_template('setup_step2.html')

        if step == 3:
            error = None
            try:
                devices = zone.fetch_devices()
            except requests.exceptions.HTTPError as exc:
                app.logger.error("Failed to fetch devices: %s", exc)
                devices = []
                error = (
                    "Impossible de récupérer les équipements. "
                    "Vérifiez le token ou l'URL."
                )
            if request.method == 'POST':
                ids = {int(x) for x in request.form.getlist('equip_ids')}
                cfg = Config.query.first()
                for dev in devices:
                    if dev['id'] in ids:
                        eq = Equipment(
                            id_traccar=dev['id'],
                            name=dev['name'],
                            token_api=cfg.traccar_token,
                        )
                        db.session.add(eq)
                db.session.commit()
                return redirect(url_for('setup'))
            return render_template(
                'setup_step3.html', devices=devices, error=error
            )

        # step 4
        now = datetime.utcnow()
        start_of_year = datetime(now.year, 1, 1)
        processed = []
        for eq in Equipment.query.all():
            zone.process_equipment(eq, since=start_of_year)
            processed.append(eq.name)
        return render_template('setup_step4.html', devices=processed)

    def save_config(
        form: MultiDict[str, str], devices: Iterable[dict[str, Any]]
    ) -> None:
        """Persist configuration parameters and selected devices."""
        token_global = form.get('token_global')
        base_url = form.get('base_url')
        checked_ids = {int(x) for x in form.getlist('equip_ids')}
        eps = form.get('eps_meters')
        min_surface = form.get('min_surface')
        alpha = form.get('alpha_shape')

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
        else:
            cfg = Config(
                traccar_url=base_url or "",
                traccar_token=token_global or "",
                eps_meters=float(eps) if eps else 25.0,
                min_surface_ha=float(min_surface) if min_surface else 0.1,
                alpha=float(alpha) if alpha else 0.02,
            )
            db.session.add(cfg)

        for dev in devices:
            if dev['id'] in checked_ids:
                eq = Equipment.query.filter_by(id_traccar=dev['id']).first()
                if not eq:
                    eq = Equipment(id_traccar=dev['id'])
                    db.session.add(eq)
                eq.name = dev['name']
                eq.token_api = token_global
        db.session.commit()

    @app.route('/admin', methods=['GET', 'POST'])
    @login_required
    def admin():
        if not current_user.is_admin:
            return redirect(url_for('index'))

        cfg = Config.query.first()
        message = request.args.get('msg')
        error = None
        try:
            devices = zone.fetch_devices()
        except requests.exceptions.HTTPError as exc:
            app.logger.error("Device fetch failed: %s", exc)
            devices = []
            error = (
                "Impossible de récupérer les équipements. "
                "Vérifiez le token ou l'URL."
            )
        followed = Equipment.query.all()
        selected_ids = {e.id_traccar for e in followed}

        if request.method == 'POST':
            save_config(request.form, devices)
            cfg = Config.query.first()
            followed = Equipment.query.all()
            selected_ids = {e.id_traccar for e in followed}
            message = "Configuration enregistrée !"

        # 👉 Pré‑remplir avec le token du premier équipement si possible
        existing_token = cfg.traccar_token if cfg else ""
        existing_url = cfg.traccar_url if cfg else ""
        existing_eps = cfg.eps_meters if cfg else 25.0
        existing_surface = cfg.min_surface_ha if cfg else 0.1
        existing_alpha = cfg.alpha if cfg else 0.02

        return render_template(
            'admin.html',
            devices=devices,
            selected_ids=selected_ids,
            existing_token=existing_token,
            existing_url=existing_url,
            existing_eps=existing_eps,
            existing_surface=existing_surface,
            existing_alpha=existing_alpha,
            message=message,
            error=error
        )

    @app.route('/reanalyze_all', methods=['POST', 'GET'])
    @login_required
    def reanalyze_all():
        if not current_user.is_admin:
            return redirect(url_for('index'))
        if reanalysis_progress["running"]:
            return redirect(url_for('admin', msg="Analyse déjà en cours"))
        if request.method == 'POST' and request.form:
            try:
                devices = zone.fetch_devices()
            except requests.exceptions.HTTPError:
                return redirect(
                    url_for(
                        'admin',
                        msg="Erreur lors de la récupération des équipements",
                    )
                )
            save_config(request.form, devices)

        equipments = Equipment.query.all()
        reanalysis_progress.update(
            {
                "running": True,
                "current": 0,
                "total": len(equipments),
                "equipment": "",
            }
        )

        def run() -> None:
            with app.app_context():
                now = datetime.utcnow()
                start_of_year = datetime(now.year, 1, 1)
                for idx, eq in enumerate(equipments, start=1):
                    reanalysis_progress["equipment"] = eq.name
                    zone.process_equipment(eq, since=start_of_year)
                    reanalysis_progress["current"] = idx
                reanalysis_progress["running"] = False
                reanalysis_progress["equipment"] = ""

        threading.Thread(target=run, daemon=True).start()
        return redirect(
            url_for('admin', msg="Analyse relancée en arrière-plan")
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
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'add':
                username = request.form.get('username')
                password = request.form.get('password')
                role = request.form.get('role')
                if username and password:
                    if User.query.filter_by(username=username).first():
                        message = "Utilisateur déjà existant"
                    else:
                        user = User(
                            username=username, is_admin=(role == 'admin')
                        )
                        user.set_password(password)
                        db.session.add(user)
                        db.session.commit()
                        message = "Utilisateur ajouté"
            elif action == 'reset':
                uid = request.form.get('user_id')
                password = request.form.get('password')
                user = db.session.get(User, int(uid)) if uid else None
                if user and password:
                    user.set_password(password)
                    db.session.commit()
                    message = "Mot de passe réinitialisé"
            elif action == 'delete':
                uid = request.form.get('user_id')
                user = db.session.get(User, int(uid)) if uid else None
                if user and user != current_user:
                    db.session.delete(user)
                    db.session.commit()
                    message = "Utilisateur supprimé"

        users = User.query.all()
        return render_template('users.html', users=users, message=message)

    @app.route('/')
    @login_required
    def index():
        # 1) Récupération des équipements
        equipments = Equipment.query.all()
        message = None

        # 2) Plus de lancement manuel d'analyse

        # 3) Préparation des données pour l’affichage
        equipment_data = []
        now = datetime.utcnow()
        for eq in equipments:
            if eq.last_position:
                last = eq.last_position.strftime('%Y-%m-%d %H:%M:%S')
                delta = now - eq.last_position
                delta_seconds = delta.total_seconds()
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                delta_str = f"{delta.days} j {hours} h {minutes} min"
            else:
                last = None
                delta_seconds = None
                delta_str = "–"

            distance_km = (eq.distance_between_zones or 0) / 1000
            rel_hectares = zone.calculate_relative_hectares(eq.id)
            ratio_eff = eq.total_hectares / distance_km if distance_km else 0.0

            equipment_data.append({
                "id": eq.id,
                "name": eq.name,
                "last_seen": last,
                "total_hectares": round(eq.total_hectares or 0, 2),
                "relative_hectares": round(rel_hectares, 2),
                "distance_km": round(distance_km, 2),
                "delta_seconds": delta_seconds,
                "ratio_eff": ratio_eff,
                "delta_str": delta_str,
            })

        # Normalisation des critères
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

        return render_template(
            'index.html',
            equipment_data=equipment_data,
            message=message
        )

    @app.route('/equipment/<int:equipment_id>')
    @login_required
    def equipment_detail(equipment_id):
        eq = Equipment.query.get_or_404(equipment_id)
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        day = request.args.get('day', type=int)
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        start_date = date.fromisoformat(start_str) if start_str else None
        end_date = date.fromisoformat(end_str) if end_str else None
        show_all = request.args.get('show') == 'all'

        agg_all = zone.get_aggregated_zones(equipment_id)
        dates = {
            date.fromisoformat(d)
            for z in agg_all
            for d in z.get("dates", [])
        }

        all_tracks = Track.query.filter_by(equipment_id=equipment_id).all()
        track_dates = set()
        for t in all_tracks:
            current = t.start_time.date()
            last = t.end_time.date()
            while current <= last:
                track_dates.add(current)
                current += timedelta(days=1)
        dates.update(track_dates)
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
        for z in agg_period:
            full_idx = next(
                (
                    i
                    for i, full in enumerate(agg_all)
                    if set(z.get("ids", [])) == set(full.get("ids", []))
                ),
                None,
            )
            if full_idx is None:
                continue
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

        sorted_dates = sorted(dates)
        available_dates = [d.isoformat() for d in sorted_dates]

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
        )

    @app.route('/equipment/<int:equipment_id>/zones.geojson')
    @login_required
    def equipment_zones_geojson(equipment_id):
        Equipment.query.get_or_404(equipment_id)
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
            full_idx = next(
                (
                    i
                    for i, full in enumerate(agg_all)
                    if set(z.get("ids", [])) <= set(full.get("ids", []))
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
        Equipment.query.get_or_404(equipment_id)
        bbox = request.args.get('bbox')
        limit = int(request.args.get('limit', 5000))
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
        eq = Equipment.query.get_or_404(equipment_id)
        if Track.query.filter_by(equipment_id=equipment_id).count() == 0:
            zone.process_equipment(eq)
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

    # Planification de la tâche quotidienne à 2h du matin
    scheduler = BackgroundScheduler()

    def scheduled_job():
        with app.app_context():
            zone.analyse_quotidienne()

    scheduler.add_job(scheduled_job, trigger='cron', hour=2)
    scheduler.start()

    # Assurer que la base est prête avant l'analyse initiale
    with app.app_context():
        db.create_all()
        upgrade_db()

    def initial_analysis():
        with app.app_context():
            try:
                Equipment.query.all()
            except Exception:
                return
            now = datetime.utcnow()
            start_of_year = datetime(now.year, 1, 1)
            for eq in Equipment.query.all():
                zone.process_equipment(eq, since=start_of_year)

    if not os.environ.get("SKIP_INITIAL_ANALYSIS"):
        initial_analysis()

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0')
