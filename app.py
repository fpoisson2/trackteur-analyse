import os

from flask import Flask, render_template, request, redirect, url_for
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, User, Equipment, Position, DailyZone
import zone

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
        if request.method == 'POST':
            selected = request.form.getlist('equip_ids')
            for dev in devices:
                if str(dev['id']) in selected:
                    token_api = request.form.get(f'token_{dev["id"]}')
                    eq = Equipment.query.filter_by(id_traccar=dev['id']).first()
                    if not eq:
                        eq = Equipment(id_traccar=dev['id'], name=dev['name'], token_api=token_api)
                        db.session.add(eq)
                    else:
                        eq.name = dev['name']
                        eq.token_api = token_api
            db.session.commit()
            return redirect(url_for('admin'))
        return render_template('admin.html', devices=devices)

    @app.route('/', methods=['GET', 'POST'])
    @login_required
    def index():
        equipments = Equipment.query.all()
        message = None
        if request.method == 'POST':
            selected = request.form.getlist('equip_ids')
            for eq in equipments:
                if str(eq.id) in selected:
                    zone.analyser_equipement(eq)
            message = f'Analyse lancée pour {len(selected)} équipement(s)'
        return render_template('index.html', equipments=equipments, message=message)

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
            multi = poly_list[0]
            for poly in poly_list[1:]:
                multi = multi.union(poly)
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
