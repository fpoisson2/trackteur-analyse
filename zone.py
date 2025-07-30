import os
import requests  # type: ignore
import pandas as pd
import numpy as np
import warnings
from datetime import datetime, timedelta, date as dt_date
from shapely.geometry import Point, Polygon, MultiPolygon, GeometryCollection
from shapely.ops import transform as shp_transform
import pyproj
import alphashape
from sklearn.cluster import DBSCAN
import folium
from geopandas import GeoDataFrame

from models import db, Equipment, Position, DailyZone, Config

# Ignorer avertissements GEOS
warnings.filterwarnings("ignore", "GEOS messages", UserWarning)

# 🔐 Paramètres de connexion au serveur Traccar


def _get_credentials():
    """Retourne le token et l'URL Traccar depuis la config ou l'env."""
    token = os.environ.get("TRACCAR_AUTH_TOKEN")
    base = os.environ.get("TRACCAR_BASE_URL")
    if not token or not base:
        cfg = Config.query.first()
        if cfg:
            token = cfg.traccar_token
            base = cfg.traccar_url
    if not token or not base:
        raise EnvironmentError(
            "TRACCAR_AUTH_TOKEN et TRACCAR_BASE_URL non configurés"
        )
    return token, base


def _auth_header():
    token, _ = _get_credentials()
    return {"Authorization": f"Bearer {token}"}


# 📥 Paramètres d’analyse
DAYS = 60
EPS_METERS = 25
MIN_SURFACE_HA = 0.1  # ha
ALPHA = 0.02

# Préparer transformer Web Mercator → WGS84
_transformer = pyproj.Transformer.from_crs(
    3857,
    4326,
    always_xy=True,
).transform


def fetch_devices():
    """Récupère la liste des dispositifs Traccar."""
    _, base = _get_credentials()
    url = f"{base.rstrip('/')}/api/devices"
    resp = requests.get(url, headers=_auth_header())
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
    params = {
        "deviceId": device_id,
        "from": fmt(from_dt),
        "to": fmt(to_dt),
    }
    _, base = _get_credentials()
    resp = requests.get(
        f"{base.rstrip('/')}/api/positions",
        headers=_auth_header(),
        params=params,
    )
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
    return [
        (x + noise[i, 0], y + noise[i, 1])
        for i, (x, y) in enumerate(points)
    ]


def cluster_positions(positions):
    """Regroupe les points par jour et clusterise en zones de travail."""
    coords = [
        (p['latitude'], p['longitude'], p['deviceTime'][:10])
        for p in positions
    ]
    df = pd.DataFrame(coords, columns=['lat', 'lon', 'date'])
    df['geometry'] = df.apply(lambda r: Point(r.lon, r.lat), axis=1)
    gdf = (
        GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
        .to_crs(epsg=3857)
    )
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
                poly_list = (
                    poly.geoms if isinstance(poly, MultiPolygon) else [poly]
                )
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
                next_final.append(
                    {'geometry': diff, 'dates': existing['dates']}
                )
            if not inter.is_empty:
                next_final.append(
                    {
                        'geometry': inter,
                        'dates': existing['dates'] + to_add_dates,
                    }
                )
            to_add_geom = to_add_geom.difference(ex_geom)
        if not to_add_geom.is_empty:
            next_final.append({'geometry': to_add_geom, 'dates': to_add_dates})
        final = next_final
    return final


