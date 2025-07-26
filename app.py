from flask import Flask, render_template, request, send_from_directory, url_for
import os
from datetime import datetime

import zone

app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
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
def carte():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'carte_passages.html')


if __name__ == '__main__':
    app.run(debug=True)
