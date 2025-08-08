import os
import logging
import requests  # type: ignore
import pandas as pd
import numpy as np
import warnings
from datetime import datetime, timedelta, date as dt_date
from shapely.geometry import (
    Point,
    Polygon,
    MultiPolygon,
    GeometryCollection,
    LineString,
)
from shapely.ops import transform as shp_transform
import pyproj
import alphashape
from sklearn.cluster import DBSCAN
import folium
from geopandas import GeoDataFrame

from typing import Dict, List, Optional, Tuple
from models import db, Equipment, Position, DailyZone, Config, Track

# Ignorer avertissements GEOS
warnings.filterwarnings("ignore", "GEOS messages", UserWarning)

logger = logging.getLogger(__name__)

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


# 📥 Paramètres d’analyse (valeurs par défaut)
DEFAULT_EPS_METERS = 25
DEFAULT_MIN_SURFACE_HA = 0.1  # ha
DEFAULT_ALPHA = 0.02


def _analysis_params():
    """Retourne les paramètres d'analyse des zones."""
    try:
        cfg = Config.query.first()
    except Exception:
        cfg = None
    eps = (
        cfg.eps_meters
        if cfg and cfg.eps_meters is not None
        else DEFAULT_EPS_METERS
    )
    min_surface = (
        cfg.min_surface_ha
        if cfg and cfg.min_surface_ha is not None
        else DEFAULT_MIN_SURFACE_HA
    )
    alpha = (
        cfg.alpha if cfg and cfg.alpha is not None else DEFAULT_ALPHA
    )
    return eps, min_surface, alpha
# Préparer transformer Web Mercator → WGS84


# Préparer transformer Web Mercator → WGS84
_transformer = pyproj.Transformer.from_crs(
    3857,
    4326,
    always_xy=True,
).transform
# Transformation inverse WGS84 -> Web Mercator
_to_webmerc = pyproj.Transformer.from_crs(
    4326,
    3857,
    always_xy=True,
).transform

# Cache pour les zones agrégées
# Clé: (equipment_id, start_date, end_date)
_AGG_CACHE: Dict[
    Tuple[int, Optional[dt_date], Optional[dt_date]], List[dict]
] = {}


def invalidate_cache(equipment_id: int) -> None:
    """Supprime les zones agrégées en cache pour l'équipement."""
    keys = [k for k in _AGG_CACHE if k[0] == equipment_id]
    for k in keys:
        _AGG_CACHE.pop(k, None)


def geom_bounds(geom):
    """Return bounding box (west, south, east, north) for a geometry."""
    if geom is None or geom.is_empty:
        return None
    geom_wgs = shp_transform(_transformer, geom)
    return geom_wgs.bounds


def _determine_period(
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    start: Optional[dt_date] = None,
    end: Optional[dt_date] = None,
) -> Tuple[Optional[dt_date], Optional[dt_date]]:
    """Calcule la période de filtrage à partir des paramètres fournis."""

    if start is not None or end is not None:
        return start, end

    if year is not None:
        if month is not None:
            if day is not None:
                d = dt_date(year, month, day)
                return d, d
            start_date = dt_date(year, month, 1)
            if month == 12:
                end_date = dt_date(year, 12, 31)
            else:
                end_date = dt_date(year, month + 1, 1) - timedelta(days=1)
            return start_date, end_date
        return dt_date(year, 1, 1), dt_date(year, 12, 31)

    return None, None


def get_aggregated_zones(
    equipment_id: int,
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
    start: Optional[dt_date] = None,
    end: Optional[dt_date] = None,
):
    """Retourne les zones agrégées pour un équipement, en cache.

    Les paramètres ``year``, ``month`` et ``day`` ou ``start``/``end``
    permettent de filtrer les zones journalières avant agrégation.
    Le cache est segmenté par période afin de conserver des performances
    acceptables même en cas de navigation temporelle.
    """

    start_date, end_date = _determine_period(
        year=year, month=month, day=day, start=start, end=end
    )

    key = (equipment_id, start_date, end_date)
    if key not in _AGG_CACHE:
        from shapely import wkt

        query = DailyZone.query.filter_by(equipment_id=equipment_id)
        if start_date is not None:
            query = query.filter(DailyZone.date >= start_date)
        if end_date is not None:
            query = query.filter(DailyZone.date <= end_date)

        zones = query.all()
        daily = [
            {
                "geometry": wkt.loads(z.polygon_wkt),
                "dates": [str(z.date)] * (z.pass_count or 1),
                "ids": [z.id],
            }
            for z in zones
            if z.polygon_wkt
        ]
        _AGG_CACHE[key] = aggregate_overlapping_zones(daily)
    return _AGG_CACHE[key]


