import os
import io
import subprocess
import json
import tarfile
import pandas as pd
from fpdf import FPDF
from datetime import datetime, timezone


def _now_iso():
    """Timestamp UTC con offset (+00:00) para que el frontend lo convierta a la
    hora local del usuario. Evita el desfase de mostrar la hora del contenedor
    (UTC) como si fuera local."""
    return datetime.now(timezone.utc).isoformat()
from flask_mail import Message
from bson import json_util
from bson.objectid import ObjectId

from app.extensions import mail
from app.mongo import get_db

# ================= CONFIG =================

MONGODUMP_PATH  = "mongodump"
PG_DUMP_PATH    = "pg_dump"

BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
BACKUP_DIR = os.path.join(BASE_DIR, "storage", "backups")

# PostgreSQL connection info (leídas del entorno, igual que docker-compose las inyecta)
PG_HOST     = os.getenv("POSTGRES_HOST",     "postgres")
PG_PORT     = os.getenv("POSTGRES_PORT",     "5432")
PG_USER     = os.getenv("POSTGRES_USER",     "gymuser")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "gympassword")
PG_DB       = os.getenv("POSTGRES_DB",       "gymprodb")

LAST_FULL_BACKUP_FILE = os.path.join(BACKUP_DIR, "last_full_backup.txt")
LAST_BACKUP_FILE = os.path.join(BACKUP_DIR, "last_backup_any.txt")
HISTORY_FILE = os.path.join(BACKUP_DIR, "backup_history.json")

# Directorios de medios subidos (imágenes/videos). Se respaldan AMBOS:
#   - storage/uploads: ubicación activa (bind-mount), donde el código guarda y
#     sirve las subidas nuevas (fotos de perfil, certificados, etc.).
#   - app/static/uploads: ubicación legacy con seeds/imágenes históricas.
# Conservar ambos hace el backup portable entre máquinas sin perder archivos.
STORAGE_UPLOADS_DIR = os.getenv("UPLOADS_DIR", "/app/storage/uploads")
STATIC_UPLOADS_DIR  = os.path.join(BASE_DIR, "app", "static", "uploads")

# Prefijos internos dentro del .tar.gz → directorio destino al restaurar.
MEDIA_SOURCES = (
    (STORAGE_UPLOADS_DIR, "storage_uploads"),
    (STATIC_UPLOADS_DIR,  "static_uploads"),
)

# ================= STATE =================

backup_state = {
    "is_running": False,
    "progress_percentage": 0,
    "current_step": None,
    "start_time": None,
    "job_id": None,
    "last_backup": None,
    "generated_files": {}
}

# ================= UTILS =================

def ensure_dirs(backup_type):
    path = os.path.join(BACKUP_DIR, backup_type)
    os.makedirs(path, exist_ok=True)
    return path

def now_str():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def save_last_backup(path):
    with open(path, "w") as f:
        f.write(datetime.now().isoformat())

def get_last_backup(path):
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return f.read().strip()

# ================= HISTORY =================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_history(entry):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    history = load_history()
    history.insert(0, entry)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _find_and_delete(filename: str) -> None:
    """Busca un archivo por nombre en BACKUP_DIR y lo elimina si existe."""
    for root, _, files in os.walk(BACKUP_DIR):
        if filename in files:
            try:
                os.remove(os.path.join(root, filename))
            except OSError as exc:
                print(f"[backup cleanup] No se pudo borrar {filename}: {exc}")
            return


def cleanup_old_backups(max_keep: int = 3) -> None:
    """
    Mantiene solo los últimos `max_keep` backups completados en disco e historial.
    Elimina los archivos físicos de los backups que quedan fuera del límite.
    Los registros de error se purgan a un máximo de 5 para no ensuciar el historial.
    """
    history = load_history()

    completed = [h for h in history if h.get("status") == "completado"]
    errors    = [h for h in history if h.get("status") == "error"]

    # Backups a eliminar (los más antiguos que excedan el límite)
    to_remove = completed[max_keep:]

    for entry in to_remove:
        for fname in (entry.get("files") or {}).values():
            if fname:
                _find_and_delete(fname)

    # Reconstruir historial limpio
    new_history = sorted(
        completed[:max_keep] + errors[:5],
        key=lambda h: h.get("date", ""),
        reverse=True,
    )

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(new_history, f, indent=2, ensure_ascii=False)


