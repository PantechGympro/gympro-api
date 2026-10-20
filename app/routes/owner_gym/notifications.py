"""
routes/notifications.py -- Notificaciones de vencimiento de membresia y
aviso motivacional por inasistencia.

Sprint 4 / US21: APScheduler ejecuta una tarea diaria a las 08:00 que detecta
miembros con membresia que vence en los proximos 7 dias y envia email via Flask-Mail.

Check-in por QR general (ver api/app/utils/checkin_qr.py y
recepcionista_routes.py): APScheduler ejecuta otra tarea diaria a las 21:00
que revisa, por gimnasio, que miembro tenia HOY un dia de rutina programado
(no descanso, con ejercicios) y no registro asistencia -- y le manda un
aviso push motivador (nunca de regano) via la misma infraestructura de
notificaciones in-app + Expo push que ya usa compartido/notificaciones.py.

Las tareas se registran en create_app() para que corran dentro del contexto Flask.

Endpoints manuales (solo Admin):
  POST /api/notifications/membresias/check    -- dispara el chequeo de vencimientos de inmediato
  GET  /api/notifications/membresias/status   -- estado del scheduler y ultima ejecucion
"""
import os
import random
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from app.utils.tenant import require_tenant
from app.constants.roles import ROL_PROPIETARIO, ROL_SUPERADMIN, normalizar_rol
from app.routes.compartido.notificaciones import crear_notificacion

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


# Estado de la tarea de aviso motivacional (separado del de vencimientos).
_motivacional_status = {
    "ultima_ejecucion": None,
    "ultimo_resultado": None,
}

_WEEKDAYS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

_MENSAJES_MOTIVACIONALES = [
    "Hoy te tocaba entrenar y no te vimos por el gym. No pasa nada -- mañana es un nuevo día, ¡te esperamos!",
    "Vimos que hoy no pudiste venir. Lo importante es seguir intentándolo -- ¡nos vemos pronto!",
    "Un día sin entrenar no borra todo tu esfuerzo. Cuando puedas, aquí seguimos para apoyarte.",
]


def _norm_dia_motivacional(s):
    return (s or "").lower().replace("á", "a").replace("é", "e").replace("í", "i")         .replace("ó", "o").replace("ú", "u").strip()


