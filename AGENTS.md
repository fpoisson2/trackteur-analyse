# Guide Agents.md du projet – pour OpenAI Codex

Ce fichier `Agents.md` fournit des directives claires pour OpenAI Codex et autres agents automatisés travaillant avec ce dépôt. Il définit la structure, les conventions de codage, les tâches planifiées et les bonnes pratiques d’intégration continue pour le projet `trackteur-analyse`.

---

## 📁 Structure du projet pour Codex

- `/` (racine) :
  - `app.py` : application Flask (factory) et enregistrement des blueprints + planificateur
  - `models.py` : modèles SQLAlchemy (User, Equipment, Position, DailyZone)
  - `zone.py` : logique principale pour le clustering GPS et le calcul de surfaces
- `/routes` : modules de routes (évolution en cours)
  - `osmand.py` : point d’entrée d’ingestion OsmAnd (JSON/batch)
  - `equipment.py` : endpoints cartes/GeoJSON par équipement (à terme)
- `/templates` : gabarits Jinja2 utilisés par Flask
- `/static` : fichiers statiques (exports de carte, CSS)
- `/instance` : base de données SQLite locale (`trackteur.db`)
- `/tests` : fichiers de test que Codex peut étendre lors de nouvelles fonctionnalités

---

## 🧑‍💻 Conventions de codage pour Codex

### Conventions générales

- Utiliser **Python 3.8+**
- Suivre les conventions **PEP8**
- Utiliser des noms de fonctions et variables explicites
- Ajouter des **docstrings** et commentaires pour toute logique complexe
- Respecter la séparation des responsabilités (`app.py`, `zone.py`, `models.py`)

### Bonnes pratiques Flask

- Utiliser **Flask-Login** pour la gestion des utilisateurs
- Ajouter les nouvelles routes via des Blueprints dans `/routes` (préféré) ou `app.py` si exceptionnel
- Réutiliser les layouts Bootstrap existants dans les templates HTML
- L'initialisation de la base s'effectue via `@app.before_first_request`
- Restreindre les routes d'administration (`/admin`, `/users`, `/initdb`)
  aux comptes disposant d'un rôle **admin**

### Traitement de données GPS

- La logique de clustering est située dans `zone.py`
- Utiliser `DBSCAN` de `sklearn` et `alphashape` pour générer les zones
- Les données GPS sont récupérées via l'API externe avec `requests`
- Conserver l’utilisation de `pyproj` pour la projection locale
- Les résultats sont persistés via SQLAlchemy dans `DailyZone`

---

## 🔄 Tâches automatisées et agents

Les agents peuvent être exécutés automatiquement ou manuellement :

| Agent                      | Fréquence        | Déclenchement |
|----------------------------|------------------|----------------|
| `analyseur_zones_journalières` | chaque nuit      | APScheduler    |
| `suivi_positions_temps_réel`   | chaque minute    | APScheduler    |
| `analyse_initiale`         | au démarrage     | automatique |
| `rapport_par_tracteur`     | futur            | script manuel  |
| `verificateur_inactivite`  | futur            | planifié       |
| `release_gh_action`        | à chaque merge de `Dev` vers `main` | GitHub Actions |
| `admin_update`             | sur demande      | interface admin (branche choisie) |

Les versions suivent le format `année.mois.version`.
Le développement se fait sur la branche `Dev` avant fusion dans `main`.

---

## 🔐 Variables d’environnement (optionnelles)

Les paramètres Traccar peuvent être saisis via l'interface d'administration et
sont enregistrés en base de données. Les variables ci-dessous permettent de
fournir ou de surcharger cette configuration lors du déploiement :

| Variable              | Rôle                                |
|-----------------------|--------------------------------------|
| `TRACCAR_AUTH_TOKEN`  | Jeton d’accès au serveur Traccar     |
| `TRACCAR_BASE_URL`    | URL de base de l’API Traccar         |
| `TRACCAR_DEVICE_NAME` | Nom de l’équipement à suivre (option)|
| `SKIP_INITIAL_ANALYSIS` | Désactiver l’analyse initiale (0/1) |
| `FLASK_SECRET_KEY`    | Clé secrète Flask                    |

**Codex ne doit jamais coder ces valeurs en dur.**

---

## ✅ Tests à effectuer avant soumission

Codex doit écrire ou modifier des tests dans `/tests` avec `pytest`.

```bash
# Lancer tous les tests
pytest

# Lancer les tests avec couverture
pytest --cov=.
```

---

## 📦 Procédures de validation Codex

Avant toute fusion de code généré par Codex :

```bash
# Vérification PEP8
flake8 .

# Vérification des types
mypy .

# Lancer les tests avec couverture
pytest --cov=.
```

---

## ✔️ Bonnes pratiques pour les Pull Requests Codex

1. Fournir une description claire des changements
2. Documenter tout nouvel agent ici dans `AGENTS.md`
3. S’assurer que tous les tests passent
4. Ne pas exposer d’informations sensibles
5. Limiter chaque PR à une seule fonctionnalité
### Détails des agents

- `suivi_positions_temps_réel`:
  - Récupère les positions les plus récentes depuis Traccar pour chaque équipement associé (sans lancer d’analyse)
  - Met à jour `Position` et le champ `Equipment.last_position`
  - Fréquence: intervalle 1 minute

### Champs du modèle Equipment

- `include_in_analysis` (bool): permet d’exclure un équipement de l’analyse tout en continuant de suivre sa position (Traccar ou OsmAnd). Modifiable depuis l’admin.