# ================= FILTROS MONGODB =================

def _construir_query_fechas(since_date):
    """Construye un query de Mongo para buscar documentos modificados desde una fecha"""
    if not since_date:
        return {}
    
    sd = datetime.fromisoformat(since_date)
    # Buscamos en cualquiera de los campos de fecha que usamos en nuestros modelos
    return {
        "$or": [
            {"fecha_actualizacion": {"$gte": sd}},
            {"fecha_creacion": {"$gte": sd}},
            {"fecha_registro": {"$gte": sd}},
            {"fecha_pago": {"$gte": sd}},
            {"fecha": {"$gte": sd}}
        ]
    }


# ================= POSTGRES: LECTURA PARA REPORTES =================
#
# El .dump de pg_dump ya contiene TODAS las tablas de PostgreSQL, así que la
# restauración nunca perdió nada. Lo que faltaba era el lado legible: el Excel y
# el JSON solo recorrían colecciones de Mongo, de modo que datos que viven en
# PostgreSQL —el catálogo de ejercicios entre ellos— no aparecían por ningún
# lado al abrir el respaldo. Estas funciones cierran ese hueco.
#
# El catálogo de ejercicios es un caso claro: `rutina_ejercicios` (Mongo) guarda
# qué ejercicio va en qué día de rutina, pero el ejercicio en sí —nombre, grupo
# muscular, instrucciones, video— está en la tabla `ejercicios` de PostgreSQL.
# Sin ella el Excel mostraba rutinas apuntando a ejercicios invisibles.

# Tablas que se exportan a los reportes legibles. Se listan explícitamente en
# lugar de volcar el esquema entero para no arrastrar tablas de infraestructura
# (alembic_version) ni credenciales cifradas de pasarelas de pago.
PG_TABLAS_REPORTE = [
    "gimnasios",
    "usuarios",
    "roles",
    "ejercicios",
    "tipos_membresia",
    "tipos_clase",
    "planes_suscripcion",
    "suscripciones",
    "facturas_suscripcion",
    "transacciones_pago",
]
# `configuracion_pasarela` queda fuera a propósito: guarda las credenciales de
# PayPal y Mercado Pago cifradas con Fernet y no tiene por qué viajar en un
# Excel que se abre en cualquier equipo. El .dump sí la incluye, que es donde
# hace falta para restaurar.

# Columnas que nunca salen en un reporte legible, en cualquier tabla.
PG_COLUMNAS_SENSIBLES = {
    "password", "password_hash", "contrasena", "contrasena_hash",
    "token", "refresh_token", "secret", "api_key", "client_secret",
    "access_token", "credenciales", "credenciales_cifradas",
}


def _leer_tablas_pg() -> dict:
    """
    Devuelve {nombre_tabla: [filas...]} para PG_TABLAS_REPORTE.

    Se ejecuta dentro del contexto de aplicación que ya abre `run_backup`, así
    que reutiliza la conexión de SQLAlchemy en lugar de abrir una propia. Si una
    tabla no existe todavía (una migración pendiente en ese entorno) se omite en
    silencio: un respaldo incompleto es mejor que un respaldo fallido.
    """
    datos = {}
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from app.extensions import db as sa_db

        existentes = set(sa_inspect(sa_db.engine).get_table_names())
    except Exception as exc:
        print(f"[backup] No se pudo inspeccionar PostgreSQL: {exc}")
        return datos

    for tabla in PG_TABLAS_REPORTE:
        if tabla not in existentes:
            continue
        try:
            filas = sa_db.session.execute(text(f'SELECT * FROM "{tabla}"')).mappings().all()
            if not filas:
                continue
            limpias = []
            for fila in filas:
                registro = {}
                for col, val in dict(fila).items():
                    if col.lower() in PG_COLUMNAS_SENSIBLES:
                        continue
                    # Excel y JSON no saben de date, Decimal ni UUID.
                    registro[col] = val if isinstance(val, (int, float, bool, type(None))) else str(val)
                limpias.append(registro)
            datos[tabla] = limpias
        except Exception as exc:
            print(f"[backup] Tabla PostgreSQL '{tabla}' omitida del reporte: {exc}")

    return datos


