import os

from flask import Flask, render_template, request, redirect, url_for
from flask_login import (
    LoginManager,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Equipment, DailyZone, Config
import zone

from datetime import datetime


def create_app():
    app = Flask(__name__)
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

    if hasattr(app, "before_first_request"):
        @app.before_first_request
        def init_db() -> None:
            """Crée les tables de la base si nécessaire."""
            db.create_all()
    else:
        @app.before_request
        def init_db_once() -> None:
            """Fallback pour Flask 3 sans before_first_request."""
            if not getattr(app, "_db_init", False):
                db.create_all()
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

        devices = zone.fetch_devices()
        followed = Equipment.query.all()
        selected_ids = {e.id_traccar for e in followed}
        message = request.args.get('msg')

        if request.method == 'POST':
            token_global = request.form.get('token_global')
            checked_ids = {int(x) for x in request.form.getlist('equip_ids')}

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
        existing_token = followed[0].token_api if followed else ""

        return render_template(
            'admin.html',
            devices=devices,
            selected_ids=selected_ids,
            existing_token=existing_token,
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
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                delta_str = f"{delta.days} j {hours} h {minutes} min"
            else:
                last = None
                delta_str = "–"

            equipment_data.append({
                "id": eq.id,
                "name": eq.name,
                "last_seen": last,
                "total_hectares": round(eq.total_hectares or 0, 2),
                "relative_hectares": round(
                    zone.calculate_relative_hectares(eq.id), 2
                ),
                "distance_km": round(
                    (eq.distance_between_zones or 0) / 1000, 2
                ),
                "delta_str": delta_str
            })

        return render_template(
            'index.html',
            equipment_data=equipment_data,
            message=message
        )

    @app.route('/equipment/<int:equipment_id>')
    @login_required
    def equipment_detail(equipment_id):
        eq = Equipment.query.get_or_404(equipment_id)
        zones = (
            DailyZone.query
            .filter_by(equipment_id=equipment_id)
            .order_by(DailyZone.date.desc())
            .all()
        )
        # Génération de la carte Folium avec comptage des passages
        map_html = None
        if zones:
            from shapely import wkt

            daily = [
                {
                    "geometry": wkt.loads(z.polygon_wkt),
                    "dates": [str(z.date)]
                }
                for z in zones
            ]

            aggregated = zone.aggregate_overlapping_zones(daily)
            map_html = zone.generate_map_html(aggregated)
        return render_template(
            'equipment.html', equipment=eq, zones=zones, map_html=map_html
        )

    # Planification de la tâche quotidienne à 2h du matin
    scheduler = BackgroundScheduler()

    def scheduled_job():
        with app.app_context():
            zone.analyse_quotidienne()

    scheduler.add_job(scheduled_job, trigger='cron', hour=2)
    scheduler.start()

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
    app.run(debug=True)
