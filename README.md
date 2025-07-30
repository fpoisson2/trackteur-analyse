# Trackteur Analyse

> Analyse et visualisation des zones de travail depuis un serveur [Traccar](https://www.traccar.org/)

Trackteur Analyse interroge l'API Traccar, agrège les positions par jour et calcule les surfaces travaillées. Les résultats sont présentés dans une interface Web conviviale.

## Table des matières
1. [Fonctionnalités](#fonctionnalités)
2. [Prérequis](#prérequis)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Utilisation](#utilisation)
6. [Structure du projet](#structure-du-projet)
7. [Contribution](#contribution)
8. [Licence](#licence)

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
Au premier lancement, ouvrez l'application dans votre navigateur. Un assistant se lance automatiquement pour :
1. Créer le compte administrateur
2. Saisir l'URL et le token du serveur Traccar
3. Choisir les appareils à suivre
4. Lancer une analyse initiale

Seule la variable `FLASK_SECRET_KEY` peut être définie au besoin.

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
Ce projet est distribué sous licence [MIT](LICENSE).
