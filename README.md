# Trackteur Analyse

> Analyse et visualisation des zones de travail depuis un serveur [Traccar](https://www.traccar.org/)

Trackteur Analyse interroge l'API Traccar, agrège les positions par jour et calcule les surfaces travaillées. Les résultats sont présentés dans une interface Web conviviale.

## Table des matières
1. [Fonctionnalités](#fonctionnalités)
2. [Prérequis](#prérequis)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Initialisation de la base](#initialisation-de-la-base)
6. [Utilisation](#utilisation)
7. [Structure du projet](#structure-du-projet)
8. [Contribution](#contribution)
9. [Licence](#licence)

## Fonctionnalités
- Authentification avec gestion d'un utilisateur administrateur
- Récupération des positions via l'API REST Traccar
- Génération de zones journalières (DBSCAN + alphashape)
- Visualisation cartographique interactive (Folium)
- Analyse planifiée chaque nuit grâce à APScheduler
- Stockage des données dans `instance/trackteur.db`

## Prérequis
- Python 3.8 ou supérieur
- Serveur Traccar accessible et jeton d'API

## Installation

```bash
git clone <repo> && cd trackteur-analyse
pip install -r requirements.txt
```

## Configuration
Avant le lancement, définissez les variables d'environnement suivantes (ou placez-les dans un fichier `.env`) :

| Variable | Description |
|----------|-------------|
| `TRACCAR_AUTH_TOKEN` | Token d'authentification pour l'API Traccar |
| `TRACCAR_BASE_URL` | URL de base de votre serveur (ex. `https://mon.traccar/api`) |
| `APP_USERNAME` | Identifiant de connexion |
| `APP_PASSWORD` | Mot de passe associé |
| `FLASK_SECRET_KEY` | *(optionnel)* Clé secrète Flask |

Exemple :
```bash
export TRACCAR_AUTH_TOKEN="votre_token"
export TRACCAR_BASE_URL="https://mon.traccar/api"
export APP_USERNAME="admin"
export APP_PASSWORD="motdepasse"
```

## Initialisation de la base

Lors du premier démarrage :
```bash
python app.py
```
Puis ouvrez [http://localhost:5000/initdb](http://localhost:5000/initdb) pour créer la base. Si `APP_USERNAME` et `APP_PASSWORD` sont définies, un compte administrateur est généré.

## Utilisation

Démarrer l'application :
```bash
python app.py
```
Accédez à [http://localhost:5000](http://localhost:5000). La page d'accueil liste les équipements, leur dernière position et les surfaces calculées. Vous pouvez lancer une analyse manuelle ou consulter le détail d'un équipement (zones par jour et carte interactive). Une analyse automatique a lieu chaque nuit à 2 h.

## Structure du projet
```
app.py         - Application Flask et routes principales
models.py      - Modèles SQLAlchemy
zone.py        - Récupération et traitement des positions
templates/     - Gabarits HTML (Bootstrap)
static/        - Ressources statiques (ex. carte générée)
```

## Contribution
Les issues et pull requests sont bienvenues pour signaler un bug ou proposer des améliorations.

## Licence
Ce projet est fourni sans fichier de licence ; contactez l'auteur pour toute question d'utilisation.
