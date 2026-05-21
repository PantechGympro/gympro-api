"""
backups/tenant_service.py — Respaldos y restauración SCOPED al gimnasio.

A diferencia del servicio global (service.py), aquí SOLO se exportan/importan
los documentos MongoDB que pertenecen al gimnasio del owner autenticado.

Colecciones exportadas y campo de filtro:
  miembros          → id_gimnasio_pg
  pagos             → id_gimnasio
  asistencias       → id_gimnasio
  sesiones          → id_gimnasio
  rutinas           → id_gimnasio
  dietas            → id_gimnasio
  ventas            → id_gimnasio
  productos         → id_gimnasio
  progreso_fisico   → (join via id_miembro de este gym)
  miembro_membresia → (join via id_miembro de este gym)

Historia por gym: storage/backups/gym_{id}/history.json
Archivos por gym: storage/backups/gym_{id}/{tipo}/backup_{ts}.json
Se conservan SOLO los 3 más recientes; los demás se borran en disco.
"""
import os
import json
import threading
from datetime import datetime, timezone
from bson import json_util, ObjectId

from app.mongo import get_db

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
BACKUPS_ROOT = os.path.join(BASE_DIR, "storage", "backups")

MAX_BACKUPS_PER_GYM = 3

# ── Estado en memoria (per-gym, thread-safe con lock simple) ──────────────────
_states: dict = {}
_lock = threading.Lock()

def get_state(gym_id: int) -> dict:
    with _lock:
        if gym_id not in _states:
            _states[gym_id] = {
                "is_running": False,
                "progress": 0,
                "step": "idle",
                "job_id": None,
                "last_file": None,
                "error": None,
            }
        return _states[gym_id]

def update_state(gym_id: int, **kwargs):
    with _lock:
        _states.setdefault(gym_id, {}).update(kwargs)


# ── Directorios ───────────────────────────────────────────────────────────────
def gym_dir(gym_id: int) -> str:
    d = os.path.join(BACKUPS_ROOT, f"gym_{gym_id}")
    os.makedirs(d, exist_ok=True)
    return d

def type_dir(gym_id: int, btype: str) -> str:
    d = os.path.join(gym_dir(gym_id), btype)
    os.makedirs(d, exist_ok=True)
    return d

def history_path(gym_id: int) -> str:
    return os.path.join(gym_dir(gym_id), "history.json")

def last_full_path(gym_id: int) -> str:
    return os.path.join(gym_dir(gym_id), "last_full.txt")

def last_any_path(gym_id: int) -> str:
    return os.path.join(gym_dir(gym_id), "last_any.txt")