# ================= EXCEL =================

def generate_excel(db, output_path, since_date=None):
    collections = db.list_collection_names()
    query = _construir_query_fechas(since_date)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        hojas_escritas = 0

        for coll in collections:
            try:
                docs = list(db[coll].find(query))
                if not docs:
                    continue

                # Aplanar los ObjectIds para que Pandas los soporte en Excel
                for d in docs:
                    for k, v in d.items():
                        if isinstance(v, ObjectId):
                            d[k] = str(v)
                        elif isinstance(v, (dict, list)):
                            d[k] = str(v) # Convertir anidados a string

                df = pd.DataFrame(docs)
                sheet_name = coll[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                hojas_escritas += 1
            except Exception as e:
                print(f"Error procesando colección {coll} para Excel: {e}")
                continue

        # Tablas de PostgreSQL. El prefijo distingue de dónde salió cada hoja
        # cuando dos motores tienen entidades de nombre parecido.
        for tabla, filas in _leer_tablas_pg().items():
            try:
                pd.DataFrame(filas).to_excel(
                    writer, sheet_name=f"pg_{tabla}"[:31], index=False,
                )
                hojas_escritas += 1
            except Exception as e:
                print(f"Error procesando tabla PostgreSQL {tabla} para Excel: {e}")

        # openpyxl no acepta un libro sin hojas: en un gimnasio recién creado, o
        # en un incremental sin cambios, no habría ninguna y el archivo saldría
        # corrupto.
        if hojas_escritas == 0:
            pd.DataFrame([{"aviso": "Sin datos en el periodo respaldado."}]).to_excel(
                writer, sheet_name="Vacio", index=False,
            )

# ================= PDF =================

def generate_pdf(db, output_path, since_date=None, mode="FULL"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)

    pdf.cell(0, 10, f"Reporte de Respaldo {mode}", ln=True, align="C")
    pdf.cell(0, 8, f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True)

    if since_date:
        pdf.cell(0, 8, f"Desde: {since_date}", ln=True)

    collections = db.list_collection_names()
    query = _construir_query_fechas(since_date)

    for coll in collections:
        try:
            docs = list(db[coll].find(query).limit(20))
            if not docs:
                continue

            pdf.ln(5)
            pdf.set_font("Arial", "B", 11)
            pdf.cell(0, 8, f"Colección: {coll}", ln=True)
            pdf.set_font("Arial", size=8)
            
            pdf.multi_cell(0, 5, f"Registros exportados en muestra: {len(docs)}")
        except Exception as e:
            print(f"Error en PDF para colección {coll}: {e}")
            continue

    # Inventario de PostgreSQL. Sin esta sección el reporte daba a entender que
    # el respaldo solo cubría Mongo.
    tablas_pg = _leer_tablas_pg()
    if tablas_pg:
        pdf.ln(6)
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 8, "PostgreSQL", ln=True)
        pdf.set_font("Arial", size=8)
        for tabla, filas in tablas_pg.items():
            pdf.multi_cell(0, 5, f"Tabla {tabla}: {len(filas)} registro(s)")

    pdf.output(output_path)

# ================= JSON =================

def generate_incremental_json(db, output_path, since_date):
    collections = db.list_collection_names()
    query = _construir_query_fechas(since_date)
    
    backup_data = {}
    
    for coll in collections:
        try:
            docs = list(db[coll].find(query))
            if docs:
                backup_data[coll] = docs
        except Exception as e:
            print(f"Error extrayendo {coll} para JSON incremental: {e}")

    # Guardamos usando json_util de bson para preservar las fechas y ObjectIds correctamente
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(backup_data, indent=2))

def generate_full_json(db, output_path):
    collections = db.list_collection_names()
    backup_data = {}

    for coll in collections:
        try:
            docs = list(db[coll].find())
            if docs:
                backup_data[coll] = docs
        except Exception as e:
            print(f"Error extrayendo {coll} para JSON FULL: {e}")

    # Aquí NO se añaden las tablas de PostgreSQL: este archivo lo consume
    # restore_service, que trata cada clave de primer nivel como una colección
    # de Mongo y crearía una colección basura. El material de PostgreSQL para
    # consulta va en el Excel y en el PDF del respaldo.
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(backup_data, indent=2))

