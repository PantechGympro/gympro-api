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
#
# Un documento pertenece a un gimnasio de tres maneras distintas y cada una
# necesita su propia consulta:
#
#   1. Lleva el id del gimnasio encima          → DIRECT_COLLECTIONS
#   2. Cuelga de un miembro                     → BY_MEMBER
#   3. Cuelga de otro documento que ya se sacó  → cascada rutinas/dietas
#
# El tercer caso era el que faltaba y por eso los ejercicios de las rutinas no
# se respaldaban: `rutinas` sí se guardaba, pero un ejercicio no conoce ni al
# gimnasio ni al miembro. Vive colgando de `rutina_dias`, que a su vez cuelga de
# `rutinas`. Restaurar un respaldo antiguo devolvía rutinas con cero días y cero
# ejercicios: la cáscara sin el contenido.

DIRECT_COLLECTIONS = {
    # colección → campo de filtro con id numérico del gimnasio
    "miembros":                 "id_gimnasio_pg",
    "pagos":                    "id_gimnasio",
    "asistencias":              "id_gimnasio",
    "sesiones":                 "id_gimnasio",
    "rutinas":                  "id_gimnasio",
    "dietas":                   "id_gimnasio",
    "ventas":                   "id_gimnasio",
    "productos":                "id_gimnasio",
    "citas":                    "id_gimnasio_pg",
    "mensajes_chat":            "id_gimnasio_pg",
    "pt_solicitudes":           "id_gimnasio_pg",
    "historial_metricas":       "id_gimnasio_pg",
    "entrenamientos_realizados": "id_gimnasio_pg",
}

# Colecciones que cuelgan de un miembro por su ObjectId.
BY_MEMBER = ("miembro_membresia", "progreso_fisico", "recetas", "consumo_recetas")


def _ids(docs) -> list:
    """ObjectIds de una lista de documentos ya extraídos."""
    return [d["_id"] for d in docs if d.get("_id") is not None]


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

    def _buscar(coll: str, query: dict, aplicar_fecha: bool = True) -> list:
        """Consulta tolerante: una colección ausente no aborta el respaldo."""
        if aplicar_fecha and date_filter:
            query = {"$and": [query, date_filter]}
        try:
            return list(db[coll].find(query))
        except Exception as exc:
            print(f"[backup gym {gym_id}] Colección '{coll}' omitida: {exc}")
            return []

    # 1. Documentos que llevan el id del gimnasio.
    for coll, field in DIRECT_COLLECTIONS.items():
        docs = _buscar(coll, {field: gym_id})
        if docs:
            result[coll] = docs

    # 2. Documentos que cuelgan de un miembro. Se guardan las dos claves con las
    #    que el sistema referencia a un miembro: el ObjectId de Mongo y el id
    #    numérico del usuario en PostgreSQL. Colecciones distintas usan una u
    #    otra, y buscar por la equivocada devuelve cero resultados en silencio.
    miembros_docs = list(db.miembros.find(
        {"id_gimnasio_pg": gym_id}, {"_id": 1, "id_usuario_pg": 1},
    ))
    member_ids = _ids(miembros_docs)
    member_pg_ids = [
        d["id_usuario_pg"] for d in miembros_docs if d.get("id_usuario_pg") is not None
    ]
    if member_ids:
        for coll in BY_MEMBER:
            docs = _buscar(coll, {"id_miembro": {"$in": member_ids}})
            if docs:
                result[coll] = docs

    # 3. Cascada de rutinas: rutina → rutina_dias → rutina_ejercicios.
    #    El filtro de fecha NO se aplica aquí. Un día o un ejercicio no tienen
    #    fecha propia, así que filtrarlos por fecha los dejaría siempre fuera y
    #    un respaldo incremental restauraría rutinas vacías.
    rutina_ids = _ids(result.get("rutinas", []))
    if rutina_ids:
        dias = _buscar("rutina_dias", {"id_rutina": {"$in": rutina_ids}}, aplicar_fecha=False)
        if dias:
            result["rutina_dias"] = dias
            dia_ids = _ids(dias)
            if dia_ids:
                ejercicios = _buscar(
                    "rutina_ejercicios",
                    {"id_rutina_dia": {"$in": dia_ids}},
                    aplicar_fecha=False,
                )
                if ejercicios:
                    result["rutina_ejercicios"] = ejercicios

    # 4. Asignaciones de rutina a miembros: el vínculo entre ambos lados. Sin
    #    esto el miembro restaurado no vería ninguna rutina aunque las rutinas
    #    sí estuvieran en la base.
    #    `miembro_rutina` referencia la rutina por ObjectId;
    #    `rutinas_asignadas` referencia al miembro por su id de PostgreSQL.
    if rutina_ids or member_ids:
        criterios = []
        if rutina_ids:
            criterios.append({"id_rutina": {"$in": rutina_ids}})
        if member_ids:
            criterios.append({"id_miembro": {"$in": member_ids}})
        if criterios:
            docs = _buscar("miembro_rutina", {"$or": criterios}, aplicar_fecha=False)
            if docs:
                result["miembro_rutina"] = docs

    if member_pg_ids or rutina_ids:
        criterios = []
        if member_pg_ids:
            criterios.append({"id_miembro_pg": {"$in": member_pg_ids}})
        if rutina_ids:
            criterios.append({"id_rutina": {"$in": rutina_ids}})
        docs = _buscar("rutinas_asignadas", {"$or": criterios}, aplicar_fecha=False)
        if docs:
            result["rutinas_asignadas"] = docs

    # 5. Perfiles y certificaciones del staff del gimnasio. Se resuelven contra
    #    PostgreSQL porque el staff vive ahí, no en Mongo.
    try:
        from app.models.pg.usuario import Usuario
        staff_ids = [
            u.id for u in Usuario.query.filter_by(id_gimnasio=gym_id).all()
        ]
    except Exception as exc:
        print(f"[backup gym {gym_id}] No se pudo listar el staff: {exc}")
        staff_ids = []

    if staff_ids:
        for coll in ("perfil_entrenador", "certificaciones_entrenador",
                     "evaluaciones_entrenador", "logros_entrenador"):
            docs = _buscar(coll, {"id_entrenador_pg": {"$in": staff_ids}}, aplicar_fecha=False)
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
            if doc.get("_id") is None:
                continue

            # Doble verificación de tenant. Si el documento declara a qué
            # gimnasio pertenece y no es este, se descarta: subir por error el
            # respaldo de otro gimnasio no debe mezclar datos.
            #
            # Los documentos que NO declaran gimnasio —rutina_dias,
            # rutina_ejercicios, miembro_membresia, progreso_fisico— cuelgan de
            # un padre que sí lo declara, así que se aceptan tal cual: es la
            # única forma de restaurar el contenido de una rutina.
            declara = "id_gimnasio" in doc or "id_gimnasio_pg" in doc
            owns = (
                doc.get("id_gimnasio") == gym_id
                or doc.get("id_gimnasio_pg") == gym_id
            )
            if declara and not owns:
                continue

            coll.update_one({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
            restored += 1

    return restored