# ── Historia ──────────────────────────────────────────────────────────────────
def load_history(gym_id: int) -> list:
    p = history_path(gym_id)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_history_entry(gym_id: int, entry: dict):
    history = load_history(gym_id)
    history.insert(0, entry)
    with open(history_path(gym_id), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def _save_timestamp(path: str):
    with open(path, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

def _read_timestamp(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read().strip()


# ── Extracción MongoDB filtrada por gimnasio ──────────────────────────────────
DIRECT_COLLECTIONS = {
    # colección → campo de filtro con id numérico del gimnasio
    "miembros":    "id_gimnasio_pg",
    "pagos":       "id_gimnasio",
    "asistencias": "id_gimnasio",
    "sesiones":    "id_gimnasio",
    "rutinas":     "id_gimnasio",
    "dietas":      "id_gimnasio",
    "ventas":      "id_gimnasio",
    "productos":   "id_gimnasio",
}

def _export_gym_data(gym_id: int, since_iso: str = None) -> dict:
    """Devuelve dict {coleccion: [docs]} con todos los datos del gym."""
    db = get_db()
    result = {}

    date_filter = {}
    if since_iso:
        since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        date_filter = {"$or": [
            {"fecha_registro": {"$gte": since_dt}},
            {"fecha_pago":     {"$gte": since_dt}},
            {"fecha":          {"$gte": since_dt}},
            {"created_at":     {"$gte": since_dt}},
            {"updated_at":     {"$gte": since_dt}},
        ]}

    for coll, field in DIRECT_COLLECTIONS.items():
        query = {field: gym_id}
        if date_filter:
            query = {"$and": [query, date_filter]}
        docs = list(db[coll].find(query))
        if docs:
            result[coll] = docs

    # Colecciones por join: obtener ids de miembros del gym primero
    member_ids = [d["_id"] for d in db.miembros.find(
        {"id_gimnasio_pg": gym_id}, {"_id": 1}
    )]
    if member_ids:
        for coll in ("miembro_membresia", "progreso_fisico"):
            query = {"id_miembro": {"$in": member_ids}}
            if date_filter:
                query = {"$and": [query, date_filter]}
            docs = list(db[coll].find(query))
            if docs:
                result[coll] = docs

    return result


def _write_backup_file(gym_id: int, btype: str, data: dict) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{btype}_{ts}.json"
    path = os.path.join(type_dir(gym_id, btype), filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(data, indent=2))
    return path


def _cleanup_old_backups(gym_id: int):
    """Mantiene solo los últimos MAX_BACKUPS_PER_GYM en disco e historial."""
    history = load_history(gym_id)
    completed = [h for h in history if h.get("status") == "completado"]
    errors    = [h for h in history if h.get("status") == "error"]

    to_remove = completed[MAX_BACKUPS_PER_GYM:]
    for entry in to_remove:
        fpath = entry.get("filepath")
        if fpath and os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass

    new_history = (
        completed[:MAX_BACKUPS_PER_GYM] +
        errors[:5]
    )
    new_history.sort(key=lambda h: h.get("date", ""), reverse=True)
    with open(history_path(gym_id), "w", encoding="utf-8") as f:
        json.dump(new_history, f, indent=2, ensure_ascii=False)


# ── Runner principal (ejecutado en thread) ────────────────────────────────────
def run_tenant_backup(job_id: str, gym_id: int, btype: str, app):
    with app.app_context():
        update_state(gym_id, is_running=True, progress=5,
                     step="Iniciando respaldo", job_id=job_id,
                     last_file=None, error=None)
        try:
            since = None

            if btype == "full":
                update_state(gym_id, progress=20, step="Exportando datos del gimnasio")
                data = _export_gym_data(gym_id)
                update_state(gym_id, progress=70, step="Escribiendo archivo")
                path = _write_backup_file(gym_id, "full", data)
                _save_timestamp(last_full_path(gym_id))
                _save_timestamp(last_any_path(gym_id))

            elif btype == "differential":
                since = _read_timestamp(last_full_path(gym_id))
                if not since:
                    raise ValueError(
                        "No existe respaldo FULL previo. "
                        "Ejecuta primero un respaldo Completo."
                    )
                update_state(gym_id, progress=20,
                             step=f"Exportando cambios desde {since[:10]}")
                data = _export_gym_data(gym_id, since)
                update_state(gym_id, progress=70, step="Escribiendo archivo")
                path = _write_backup_file(gym_id, "differential", data)
                _save_timestamp(last_any_path(gym_id))

            elif btype == "incremental":
                since = _read_timestamp(last_any_path(gym_id))
                if not since:
                    raise ValueError(
                        "No existe respaldo previo. "
                        "Ejecuta primero un respaldo Completo."
                    )
                update_state(gym_id, progress=20,
                             step=f"Exportando cambios desde {since[:10]}")
                data = _export_gym_data(gym_id, since)
                update_state(gym_id, progress=70, step="Escribiendo archivo")
                path = _write_backup_file(gym_id, "incremental", data)
                _save_timestamp(last_any_path(gym_id))

            else:
                raise ValueError(f"Tipo de respaldo inválido: {btype}")

            # Contar documentos exportados
            total_docs = sum(len(v) for v in data.values())
            size_kb = round(os.path.getsize(path) / 1024, 1)

            update_state(gym_id, progress=90, step="Guardando historial")
            save_history_entry(gym_id, {
                "date":     datetime.now(timezone.utc).isoformat(),
                "type":     btype,
                "status":   "completado",
                "job_id":   job_id,
                "size":     f"{size_kb} KB",
                "docs":     total_docs,
                "filename": os.path.basename(path),
                "filepath": path,
                "since":    since,
            })

            _cleanup_old_backups(gym_id)

            update_state(gym_id, is_running=False, progress=100,
                         step="Completado", last_file=path)

        except Exception as exc:
            err_msg = str(exc)
            update_state(gym_id, is_running=False, progress=0,
                         step=f"Error: {err_msg}", error=err_msg)
            save_history_entry(gym_id, {
                "date":   datetime.now(timezone.utc).isoformat(),
                "type":   btype,
                "status": "error",
                "job_id": job_id,
                "error":  err_msg,
            })


# ── Restauración ──────────────────────────────────────────────────────────────
def restore_tenant_backup(filepath: str, gym_id: int):
    """
    Restaura documentos de un JSON de respaldo.
    SOLO sobrescribe documentos del gym; no toca otros tenants.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Archivo no encontrado: {filepath}")

    with open(filepath, encoding="utf-8") as f:
        data = json_util.loads(f.read())

    db = get_db()
    restored = 0

    for coll_name, docs in data.items():
        coll = db[coll_name]
        for doc in docs:
            # Doble verificación: el doc pertenece a este gym
            owns = (
                doc.get("id_gimnasio") == gym_id
                or doc.get("id_gimnasio_pg") == gym_id
            )
            # Para colecciones join (miembro_membresia, progreso_fisico)
            # no tienen id_gimnasio directo — se confía en el archivo
            if not owns and "id_gimnasio" in doc and "id_gimnasio_pg" in doc:
                continue
            coll.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
            restored += 1

    return restored
