README pour l'application de suivi de zones Traccar

Cette application Flask permet de récupérer, analyser et visualiser
les zones de travail d'un dispositif Traccar.

Configuration
-------------
Avant d'exécuter l'application, définissez les variables d'environnement suivantes :

- TRACCAR_AUTH_TOKEN : le token d'authentification pour l'API Traccar
- TRACCAR_BASE_URL   : l'URL de base de votre serveur Traccar (exemple : https://serveur1b.trackteur.cc)
- TRACCAR_DEVICE_NAME (optionnel) : le nom du dispositif (par défaut "Tracteur 4")

Ajout de l’authentification :
- APP_USERNAME       : nom d’utilisateur pour accéder à l’application (obligatoire)
- APP_PASSWORD       : mot de passe associé à cet utilisateur (obligatoire)
- FLASK_SECRET_KEY   : clé secrète de Flask pour les sessions (optionnelle, générée aléatoirement si non fournie)

Exemple (Linux/macOS) :

  export TRACCAR_AUTH_TOKEN="votre_token_ici"
  export TRACCAR_BASE_URL="https://votre.serveur.traccar/api"
  export TRACCAR_DEVICE_NAME="Nom de votre dispositif"

Installation
------------
Installez les dépendances :

  pip install -r requirements.txt

Exécution
---------
Démarrez l'application Flask :

  python app.py

Accédez ensuite à http://localhost:5000 dans votre navigateur.

Vous serez redirigé vers la page de connexion si nécessaire. En cas de première visite,
rendez-vous sur http://localhost:5000/login pour vous authentifier.