def get_bounds_for_equipment(
    equipment_id: int, year: Optional[int] = None, month: Optional[int] = None
):
    """Return bounding box for aggregated zones in WGS84.

    The return format is ``(west, south, east, north)`` or ``None`` if no
    geometry is available.
    """
    agg = get_aggregated_zones(equipment_id, year=year, month=month)
    if not agg:
        return None

    from shapely.ops import unary_union

    union = unary_union([z["geometry"] for z in agg])
    if union.is_empty:
        return None

    union_wgs = shp_transform(_transformer, union)
    return union_wgs.bounds


def simplify_for_zoom(geom, zoom: int):
    """Simplifie la géométrie en fonction du niveau de zoom."""
    tolerance = max(1, 19 - int(zoom)) * 2  # en mètres
    return geom.simplify(tolerance, preserve_topology=True)


def fetch_devices():
    """Récupère la liste des dispositifs Traccar."""
    _, base = _get_credentials()
    url = f"{base.rstrip('/')}/api/devices"
    logger.debug("Fetching devices from %s", url)
    resp = requests.get(url, headers=_auth_header())
    resp.raise_for_status()
    devices = resp.json()
    logger.debug("Received %d devices", len(devices))
    device_name = os.environ.get('TRACCAR_DEVICE_NAME')
    if device_name:
        logger.debug("Filtering devices with name '%s'", device_name)
        devices = [d for d in devices if d.get('name') == device_name]
    logger.info("Fetched %d device(s)", len(devices))
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
    url = f"{base.rstrip('/')}/api/positions"
    logger.debug(
        "Fetching positions for %s between %s and %s",
        device_id,
        params["from"],
        params["to"],
    )
    resp = requests.get(url, headers=_auth_header(), params=params)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        if resp.status_code == 404:
            return []
        raise
    if resp.status_code == 204 or not resp.content.strip():
        return []
    try:
        data = resp.json()
        logger.debug("Received %d positions", len(data))
        return data
    except ValueError:
        logger.warning("Unexpected response %s", resp.text[:200])
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
        (
            p['latitude'],
            p['longitude'],
            p['deviceTime'][:10],
            p['deviceTime'],
        )
        for p in positions
    ]
    df = pd.DataFrame(coords, columns=['lat', 'lon', 'date', 'timestamp'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['geometry'] = df.apply(lambda r: Point(r.lon, r.lat), axis=1)
    gdf = (
        GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
        .to_crs(epsg=3857)
    )
    zones = []
    noise_by_date = {}
    eps, min_surface, alpha = _analysis_params()
    for date, group in gdf.groupby('date'):
        if len(group) < 3:
            continue
        X = np.vstack([group.geometry.x, group.geometry.y]).T
        labels = DBSCAN(eps=eps, min_samples=3).fit_predict(X)
        group['cluster'] = labels
        noise = group[group.cluster == -1].sort_values('timestamp')
        if not noise.empty:
            noise_by_date[date] = [
                (row.lon, row.lat, row.timestamp.to_pydatetime())
                for _, row in noise.iterrows()
            ]
        for lbl in set(labels):
            if lbl == -1:
                continue
            pts = [(pt.x, pt.y) for pt in group[group.cluster == lbl].geometry]
            pts = add_joggle(pts)
            poly = alphashape.alphashape(pts, alpha)
            poly = poly.buffer(0)
            if isinstance(poly, (Polygon, MultiPolygon)):
                poly_list = (
                    poly.geoms if isinstance(poly, MultiPolygon) else [poly]
                )
                for sub in poly_list:
                    sub = sub.buffer(0)
                    if sub.area / 1e4 >= min_surface:
                        zones.append({'geometry': sub, 'dates': [date]})
    return zones, noise_by_date


def aggregate_overlapping_zones(daily_zones):
    """Découpe et comptabilise les passages sur zones chevauchantes."""
    if not daily_zones:
        return []
    first = {
        'geometry': daily_zones[0]['geometry'],
        'dates': daily_zones[0]['dates'],
    }
    if 'ids' in daily_zones[0]:
        first['ids'] = daily_zones[0]['ids']
    final = [first]
    for zone in daily_zones[1:]:
        to_add_geom = zone['geometry']
        to_add_dates = zone['dates']
        to_add_ids = zone.get('ids')
        next_final = []
        for existing in final:
            ex_geom = existing['geometry']
            diff = ex_geom.difference(to_add_geom)
            inter = ex_geom.intersection(to_add_geom)
            if not diff.is_empty:
                entry = {'geometry': diff, 'dates': existing['dates']}
                if 'ids' in existing:
                    entry['ids'] = existing['ids']
                next_final.append(entry)
            if not inter.is_empty:
                entry = {
                    'geometry': inter,
                    'dates': existing['dates'] + to_add_dates,
                }
                if 'ids' in existing or to_add_ids:
                    entry['ids'] = []
                    if 'ids' in existing:
                        entry['ids'].extend(existing['ids'])
                    if to_add_ids:
                        entry['ids'].extend(to_add_ids)
                next_final.append(entry)
            to_add_geom = to_add_geom.difference(ex_geom)
        if not to_add_geom.is_empty:
            entry = {'geometry': to_add_geom, 'dates': to_add_dates}
            if to_add_ids:
                entry['ids'] = to_add_ids
            next_final.append(entry)
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
    """Calcule la distance totale entre les centroids des zones successives.

    Les polygones doivent être en projection métrique (EPSG:3857). Pour
    chaque polygone on calcule son centroïde, puis on additionne les
    distances euclidiennes entre centroids consécutifs. Le résultat est
    retourné en mètres.
    """
    if not polygons or len(polygons) < 2:
        return 0.0

    total = 0.0
    for a, b in zip(polygons, polygons[1:]):
        total += a.centroid.distance(b.centroid)
    return float(total)


def _boundary_intersection(
    inner: Tuple[float, float],
    outer: Tuple[float, float],
    polygons: List[Polygon],
):
    """Return intersection point on zone boundary between two coordinates.

    ``inner`` should lie inside one of the ``polygons`` and ``outer`` outside
    of it. The function returns the intersection point between the line segment
    joining them and the matching polygon's exterior. If no intersection is
    found, ``None`` is returned.
    """
    if not polygons:
        return None
    line = LineString([inner, outer])
    pt = Point(inner)
    for poly in polygons:
        if poly.contains(pt):
            inter = line.intersection(poly.exterior)
            if inter.is_empty:
                continue
            if isinstance(inter, Point):
                return inter
            if hasattr(inter, "geoms"):
                return list(inter.geoms)[0]
    return None


def process_equipment(eq, since=None):
    """Analyse et enregistre les zones journalières de l'équipement."""
    to_dt = datetime.utcnow()
    from_dt = since if since else to_dt - timedelta(days=1)
    logger.info(
        "Processing equipment %s (%s) from %s to %s",
        eq.id_traccar,
        eq.name,
        from_dt.isoformat(),
        to_dt.isoformat(),
    )

    # 1) Récupérer et stocker les positions
    positions = fetch_positions(eq.id_traccar, from_dt, to_dt)
    logger.debug("Fetched %d positions", len(positions))

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

    if not eq.last_position:
        latest = (
            Position.query.filter_by(equipment_id=eq.id)
            .order_by(Position.timestamp.desc())
            .first()
        )
        if latest:
            eq.last_position = latest.timestamp

    db.session.commit()

    # 2) Créer les clusters et zones
    daily, noise_points = cluster_positions(positions)
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

            # Enregistrer chaque morceau avec le nombre de passages
            for part in agg:
                dz = DailyZone(
                    equipment_id=eq.id,
                    date=date_obj,
                    surface_ha=part['geometry'].area / 1e4,
                    polygon_wkt=part['geometry'].wkt,
                    pass_count=len(part['dates']),
                )
                db.session.add(dz)

    # Créer les tracés à partir des points bruts hors zones
    for date_str, pts in noise_points.items():
        Track.query.filter(
            Track.equipment_id == eq.id,
            db.func.date(Track.start_time) == date_str,
        ).delete(synchronize_session=False)
        segments: List[List[tuple]] = []
        current: List[tuple] = []
        prev = None
        for lon, lat, ts in pts:
            if prev and (ts - prev).total_seconds() > 600:
                if current:
                    segments.append(current)
                current = []
            current.append((lon, lat, ts))
            prev = ts
        if current:
            segments.append(current)
        for seg in segments:
            prev_pos = (
                Position.query.filter(
                    Position.equipment_id == eq.id,
                    Position.timestamp < seg[0][2],
                )
                .order_by(Position.timestamp.desc())
                .first()
            )
            next_pos = (
                Position.query.filter(
                    Position.equipment_id == eq.id,
                    Position.timestamp > seg[-1][2],
                )
                .order_by(Position.timestamp)
                .first()
            )
            coords: List[tuple] = []
            if prev_pos:
                prev_polys = zones_by_date.get(
                    prev_pos.timestamp.date().isoformat(), []
                )
                start = _boundary_intersection(
                    (prev_pos.longitude, prev_pos.latitude),
                    (seg[0][0], seg[0][1]),
                    prev_polys,
                )
                if start:
                    coords.append((start.x, start.y, prev_pos.timestamp))
                else:
                    coords.append(
                        (
                            prev_pos.longitude,
                            prev_pos.latitude,
                            prev_pos.timestamp,
                        )
                    )
            coords.extend(seg)
            if next_pos:
                next_polys = zones_by_date.get(
                    next_pos.timestamp.date().isoformat(), []
                )
                end = _boundary_intersection(
                    (next_pos.longitude, next_pos.latitude),
                    (seg[-1][0], seg[-1][1]),
                    next_polys,
                )
                if end:
                    coords.append((end.x, end.y, next_pos.timestamp))
                else:
                    coords.append(
                        (
                            next_pos.longitude,
                            next_pos.latitude,
                            next_pos.timestamp,
                        )
                    )
            if len(coords) < 2:
                continue
            line = LineString([(x, y) for x, y, _ in coords])
            tr = Track(
                equipment_id=eq.id,
                start_time=coords[0][2],
                end_time=coords[-1][2],
                line_wkt=line.wkt,
            )
            db.session.add(tr)
            db.session.flush()
            for lon, lat, ts in seg:
                pos = Position.query.filter_by(
                    equipment_id=eq.id,
                    latitude=lat,
                    longitude=lon,
                    timestamp=ts,
                ).first()
                if pos:
                    pos.track_id = tr.id

    # 4) ✅ FIX : Recalculer le total sur TOUTES les zones existantes
    all_zones = (
        DailyZone.query.filter_by(equipment_id=eq.id)
        .order_by(DailyZone.date)
        .all()
    )

    from shapely import wkt
    from shapely.ops import unary_union

    zones_by_date = {}
    for dz in all_zones:
        if not dz.polygon_wkt:
            continue
        poly = wkt.loads(dz.polygon_wkt)
        zones_by_date.setdefault(dz.date, []).append(poly)

    total = 0.0
    daily_polys = []
    for _, polys in sorted(zones_by_date.items()):
        union = unary_union(polys) if len(polys) > 1 else polys[0]
        total += union.area / 1e4
        daily_polys.append(union)

    eq.total_hectares = total
    eq.distance_between_zones = calculate_distance_between_zones(daily_polys)

    logger.debug("Computed %d daily zones", len(all_zones))
    logger.info(
        "Totals for %s: %.2f ha, distance %.0f m",
        eq.name,
        eq.total_hectares,
        eq.distance_between_zones or 0.0,
    )

    db.session.commit()
    invalidate_cache(eq.id)


# ✅ NOUVELLE FONCTION : Recalculer proprement les hectares depuis la base
def recalculate_hectares_from_positions(equipment_id, since_date=None):
    """Recalcule les hectares depuis toutes les positions stockées."""
    eq = db.session.get(Equipment, equipment_id)
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
    daily, _ = cluster_positions(positions_formatted)
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

            for part in agg:
                dz = DailyZone(
                    equipment_id=equipment_id,
                    date=date_obj,
                    surface_ha=part['geometry'].area / 1e4,
                    polygon_wkt=part['geometry'].wkt,
                    pass_count=len(part['dates']),
                )
                db.session.add(dz)

    # Recalculer le total
    all_zones = (
        DailyZone.query.filter_by(equipment_id=equipment_id)
        .order_by(DailyZone.date)
        .all()
    )

    from shapely import wkt
    from shapely.ops import unary_union

    zones_by_date2 = {}
    for dz in all_zones:
        if not dz.polygon_wkt:
            continue
        poly = wkt.loads(dz.polygon_wkt)
        zones_by_date2.setdefault(dz.date, []).append(poly)

    total = 0.0
    daily_polys = []
    for _, polys in sorted(zones_by_date2.items()):
        union = unary_union(polys) if len(polys) > 1 else polys[0]
        total += union.area / 1e4
        daily_polys.append(union)

    eq.total_hectares = total

    db.session.commit()
    invalidate_cache(equipment_id)
    return total


def calculate_relative_hectares(equipment_id):
    """Calcule la surface unique (hectares relatifs) pour un équipement."""
    zones = DailyZone.query.filter_by(equipment_id=equipment_id).all()
    if not zones:
        return 0.0
    from shapely import wkt

    daily = [
        {
            "geometry": wkt.loads(z.polygon_wkt),
            "dates": [str(z.date)] * (z.pass_count or 1),
        }
        for z in zones
    ]
    aggregated = aggregate_overlapping_zones(daily)
    total = sum(z["geometry"].area for z in aggregated) / 1e4
    return total


# ✅ FONCTION DE DEBUG : Pour voir ce qui se passe
def debug_hectares_calculation(equipment_id):
    """Affiche des infos de debug sur le calcul des hectares."""
    eq = db.session.get(Equipment, equipment_id)
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
