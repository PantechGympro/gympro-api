"""
restore_service.py — Restauración de respaldos GymPro.

Soporta los 3 artefactos restaurables que genera el servicio de backup:

    .archive  → dump nativo de MongoDB (mongodump)  → mongorestore
    .json     → export JSON de MongoDB (full/incremental) → upsert por _id
    .dump     → dump custom de PostgreSQL (pg_dump -Fc) → pg_restore

Las herramientas mongorestore / pg_restore se instalan en el contenedor
(ver api/Dockerfile: mongodb-database-tools + postgresql-client-16).

NOTA sobre Postgres: la restauración es NO destructiva (merge). pg_restore se
ejecuta SIN --clean, por lo que no elimina objetos existentes; los errores de
"already exists" o conflictos de PK se reportan como advertencias en vez de
abortar. Para una restauración idéntica al backup, vaciar la BD antes.
"""
import os
import subprocess

from bson import json_util

from app.mongo import get_db

MONGORESTORE_PATH = "mongorestore"
PG_RESTORE_PATH   = "pg_restore"

# Extensiones soportadas (usado por las rutas para validar antes de restaurar)
RESTORABLE_EXTS = (".archive", ".json", ".dump")


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB — dump nativo (.archive)
# ─────────────────────────────────────────────────────────────────────────────
def restore_mongo_archive(file_path: str) -> dict:
    """
    Restaura un dump nativo de MongoDB generado por mongodump (--archive).

    Usa MONGO_URI (misma fuente que service.py / mongo.py), de modo que funciona
    tanto en Docker (mongodb://mongo:27017/gymdb) como en Atlas
    (mongodb+srv://...). --drop elimina cada colección antes de restaurarla para
    evitar duplicados; --nsInclude acota la restauración a la base del proyecto.
    """
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
    db_name   = os.getenv("MONGO_DB", "gymdb")

    result = subprocess.run(
        [
            MONGORESTORE_PATH,
            "--uri", mongo_uri,
            "--nsInclude", f"{db_name}.*",
            "--drop",
            f"--archive={file_path}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        stderr_msg = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"mongorestore falló (código {result.returncode}): {stderr_msg}")

    return {"engine": "mongodb", "mode": "archive", "target": db_name}


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB — export JSON (.json)
# ─────────────────────────────────────────────────────────────────────────────
def restore_mongo_json(file_path: str) -> dict:
    """
    Restaura un backup JSON (full / incremental / diferencial) generado por
    nuestro propio export. json_util preserva fechas y ObjectId. Se hace upsert
    por _id: actualiza si existe, inserta si no (no destructivo).
    """
    db = get_db()
    with open(file_path, "r", encoding="utf-8") as f:
        data = json_util.loads(f.read())

    colecciones = 0
    documentos  = 0
    for coll_name, docs in data.items():
        coll = db[coll_name]
        colecciones += 1
        for doc in docs:
            coll.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
            documentos += 1

    return {"engine": "mongodb", "mode": "json", "colecciones": colecciones, "documentos": documentos}


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL — dump custom (.dump)
# ─────────────────────────────────────────────────────────────────────────────
def restore_pg_dump(file_path: str) -> dict:
    """
    Restaura un dump custom de PostgreSQL (pg_dump -Fc) con pg_restore.

    Estrategia NO destructiva (merge): se omite --clean, por lo que no se
    eliminan objetos existentes. pg_restore NO usa --exit-on-error, así que los
    errores recuperables (objeto ya existe, PK duplicada) se acumulan como
    advertencias sin abortar el resto de la restauración.

    La contraseña se pasa por PGPASSWORD para no depender de .pgpass.
    """
    pg_host     = os.getenv("POSTGRES_HOST",     "postgres")
    pg_port     = os.getenv("POSTGRES_PORT",     "5432")
    pg_user     = os.getenv("POSTGRES_USER",     "gymuser")
    pg_password = os.getenv("POSTGRES_PASSWORD", "gympassword")
    pg_db       = os.getenv("POSTGRES_DB",       "gymprodb")

    env = os.environ.copy()
    env["PGPASSWORD"] = pg_password

    result = subprocess.run(
        [
            PG_RESTORE_PATH,
            "-h", pg_host,
            "-p", pg_port,
            "-U", pg_user,
            "-d", pg_db,
            "--no-owner",
            "--no-privileges",
            # sin --clean: merge no destructivo
            # sin --exit-on-error: continúa ante objetos ya existentes
            file_path,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stderr_msg = result.stderr.decode("utf-8", errors="replace").strip()

    # pg_restore devuelve un código != 0 cuando hubo errores ignorados (p.ej.
    # "already exists"). Distinguimos un fallo de conexión real de simples
    # advertencias de merge: si no pudo conectar, es un error duro.
    if result.returncode != 0:
        lower = stderr_msg.lower()
        fallo_conexion = (
            "could not connect" in lower
            or "connection refused" in lower
            or "authentication failed" in lower
            or "does not exist" in lower and "database" in lower
        )
        if fallo_conexion:
            raise RuntimeError(f"pg_restore no pudo conectar a Postgres: {stderr_msg}")

        # Errores recuperables → advertencia (merge sobre esquema existente)
        warnings = [l for l in stderr_msg.splitlines() if "error" in l.lower()]
        return {
            "engine": "postgresql",
            "mode": "merge",
            "target": pg_db,
            "warnings": len(warnings),
            "detail": stderr_msg[-1500:] if stderr_msg else "",
        }

    return {"engine": "postgresql", "mode": "merge", "target": pg_db, "warnings": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher por extensión
# ─────────────────────────────────────────────────────────────────────────────
def restore_backup_file(file_path: str) -> dict:
    """
    Restaura un backup detectando el motor por la extensión del archivo.
    Devuelve un dict con metadatos del resultado. Lanza excepción ante fallo duro.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError("El archivo de respaldo no existe.")

    lower = file_path.lower()
    if lower.endswith(".archive"):
        return restore_mongo_archive(file_path)
    if lower.endswith(".json"):
        return restore_mongo_json(file_path)
    if lower.endswith(".dump"):
        return restore_pg_dump(file_path)

    raise ValueError(
        "Formato no soportado. Solo se permiten: "
        + ", ".join(RESTORABLE_EXTS)
    )
