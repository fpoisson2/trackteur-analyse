import os
import logging

from flask import Flask, render_template, request, redirect, url_for, abort
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


def create_app():
    app = Flask(__name__)
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
        if "daily_zone" in tables:
            cols = [c["name"] for c in inspector.get_columns("daily_zone")]
            if "pass_count" not in cols:
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
            cols = [c["name"] for c in inspector.get_columns("position")]
            if "track_id" not in cols:
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
        return User.query.get(int(user_id))

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
            devices = zone.fetch_devices()
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
            return render_template('setup_step3.html', devices=devices)

        # step 4
        now = datetime.utcnow()
        start_of_year = datetime(now.year, 1, 1)
        processed = []
        for eq in Equipment.query.all():
            zone.process_equipment(eq, since=start_of_year)
            processed.append(eq.name)
        return render_template('setup_step4.html', devices=processed)

    @app.route('/admin', methods=['GET', 'POST'])
    @login_required
    def admin():
        if not current_user.is_admin:
            return redirect(url_for('index'))

        cfg = Config.query.first()
        devices = zone.fetch_devices()
        followed = Equipment.query.all()
        selected_ids = {e.id_traccar for e in followed}
        message = request.args.get('msg')

        if request.method == 'POST':
            token_global = request.form.get('token_global')
            base_url = request.form.get('base_url')
            checked_ids = {int(x) for x in request.form.getlist('equip_ids')}

            if cfg:
                if base_url:
                    cfg.traccar_url = base_url
                if token_global:
                    cfg.traccar_token = token_global
            else:
                cfg = Config(
                    traccar_url=base_url or "",
                    traccar_token=token_global or "",
                )
                db.session.add(cfg)

            for dev in devices:
                if dev['id'] in checked_ids:
                    eq = Equipment.query.filter_by(
                        id_traccar=dev['id']
                    ).first()
                    if not eq:
                        eq = Equipment(id_traccar=dev['id'])
                        db.session.add(eq)
                    eq.name = dev['name']
                    eq.token_api = token_global
            db.session.commit()

            # 🔄 mise à jour après commit
            followed = Equipment.query.all()
            selected_ids = {e.id_traccar for e in followed}
            message = "Configuration enregistrée !"

        # 👉 Pré‑remplir avec le token du premier équipement si possible
        existing_token = cfg.traccar_token if cfg else ""
        existing_url = cfg.traccar_url if cfg else ""

        return render_template(
            'admin.html',
            devices=devices,
            selected_ids=selected_ids,
            existing_token=existing_token,
            existing_url=existing_url,
            message=message
        )

    @app.route('/reanalyze_all', methods=['POST'])
    @login_required
    def reanalyze_all():
        if not current_user.is_admin:
            return redirect(url_for('index'))

        now = datetime.utcnow()
        start_of_year = datetime(now.year, 1, 1)
        for eq in Equipment.query.all():
            zone.process_equipment(eq, since=start_of_year)

        return redirect(url_for('admin', msg="Analyse complète terminée"))

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
                user = User.query.get(int(uid)) if uid else None
                if user and password:
                    user.set_password(password)
                    db.session.commit()
                    message = "Mot de passe réinitialisé"
            elif action == 'delete':
                uid = request.form.get('user_id')
                user = User.query.get(int(uid)) if uid else None
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

        if start_date and end_date:
            current = start_date
            while current <= end_date:
                if current not in dates:
                    abort(404)
                current += timedelta(days=1)

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
                    if set(z.get("ids", [])) <= set(full.get("ids", []))
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
                    "pass_count": len(info["dates"]),
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
        if start_date is not None:
            start_dt = datetime.combine(start_date, datetime.min.time())
            track_query = track_query.filter(Track.end_time >= start_dt)
        if end_date is not None:
            end_dt = datetime.combine(
                end_date + timedelta(days=1), datetime.min.time()
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

        prev_day_url = next_day_url = None
        if (
            start_date is not None
            and end_date is not None
            and start_date == end_date
        ):
            current = start_date
            if current in sorted_dates:
                idx = sorted_dates.index(current)
                if idx > 0:
                    pd = sorted_dates[idx - 1]
                    prev_day_url = url_for(
                        'equipment_detail',
                        equipment_id=equipment_id,
                        year=pd.year,
                        month=pd.month,
                        day=pd.day,
                    )
                if idx + 1 < len(sorted_dates):
                    nd = sorted_dates[idx + 1]
                    next_day_url = url_for(
                        'equipment_detail',
                        equipment_id=equipment_id,
                        year=nd.year,
                        month=nd.month,
                        day=nd.day,
                    )

        date_value = ""
        if start_date and end_date:
            if start_date == end_date:
                date_value = start_date.isoformat()
            else:
                date_value = (
                    f"{start_date.isoformat()} to {end_date.isoformat()}"
                )

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
            prev_day_url=prev_day_url,
            next_day_url=next_day_url,
            show_all=show_all,
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
        if start and end:
            agg_all = zone.get_aggregated_zones(equipment_id)
            available = {
                date.fromisoformat(d)
                for z in agg_all
                for d in z.get("dates", [])
            }
            cur = start
            while cur <= end:
                if cur not in available:
                    abort(404)
                cur += timedelta(days=1)
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
            geom = z['geometry']
            if bbox_geom and not geom.intersects(bbox_geom):
                continue
            if bbox_geom:
                geom = geom.intersection(bbox_geom)
            geom = zone.simplify_for_zoom(geom, zoom)
            geom_wgs = shp_transform(zone._transformer, geom)
            features.append({
                'type': 'Feature',
                'id': str(idx),
                'properties': {
                    'dates': z['dates'],
                    'dz_ids': z.get('ids', []),
                    'count': len(z['dates']),
                    'surface_ha': round(geom.area / 1e4, 2),
                },
                'geometry': geom_wgs.__geo_interface__,
            })

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
            if start_d and end_d:
                agg_all = zone.get_aggregated_zones(equipment_id)
                available = {
                    date.fromisoformat(d)
                    for z in agg_all
                    for d in z.get("dates", [])
                }
                cur = start_d
                while cur <= end_d:
                    if cur not in available:
                        abort(404)
                    cur += timedelta(days=1)
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
            if start_d and end_d:
                agg_all = zone.get_aggregated_zones(equipment_id)
                available = {
                    date.fromisoformat(d)
                    for z in agg_all
                    for d in z.get("dates", [])
                }
                cur = start_d
                while cur <= end_d:
                    if cur not in available:
                        abort(404)
                    cur += timedelta(days=1)
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