def _build_map(zones, raw_points=None):
    """Construit un objet ``folium.Map`` pour les zones fournies."""
    if not zones:
        return None
    polys = []
    for z in zones:
        g = z["geometry"]
        if isinstance(g, Polygon):
            polys.append(g)
        elif isinstance(g, MultiPolygon):
            polys.extend([p for p in g.geoms])
        elif isinstance(g, GeometryCollection):
            polys.extend(
                [geom for geom in g.geoms if isinstance(geom, Polygon)]
            )
    if not polys:
        return None
    from shapely.ops import unary_union
    union = unary_union(polys)
    ctr = shp_transform(_transformer, union.centroid)
    m = folium.Map(location=[ctr.y, ctr.x], zoom_start=12)
    if raw_points:
        for pt in raw_points:
            folium.CircleMarker(
                location=[pt.y, pt.x], radius=1, fill=True
            ).add_to(m)
    colors = ['#2b83ba', '#abdda4', '#ffffbf', '#fdae61', '#d7191c']
    for idx_zone, z in enumerate(zones):
        geom = shp_transform(_transformer, z['geometry'])
        if isinstance(geom, GeometryCollection):
            geoms = [g for g in geom.geoms if isinstance(g, Polygon)]
            geom = MultiPolygon(geoms)
        count = len(z['dates'])
        dates_list = ", ".join(sorted(z['dates']))
        popup_text = (
            f"<b>Passages:</b> {count}<br>"
            f"<b>Surface:</b> {(z['geometry'].area/1e4):.2f} ha"
        )
        if dates_list:
            popup_text += f"<br><b>Dates:</b> {dates_list}"
        popup = folium.Popup(popup_text, max_width=250)
        color_idx = min(count - 1, len(colors) - 1)

        feature = {
            "type": "Feature",
            "id": str(idx_zone),
            "properties": {"dates": z['dates']},
            "geometry": geom.__geo_interface__,
        }

        folium.GeoJson(
            data=feature,
            style_function=lambda x, col=colors[color_idx]: {
                'fillColor': col,
                'color': 'black',
                'weight': 1,
                'fillOpacity': 0.6,
            },
            popup=popup,
            tooltip=f"{count} passage(s)",
        ).add_to(m)
    return m


def generate_map_html(zones, raw_points=None):
    """Retourne le code HTML d'une carte Folium pour les zones."""
    m = _build_map(zones, raw_points)
    if m is None:
        return None
    # ``Map.get_root().render()`` renvoie une page HTML complète, ce qui
    # n'est pas adapté à l'inclusion dans un gabarit existant. ``_repr_html_``
    # renvoie uniquement le fragment à insérer.
    return m._repr_html_()


def generate_map(zones, raw_points=None, output="static/carte.html"):
    """Créer un fichier HTML contenant la carte des zones."""
    html = generate_map_html(zones, raw_points)
    if html is None:
        print("Aucune zone à afficher.")
        return
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(html)


def calculate_distance_between_zones(polygons):
    """Calcule la distance totale entre les centroids des zones successives."""
    if not polygons or len(polygons) < 2:
        return 0.0

    total = 0.0
    for a, b in zip(polygons, polygons[1:]):
        total += a.centroid.distance(b.centroid)
    return float(total)


