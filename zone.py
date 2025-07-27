import os
import requests
import pandas as pd
import numpy as np
import base64
import warnings
from datetime import datetime, timedelta, timezone
from shapely.geometry import Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform as shp_transform
import pyproj
import alphashape
from sklearn.cluster import DBSCAN
import folium
from geopandas import GeoDataFrame

from models import db, Equipment, Position, DailyZone

# Ignorer avertissements GEOS
warnings.filterwarnings("ignore", "GEOS messages", UserWarning)

# 🔐 Paramètres de connexion au serveur Traccar
AUTH_TOKEN = os.environ.get("TRACCAR_TOKEN")
BASE_URL = os.environ.get("TRACCAR_BASE_URL")
if not AUTH_TOKEN or not BASE_URL:
    raise EnvironmentError(
        "Les variables d'environnement TRACCAR_TOKEN et TRACCAR_BASE_URL doivent être définies"
    )
AUTH_HEADER = {"Authorization": f"Bearer {AUTH_TOKEN}"}

# 📥 Paramètres d’analyse
DAYS = 60
EPS_METERS = 25
MIN_SURFACE_HA = 0.1  # ha
ALPHA = 0.02

# Préparer transformer Web Mercator → WGS84
_transformer = pyproj.Transformer.from_crs(3857, 4326, always_xy=True).transform


def fetch_devices():
    """Récupère la liste des dispositifs Traccar."""
    base = BASE_URL.rstrip('/')
    resp = requests.get(f"{base}/api/devices", headers=AUTH_HEADER)
    resp.raise_for_status()
    devices = resp.json()
    device_name = os.environ.get('TRACCAR_DEVICE_NAME')
    if device_name:
        devices = [d for d in devices if d.get('name') == device_name]
    return devices


def fetch_positions(device_id, from_dt, to_dt):
    """Récupère les positions JSON pour une plage donnée."""
    def fmt(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"deviceId": device_id, "from": fmt(from_dt), "to": fmt(to_dt)}
    base = BASE_URL.rstrip('/')
    resp = requests.get(f"{base}/api/positions", headers=AUTH_HEADER, params=params)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        if resp.status_code == 404:
            return []
        raise
    if resp.status_code == 204 or not resp.content.strip():
        return []
    try:
        return resp.json()
    except ValueError:
        print("Réponse inattendue :", resp.status_code, resp.text[:200])
        return []


def add_joggle(points, noise_scale=1e-6):
    noise = np.random.uniform(-noise_scale, noise_scale, size=(len(points), 2))
    return [(x + noise[i,0], y + noise[i,1]) for i,(x,y) in enumerate(points)]


def cluster_positions(positions):
    """Regroupe les points par jour et clusterise en zones de travail."""
    coords = [(p['latitude'], p['longitude'], p['deviceTime'][:10]) for p in positions]
    df = pd.DataFrame(coords, columns=['lat','lon','date'])
    df['geometry'] = df.apply(lambda r: Point(r.lon, r.lat), axis=1)
    gdf = GeoDataFrame(df, geometry='geometry', crs='EPSG:4326').to_crs(epsg=3857)
    zones = []
    for date, group in gdf.groupby('date'):
        if len(group) < 3:
            continue
        X = np.vstack([group.geometry.x, group.geometry.y]).T
        labels = DBSCAN(eps=EPS_METERS, min_samples=3).fit_predict(X)
        group['cluster'] = labels
        for lbl in set(labels):
            if lbl == -1:
                continue
            pts = [(pt.x, pt.y) for pt in group[group.cluster == lbl].geometry]
            pts = add_joggle(pts)
            poly = alphashape.alphashape(pts, ALPHA)
            poly = poly.buffer(0)
            if isinstance(poly, (Polygon, MultiPolygon)):
                poly_list = poly.geoms if isinstance(poly, MultiPolygon) else [poly]
                for sub in poly_list:
                    sub = sub.buffer(0)
                    if sub.area / 1e4 >= MIN_SURFACE_HA:
                        zones.append({'geometry': sub, 'dates': [date]})
    return zones


def aggregate_overlapping_zones(daily_zones):
    """Découpe et comptabilise les passages sur zones chevauchantes."""
    if not daily_zones:
        return []
    final = [daily_zones[0]]
    for zone in daily_zones[1:]:
        to_add_geom = zone['geometry']
        to_add_dates = zone['dates']
        next_final = []
        for existing in final:
            ex_geom = existing['geometry']
            diff = ex_geom.difference(to_add_geom)
            inter = ex_geom.intersection(to_add_geom)
            if not diff.is_empty:
                next_final.append({'geometry': diff, 'dates': existing['dates']})
            if not inter.is_empty:
                next_final.append({'geometry': inter, 'dates': existing['dates'] + to_add_dates})
            to_add_geom = to_add_geom.difference(ex_geom)
        if not to_add_geom.is_empty:
            next_final.append({'geometry': to_add_geom, 'dates': to_add_dates})
        final = next_final
    return final


