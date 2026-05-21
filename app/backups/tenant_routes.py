"""
backups/tenant_routes.py — Endpoints de respaldo/restauración para owner_gym.

Rutas (prefijo /api/owner_gym injected in __init__.py):
  GET  /backups/summary          estado + historial del gym
  POST /backups/trigger          lanzar backup (full | differential | incremental)
  GET  /backups/status           progreso del job en curso
  GET  /backups/download/<fname> descargar archivo de respaldo
  POST /backups/restore          restaurar desde archivo del historial

El tenantId se extrae del JWT (id_gimnasio).
"""
import os
import threading
import uuid

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import jwt_required, get_jwt

from app.utils.security import require_role
from app.backups.tenant_service import (
    get_state, update_state, run_tenant_backup,
    restore_tenant_backup, load_history,
    gym_dir, last_full_path, last_any_path,
    _read_timestamp,
)

tenant_backups_bp = Blueprint("tenant_backups", __name__)


def _gym_id() -> int:
    return int(get_jwt().get("id_gimnasio", 0))


# ── GET /backups/summary ──────────────────────────────────────────────────────
@tenant_backups_bp.route("/backups/summary", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
def summary():
    gym = _gym_id()
    state = get_state(gym)
    history = load_history(gym)

    last_full = _read_timestamp(last_full_path(gym))
    last_any  = _read_timestamp(last_any_path(gym))

    return jsonify({
        "is_running":   state["is_running"],
        "progress":     state["progress"],
        "step":         state["step"],
        "last_full":    last_full,
        "last_backup":  last_any,
        "history":      history,
        "can_differential": last_full is not None,
        "can_incremental":  last_any  is not None,
    }), 200


# ── POST /backups/trigger ─────────────────────────────────────────────────────
@tenant_backups_bp.route("/backups/trigger", methods=["POST"])
@jwt_required()
@require_role("owner_gym")
def trigger():
    from flask import current_app
    gym  = _gym_id()
    state = get_state(gym)

    if state["is_running"]:
        return jsonify({"error": "Ya hay un respaldo en curso", "job_id": state["job_id"]}), 409

    data  = request.get_json() or {}
    btype = data.get("type", "incremental")

    if btype not in ("full", "differential", "incremental"):
        return jsonify({"error": "Tipo inválido. Use: full | differential | incremental"}), 400

    job_id = f"gym{gym}_{uuid.uuid4().hex[:8]}"
    app_obj = current_app._get_current_object()

    t = threading.Thread(
        target=run_tenant_backup,
        args=(job_id, gym, btype, app_obj),
        daemon=True,
    )
    t.start()

    return jsonify({"msg": f"Respaldo {btype} iniciado", "job_id": job_id}), 202


# ── GET /backups/status ───────────────────────────────────────────────────────
@tenant_backups_bp.route("/backups/status", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
def status():
    gym   = _gym_id()
    state = get_state(gym)
    return jsonify(state), 200


# ── GET /backups/download/<filename> ─────────────────────────────────────────
@tenant_backups_bp.route("/backups/download/<filename>", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
def download(filename):
    gym  = _gym_id()
    safe = os.path.basename(filename)          # evitar path traversal
    base = gym_dir(gym)

    for root, _, files in os.walk(base):
        if safe in files:
            return send_file(os.path.join(root, safe), as_attachment=True)

    return jsonify({"error": "Archivo no encontrado"}), 404


# ── POST /backups/restore ─────────────────────────────────────────────────────
@tenant_backups_bp.route("/backups/restore", methods=["POST"])
@jwt_required()
@require_role("owner_gym")
def restore():
    gym  = _gym_id()
    data = request.get_json() or {}
    filename = os.path.basename(data.get("filename", ""))

    if not filename:
        return jsonify({"error": "filename requerido"}), 400

    # Buscar el archivo SOLO dentro del directorio del gym (seguridad)
    base = gym_dir(gym)
    filepath = None
    for root, _, files in os.walk(base):
        if filename in files:
            filepath = os.path.join(root, filename)
            break

    if not filepath:
        return jsonify({"error": "Archivo no encontrado en este gimnasio"}), 404

    try:
        restored = restore_tenant_backup(filepath, gym)
        return jsonify({"msg": f"Restauración completada — {restored} documentos", "docs": restored}), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
