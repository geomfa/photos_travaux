# Photos géolocalisées → PostGIS

Application Streamlit qui extrait les coordonnées GPS (EXIF) de photos, les enregistre dans une table PostGIS et envoie les photos sur le serveur FTP.

## Utilisation

1. Ouvrir l'application.
2. Charger le fichier de configuration du projet (.toml) dans la barre latérale.
3. Vérifier les infos projet et la cible affichées.
4. Charger les photos (jpg, jpeg, tif, tiff) depuis le dossier indiqué dans la configuration.
5. Cliquer sur **Extraire les coordonnées** et vérifier la carte et le tableau.
6. Cliquer sur **Exporter vers PostGIS et envoyer sur le FTP**.

Les photos sans GPS sont ignorées et listées. Les photos déjà présentes en base (même nom de fichier) sont ignorées si l'option est cochée.

## Fichier de configuration

Partir de `config_exemple.toml` et le compléter pour chaque projet :

- `[projet]` : nom, numéro d'étude, référent, référent app, chemin des photos
- `[cible]` : schéma, table, dossier FTP (doit exister), URL de base des photos
- `[postgis]` : URL de connexion
- `[ftp]` : hôte, utilisateur, mot de passe

Ce fichier contient des identifiants : ne jamais le commiter, ne pas l'envoyer par mail en clair. Il n'est conservé qu'en mémoire pendant la session et doit être rechargé à chaque visite.

## Lancer en local

    uv venv
    uv pip install -r requirements.txt
    uv run streamlit run app.py

Sous Windows avec OneDrive : définir `UV_LINK_MODE=copy` avant.

## Déploiement Streamlit Cloud

- Pousser le dépôt sur GitHub (le `.gitignore` exclut les .toml sauf l'exemple et `.streamlit/config.toml`).
- Python 3.11 minimum (Advanced settings), requis par `tomllib`.
- Le FTP doit accepter le mode passif.
- La base PostGIS doit accepter les connexions externes.
- Utiliser un compte PostgreSQL dédié aux droits limités au schéma cible.