def generate_map(zones, raw_points=None, output="static/carte.html"):
    """Créer une carte Folium avec zones et points GPS."""
    if not zones:
        print("Aucune zone à afficher.")
        return
    polys = []
    for z in zones:
        g = z['geometry']
        if isinstance(g, Polygon):
            polys.append(g)
        else:
            polys.extend(list(g.geoms))
    multi = MultiPolygon(polys)
    ctr = shp_transform(_transformer, multi.centroid)
    m = folium.Map(location=[ctr.y, ctr.x], zoom_start=12)
    if raw_points:
        for pt in raw_points:
            folium.CircleMarker(location=[pt.y, pt.x], radius=1, fill=True).add_to(m)
    colors = ['#2b83ba', '#abdda4', '#ffffbf', '#fdae61', '#d7191c']
    for z in zones:
        geom = shp_transform(_transformer, z['geometry'])
        if isinstance(geom, GeometryCollection):
            geoms = [g for g in geom.geoms if isinstance(g, Polygon)]
            geom = MultiPolygon(geoms)
        count = len(z['dates'])
        popup = folium.Popup(
            f"<b>Passages:</b> {count}<br><b>Surface:</b> {(z['geometry'].area/1e4):.2f} ha",
            max_width=250
        )
        idx = min(count - 1, len(colors) - 1)
        folium.GeoJson(
            geom,
            style_function=lambda x, col=colors[idx]: {'fillColor': col, 'color': 'black', 'weight': 1, 'fillOpacity': 0.6},
            popup=popup,
            tooltip=f"{count} passage(s)"
        ).add_to(m)
    m.save(output)



def process_equipment(eq, traccar_url, db, since=None):
    """Récupère, analyse et enregistre les zones journalières de l'équipement."""
    to_dt = datetime.utcnow()
    from_dt = since if since else to_dt - timedelta(days=1)

    # 1) Récupérer et stocker les positions
    positions = fetch_positions(eq.id_traccar, from_dt, to_dt)
    
    # ✅ CORRECTION : Trouver la position la plus récente AVANT de stocker
    latest_position_time = None
    if positions:
        # Trier les positions par timestamp pour trouver la plus récente
        sorted_positions = sorted(positions, key=lambda p: p['deviceTime'])
        latest_position_time = datetime.fromisoformat(
            sorted_positions[-1]['deviceTime'].replace('Z', '+00:00')
        )
    
    for p in positions:
        ts = datetime.fromisoformat(p['deviceTime'].replace('Z', '+00:00'))
        # Convertir en naive datetime pour stockage cohérent
        ts_naive = ts.replace(tzinfo=None)
        db.session.add(Position(equipment_id=eq.id, latitude=p['latitude'], longitude=p['longitude'], timestamp=ts_naive))
    
    # ✅ CORRECTION : Mettre à jour last_position seulement si on a des positions
    # et seulement si cette position est plus récente que l'actuelle
    if latest_position_time:
        # Convertir en naive datetime (UTC) pour la comparaison
        latest_naive = latest_position_time.replace(tzinfo=None)
        if not eq.last_position or latest_naive > eq.last_position:
            eq.last_position = latest_naive
    
    db.session.commit()

    # 2) Créer les clusters et zones (reste identique)
    daily = cluster_positions(positions)
    zones_by_date = {}
    for z in daily:
        date_str = z['dates'][0]
        zones_by_date.setdefault(date_str, []).append(z['geometry'])

    # 3) Pour chaque date, remplacer l'ancien par la surface totale agrégée
    for date_str, geoms in zones_by_date.items():
        date_obj = datetime.fromisoformat(date_str).date()
        DailyZone.query.filter_by(equipment_id=eq.id, date=date_obj).delete()
        daily_zones = [{'geometry': g, 'dates': [date_str]} for g in geoms]
        agg = aggregate_overlapping_zones(daily_zones)
        total_daily = sum(z['geometry'].area for z in agg) / 1e4
        dz = DailyZone(
                equipment_id=eq.id,
                date=date_obj,
                surface_ha=total_daily,
                polygon_wkt=daily_zones[0]['geometry'].wkt if daily_zones else None
            )
        db.session.add(dz)

    # 4) Mettre à jour les stats sur l'équipement
    total = sum(d.surface_ha for d in DailyZone.query.filter_by(equipment_id=eq.id))
    eq.total_hectares = total
    eq.distance_between_zones = 0.0
    # ✅ CORRECTION : last_position déjà mise à jour plus haut
    
    db.session.commit()




def analyse_quotidienne():
    """Tâche planifiée: analyse pour tous les équipements."""
    for eq in Equipment.query.all():
        process_equipment(eq, BASE_URL, db)


def analyser_equipement(eq, start_date=None):
    """Alias pour compatibilité: analyse d'un équipement donné depuis start_date."""
    process_equipment(eq, BASE_URL, db, since=start_date, date_ref=start_date)
