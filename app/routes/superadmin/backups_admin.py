"""
superadmin/backups_admin.py — Gestión centralizada de backups de la plataforma.

Reutiliza el servicio de backups existente (app.backups.service) y añade:
  - Selección de tipo: full | incremental | differential
  - Programación de backups automáticos (almacenada en MongoDB)
  - Log centralizado multi-gimnasio
  - Descarga y restauración (misma lógica que backups/routes.py)

Endpoints:
    GET  /api/superadmin/backups/status          estado actual del proceso
    POST /api/superadmin/backups/trigger         iniciar backup con tipo
    GET  /api/superadmin/backups/historial       log completo de backups
    GET  /api/superadmin/backups/schedule        configuración de programación
    POST /api/superadmin/backups/schedule        actualizar programación
    GET  /api/superadmin/backups/download/<f>    descargar archivo
    POST /api/superadmin/backups/restore         restaurar desde archivo
"""
from flask import Blueprint, jsonify, request, current_app, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
from datetime import datetime
import threading
import uuid
import os
import json

from app.backups.service import (
    backup_state,
    run_backup,
    BACKUP_DIR,
    HISTORY_FILE,
    load_history,
    save_history,
)
from app.backups.restore_service import restore_backup_file, RESTORABLE_EXTS
from app.utils.security import require_role
from app.routes.ia.spark_config import get_mongo_db

backups_admin_bp = Blueprint("backups_admin", __name__)

# Clave del documento de configuración de programación en MongoDB
_SCHEDULE_DOC_ID = "backup_schedule_config"
_SCHEDULE_COLLECTION = "backups_config"

# Límite de tamaño para restauración por upload externo (dumps grandes).
# Se aplica por-request, no afecta el MAX_CONTENT_LENGTH global (15 MB).
_MAX_RESTORE_UPLOAD = 1024 * 1024 * 1024  # 1 GB


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_schedule_config() -> dict:
    """Lee la configuración de programación desde MongoDB."""
    try:
        mdb = get_mongo_db()
        doc = mdb[_SCHEDULE_COLLECTION].find_one({"_id": _SCHEDULE_DOC_ID}) or {}
        doc.pop("_id", None)
        return doc or {
            "enabled":      True,
            "cron":         "0 3 * * *",   # 3:00 AM diario
            "tipo_default": "incremental",
            "full_dia":     "sunday",       # backup full los domingos
            "retener_dias": 30,
        }
    except Exception:
        return {}


