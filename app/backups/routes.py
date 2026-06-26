from flask import Blueprint, jsonify, request, current_app, send_file
from flask_jwt_extended import jwt_required
from datetime import datetime, timedelta, timezone
import uuid
import threading
import os

from app.backups.service import (
    backup_state,
    run_backup,
    BACKUP_DIR,
    load_history,
    save_history
)
from app.backups.restore_service import restore_backup_file
from app.utils.security import require_role

backups_bp = Blueprint("backups", __name__, url_prefix="/api/backups")


@backups_bp.route("/dashboard-summary", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def dashboard_summary():
    history = load_history()

    response = {
        "system_status": "OK" if not backup_state["is_running"] else "PENDIENTE",
        "last_backup": (
            backup_state["last_backup"].isoformat()
            if backup_state["last_backup"]
            else None
        ),
        "config": {
            "frequency": "Diaria",
            "default_type": "Incremental",
            "next_scheduled": (
                datetime.utcnow() + timedelta(days=1)
            ).replace(hour=3, minute=0, second=0).isoformat(),
        },
        "backup_plan": [
            {"title": "Respaldos diarios", "desc": "Cada madrugada a las 3:00 AM"},
            {"title": "Respaldo semanal completo", "desc": "Domingos - Base de datos completa"},
        ],
        "recent_history": history[:3]
    }

    return jsonify(response), 200


@backups_bp.route("/history", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def backup_history():
    history = load_history()
    return jsonify(history), 200


@backups_bp.route("/trigger", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def trigger_backup():
    if backup_state["is_running"]:
        return jsonify({
            "message": "Ya hay un backup en curso",
            "status": "running",
            "job_id": backup_state.get("job_id")
        }), 409

    data = request.get_json() or {}
    backup_type = data.get("type", "incremental")

    valid_types = ["full", "differential", "incremental"]
    if backup_type not in valid_types:
        return jsonify({
            "error": f"Tipo de backup inválido. Use: {', '.join(valid_types)}"
        }), 400

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    app_instance = current_app._get_current_object()

    thread = threading.Thread(
        target=run_backup,
        args=(job_id, backup_type, app_instance),
        daemon=True
    )
    thread.start()

    return jsonify({
        "message": f"Backup {backup_type} iniciado",
        "job_id": job_id,
        "status": "running"
    }), 202


@backups_bp.route("/status", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def backup_status():
    response = {
        "is_running": backup_state["is_running"],
        "progress_percentage": backup_state["progress_percentage"],
        "current_step": backup_state["current_step"],
        "last_backup": (
            backup_state["last_backup"].isoformat()
            if backup_state["last_backup"]
            else None
        ),
        "files": {}
    }

    if backup_state.get("generated_files"):
        for f_type, f_path in backup_state["generated_files"].items():
            if f_path and os.path.exists(f_path):
                filename = os.path.basename(f_path)
                response["files"][f_type] = f"/api/backups/download/{filename}"

    return jsonify(response), 200


@backups_bp.route("/download/<filename>", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def download_backup(filename):
    # Sanitizar el nombre de archivo para evitar path traversal
    filename = os.path.basename(filename)

    for root, dirs, files in os.walk(BACKUP_DIR):
        if filename in files:
            file_path = os.path.join(root, filename)
            return send_file(file_path, as_attachment=True)

    return jsonify({"error": "Archivo no encontrado"}), 404


@backups_bp.route("/restore", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def restore_backup():
    data = request.get_json()

    if not data or "filename" not in data:
        return jsonify({"error": "Archivo no especificado"}), 400

    raw_filename = data["filename"]
    filename = os.path.basename(raw_filename)

    file_path = None
    for root, _, files in os.walk(BACKUP_DIR):
        if filename in files and (filename.endswith(".archive") or filename.endswith(".json")):
            file_path = os.path.join(root, filename)
            break

    if not file_path:
        return jsonify({"error": "Backup no encontrado o formato inválido"}), 404

    try:
        restore_backup_file(file_path)

        save_history({
            "date": datetime.now(timezone.utc).isoformat(),
            "type": "restore",
            "size": "N/A",
            "url": filename
        })

        return jsonify({
            "message": "Base de datos restaurada correctamente",
            "file": filename
        }), 200

    except Exception as e:
        current_app.logger.error(f"Error crítico restaurando backup: {e}")
        return jsonify({
            "error": "Error al restaurar",
            "detail": str(e)
        }), 500


@backups_bp.route("/test-email", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "superadmin")
def test_email():
    from flask_mail import Message
    from app.extensions import mail

    try:
        msg = Message(
            subject="Prueba de Configuración - Sistema de Backups",
            sender=current_app.config.get("MAIL_USERNAME"),
            recipients=[current_app.config.get("MAIL_RECIPIENT") or current_app.config.get("MAIL_USERNAME")],
            body="Si recibes este correo, la configuración de email está funcionando correctamente."
        )
        mail.send(msg)
        return jsonify({"message": "Correo enviado con éxito"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
