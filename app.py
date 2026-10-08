import tomllib
import warnings
from ftplib import FTP, all_errors
from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from shapely.geometry import Point
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError

st.set_page_config(page_title="Photos géolocalisées → PostGIS", page_icon="📷", layout="wide")

CRS = "EPSG:4326"
EXTENSIONS = ["jpg", "jpeg", "tif", "tiff"]


# ── Connexion
@st.cache_resource
def get_engine(db_url: str):
    return create_engine(db_url, pool_pre_ping=True)


# ── Fonctions utilitaires
def extraire_exif(fichier) -> dict:
    try:
        img = Image.open(fichier)
        exif_data = img._getexif()
        if not exif_data:
            return {}
        return {TAGS.get(tag, tag): value for tag, value in exif_data.items()}
    except Exception as e:
        warnings.warn(f"Impossible de lire les EXIF de {fichier.name} : {e}")
        return {}


def convertir_gps(valeur_gps) -> float:
    degres, minutes, secondes = valeur_gps
    return float(degres) + float(minutes) / 60 + float(secondes) / 3600


def extraire_coordonnees(exif: dict) -> tuple | None:
    gps_info_raw = exif.get("GPSInfo")
    if not gps_info_raw:
        return None
    gps_info = {GPSTAGS.get(tag, tag): val for tag, val in gps_info_raw.items()}
    try:
        lat = convertir_gps(gps_info["GPSLatitude"])
        lon = convertir_gps(gps_info["GPSLongitude"])
        if gps_info.get("GPSLatitudeRef") == "S":
            lat = -lat
        if gps_info.get("GPSLongitudeRef") == "W":
            lon = -lon
        return lon, lat
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def extraire_metadonnees_utiles(exif: dict) -> dict:
    def safe_str(val):
        try:
            return str(val) if val is not None else None
        except Exception:
            return None

    altitude = None
    gps_info = exif.get("GPSInfo", {})
    if 6 in gps_info:
        try:
            altitude = float(gps_info[6])
        except (TypeError, ZeroDivisionError):
            pass

    return {
        "date_prise":  safe_str(exif.get("DateTimeOriginal") or exif.get("DateTime")),
        "appareil":    safe_str(exif.get("Make")),
        "modele":      safe_str(exif.get("Model")),
        "orientation": safe_str(exif.get("Orientation")),
        "altitude_m":  altitude,
    }


def clean_null_bytes(df):
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].apply(lambda x: x.replace("\x00", "") if isinstance(x, str) else x)
    return df


def charger_config(fichier) -> dict:
    cfg = tomllib.loads(fichier.getvalue().decode("utf-8"))
    return {
        "projet": {
            "nom":           cfg["projet"]["nom"],
            "numero_etude":  cfg["projet"]["numero_etude"],
            "referent":      cfg["projet"]["referent"],
            "referent_app":  cfg["projet"]["referent_app"],
            "chemin_photos": cfg["projet"]["chemin_photos"],
        },
        "schema":      cfg["cible"]["schema"],
        "table":       cfg["cible"]["table"],
        "dossier_ftp": cfg["cible"]["dossier_ftp"],
        "url_base":    cfg["cible"].get("url_base", "https://dev.datakeran.naomis.fr/upload/"),
        "db_url":      cfg["postgis"]["url"],
        "ftp_host":    cfg["ftp"]["host"],
        "ftp_user":    cfg["ftp"]["user"],
        "ftp_pass":    cfg["ftp"]["password"],
    }


# ── Barre latérale : configuration
with st.sidebar:
    st.header("Configuration")
    cfg_file = st.file_uploader("Fichier de configuration (.toml)", type=["toml"])

if cfg_file is None:
    st.title("Extraction GPS des photos → PostGIS + FTP")
    st.info("Chargez le fichier de configuration du projet dans la barre latérale pour commencer.")
    st.stop()

try:
    cfg = charger_config(cfg_file)
except (tomllib.TOMLDecodeError, KeyError, UnicodeDecodeError) as e:
    detail = f" (clé manquante : {e})" if isinstance(e, KeyError) else ""
    st.error(f"Fichier de configuration invalide{detail}.")
    st.stop()

projet = cfg["projet"]

with st.sidebar:
    st.subheader("Projet")
    st.markdown(
        f"**{projet['nom']}**  \n"
        f"Étude : {projet['numero_etude']}  \n"
        f"Référent : {projet['referent']}  \n"
        f"Référent app : {projet['referent_app']}"
    )
    st.caption("Dossier source des photos")
    st.code(projet["chemin_photos"], language=None)

    st.subheader("Cible")
    st.markdown(
        f"Table : `{cfg['schema']}.{cfg['table']}`  \n"
        f"Dossier FTP : `{cfg['dossier_ftp']}`"
    )

    st.subheader("Options")
    ftp_passif = st.checkbox("FTP mode passif", value=True,
                             help="Obligatoire sur Streamlit Cloud (pas de connexion entrante possible).")
    ignorer_doublons = st.checkbox("Ignorer les photos déjà en base (nom_fichier)", value=True)


st.title(f"{projet['nom']} — photos géolocalisées")