def process_equipment(eq, since=None):
    """Analyse et enregistre les zones journalières de l'équipement."""
    to_dt = datetime.utcnow()
    from_dt = since if since else to_dt - timedelta(days=1)

    # 1) Récupérer et stocker les positions
    positions = fetch_positions(eq.id_traccar, from_dt, to_dt)

    # ✅ FIX : Trouver la position la plus récente et gérer les timezones
    latest_position_time = None
    if positions:
        sorted_positions = sorted(positions, key=lambda p: p['deviceTime'])
        latest_position_time = datetime.fromisoformat(
            sorted_positions[-1]['deviceTime'].replace('Z', '+00:00')
        )

    for p in positions:
        ts = datetime.fromisoformat(p['deviceTime'].replace('Z', '+00:00'))
        # Convertir en naive datetime pour stockage cohérent
        ts_naive = ts.replace(tzinfo=None)
        # ✅ FIX : Vérifier si la position existe déjà pour éviter les doublons
        existing = Position.query.filter_by(
            equipment_id=eq.id,
            latitude=p['latitude'],
            longitude=p['longitude'],
            timestamp=ts_naive
        ).first()
        if not existing:
            db.session.add(Position(
                equipment_id=eq.id,
                latitude=p['latitude'],
                longitude=p['longitude'],
                timestamp=ts_naive
            ))

    # ✅ FIX : Mettre à jour last_position correctement
    if latest_position_time:
        latest_naive = latest_position_time.replace(tzinfo=None)
        if not eq.last_position or latest_naive > eq.last_position:
            eq.last_position = latest_naive

    db.session.commit()

    # 2) Créer les clusters et zones
    daily = cluster_positions(positions)
    zones_by_date = {}
    for z in daily:
        date_str = z['dates'][0]
        zones_by_date.setdefault(date_str, []).append(z['geometry'])

    # 3) ✅ FIX MAJEUR : Améliorer le calcul des zones journalières
    for date_str, geoms in zones_by_date.items():
        date_obj = datetime.fromisoformat(date_str).date()

        # Supprimer l'ancienne zone pour cette date
        DailyZone.query.filter_by(equipment_id=eq.id, date=date_obj).delete()

        if geoms:  # ✅ FIX : Vérifier qu'on a des géométries
            # Créer les zones avec leurs dates
            daily_zones = [{'geometry': g, 'dates': [date_str]} for g in geoms]

            # ✅ FIX : Appliquer l'agrégation des zones chevauchantes
            agg = aggregate_overlapping_zones(daily_zones)

            # Calculer la surface totale pour cette date
            total_daily = sum(z['geometry'].area for z in agg) / 1e4

            # ✅ FIX : Créer un polygon_wkt représentant l'union des zones
            from shapely.ops import unary_union
            all_geoms = [z['geometry'] for z in agg]
            if len(all_geoms) == 1:
                union_geom = all_geoms[0]
            else:
                union_geom = unary_union(all_geoms)

            # Créer la zone journalière
            dz = DailyZone(
                equipment_id=eq.id,
                date=date_obj,
                surface_ha=total_daily,
                polygon_wkt=union_geom.wkt
            )
            db.session.add(dz)

    # 4) ✅ FIX : Recalculer le total sur TOUTES les zones existantes
    all_zones = (
        DailyZone.query.filter_by(equipment_id=eq.id)
        .order_by(DailyZone.date)
        .all()
    )
    total = sum(d.surface_ha for d in all_zones)
    eq.total_hectares = total

    from shapely import wkt

    polygons = [
        wkt.loads(z.polygon_wkt)
        for z in all_zones
        if z.polygon_wkt
    ]
    eq.distance_between_zones = calculate_distance_between_zones(polygons)

    db.session.commit()


# ✅ NOUVELLE FONCTION : Recalculer proprement les hectares depuis la base
def recalculate_hectares_from_positions(equipment_id, since_date=None):
    """Recalcule les hectares depuis toutes les positions stockées."""
    eq = Equipment.query.get(equipment_id)
    if not eq:
        return None

    # Récupérer toutes les positions depuis since_date
    query = Position.query.filter_by(equipment_id=equipment_id)
    if since_date:
        query = query.filter(Position.timestamp >= since_date)

    positions_db = query.order_by(Position.timestamp).all()

    if not positions_db:
        return 0

    # Convertir en format compatible avec cluster_positions
    positions_formatted = []
    for pos in positions_db:
        positions_formatted.append({
            'latitude': pos.latitude,
            'longitude': pos.longitude,
            'deviceTime': pos.timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
        })

    # Recalculer les zones
    daily = cluster_positions(positions_formatted)
    zones_by_date = {}
    for z in daily:
        date_str = z['dates'][0]
        zones_by_date.setdefault(date_str, []).append(z['geometry'])

    # Nettoyer les anciennes zones et recalculer
    if since_date:
        DailyZone.query.filter(
            DailyZone.equipment_id == equipment_id,
            DailyZone.date >= since_date.date()
        ).delete()
    else:
        DailyZone.query.filter_by(equipment_id=equipment_id).delete()

    # Créer les nouvelles zones
    for date_str, geoms in zones_by_date.items():
        date_obj = datetime.fromisoformat(date_str).date()

        if geoms:
            daily_zones = [{'geometry': g, 'dates': [date_str]} for g in geoms]
            agg = aggregate_overlapping_zones(daily_zones)
            total_daily = sum(z['geometry'].area for z in agg) / 1e4

            from shapely.ops import unary_union
            all_geoms = [z['geometry'] for z in agg]
            union_geom = (
                unary_union(all_geoms) if len(all_geoms) > 1 else all_geoms[0]
            )

            dz = DailyZone(
                equipment_id=equipment_id,
                date=date_obj,
                surface_ha=total_daily,
                polygon_wkt=union_geom.wkt
            )
            db.session.add(dz)

    # Recalculer le total
    all_zones = DailyZone.query.filter_by(equipment_id=equipment_id).all()
    total = sum(d.surface_ha for d in all_zones)
    eq.total_hectares = total

    db.session.commit()
    return total