# ================= MEDIOS (imágenes / videos) =================

def _dir_tiene_archivos(path: str) -> bool:
    """True si el directorio existe y contiene al menos un archivo real (≠ .gitkeep)."""
    if not os.path.isdir(path):
        return False
    for root, _, files in os.walk(path):
        for f in files:
            if f != ".gitkeep" and os.path.isfile(os.path.join(root, f)):
                return True
    return False


def generate_media_archive(output_path: str):
    """
    Empaqueta los medios subidos (imágenes/videos) en un .tar.gz.

    Cada directorio fuente se guarda bajo un prefijo conocido (storage_uploads,
    static_uploads) para poder restaurarlo a su ubicación correcta. Si no hay
    ningún archivo de medios, no se crea el archivo y se devuelve None.
    """
    incluidos = 0
    # compresslevel=6 → buen balance velocidad/tamaño (9 es mucho más lento).
    with tarfile.open(output_path, "w:gz", compresslevel=6) as tar:
        for src, arc in MEDIA_SOURCES:
            if _dir_tiene_archivos(src):
                tar.add(src, arcname=arc)
                incluidos += 1

    if incluidos == 0:
        if os.path.exists(output_path):
            os.remove(output_path)
        return None
    return output_path


# ================= BUNDLE (paquete único) =================

def build_bundle(*, bundle_path, backup_type, archive, json_file,
                 pg_dump, media, xlsx, pdf, mongo_db, pg_db):
    """
    Empaqueta TODO el respaldo en un único .tar.gz portable, con esta estructura:

        manifest.json        metadatos (tipo, fecha, componentes, nombres de BD)
        mongo.archive        dump nativo Mongo (full)   ── o ──
        mongo.json           export Mongo (incremental/diferencial)
        postgres.dump        dump custom PostgreSQL
        media.tar.gz         imágenes/videos (si existen)
        reports/datos.xlsx   reporte Excel (no se restaura)
        reports/reporte.pdf  reporte PDF   (no se restaura)

    Devuelve el dict de componentes incluidos (para el manifest/historial).
    """
    componentes = {}
    with tarfile.open(bundle_path, "w:gz", compresslevel=6) as tar:
        if archive and os.path.exists(archive):
            tar.add(archive, arcname="mongo.archive")
            componentes["mongo"] = "mongo.archive"
        elif json_file and os.path.exists(json_file):
            tar.add(json_file, arcname="mongo.json")
            componentes["mongo"] = "mongo.json"

        if pg_dump and os.path.exists(pg_dump):
            tar.add(pg_dump, arcname="postgres.dump")
            componentes["postgres"] = "postgres.dump"

        if media and os.path.exists(media):
            tar.add(media, arcname="media.tar.gz")
            componentes["media"] = "media.tar.gz"

        if xlsx and os.path.exists(xlsx):
            tar.add(xlsx, arcname="reports/datos.xlsx")
        if pdf and os.path.exists(pdf):
            tar.add(pdf, arcname="reports/reporte.pdf")

        manifest = {
            "formato":     "gympro-bundle",
            "version":     1,
            "tipo":        backup_type,
            "created_at":  _now_iso(),
            "componentes": componentes,
            "db":          {"mongo": mongo_db, "postgres": pg_db},
        }
        data = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    return componentes


# ================= POSTGRES =================

