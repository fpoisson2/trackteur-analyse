from flask import Flask, render_template, request, send_from_directory, url_for, redirect
import os
from datetime import datetime

import zone
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

# Flask-Login setup
login_manager = LoginManager()
login_manager.login_view = 'login'

# Admin credentials from environment variables
ADMIN_USERNAME = os.environ.get('APP_USERNAME')
ADMIN_PASSWORD = os.environ.get('APP_PASSWORD')
if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise EnvironmentError("Les variables d'environnement APP_USERNAME et APP_PASSWORD doivent être définies")


class User(UserMixin):
    def __init__(self, id):
        self.id = id


@login_manager.user_loader
def load_user(user_id):
    if user_id == ADMIN_USERNAME:
        return User(user_id)
    return None

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))
login_manager.init_app(app)


@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    error = None
    devices = zone.fetch_devices()
    results = False
    summary = {}
    if request.method == 'POST':
        device_id = request.form.get('device_id')
        from_str = request.form.get('from_date')
        to_str = request.form.get('to_date')
        try:
            from_dt = datetime.fromisoformat(from_str)
            to_dt = datetime.fromisoformat(to_str)
            positions = zone.fetch_positions(device_id, from_dt, to_dt)
            daily_zones = zone.cluster_positions(positions)
            aggregated = zone.aggregate_overlapping_zones(daily_zones)
            raw_points = zone.extract_raw_points(positions)
            map_path = os.path.join(app.root_path, 'static', 'carte_passages.html')
            zone.generate_map(aggregated, raw_points, output_file=map_path)
            total_area = sum(z['geometry'].area for z in aggregated) / 1e4
            summary = {
                'total_area': total_area,
                'zone_count': len(aggregated),
            }
            results = True
        except Exception as exc:
            error = str(exc)
    return render_template('index.html', devices=devices, results=results,
                           summary=summary, error=error)


@app.route('/carte')
@login_required
def carte():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'carte_passages.html')



@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            user = User(username)
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            error = 'Nom d’utilisateur ou mot de passe incorrect'
    return render_template('login.html', error=error)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(debug=True)