def calculate_relative_hectares(equipment_id, year=None):
    """Calcule la surface unique (hectares relatifs) pour un équipement.

    Si ``year`` est fourni, ne prend en compte que les zones de cette année.
    """
    query = DailyZone.query.filter_by(equipment_id=equipment_id)
    if year is not None:
        start = dt_date(int(year), 1, 1)
        end = dt_date(int(year) + 1, 1, 1)
        query = query.filter(DailyZone.date >= start, DailyZone.date < end)

    zones = query.all()
    if not zones:
        return 0.0

    from shapely import wkt

    daily = [
        {"geometry": wkt.loads(z.polygon_wkt), "dates": [str(z.date)]}
        for z in zones
    ]
    aggregated = aggregate_overlapping_zones(daily)
    total = sum(z["geometry"].area for z in aggregated) / 1e4
    return total


# ✅ FONCTION DE DEBUG : Pour voir ce qui se passe
def debug_hectares_calculation(equipment_id):
    """Affiche des infos de debug sur le calcul des hectares."""
    eq = Equipment.query.get(equipment_id)
    if not eq:
        print(f"Équipement {equipment_id} introuvable")
        return

    print(f"\n=== DEBUG HECTARES pour {eq.name} ===")

    # Stats des positions
    pos_count = Position.query.filter_by(equipment_id=equipment_id).count()
    print(f"Positions en base: {pos_count}")

    if pos_count > 0:
        first_pos = (
            Position.query
            .filter_by(equipment_id=equipment_id)
            .order_by(Position.timestamp.asc())
            .first()
        )
        last_pos = (
            Position.query
            .filter_by(equipment_id=equipment_id)
            .order_by(Position.timestamp.desc())
            .first()
        )
        print(f"Première position: {first_pos.timestamp}")
        print(f"Dernière position: {last_pos.timestamp}")

    # Stats des zones
    zones = (
        DailyZone.query.filter_by(equipment_id=equipment_id)
        .order_by(DailyZone.date)
        .all()
    )
    print(f"Zones journalières: {len(zones)}")

    if zones:
        print(f"Première zone: {zones[0].date} ({zones[0].surface_ha:.2f} ha)")
        print(
            f"Dernière zone: {zones[-1].date} "
            f"({zones[-1].surface_ha:.2f} ha)"
        )

        total_calculated = sum(z.surface_ha for z in zones)
        print(f"Total calculé: {total_calculated:.2f} ha")
        print(f"Total stocké: {eq.total_hectares:.2f} ha")

        # Répartition par mois
        monthly = {}
        for z in zones:
            month_key = f"{z.date.year}-{z.date.month:02d}"
            monthly[month_key] = monthly.get(month_key, 0) + z.surface_ha

        print("\nRépartition mensuelle:")
        for month, ha in sorted(monthly.items()):
            print(f"  {month}: {ha:.2f} ha")

    print("=" * 50)


def analyse_quotidienne():
    """Tâche planifiée: analyse pour tous les équipements."""
    for eq in Equipment.query.all():
        process_equipment(eq)


def analyser_equipement(eq, start_date=None):
    """Analyse un équipement donné à partir de start_date."""
    process_equipment(eq, since=start_date)