def run_pg_dump(output_path: str) -> None:
    """
    Ejecuta pg_dump en formato custom (-Fc) para PostgreSQL.
    La contraseña se pasa por variable de entorno PGPASSWORD para evitar .pgpass.
    Lanza RuntimeError con el stderr real de pg_dump si falla.
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASSWORD

    result = subprocess.run(
        [
            PG_DUMP_PATH,
            "-h", PG_HOST,
            "-p", PG_PORT,
            "-U", PG_USER,
            "-Fc",          # formato custom binario (restaurable con pg_restore)
            "-d", PG_DB,
            "-f", output_path,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        # Limpiar el archivo parcial/vacío que pg_dump deja al fallar
        if os.path.exists(output_path):
            os.remove(output_path)
        stderr_msg = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"pg_dump falló (código {result.returncode}) — "
            f"host={PG_HOST} user={PG_USER} db={PG_DB} | {stderr_msg}"
        )


def send_email_notification(app, files, backup_type):
    """
    Notifica por correo que el respaldo se generó. NO adjunta el bundle: puede
    pesar varios MB y un adjunto grande hace lento/inestable el envío SMTP
    (timeouts que retrasarían el cierre del backup). El archivo se descarga
    desde el panel.
    """
    try:
        recipient = app.config.get("MAIL_RECIPIENT") or app.config.get("MAIL_USERNAME")
        if not recipient:
            return False

        bundle = files.get("bundle")
        nombre = os.path.basename(bundle) if bundle else "—"

        msg = Message(
            subject=f"[Backup] Respaldo {backup_type.upper()} generado",
            sender=app.config.get("MAIL_USERNAME"),
            recipients=[recipient],
            body=(
                f"El respaldo {backup_type} se generó correctamente.\n\n"
                f"Paquete único (Mongo + PostgreSQL + medios): {nombre}\n"
                f"Descárgalo desde el panel de Backups.\n\n"
                f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False

# ================= MAIN =================

def run_backup(job_id: str, backup_type: str, app):
    with app.app_context():
        backup_state.update({
            "is_running": True,
            "progress_percentage": 10,
            "current_step": "Iniciando respaldo",
            "job_id": job_id,
            "generated_files": {}
        })

        try:
            timestamp = now_str()
            path = ensure_dirs(backup_type)
            db = get_db()

            # URI de MongoDB leída de MONGO_URI (misma fuente que mongo.py y spark_config.py)
            # NOTA: mongodump debe estar disponible en el contenedor.
            # Para instalar: apt-get install -y mongodb-database-tools (ver Dockerfile)
            mongo_uri = os.getenv("MONGO_URI", "mongodb://mongo:27017/gymdb")
            db_name = os.getenv("MONGO_DB", "gymdb")

            archive   = None
            pg_dump   = None
            json_file = None
            xlsx      = None
            pdf       = None
            media     = None
            file_size = 0

            # Reportes Excel/PDF: OPCIONALES. Son lo que más RAM/tiempo consume
            # (pandas + fpdf cargan todas las colecciones en memoria) y NO se usan
            # para restaurar. Por defecto OFF → backup rápido y sin riesgo de OOM.
            # Activar con BACKUP_INCLUDE_REPORTS=true si se quieren dentro del bundle.
            include_reports = os.getenv("BACKUP_INCLUDE_REPORTS", "false").lower() in ("1", "true", "yes")

            if backup_type == "full":
                backup_state["current_step"] = "Generando dump MongoDB (.archive)"
                backup_state["progress_percentage"] = 20

                archive = os.path.join(path, f"backup_full_{timestamp}.archive")
                pg_dump = os.path.join(path, f"backup_pg_full_{timestamp}.dump")

                # Dump nativo de Mongo (mongodump): rápido y completo. Es el
                # artefacto que se restaura; NO se genera JSON completo porque
                # sería redundante con el .archive y muy costoso en memoria.
                subprocess.run(
                    [MONGODUMP_PATH, "--uri", mongo_uri, "--db", db_name, f"--archive={archive}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
                )

                backup_state["progress_percentage"] = 45
                backup_state["current_step"] = "Generando dump PostgreSQL (.dump)"
                run_pg_dump(pg_dump)

                if include_reports:
                    backup_state["progress_percentage"] = 65
                    backup_state["current_step"] = "Generando reportes (Excel/PDF)"
                    xlsx = os.path.join(path, f"backup_full_{timestamp}.xlsx")
                    pdf  = os.path.join(path, f"backup_full_{timestamp}.pdf")
                    generate_excel(db, xlsx)
                    generate_pdf(db, pdf, mode="FULL")

                save_last_backup(LAST_FULL_BACKUP_FILE)
                save_last_backup(LAST_BACKUP_FILE)

            elif backup_type in ("differential", "incremental"):
                ref_file = LAST_FULL_BACKUP_FILE if backup_type == "differential" else LAST_BACKUP_FILE
                since = get_last_backup(ref_file)
                if not since:
                    falta = "FULL" if backup_type == "differential" else "previo"
                    raise Exception(
                        f"No existe respaldo {falta}. Ejecute primero un backup completo."
                    )

                etiqueta = "diff" if backup_type == "differential" else "inc"
                backup_state["current_step"] = f"Generando backup {backup_type} MongoDB (.json)"
                backup_state["progress_percentage"] = 25

                json_file = os.path.join(path, f"backup_{etiqueta}_{timestamp}.json")
                pg_dump   = os.path.join(path, f"backup_pg_{etiqueta}_{timestamp}.dump")

                generate_incremental_json(db, json_file, since)

                backup_state["progress_percentage"] = 50
                backup_state["current_step"] = "Generando dump PostgreSQL (.dump)"
                run_pg_dump(pg_dump)

                if include_reports:
                    backup_state["progress_percentage"] = 65
                    backup_state["current_step"] = "Generando reportes (Excel/PDF)"
                    xlsx = os.path.join(path, f"backup_{etiqueta}_{timestamp}.xlsx")
                    pdf  = os.path.join(path, f"backup_{etiqueta}_{timestamp}.pdf")
                    generate_excel(db, xlsx, since)
                    generate_pdf(db, pdf, since, backup_type.upper())

                save_last_backup(LAST_BACKUP_FILE)

            else:
                raise Exception("Tipo de respaldo no válido")

            # Medios (imágenes/videos subidos). None si no hay.
            backup_state["current_step"] = "Empaquetando imágenes/videos"
            backup_state["progress_percentage"] = 85
            media = generate_media_archive(
                os.path.join(path, f"backup_media_{timestamp}.tar.gz")
            )

            # ── Paquete único: todo el respaldo en un solo archivo portable ─────
            backup_state["current_step"] = "Generando paquete único"
            backup_state["progress_percentage"] = 92
            bundle = os.path.join(path, f"backup_{backup_type}_{timestamp}.tar.gz")
            build_bundle(
                bundle_path=bundle,
                backup_type=backup_type,
                archive=archive,
                json_file=json_file,
                pg_dump=pg_dump,
                media=media,
                xlsx=xlsx,
                pdf=pdf,
                mongo_db=db_name,
                pg_db=PG_DB,
            )

            # Los componentes sueltos ya viven dentro del bundle → eliminarlos.
            for f in (archive, json_file, pg_dump, media, xlsx, pdf):
                if f and os.path.exists(f) and os.path.abspath(f) != os.path.abspath(bundle):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

            if os.path.exists(bundle):
                file_size = os.path.getsize(bundle) / (1024 * 1024)  # MB

            # Un único artefacto descargable/restaurable
            backup_state["generated_files"] = {"bundle": bundle}

            backup_state["progress_percentage"] = 90
            backup_state["current_step"] = "Guardando historial"

            save_history({
                "date":    _now_iso(),
                "type":    backup_type,
                "status":  "completado",
                "job_id":  job_id,
                "size":    f"{file_size:.2f} MB",
                "files": {
                    k: os.path.basename(v)
                    for k, v in backup_state["generated_files"].items()
                    if v and os.path.exists(v)
                },
            })

            backup_state["progress_percentage"] = 95
            backup_state["current_step"] = "Enviando email"

            send_email_notification(app, backup_state["generated_files"], backup_type)

            backup_state["current_step"] = "Completado"
            backup_state["progress_percentage"] = 100
            backup_state["last_backup"] = datetime.now(timezone.utc)

            # Purgar backups viejos — mantener solo los 3 más recientes
            try:
                cleanup_old_backups(max_keep=3)
            except Exception as cleanup_err:
                print(f"[backup cleanup] Error al purgar: {cleanup_err}")

        except Exception as e:
            backup_state["current_step"] = f"Error: {str(e)}"
            backup_state["progress_percentage"] = 0
            print("[BACKUP ERROR]", e)

            save_history({
                "date":   _now_iso(),
                "type":   backup_type,
                "status": "error",
                "job_id": job_id,
                "error":  str(e),
            })

        finally:
            backup_state["is_running"] = False