def chequear_asistencia_motivacional(app=None):
    """
    US (check-in por QR general): corre de noche, cuando ya cerró el
    horario normal de entrenamiento, y revisa a cada miembro activo cuyo
    día de rutina de HOY era un día de entreno programado (no descanso,
    con ejercicios) -- si no tiene asistencia registrada hoy, le manda un
    aviso push motivador (nunca de regaño ni culpabilizador). Idempotente
    por día: una sola vez por miembro por día (tipo + referencia_id).

    No revisa "le tocaba registrar una rutina" sino simplemente "vino al
    gimnasio" (asistencias), que es la señal real desde que el check-in
    por QR reemplazó el auto-registro sin verificación (ver
    api/app/utils/checkin_qr.py).
    """
    from app.mongo import get_db
    from app.utils.timezone import local_now_naive

    ctx = app.app_context() if app else None
    if ctx:
        ctx.push()

    try:
        db    = get_db()
        ahora = local_now_naive()
        hoy_str = ahora.strftime("%Y-%m-%d")
        dia_hoy = _norm_dia_motivacional(_WEEKDAYS_ES[ahora.weekday()])
        hoy_ini = datetime.combine(ahora.date(), datetime.min.time())
        hoy_fin = hoy_ini + timedelta(days=1)

        revisados = 0
        enviados  = 0

        for miembro in db.miembros.find(
            {"estado": "Activo"},
            {"_id": 1, "id_usuario_pg": 1, "id_gimnasio_pg": 1},
        ):
            revisados += 1
            id_usuario = miembro.get("id_usuario_pg")
            if not id_usuario:
                continue

            # Ya se le avisó hoy -- no duplicar aunque el job se dispare de nuevo.
            if db.notificaciones.find_one({
                "id_usuario_pg":  int(id_usuario),
                "tipo":           "aviso_no_asistio",
                "referencia_id":  hoy_str,
            }):
                continue

            # Ya asistió hoy -- nada que avisar.
            if db.asistencias.find_one({
                "id_miembro": miembro["_id"],
                "fecha":      {"$gte": hoy_ini, "$lt": hoy_fin},
            }):
                continue

            # ¿Hoy era de verdad un día de entreno programado para este miembro?
            rutina_ids = [r["_id"] for r in db.rutinas.find({"id_miembro": miembro["_id"]}, {"_id": 1})]
            if not rutina_ids:
                continue

            tiene_entreno_hoy = False
            for d in db.rutina_dias.find({"id_rutina": {"$in": rutina_ids}}):
                if _norm_dia_motivacional(d.get("dia_semana")) == dia_hoy                         and _norm_dia_motivacional(d.get("grupo_muscular")) != "descanso":
                    if db.rutina_ejercicios.count_documents({"id_rutina_dia": d["_id"]}) > 0:
                        tiene_entreno_hoy = True
                        break
            if not tiene_entreno_hoy:
                continue

            crear_notificacion(
                db, int(id_usuario), miembro.get("id_gimnasio_pg"),
                tipo="aviso_no_asistio",
                titulo="Te extrañamos hoy",
                mensaje=random.choice(_MENSAJES_MOTIVACIONALES),
                referencia_tipo="asistencia",
                referencia_id=hoy_str,
            )
            enviados += 1

        resultado = {
            "ejecutado_en":       ahora.isoformat(),
            "miembros_revisados": revisados,
            "avisos_enviados":    enviados,
        }
        _motivacional_status["ultima_ejecucion"] = ahora.isoformat()
        _motivacional_status["ultimo_resultado"] = resultado
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
        # Auto-renovación de suscripciones de la plataforma. Corre a las 03:00,
        # antes de que nadie use el sistema, para que a primera hora del día el
        # panel ya refleje el estado correcto de cada gimnasio.
        from app.routes.owner_gym.billing import procesar_auto_renovaciones
        scheduler.add_job(
            func=procesar_auto_renovaciones,
            trigger=CronTrigger(hour=3, minute=0),
            id="billing_auto_renovacion",
            name="Auto-renovacion de suscripciones",
            replace_existing=True,
            kwargs={"app": app},
        )

        # Aviso motivacional por inasistencia. Corre a las 21:00 (9pm MX),
        # ya pasado el horario normal de entreno de la mayoría de los
        # gimnasios piloto, para no avisar "no viniste hoy" a alguien que
        # todavía puede ir en la noche.
        scheduler.add_job(
            func=chequear_asistencia_motivacional,
            trigger=CronTrigger(hour=21, minute=0),
            id="aviso_no_asistio",
            name="Aviso motivacional por inasistencia",
            replace_existing=True,
            kwargs={"app": app},
        )

        scheduler.start()
        _scheduler_status["activo"] = True
        app.logger.info("[Scheduler] Tareas registradas: 'notif_vencimientos' (08:00 MX), "
                        "'billing_auto_renovacion' (03:00 MX) y 'aviso_no_asistio' (21:00 MX)")

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
    if normalizar_rol(claims.get("role")) not in (ROL_PROPIETARIO, ROL_SUPERADMIN):
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


@notifications_bp.route("/asistencia-motivacional/check", methods=["POST"])
@jwt_required()
@require_tenant
def trigger_check_motivacional():
    """Dispara el aviso motivacional por inasistencia de forma manual (solo Admin)."""
    claims = get_jwt()
    if normalizar_rol(claims.get("role")) not in (ROL_PROPIETARIO, ROL_SUPERADMIN):
        return jsonify({"error": "Solo administradores pueden ejecutar esta accion"}), 403

    from flask import current_app
    resultado = chequear_asistencia_motivacional(app=current_app._get_current_object())
    return jsonify(resultado), 200


@notifications_bp.route("/asistencia-motivacional/status", methods=["GET"])
@jwt_required()
@require_tenant
def status_motivacional():
    """Devuelve el resultado de la ultima ejecucion del aviso motivacional."""
    return jsonify({
        "ultima_ejecucion": _motivacional_status["ultima_ejecucion"],
        "ultimo_resultado": _motivacional_status["ultimo_resultado"],
    }), 200
