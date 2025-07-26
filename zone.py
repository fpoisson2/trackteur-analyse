import requests
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta, timezone
# Ajout de union_all pour corriger le DeprecationWarning
from shapely.geometry import Point, Polygon, MultiPolygon, GeometryCollection
from shapely import union_all
from shapely.ops import transform
from sklearn.cluster import DBSCAN
import folium
import base64
from geopandas import GeoDataFrame
import pyproj
import warnings
import alphashape

# Ignore les avertissements de Shapely sur les polygones non valides qui peuvent apparaître lors des opérations de différence
warnings.filterwarnings("ignore", "GEOS messages", UserWarning)


# 🔐 Paramètres de connexion au serveur Traccar (à fournir via variables d'environnement)
AUTH_TOKEN = os.environ.get("TRACCAR_AUTH_TOKEN")
BASE_URL = os.environ.get("TRACCAR_BASE_URL")
DEVICE_NAME = os.environ.get("TRACCAR_DEVICE_NAME", "Tracteur 4")

# Vérification des variables d'environnement
if not AUTH_TOKEN or not BASE_URL:
    raise EnvironmentError(
        "Les variables d'environnement TRACCAR_AUTH_TOKEN et TRACCAR_BASE_URL doivent être définies"
    )

# Auth HTTP Bearer
AUTH_HEADER = {"Authorization": f"Bearer {AUTH_TOKEN}"}

# 📥 Paramètres d’analyse
DAYS = 60
EPS_METERS = 25
MIN_SURFACE_HA = 0.1
ALPHA = 0.02


# Période à analyser (pour usage direct, passer des plages personnalisées à fetch_positions)
# Fonctions d'accès à l'API Traccar
def fetch_devices():
    """Récupère la liste des dispositifs Traccar."""
    r = requests.get(f"{BASE_URL}/api/devices", headers=AUTH_HEADER)
    r.raise_for_status()
    return r.json()

def fetch_device_id():
    """Récupère l'ID du dispositif Traccar."""
    r = requests.get(f"{BASE_URL}/api/devices", headers=AUTH_HEADER)
    r.raise_for_status()
    for device in r.json():
        if device["name"].strip().lower() == DEVICE_NAME.strip().lower():
            return device["id"]
    raise Exception("Dispositif non trouvé")

def fetch_positions(device_id, from_dt, to_dt):
    """Récupère les positions pour une plage de dates donnée."""
    def fmt(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "deviceId": device_id,
        "from": fmt(from_dt),
        "to": fmt(to_dt),
    }
    r = requests.get(f"{BASE_URL}/api/positions", headers=AUTH_HEADER, params=params)
    r.raise_for_status()
    return r.json()

def add_joggle(points, noise_scale=1e-6):
    """Ajoute un léger bruit aux coordonnées pour éviter les erreurs de coplanarité."""
    noise = np.random.uniform(-noise_scale, noise_scale, size=(len(points), 2))
    return [(p[0] + noise[i, 0], p[1] + noise[i, 1]) for i, p in enumerate(points)]

def cluster_positions(positions):
    """Regroupe les points GPS en zones de travail pour chaque jour."""
    coords = [(p["latitude"], p["longitude"], p["deviceTime"][:10]) for p in positions]
    df = pd.DataFrame(coords, columns=["lat", "lon", "date"])
    df["geometry"] = df.apply(lambda r: Point(r["lon"], r["lat"]), axis=1)
    gdf = GeoDataFrame(df, geometry="geometry", crs="EPSG:4326").to_crs(epsg=3857)

    daily_zones = []
    for date, group in gdf.groupby("date"):
        if len(group) < 3: continue
        X = np.vstack([group.geometry.x, group.geometry.y]).T
        labels = DBSCAN(eps=EPS_METERS, min_samples=3).fit_predict(X)
        group = group.assign(cluster=labels)
        for lbl in set(labels):
            if lbl == -1: continue
            cl = group[group.cluster == lbl]
            # Utilisation d'alpha shape pour générer un polygone concave
            points = [(geom.x, geom.y) for geom in cl.geometry]
            points = add_joggle(points, noise_scale=1e-6)  # Ajout d'un léger bruit
            poly = alphashape.alphashape(points, ALPHA)
            if isinstance(poly, Polygon) and poly.area / 1e4 >= MIN_SURFACE_HA:
                daily_zones.append({
                    "geometry": poly,
                    "dates": [date],
                })
            elif isinstance(poly, MultiPolygon):
                # Si l'alpha shape produit un MultiPolygon, on garde seulement les polygones assez grands
                for sub_poly in poly.geoms:
                    if sub_poly.area / 1e4 >= MIN_SURFACE_HA:
                        daily_zones.append({
                            "geometry": sub_poly,
                            "dates": [date],
                        })
    return daily_zones