def _save_schedule_config(config: dict) -> None:
    mdb = get_mongo_db()
    mdb[_SCHEDULE_COLLECTION].replace_one(
        {"_id": _SCHEDULE_DOC_ID},
        {"_id": _SCHEDULE_DOC_ID, **config, "updated_at": datetime.utcnow()},
        upsert=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@backups_admin_bp.route("/backups/status", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def backup_status():
    """Estado actual del proceso de backup."""
    # last_backup: prioridad al estado en memoria (backup en curso o reciente),
    # si no hay, leer el último "completado" del historial (persiste entre reinicios).
    last_bk = backup_state["last_backup"]
    if not last_bk:
        try:
            hist = load_history()
            completados = [h for h in hist if h.get("status") == "completado"]
            if completados:
                last_bk = completados[0].get("date")   # ya es ISO string
        except Exception:
            pass

    response = {
        "is_running":          backup_state["is_running"],
        "progress_percentage": backup_state["progress_percentage"],
        "current_step":        backup_state["current_step"],
        "last_backup":         (
            last_bk.isoformat() if hasattr(last_bk, "isoformat") else last_bk
        ),
        "files": {},
    }
    if backup_state.get("generated_files"):
        for f_type, f_path in backup_state["generated_files"].items():
            if f_path and os.path.exists(f_path):
                filename = os.path.basename(f_path)
                response["files"][f_type] = f"/api/superadmin/backups/download/{filename}"
    return jsonify(response), 200


@backups_admin_bp.route("/backups/trigger", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def trigger_backup():
    """
    Inicia un backup de forma asíncrona.

    Body JSON:
        {
            "tipo": "full" | "incremental" | "differential"
        }

    Los tres tipos se diferencian en el servicio de backup:
        full          — volcado completo de PG + Mongo
        incremental   — solo cambios desde el último backup (basado en oplog Mongo / WAL PG)
        differential  — cambios desde el último backup full
    """
    if backup_state["is_running"]:
        return jsonify({
            "msg":    "Ya hay un backup en curso.",
            "status": "running",
            "job_id": backup_state.get("job_id"),
        }), 409

    data       = request.get_json() or {}
    tipo       = data.get("tipo", "incremental")
    tipos_ok   = ["full", "incremental", "differential"]

    if tipo not in tipos_ok:
        return jsonify({"msg": f"Tipo inválido. Usa: {', '.join(tipos_ok)}"}), 400

    job_id       = f"sa_job_{uuid.uuid4().hex[:8]}"
    app_instance = current_app._get_current_object()

    thread = threading.Thread(
        target=run_backup,
        args=(job_id, tipo, app_instance),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "msg":    f"Backup {tipo} iniciado.",
        "job_id": job_id,
        "status": "running",
        "tipo":   tipo,
    }), 202


@backups_admin_bp.route("/backups/historial", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def historial_backups():
    """
    Historial completo de backups de la plataforma.

    Query params:
        tipo   str  — filtrar por tipo (full | incremental | differential | restore)
        limit  int  — máximo de resultados (default 50)
    """
    tipo_param  = request.args.get("tipo")
    limit       = min(200, int(request.args.get("limit", 50)))

    history = load_history()

    if tipo_param:
        history = [h for h in history if h.get("type") == tipo_param]

    return jsonify({
        "historial": history[:limit],
        "total":     len(history),
    }), 200


@backups_admin_bp.route("/backups/historial/<job_id>", methods=["DELETE"])
@jwt_required()
@require_role("superadmin")
def delete_historial_entry(job_id: str):
    """
    Elimina una entrada del historial por job_id.
    Si tiene archivos asociados ('files'), los borra del disco también.
    job_id especial: '__errors__' elimina todas las entradas con status 'error'.
    """
    history = load_history()

    def _is_junk(h: dict) -> bool:
        """
        Detecta entradas que no son backups completados, cubriendo ambos formatos:
        - Nuevo formato:  status == "error"
        - Viejo formato:  size == "ERROR"  o  tiene campo "error"  o  status == "iniciado"
        - Sin status y sin files → entrada huérfana (nunca completó)
        """
        s = h.get("status")
        return (
            s == "error"
            or s == "iniciado"
            or h.get("size") == "ERROR"
            or (h.get("error") is not None)
            or (s is None and not h.get("files") and not h.get("url"))
        )

    if job_id == "__errors__":
        to_remove = [h for h in history if _is_junk(h)]
        new_hist  = [h for h in history if not _is_junk(h)]
    else:
        to_remove = [h for h in history if h.get("job_id") == job_id]
        new_hist  = [h for h in history if h.get("job_id") != job_id]

    if not to_remove:
        return jsonify({"msg": "Entrada no encontrada."}), 404

    # Eliminar archivos físicos del backup
    deleted_files = []
    for entry in to_remove:
        for fname in (entry.get("files") or {}).values():
            if fname:
                for root, _, files in os.walk(BACKUP_DIR):
                    if fname in files:
                        try:
                            os.remove(os.path.join(root, fname))
                            deleted_files.append(fname)
                        except OSError:
                            pass
                        break

    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(new_hist, f, indent=2, ensure_ascii=False)

    return jsonify({
        "msg":           f"{len(to_remove)} entrada(s) eliminada(s).",
        "deleted_files": deleted_files,
    }), 200


@backups_admin_bp.route("/backups/schedule", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def get_schedule():
    """Devuelve la configuración actual de programación de backups automáticos."""
    config = _get_schedule_config()
    return jsonify(config), 200


@backups_admin_bp.route("/backups/schedule", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def update_schedule():
    """
    Actualiza la configuración de programación de backups.

    Body JSON (todos los campos son opcionales):
        {
            "enabled":      true,
            "cron":         "0 3 * * *",   expresión cron (referencial, el scheduler se configura externamente)
            "tipo_default": "incremental",
            "full_dia":     "sunday",
            "retener_dias": 30
        }
    """
    data = request.get_json() or {}
    if not data:
        return jsonify({"msg": "Body vacío"}), 400

    config = _get_schedule_config()

    if "enabled"      in data: config["enabled"]      = bool(data["enabled"])
    if "cron"         in data: config["cron"]         = str(data["cron"])
    if "tipo_default" in data:
        if data["tipo_default"] not in ["full", "incremental", "differential"]:
            return jsonify({"msg": "tipo_default inválido"}), 400
        config["tipo_default"] = data["tipo_default"]
    if "full_dia"     in data: config["full_dia"]     = str(data["full_dia"])
    if "retener_dias" in data: config["retener_dias"] = int(data["retener_dias"])

    _save_schedule_config(config)
    return jsonify({"msg": "Configuración guardada.", **config}), 200


@backups_admin_bp.route("/backups/download/<filename>", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def download_backup(filename: str):
    """Descarga un archivo de backup por nombre."""
    filename = os.path.basename(filename)   # sanitizar path traversal
    for root, _, files in os.walk(BACKUP_DIR):
        if filename in files:
            return send_file(os.path.join(root, filename), as_attachment=True)
    return jsonify({"msg": "Archivo no encontrado."}), 404


@backups_admin_bp.route("/backups/restore", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def restore_backup():
    """
    Restaura la base de datos desde un archivo de backup YA EXISTENTE en el
    historial de la plataforma.

    Body JSON:
        { "filename": "backup_full_20260517_030000.archive" }

    Formatos restaurables: .archive (MongoDB), .json (MongoDB), .dump (PostgreSQL).

    ⚠️  Operación sensible — modifica la BD activa. Mongo (.archive) reemplaza
        colecciones; Postgres (.dump) hace merge no destructivo.
    """
    data = request.get_json() or {}
    if not data.get("filename"):
        return jsonify({"msg": "filename es requerido"}), 400

    filename  = os.path.basename(data["filename"])
    file_path = None

    for root, _, files in os.walk(BACKUP_DIR):
        if filename in files and filename.lower().endswith(RESTORABLE_EXTS):
            file_path = os.path.join(root, filename)
            break

    if not file_path:
        return jsonify({"msg": "Backup no encontrado o formato inválido."}), 404

    return _ejecutar_restore(file_path, filename, origen="historial")


@backups_admin_bp.route("/backups/restore-upload", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def restore_upload():
    """
    Restaura desde un archivo EXTERNO subido por el usuario (multipart/form-data).

    Pensado para mover datos entre entornos (p.ej. otra laptop): el superadmin
    sube su .archive / .json / .dump y se restaura directamente.

    Form-data:
        file   archivo de backup (.archive | .json | .dump)
    """
    # Permitir archivos grandes solo en esta ruta (el límite global de 15 MB es
    # para fotos de perfil; los dumps pueden ser mucho mayores).
    try:
        request.max_content_length = _MAX_RESTORE_UPLOAD
    except Exception:
        pass

    if "file" not in request.files:
        return jsonify({"msg": "No se envió ningún archivo (campo 'file')."}), 400

    upload = request.files["file"]
    if not upload or not upload.filename:
        return jsonify({"msg": "Archivo vacío."}), 400

    filename = secure_filename(os.path.basename(upload.filename))
    if not filename.lower().endswith(RESTORABLE_EXTS):
        return jsonify({
            "msg": f"Formato inválido. Permitidos: {', '.join(RESTORABLE_EXTS)}",
        }), 400

    # Guardar en un subdirectorio aislado para no mezclar con los backups locales
    upload_dir = os.path.join(BACKUP_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    stamp     = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(upload_dir, f"{stamp}_{filename}")

    try:
        upload.save(file_path)
    except Exception as e:
        current_app.logger.error(f"[restore upload] Error guardando archivo: {e}")
        return jsonify({"msg": "No se pudo guardar el archivo subido.", "detalle": str(e)}), 500

    resultado = _ejecutar_restore(file_path, filename, origen="upload")

    # Limpiar el archivo temporal subido (ya restaurado)
    try:
        os.remove(file_path)
    except OSError:
        pass

    return resultado


def _ejecutar_restore(file_path: str, filename: str, origen: str):
    """Lógica común de restauración + registro en historial."""
    try:
        current_app.logger.info(f"[restore] inicio ({origen}) archivo={filename}")
        meta = restore_backup_file(file_path)
        current_app.logger.info(f"[restore] OK ({origen}) {filename} → {meta}")

        entry = {
            "date":          datetime.utcnow().isoformat(),
            "type":          "restore",
            "status":        "completado",
            "file":          filename,
            "origen":        origen,           # 'historial' | 'upload'
            "engine":        meta.get("engine"),
            "disparado_por": get_jwt_identity(),
        }
        if meta.get("warnings"):
            entry["warnings"] = meta["warnings"]
        save_history(entry)

        return jsonify({
            "msg":       "Restauración completada.",
            "file":      filename,
            "resultado": meta,
        }), 200

    except Exception as e:
        current_app.logger.error(f"[backup restore] Error ({origen}) {filename}: {e}")
        save_history({
            "date":   datetime.utcnow().isoformat(),
            "type":   "restore",
            "status": "error",
            "file":   filename,
            "origen": origen,
            "error":  str(e),
        })
        return jsonify({"msg": "Error al restaurar.", "detalle": str(e)}), 500
