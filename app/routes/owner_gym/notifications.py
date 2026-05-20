"""
routes/notifications.py -- Notificaciones de vencimiento de membresia.

Sprint 4 / US21: APScheduler ejecuta una tarea diaria a las 08:00 que detecta
miembros con membresia que vence en los proximos 7 dias y envia email via Flask-Mail.

La tarea se registra en create_app() para que corra dentro del contexto Flask.

Endpoints manuales (solo Admin):
  POST /api/notifications/membresias/check    -- dispara el chequeo inmediatamente
  GET  /api/notifications/membresias/status   -- estado del scheduler y ultima ejecucion
"""
import os
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from app.utils.tenant import require_tenant

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")

# Estado global de la tarea (en memoria, suficiente para un proceso Gunicorn)
_scheduler_status = {
    "activo":         False,
    "ultima_ejecucion": None,
    "ultimo_resultado": None,
}


# ─── Logica de chequeo ────────────────────────────────────────────────────────

def chequear_vencimientos(app=None):
    """
    Detecta miembros con membresia que vence en 7 dias y envia email.
    Puede llamarse desde el scheduler o desde el endpoint manual.
    Devuelve dict con resumen de la ejecucion.
    """
    from app.mongo import get_db
    from flask_mail import Message
    from app.extensions import mail

    ctx = app.app_context() if app else None
    if ctx:
        ctx.push()

    try:
        db   = get_db()
        ahora = datetime.now(timezone.utc)
        limite = ahora + timedelta(days=7)

        # Buscar membresías que vencen en los proximos 7 días
        # miembro_membresia.fecha_fin: almacenada como string "YYYY-MM-DD"
        pipeline = [
            {"$match": {"estado": "Activa"}},
            {"$addFields": {
                "fecha_fin_dt": {"$dateFromString": {
                    "dateString": "$fecha_fin",
                    "onError":    None,
                }},
            }},
            {"$match": {
                "fecha_fin_dt": {
                    "$gte": ahora,
                    "$lte": limite,
                },
            }},
            {"$lookup": {
                "from":         "miembros",
                "localField":   "id_miembro",
                "foreignField": "_id",
                "as":           "miembro_info",
            }},
            {"$unwind": {"path": "$miembro_info", "preserveNullAndEmptyArrays": True}},
            {"$project": {
                "fecha_fin":    1,
                "nombre":       "$miembro_info.nombre",
                "email":        "$miembro_info.email",
                "id_gimnasio":  "$miembro_info.id_gimnasio_pg",
            }},
        ]

        vencimientos = list(db.miembro_membresia.aggregate(pipeline))

        enviados   = 0
        fallidos   = 0
        sin_email  = 0

        for doc in vencimientos:
            email  = doc.get("email","").strip()
            nombre = doc.get("nombre","Miembro")
            f_fin  = doc.get("fecha_fin","")

            if not email or "@" not in email:
                sin_email += 1
                continue

            dias_restantes = (
                datetime.strptime(f_fin, "%Y-%m-%d").replace(tzinfo=timezone.utc) - ahora
            ).days if f_fin else 0

            asunto = f"Tu membresia GymPro vence en {dias_restantes} dias"
            html = f"""
            <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f0f1a;color:#fff;padding:32px;border-radius:12px;">
              <h2 style="color:#fbe379;margin-top:0;">GymPro — Aviso de vencimiento</h2>
              <p>Hola <strong>{nombre}</strong>,</p>
              <p>Tu membresia vence el <strong style="color:#fbe379;">{f_fin}</strong>
                 ({dias_restantes} {'dia' if dias_restantes == 1 else 'dias'} restantes).</p>
              <p>Renuevala antes de que expire para mantener acceso sin interrupciones.</p>
              <a href="#" style="display:inline-block;margin-top:16px;padding:12px 28px;
                 background:#fbe379;color:#000;border-radius:8px;font-weight:700;
                 text-decoration:none;">Renovar ahora</a>
              <p style="margin-top:28px;font-size:12px;color:#888;">
                GymPro SaaS — Este mensaje es automatico, por favor no respondas.
              </p>
            </div>"""

            try:
                msg = Message(
                    subject=asunto,
                    recipients=[email],
                    html=html,
                )
                mail.send(msg)
                enviados += 1
            except Exception:
                fallidos += 1

        resultado = {
            "ejecutado_en":     ahora.isoformat(),
            "vencimientos_detectados": len(vencimientos),
            "emails_enviados":  enviados,
            "emails_fallidos":  fallidos,
            "sin_email":        sin_email,
        }

        _scheduler_status["ultima_ejecucion"] = ahora.isoformat()
        _scheduler_status["ultimo_resultado"] = resultado
        return resultado

    finally:
        if ctx:
            ctx.pop()


# ─── Inicializar APScheduler ──────────────────────────────────────────────────

def init_scheduler(app):
    """
    Registra la tarea cron en APScheduler.
    Llamar desde create_app() despues de inicializar extensiones.
    Solo corre en el proceso principal (evita duplicados con Gunicorn multi-worker).
    """
    # Evitar doble registro en modo desarrollo con reloader
    if os.getenv("WERKZEUG_RUN_MAIN") == "true" or os.getenv("SCHEDULER_DISABLED"):
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler(timezone="America/Mexico_City")
        scheduler.add_job(
            func=chequear_vencimientos,
            trigger=CronTrigger(hour=8, minute=0),
            id="notif_vencimientos",
            name="Notificaciones de vencimiento de membresia",
            replace_existing=True,
            kwargs={"app": app},
        )
        scheduler.start()
        _scheduler_status["activo"] = True
        app.logger.info("[Scheduler] Tarea 'notif_vencimientos' registrada — diaria 08:00 MX")

    except ImportError:
        app.logger.warning("[Scheduler] APScheduler no disponible. Instalar: pip install apscheduler")
    except Exception as e:
        app.logger.error(f"[Scheduler] Error al inicializar: {e}")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@notifications_bp.route("/membresias/check", methods=["POST"])
@jwt_required()
@require_tenant
def trigger_check():
    """Dispara el chequeo de vencimientos de forma manual (solo Admin)."""
    claims = get_jwt()
    if claims.get("role") not in ("owner_gym", "superadmin"):
        return jsonify({"error": "Solo administradores pueden ejecutar esta accion"}), 403

    from flask import current_app
    resultado = chequear_vencimientos(app=current_app._get_current_object())
    return jsonify(resultado), 200


@notifications_bp.route("/membresias/status", methods=["GET"])
@jwt_required()
@require_tenant
def scheduler_status():
    """Devuelve el estado del scheduler y resultado de la ultima ejecucion."""
    return jsonify({
        "scheduler_activo":  _scheduler_status["activo"],
        "ultima_ejecucion":  _scheduler_status["ultima_ejecucion"],
        "ultimo_resultado":  _scheduler_status["ultimo_resultado"],
    }), 200