def aggregate_overlapping_zones(daily_zones):
    """
    Agrège les zones qui se superposent.
    Découpe les polygones pour créer des zones distinctes avec un comptage des passages.
    """
    if not daily_zones:
        return []

    final_zones = daily_zones[:1]
    
    for zone_to_add in daily_zones[1:]:
        next_final_zones = []
        geom_to_add = zone_to_add["geometry"]
        date_to_add = zone_to_add["dates"]

        for existing_zone in final_zones:
            existing_geom = existing_zone["geometry"]
            
            diff = existing_geom.difference(geom_to_add)
            intersection = existing_geom.intersection(geom_to_add)
            
            if not diff.is_empty:
                next_final_zones.append({
                    "geometry": diff,
                    "dates": existing_zone["dates"]
                })
            
            if not intersection.is_empty:
                next_final_zones.append({
                    "geometry": intersection,
                    "dates": existing_zone["dates"] + date_to_add
                })
            
            geom_to_add = geom_to_add.difference(existing_geom)

        if not geom_to_add.is_empty:
            next_final_zones.append({
                "geometry": geom_to_add,
                "dates": date_to_add
            })
            
        final_zones = next_final_zones
        
    return final_zones

proj = pyproj.Transformer.from_crs(3857, 4326, always_xy=True).transform

def extract_raw_points(positions):
    """Retourne une liste de Points (WGS84) pour l'affichage du tracé brut."""
    return [Point(p["longitude"], p["latitude"]) for p in positions]

def generate_map(zones, raw_points=None, output_file="static/carte_passages.html"):
    """Génère et enregistre une carte Folium avec les zones et les passages."""
    if not zones:
        print("❌ Aucune zone à afficher sur la carte.")
        return

    # CORRECTION : Aplatir la liste des géométries avant de créer le MultiPolygon
    # Ceci résout l'erreur "Sequences of multi-polygons are not valid arguments"
    all_polygons = []
    for z in zones:
        geom = z["geometry"]
        if isinstance(geom, Polygon):
            all_polygons.append(geom)
        elif isinstance(geom, MultiPolygon):
            all_polygons.extend(list(geom.geoms))

    if not all_polygons:
        print("❌ Aucune géométrie valide à afficher.")
        return

    multi = MultiPolygon(all_polygons)
    ctr_m = multi.centroid
    ctr = transform(proj, ctr_m)
    m = folium.Map(location=[ctr.y, ctr.x], zoom_start=15)

    if raw_points:
        for pt in raw_points:
            folium.CircleMarker(
                location=[pt.y, pt.x],
                radius=1,
                color="grey",
                fill=True,
                fill_opacity=0.5,
                popup="Point GPS"
            ).add_to(m)
            
    colors = ['#2b83ba', '#abdda4', '#ffffbf', '#fdae61', '#d7191c']

    for z in zones:
        poly_wgs = transform(proj, z["geometry"])
        
        if isinstance(poly_wgs, GeometryCollection):
            geoms = [g for g in poly_wgs.geoms if isinstance(g, Polygon)]
            if not geoms: continue
            poly_wgs = MultiPolygon(geoms)

        count = len(z['dates'])
        surface_ha = z["geometry"].area / 10000
        
        popup_html = (
            f"<b>Passages : {count}</b><br>"
            f"<b>Surface :</b> {surface_ha:.2f} ha<br>"
            f"<b>Dates :</b> {', '.join(sorted(list(set(z['dates']))))}"
        )
        popup = folium.Popup(popup_html, max_width=300)
        
        color_idx = min(count - 1, len(colors) - 1)
        
        folium.GeoJson(
            poly_wgs,
            style_function=lambda x, color=colors[color_idx]: {
                "fillColor": color,
                "color": "black",
                "weight": 1,
                "fillOpacity": 0.6,
            },
            popup=popup,
            tooltip=f"{count} passage(s)"
        ).add_to(m)

    # Enregistre la carte dans le fichier spécifié
    m.save(output_file)

def print_summary(zones):
    """Affiche un résumé des zones analysées."""
    if not zones:
        print("ℹ️ Aucune zone détectée après analyse.")
        return
        
    total_unique_area_ha = sum(z["geometry"].area for z in zones) / 1e4
    print(f"🟩 Surface unique totale travaillée : {total_unique_area_ha:.2f} ha")
    print(f"🔳 Nombre de zones distinctes (par nbre de passages) : {len(zones)}")

if __name__ == "__main__":
    try:
        print("1. Récupération de l'ID du dispositif...")
        device_id = fetch_device_id()
        print(f"   ID trouvé : {device_id}")

        print("2. Récupération des positions GPS...")
        # Par défaut, récupère les %d derniers jours
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=DAYS)
        positions = fetch_positions(device_id, from_dt, to_dt)
        print(f"   {len(positions)} positions récupérées.")

        print("3. Détection des zones de travail journalières...")
        daily_zones = cluster_positions(positions)
        print(f"   {len(daily_zones)} zones journalières détectées.")
        total_absolute_area_ha = sum(z["geometry"].area for z in daily_zones) / 1e4
        print(f"   🔹 Surface totale brute (avant découpe) : {total_absolute_area_ha:.2f} ha")

        print("4. Agrégation des zones superposées...")
        aggregated_zones = aggregate_overlapping_zones(daily_zones)
        print(f"   {len(aggregated_zones)} zones distinctes après agrégation.")

        print("5. Génération de la carte...")
        raw_points = extract_raw_points(positions)
        generate_map(aggregated_zones, raw_points)

        print("\n📊 Résumé de l'analyse :")
        print_summary(aggregated_zones)

    except Exception as e:
        print(f"❌ Une erreur est survenue : {e}")