# ── Transfert
if st.button("Transférer les nouvelles photos", type="primary"):
    with st.status("Transfert en cours...", expanded=True) as status:

        # Étape 1 — Scan du dossier
        status.write(f"**Étape 1/4 — Scan du dossier** `{projet['chemin_photos']}` (sous-dossiers compris)")
        photos_dir = Path(projet["chemin_photos"])
        if not photos_dir.is_dir():
            status.update(label="Dossier photos introuvable", state="error")
            st.error("Dossier inaccessible depuis ce poste (l'app doit tourner sur un poste du réseau).")
            st.stop()
        fichiers = [f for f in photos_dir.rglob("*") if f.suffix.lower().lstrip(".") in EXTENSIONS]
        status.write(f"{len(fichiers)} photo(s) trouvée(s).")

        # Étape 2 — Extraction GPS
        status.write("**Étape 2/4 — Lecture des coordonnées GPS (EXIF)**")
        lignes, sans_gps = [], []
        for fichier in fichiers:
            exif = extraire_exif(fichier)
            coords = extraire_coordonnees(exif)
            meta = extraire_metadonnees_utiles(exif)
            if coords is None or coords == (0, 0):
                sans_gps.append(fichier.name)
                continue
            lon, lat = coords
            lignes.append({
                "nom_fichier": fichier.name,
                "chemin":      str(fichier.resolve()),
                "longitude":   lon,
                "latitude":    lat,
                **meta,
                "geometry":    Point(lon, lat),
            })
        status.write(f"{len(lignes)} photo(s) avec GPS, {len(sans_gps)} sans GPS.")
        if sans_gps:
            status.write(f"Ignorées (pas de GPS) : {', '.join(sans_gps)}")
        if not lignes:
            status.update(label="Aucune photo géolocalisée", state="error")
            st.stop()

        gdf = gpd.GeoDataFrame(lignes, geometry="geometry", crs=CRS)
        gdf["photo"] = cfg["url_base"].rstrip("/") + "/" + cfg["dossier_ftp"] + "/" + gdf["nom_fichier"]
        gdf["valide"] = "oui"
        gdf["date_prise"] = pd.to_datetime(gdf["date_prise"], format="%Y:%m:%d %H:%M:%S", errors="coerce")

        # Étape 3 — PostGIS
        schema, table_name = cfg["schema"], cfg["table"]
        status.write(f"**Étape 3/4 — Enregistrement dans PostGIS** `{schema}.{table_name}`")
        try:
            engine = get_engine(cfg["db_url"])
            with engine.connect() as conn:
                if schema not in inspect(engine).get_schema_names():
                    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                    conn.commit()
                    status.write(f"Schéma {schema} créé.")

            if ignorer_doublons and inspect(engine).has_table(table_name, schema=schema):
                existants = pd.read_sql(
                    text(f'SELECT nom_fichier FROM "{schema}"."{table_name}"'), engine
                )["nom_fichier"].tolist()
                gdf = gdf[~gdf["nom_fichier"].isin(existants)]
                status.write(f"{len(existants)} photo(s) déjà en base, {len(gdf)} nouvelle(s).")

            if gdf.empty:
                status.update(label="Aucune nouvelle photo à transférer", state="complete")
                st.stop()

            gdf = clean_null_bytes(gdf)
            gdf.to_postgis(name=table_name, con=engine, schema=schema, if_exists="append", index=False)
            status.write(f"{len(gdf)} ligne(s) ajoutée(s).")
        except SQLAlchemyError as e:
            # Message générique : l'exception brute peut contenir l'URL de connexion
            status.update(label="Erreur PostGIS", state="error")
            st.error(f"Erreur PostGIS ({type(e).__name__}). Contactez {projet['referent_app']}.")
            st.stop()

        # Étape 4 — FTP
        status.write(f"**Étape 4/4 — Envoi sur le FTP** `{cfg['dossier_ftp']}`")
        noms = set(gdf["nom_fichier"])
        a_envoyer = [f for f in fichiers if f.name in noms]
        envoyes, echecs = 0, []
        try:
            with FTP(cfg["ftp_host"], timeout=60) as ftp:
                ftp.login(cfg["ftp_user"], cfg["ftp_pass"])
                ftp.set_pasv(ftp_passif)
                ftp.cwd(cfg["dossier_ftp"])
                for fichier in a_envoyer:
                    try:
                        with open(fichier, "rb") as f:
                            ftp.storbinary(f"STOR {fichier.name}", f)
                        envoyes += 1
                    except all_errors as e:
                        echecs.append(f"{fichier.name} — {e}")
        except all_errors as e:
            status.update(label="Erreur FTP", state="error")
            st.error(f"Connexion FTP impossible ({type(e).__name__}). Contactez {projet['referent_app']}.")
            st.stop()
        status.write(f"{envoyes} fichier(s) envoyé(s).")
        if echecs:
            status.write("Échecs : " + " ; ".join(echecs))

        status.update(label=f"Transfert terminé : {len(gdf)} nouvelle(s) photo(s)", state="complete")

    st.map(gdf[["latitude", "longitude"]])
    st.dataframe(gdf.drop(columns="geometry"), use_container_width=True)