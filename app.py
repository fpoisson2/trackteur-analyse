import os

from flask import Flask, render_template, request, redirect, url_for
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Equipment, Position, DailyZone
import zone

from datetime import datetime

from shapely.ops import unary_union


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        'sqlite:///' + os.path.join(app.instance_path, 'trackteur.db')
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = 'login'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @app.route('/initdb')
    def initdb():
        db.create_all()
        # Création de l'utilisateur admin initial
        admin_user = os.environ.get('APP_USERNAME')
        admin_pass = os.environ.get('APP_PASSWORD')
        if admin_user and admin_pass:
            if not User.query.filter_by(username=admin_user).first():
                u = User(username=admin_user, is_admin=True)
                u.set_password(admin_pass)
                db.session.add(u)
                db.session.commit()
        return 'Base de données initialisée.'

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

    @app.route('/admin', methods=['GET', 'POST'])
    @login_required
    def admin():
        if not current_user.is_admin:
            return redirect(url_for('index'))

        devices = zone.fetch_devices()
        followed = Equipment.query.all()
        selected_ids = {e.id_traccar for e in followed}
        message = None

        if request.method == 'POST':
            token_global = request.form.get('token_global')
            checked_ids = {int(x) for x in request.form.getlist('equip_ids')}

            for dev in devices:
                if dev['id'] in checked_ids:
                    eq = Equipment.query.filter_by(id_traccar=dev['id']).first()
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

    @app.route('/', methods=['GET', 'POST'])
    @login_required
    def index():
        # 1) Récupération des équipements
        equipments = Equipment.query.all()
        message = None

        # 2) Si on clique sur "Analyser"
        if request.method == 'POST':
            equip_id = request.form.get('equip_id')
            if equip_id:
                eq = Equipment.query.get(int(equip_id))
                if eq:
                    # a) Calcul du début de l’année courante (UTC)
                    now = datetime.utcnow()
                    start_of_year = datetime(now.year, 1, 1)

                    # Nouveau : on transmet start_of_year en since
                    zone.process_equipment(eq, zone.BASE_URL, db, since=start_of_year)

                    message = (
                        f"Analyse lancée pour « {eq.name} » "
                        f"depuis le {start_of_year.date()}"
                    )
            else:
                message = "Aucun équipement sélectionné."

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
                "distance_km": round((eq.distance_between_zones or 0) / 1000, 2),
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
        zones = DailyZone.query.filter_by(equipment_id=equipment_id).order_by(DailyZone.date.desc()).all()
        # Génération de la carte Folium
        map_html = None
        if zones:
            from shapely import wkt
            from shapely.ops import transform as shp_transform
            import pyproj
            import folium

            transformer = pyproj.Transformer.from_crs(3857, 4326, always_xy=True)
            proj = transformer.transform
            # Centre de la carte
            poly_list = [shp_transform(proj, wkt.loads(z.polygon_wkt)) for z in zones]
            # 1) Réparer chaque polygone projeté
            fixed = [p.buffer(0) for p in poly_list]

            # 2) Fusionner proprement
            multi = unary_union(fixed)

            # 3) Centrer la carte
            ctr = multi.centroid
            m = folium.Map(location=[ctr.y, ctr.x], zoom_start=12)
            for z in zones:
                geom = shp_transform(proj, wkt.loads(z.polygon_wkt))
                gj = folium.GeoJson(
                    geom,
                    style_function=lambda x: {'fillColor': 'blue', 'color': 'black', 'weight': 1, 'fillOpacity': 0.5}
                )
                popup = folium.Popup(f"{z.date}: {z.surface_ha:.2f} ha", max_width=300)
                gj.add_child(popup)
                gj.add_to(m)
            map_html = m._repr_html_()
        return render_template('equipment.html', equipment=eq, zones=zones, map_html=map_html)

    # Planification de la tâche quotidienne à 2h du matin
    scheduler = BackgroundScheduler()
    def scheduled_job():
        with app.app_context():
            zone.analyse_quotidienne()

    scheduler.add_job(scheduled_job, trigger='cron', hour=2)
    scheduler.start()

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)
