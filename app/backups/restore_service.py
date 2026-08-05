"""
restore_service.py — Restauración de respaldos GymPro.

Soporta los artefactos restaurables que genera el servicio de backup:

    .archive  → dump nativo de MongoDB (mongodump)  → mongorestore
    .json     → export JSON de MongoDB (full/incremental) → upsert/merge
    .dump     → dump custom de PostgreSQL (pg_dump -Fc) → pg_restore
    .tar.gz   → medios subidos (imágenes/videos)         → extracción a uploads

Las herramientas mongorestore / pg_restore se instalan en el contenedor
(ver api/Dockerfile: mongodb-database-tools + postgresql-client-16).

NOTA sobre Postgres: la restauración es NO destructiva (merge). pg_restore se
ejecuta SIN --clean, por lo que no elimina objetos existentes; los errores de
"already exists" o conflictos de PK se reportan como advertencias en vez de
abortar. Para una restauración idéntica al backup, vaciar la BD antes.
"""
import os
import json
import shutil
import tempfile
import subprocess
import tarfile

from bson import json_util
from pymongo import ReplaceOne
from pymongo.errors import DuplicateKeyError, WriteError, BulkWriteError

from app.mongo import get_db, get_client
from app.backups.service import MEDIA_SOURCES

MONGORESTORE_PATH = "mongorestore"
PG_RESTORE_PATH   = "pg_restore"

