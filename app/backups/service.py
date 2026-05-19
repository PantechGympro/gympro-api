import os
import subprocess
import json
import pandas as pd
from fpdf import FPDF
from datetime import datetime
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
    os.makedirs(BACKUP_DIR, exist_ok=True)   # garantiza que el dir existe antes de escribir
    history = load_history()
    history.insert(0, entry)
    history = history[:10]  # máximo 10 registros
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


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


# ================= EXCEL =================

def generate_excel(db, output_path, since_date=None):
    collections = db.list_collection_names()
    query = _construir_query_fechas(since_date)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
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
            except Exception as e:
                print(f"Error procesando colección {coll} para Excel: {e}")
                continue

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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(backup_data, indent=2))

# ================= EMAIL =================

def run_pg_dump(output_path: str) -> None:
    """
    Ejecuta pg_dump en formato custom (-Fc) para PostgreSQL.
    Lanza subprocess.CalledProcessError si falla.
    La contraseña se pasa por variable de entorno PGPASSWORD para evitar .pgpass.
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = PG_PASSWORD

    subprocess.run(
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
        check=True,
    )


def send_email_with_attachments(app, files, backup_type):
    try:
        recipient = app.config.get("MAIL_RECIPIENT") or app.config.get("MAIL_USERNAME")

        msg = Message(
            subject=f"[Backup] Respaldo {backup_type.upper()} generado",
            sender=app.config.get("MAIL_USERNAME"),
            recipients=[recipient],
            body=(
                f"El respaldo {backup_type} se generó correctamente.\n\n"
                f"• MongoDB: dump nativo + JSON + Excel + PDF\n"
                f"• PostgreSQL: pg_dump (formato custom)\n\n"
                f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        )

        for file_type, file_path in files.items():
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    msg.attach(
                        os.path.basename(file_path), 
                        "application/octet-stream", 
                        f.read()
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
            file_size = 0

            if backup_type == "full":
                backup_state["current_step"] = "Generando dump MongoDB (.archive)"
                backup_state["progress_percentage"] = 15

                archive   = os.path.join(path, f"backup_full_{timestamp}.archive")
                json_file = os.path.join(path, f"backup_full_{timestamp}.json")
                xlsx      = os.path.join(path, f"backup_full_{timestamp}.xlsx")
                pdf       = os.path.join(path, f"backup_full_{timestamp}.pdf")
                pg_dump   = os.path.join(path, f"backup_pg_full_{timestamp}.dump")

                subprocess.run(
                    [MONGODUMP_PATH, "--uri", mongo_uri, "--db", db_name, f"--archive={archive}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
                )

                backup_state["progress_percentage"] = 30
                backup_state["current_step"] = "Generando dump PostgreSQL (.dump)"
                run_pg_dump(pg_dump)

                backup_state["progress_percentage"] = 50
                backup_state["current_step"] = "Generando JSON completo"
                generate_full_json(db, json_file)

                backup_state["progress_percentage"] = 65
                backup_state["current_step"] = "Generando Excel"
                generate_excel(db, xlsx)

                backup_state["progress_percentage"] = 80
                backup_state["current_step"] = "Generando PDF"
                generate_pdf(db, pdf, mode="FULL")

                save_last_backup(LAST_FULL_BACKUP_FILE)
                save_last_backup(LAST_BACKUP_FILE)

            elif backup_type == "differential":
                since = get_last_backup(LAST_FULL_BACKUP_FILE)
                if not since:
                    raise Exception("No existe respaldo FULL previo. Ejecute primero un backup completo.")

                backup_state["current_step"] = "Generando backup diferencial MongoDB (.json)"
                backup_state["progress_percentage"] = 20

                json_file = os.path.join(path, f"backup_diff_{timestamp}.json")
                xlsx      = os.path.join(path, f"backup_diff_{timestamp}.xlsx")
                pdf       = os.path.join(path, f"backup_diff_{timestamp}.pdf")
                pg_dump   = os.path.join(path, f"backup_pg_diff_{timestamp}.dump")

                generate_incremental_json(db, json_file, since)

                backup_state["progress_percentage"] = 40
                backup_state["current_step"] = "Generando dump PostgreSQL (.dump)"
                run_pg_dump(pg_dump)

                backup_state["progress_percentage"] = 65
                generate_excel(db, xlsx, since)

                backup_state["progress_percentage"] = 80
                generate_pdf(db, pdf, since, "DIFERENCIAL")

                save_last_backup(LAST_BACKUP_FILE)

            elif backup_type == "incremental":
                since = get_last_backup(LAST_BACKUP_FILE)
                if not since:
                    raise Exception("No existe respaldo previo. Ejecute primero un backup completo.")

                backup_state["current_step"] = "Generando backup incremental MongoDB (.json)"
                backup_state["progress_percentage"] = 20

                json_file = os.path.join(path, f"backup_inc_{timestamp}.json")
                xlsx      = os.path.join(path, f"backup_inc_{timestamp}.xlsx")
                pdf       = os.path.join(path, f"backup_inc_{timestamp}.pdf")
                pg_dump   = os.path.join(path, f"backup_pg_inc_{timestamp}.dump")

                generate_incremental_json(db, json_file, since)

                backup_state["progress_percentage"] = 40
                backup_state["current_step"] = "Generando dump PostgreSQL (.dump)"
                run_pg_dump(pg_dump)

                backup_state["progress_percentage"] = 65
                generate_excel(db, xlsx, since)

                backup_state["progress_percentage"] = 80
                generate_pdf(db, pdf, since, "INCREMENTAL")

                save_last_backup(LAST_BACKUP_FILE)

            else:
                raise Exception("Tipo de respaldo no válido")

            # Tamaño basado en el archivo principal de Mongo
            main_file = archive if archive else json_file
            if main_file and os.path.exists(main_file):
                file_size = os.path.getsize(main_file) / (1024 * 1024)  # MB

            backup_state["generated_files"] = {
                "db_dump":  main_file,
                "pg_dump":  pg_dump,
                "json":     json_file,
                "excel":    xlsx,
                "pdf":      pdf,
            }

            backup_state["progress_percentage"] = 90
            backup_state["current_step"] = "Guardando historial"

            save_history({
                "date": datetime.now().isoformat(),
                "type": backup_type,
                "size": f"{file_size:.2f} MB",
                "url": main_file
            })

            backup_state["progress_percentage"] = 95
            backup_state["current_step"] = "Enviando email"
            
            send_email_with_attachments(app, backup_state["generated_files"], backup_type)

            backup_state["current_step"] = "Completado"
            backup_state["progress_percentage"] = 100
            backup_state["last_backup"] = datetime.now()

        except Exception as e:
            backup_state["current_step"] = f"Error: {str(e)}"
            backup_state["progress_percentage"] = 0
            print("[BACKUP ERROR]", e)
            
            save_history({
                "date": datetime.now().isoformat(),
                "type": backup_type,
                "size": "ERROR",
                "url": None,
                "erro