# Extensiones soportadas (usado por las rutas para validar antes de restaurar)
RESTORABLE_EXTS = (".archive", ".json", ".dump", ".tar.gz", ".tgz")


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB — dump nativo (.archive)
# ─────────────────────────────────────────────────────────────────────────────
def restore_mongo_archive(file_path: str, drop_db: bool = False) -> dict:
    """
    Restaura un dump nativo de MongoDB generado por mongodump (--archive).

    Usa MONGO_URI (misma fuente que service.py / mongo.py), de modo que funciona
    tanto en Docker (mongodb://mongo:27017/gymdb) como en Atlas
    (mongodb+srv://...).

    drop_db=True (clon exacto, usado por el bundle): elimina TODA la base de
    datos antes de restaurar, de modo que el resultado sea idéntico al backup
    (incluso colecciones que ya no existen en el origen). Si es False, se usa
    --drop por-colección (reemplaza solo las colecciones presentes en el dump).
    """
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
    db_name   = os.getenv("MONGO_DB", "gymdb")

    if drop_db:
        # Reemplazo total: parte de una base limpia.
        get_client().drop_database(db_name)

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

    return {"engine": "mongodb", "mode": "drop_db" if drop_db else "archive", "target": db_name}


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB — export JSON (.json)
# ─────────────────────────────────────────────────────────────────────────────
def restore_mongo_json(file_path: str) -> dict:
    """
    Restaura un backup JSON (full / incremental / diferencial) generado por
    nuestro propio export. json_util preserva fechas y ObjectId.

    Estrategia de merge (integra datos nuevos sin perder los existentes):

      1. Se intenta upsert por _id (camino normal: actualiza si existe, inserta
         si no). El _id original se preserva para no romper referencias entre
         colecciones.
      2. Si choca contra un índice único de negocio (p.ej. otro registro ya
         tiene ese 'id_usuario_pg' con un _id distinto), NO se descarta: se
         FUSIONAN los campos sobre el registro existente, identificándolo por
         esa clave única y conservando su _id.
      3. Solo si tras eso aún falla, se cuenta como advertencia.

    Esto permite importar un backup de otra máquina y que los registros nuevos
    se integren con los ya presentes.
    """
    db = get_db()
    with open(file_path, "r", encoding="utf-8") as f:
        data = json_util.loads(f.read())

    colecciones = 0
    insertados  = 0
    fusionados  = 0
    omitidos    = 0

    for coll_name, docs in data.items():
        if not docs:
            continue
        # Claves reservadas: si un respaldo trae material que no es de Mongo, no
        # se convierte en una colección. PostgreSQL se restaura desde su .dump.
        if coll_name in ("postgres", "manifest", "_meta"):
            continue
        coll = db[coll_name]
        colecciones += 1

        # 1) Camino rápido: bulk_write ordered=False de ReplaceOne por _id.
        #    Procesa miles de documentos en una sola ida a Mongo (vs. un
        #    update_one por documento). ordered=False no aborta ante un fallo:
        #    sigue con el resto y reporta los errores al final.
        ops = [ReplaceOne({"_id": d.get("_id")}, d, upsert=True) for d in docs]
        try:
            coll.bulk_write(ops, ordered=False)
            insertados += len(ops)
        except BulkWriteError as bwe:
            we = bwe.details.get("writeErrors", [])
            # Los que SÍ entraron en el lote
            insertados += (len(ops) - len(we))
            # 2) Reintento puntual de los que chocaron contra un índice único de
            #    negocio: se FUSIONAN sobre el registro existente por su keyValue.
            for err in we:
                if err.get("code") != 11000:
                    omitidos += 1
                    continue
                idx = err.get("index")
                doc = docs[idx] if isinstance(idx, int) and idx < len(docs) else None
                key_value = err.get("keyValue") or (err.get("errInfo") or {}).get("keyValue")
                if doc is not None and key_value:
                    payload = {k: v for k, v in doc.items() if k != "_id"}
                    try:
                        coll.update_one(key_value, {"$set": payload}, upsert=False)
                        fusionados += 1
                        continue
                    except Exception:
                        pass
                omitidos += 1

    return {
        "engine": "mongodb",
        "mode": "json",
        "colecciones": colecciones,
        "insertados": insertados,
        "fusionados": fusionados,
        "warnings": omitidos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL — dump custom (.dump)
# ─────────────────────────────────────────────────────────────────────────────
def restore_pg_dump(file_path: str, clean: bool = False) -> dict:
    """
    Restaura un dump custom de PostgreSQL (pg_dump -Fc) con pg_restore.

    clean=False (default) → merge NO destructivo: se omite --clean, no se
        eliminan objetos existentes; los errores recuperables se reportan como
        advertencias sin abortar.
    clean=True → reemplazo total: añade --clean --if-exists, dejando la BD
        idéntica al dump (usado en la restauración por bundle / clon completo).

    La contraseña se pasa por PGPASSWORD para no depender de .pgpass.
    """
    pg_host     = os.getenv("POSTGRES_HOST",     "postgres")
    pg_port     = os.getenv("POSTGRES_PORT",     "5432")
    pg_user     = os.getenv("POSTGRES_USER",     "gymuser")
    pg_password = os.getenv("POSTGRES_PASSWORD", "gympassword")
    pg_db       = os.getenv("POSTGRES_DB",       "gymprodb")

    env = os.environ.copy()
    env["PGPASSWORD"] = pg_password

    cmd = [
        PG_RESTORE_PATH,
        "-h", pg_host,
        "-p", pg_port,
        "-U", pg_user,
        "-d", pg_db,
        "--no-owner",
        "--no-privileges",
    ]
    if clean:
        # Reemplazo total: elimina y recrea objetos antes de restaurar.
        cmd += ["--clean", "--if-exists"]
    # sin --exit-on-error: continúa ante objetos ya existentes / inexistentes
    cmd.append(file_path)

    result = subprocess.run(
        cmd,
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
            or ("does not exist" in lower and "database" in lower)
        )
        if fallo_conexion:
            raise RuntimeError(f"pg_restore no pudo conectar a Postgres: {stderr_msg}")

        # Errores recuperables → advertencia (objetos ya/no existentes)
        warnings = [l for l in stderr_msg.splitlines() if "error" in l.lower()]
        return {
            "engine": "postgresql",
            "mode": "clean" if clean else "merge",
            "target": pg_db,
            "warnings": len(warnings),
            "detail": stderr_msg[-1500:] if stderr_msg else "",
        }

    return {"engine": "postgresql", "mode": "clean" if clean else "merge", "target": pg_db, "warnings": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Medios — imágenes / videos (.tar.gz)
# ─────────────────────────────────────────────────────────────────────────────
def restore_media_archive(file_path: str) -> dict:
    """
    Restaura los medios subidos desde un .tar.gz generado por
    generate_media_archive. Cada prefijo interno (storage_uploads /
    static_uploads) se extrae a su directorio destino correspondiente.

    Extracción segura: solo se aceptan archivos bajo los prefijos conocidos y se
    rechaza cualquier ruta con traversal ('..' o absoluta).
    """
    # prefijo interno → directorio destino real
    destinos = {arc: src for src, arc in MEDIA_SOURCES}

    restaurados = 0
    omitidos    = 0
    with tarfile.open(file_path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            partes = member.name.replace("\\", "/").split("/", 1)
            if len(partes) != 2:
                continue
            prefijo, rel = partes
            base = destinos.get(prefijo)
            if not base or not rel:
                omitidos += 1
                continue
            # Anti path-traversal
            if rel.startswith("/") or ".." in rel.split("/"):
                omitidos += 1
                continue

            dest_path = os.path.join(base, rel)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with extracted as fsrc, open(dest_path, "wb") as fdst:
                fdst.write(fsrc.read())
            restaurados += 1

    return {"engine": "media", "mode": "files", "restaurados": restaurados, "warnings": omitidos}


# ─────────────────────────────────────────────────────────────────────────────
# Bundle — paquete único (Mongo + PostgreSQL + medios)
# ─────────────────────────────────────────────────────────────────────────────
def _es_bundle(file_path: str) -> bool:
    """True si el .tar.gz contiene manifest.json (paquete completo GymPro)."""
    try:
        with tarfile.open(file_path, "r:gz") as tar:
            return "manifest.json" in tar.getnames()
    except Exception:
        return False


def restore_bundle(file_path: str) -> dict:
    """
    Restaura un paquete único generado por build_bundle. Reemplazo TOTAL (clon):

        1. PostgreSQL primero (usuarios, gimnasios, finanzas) con --clean.
        2. MongoDB: .archive con --drop, o .json con merge si es incremental.
        3. Medios (imágenes/videos) a sus directorios.

    Se extrae a un directorio temporal aislado que se limpia al finalizar.
    """
    tmp = tempfile.mkdtemp(prefix="gpbundle_")
    resultados: dict = {}
    try:
        with tarfile.open(file_path, "r:gz") as tar:
            for member in tar.getmembers():
                # Extracción segura: nada fuera del directorio temporal
                dest = os.path.realpath(os.path.join(tmp, member.name))
                if not dest.startswith(os.path.realpath(tmp) + os.sep):
                    continue
                tar.extract(member, tmp)

        manifest = {}
        mpath = os.path.join(tmp, "manifest.json")
        if os.path.exists(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                manifest = json.load(f)

        # 1) PostgreSQL — reemplazo total
        pg = os.path.join(tmp, "postgres.dump")
        if os.path.exists(pg):
            resultados["postgresql"] = restore_pg_dump(pg, clean=True)

        # 2) MongoDB — clon exacto: drop_db en el .archive (full). En bundles
        #    incrementales/diferenciales el componente es .json (merge de delta).
        m_archive = os.path.join(tmp, "mongo.archive")
        m_json    = os.path.join(tmp, "mongo.json")
        if os.path.exists(m_archive):
            resultados["mongodb"] = restore_mongo_archive(m_archive, drop_db=True)
        elif os.path.exists(m_json):
            resultados["mongodb"] = restore_mongo_json(m_json)

        # 3) Medios
        media = os.path.join(tmp, "media.tar.gz")
        if os.path.exists(media):
            resultados["media"] = restore_media_archive(media)

        warnings = sum(int(r.get("warnings", 0)) for r in resultados.values())
        return {
            "engine":      "bundle",
            "mode":        "clon",
            "tipo":        manifest.get("tipo"),
            "componentes": resultados,
            "warnings":    warnings,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher por extensión
# ─────────────────────────────────────────────────────────────────────────────
def restore_backup_file(file_path: str) -> dict:
    """
    Restaura un backup detectando el motor por la extensión / contenido.
    Devuelve un dict con metadatos del resultado. Lanza excepción ante fallo duro.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError("El archivo de respaldo no existe.")

    lower = file_path.lower()
    if lower.endswith(".archive"):
        return restore_mongo_archive(file_path)
    if lower.endswith((".tar.gz", ".tgz")):
        # Un .tar.gz con manifest.json es un paquete completo; si no, son medios.
        if _es_bundle(file_path):
            return restore_bundle(file_path)
        return restore_media_archive(file_path)
    if lower.endswith(".json"):
        return restore_mongo_json(file_path)
    if lower.endswith(".dump"):
        return restore_pg_dump(file_path)

    raise ValueError(
        "Formato no soportado. Solo se permiten: "
        + ", ".join(RESTORABLE_EXTS)
    )
