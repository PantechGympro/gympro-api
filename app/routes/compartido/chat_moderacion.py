"""
routes/compartido/chat_moderacion.py — Bloqueo y denuncia en el chat (HU-12).

Compartido entre Miembro y Entrenador: ambos lados del chat 1:1 pueden
bloquear y denunciar al otro con el mismo contrato -- no hay una versión
"de miembro" y otra "de entrenador" de estas reglas, a diferencia de
training.py / trainer_routes.py (que sí difieren porque cada rol ve una
lista distinta de contactos).

Endpoints (todos requieren JWT + tenant):
  GET    /api/chat/bloqueos               — mis bloqueos activos
  POST   /api/chat/bloqueos               — bloquear a un usuario
  DELETE /api/chat/bloqueos/<id_usuario>  — desbloquear
  POST   /api/chat/reportes               — denunciar un mensaje o usuario
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_mail import Message

from app.extensions import db, mail
from app.mongo import get_db as get_mongo_db
from app.models.pg.usuario import Usuario
from app.models.pg.rol import Rol
from app.models.pg.bloqueo_chat import BloqueoChat
from app.models.pg.reporte_chat import ReporteChat, MOTIVOS_REPORTE_CHAT
from app.utils.tenant import require_tenant
from app.utils.alcance import registrar_evento, usuario_propietario_de
from app.utils.email_remitentes import remitente
from app.constants.roles import ROL_ADMINISTRADOR, ROL_RECEPCIONISTA
from app.routes.compartido.notificaciones import crear_notificacion

chat_moderacion_bp = Blueprint("chat_moderacion", __name__, url_prefix="/api/chat")


def _actor_id() -> int:
    return int(get_jwt_identity())


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/chat/bloqueos
# ─────────────────────────────────────────────────────────────────────────────
@chat_moderacion_bp.route("/bloqueos", methods=["GET"])
@jwt_required()
@require_tenant
def listar_bloqueos():
    """Bloqueos que YO hice -- para que el frontend marque esos contactos
    como bloqueados sin tener que preguntar uno por uno."""
    mis_bloqueos = BloqueoChat.query.filter_by(id_usuario_bloqueador=_actor_id()).all()
    return jsonify([b.to_dict() for b in mis_bloqueos]), 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chat/bloqueos
# ─────────────────────────────────────────────────────────────────────────────
@chat_moderacion_bp.route("/bloqueos", methods=["POST"])
@jwt_required()
@require_tenant
def bloquear_usuario():
    """
    Bloquea a otro usuario del chat.

    AC1: el bloqueo corta los mensajes en AMBOS sentidos de inmediato --
    la verificación la aplican training.py/trainer_routes.py en cada
    GET/POST de chat (ver utils/chat_moderacion.py::existe_bloqueo), no
    hace falta ninguna acción del otro lado.

    Body: { "id_usuario_bloqueado": <int> }
    """
    actor_id = _actor_id()
    gym_id   = g.tenant_id
    data     = request.get_json(silent=True) or {}

    try:
        objetivo_id = int(data.get("id_usuario_bloqueado"))
    except (TypeError, ValueError):
        return jsonify({"msg": "id_usuario_bloqueado es requerido y debe ser numérico"}), 400

    if objetivo_id == actor_id:
        return jsonify({"msg": "No puedes bloquearte a ti mismo"}), 400

    # Aislamiento por gimnasio (RNF-03): solo se puede bloquear a alguien
    # de la misma sucursal -- el chat tampoco cruza gimnasios.
    objetivo = Usuario.query.filter_by(id=objetivo_id, id_gimnasio=gym_id).first()
    if not objetivo:
        return jsonify({"msg": "Usuario no encontrado en este gimnasio"}), 404

    existente = BloqueoChat.query.filter_by(
        id_usuario_bloqueador=actor_id, id_usuario_bloqueado=objetivo_id
    ).first()
    if existente:
        return jsonify({"msg": "Ya habías bloqueado a este usuario", **existente.to_dict()}), 200

    bloqueo = BloqueoChat(
        id_gimnasio=gym_id,
        id_usuario_bloqueador=actor_id,
        id_usuario_bloqueado=objetivo_id,
    )
    db.session.add(bloqueo)

    registrar_evento(
        actor_id, "chat.bloquear_usuario", id_gimnasio=gym_id,
        detalle={"id_usuario_bloqueado": objetivo_id},
    )
    db.session.commit()

    return jsonify({"msg": "Usuario bloqueado", **bloqueo.to_dict()}), 201


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/chat/bloqueos/<id_usuario>
# ─────────────────────────────────────────────────────────────────────────────
@chat_moderacion_bp.route("/bloqueos/<int:id_usuario>", methods=["DELETE"])
@jwt_required()
@require_tenant
def desbloquear_usuario(id_usuario: int):
    actor_id = _actor_id()
    bloqueo = BloqueoChat.query.filter_by(
        id_usuario_bloqueador=actor_id, id_usuario_bloqueado=id_usuario
    ).first()
    if not bloqueo:
        return jsonify({"msg": "No tenías bloqueado a este usuario"}), 404

    db.session.delete(bloqueo)
    registrar_evento(
        actor_id, "chat.desbloquear_usuario", id_gimnasio=g.tenant_id,
        detalle={"id_usuario_desbloqueado": id_usuario},
    )
    db.session.commit()

    return jsonify({"msg": "Usuario desbloqueado"}), 200


def _destinatarios_staff(gym_id: int) -> list:
    """
    HU-12/AC2: quienes deben enterarse de un reporte de chat en este
    gimnasio/sucursal -- el Propietario (alcance de organizacion:
    usuario_propietario_de() ya resuelve Gimnasio -> Organizacion -> sede
    principal -> Usuario, el mismo helper que ya usan pasarelas.py y
    utils/recibos.py para esto) mas los Administradores y Recepcionistas
    de ESTA sucursal (su alcance es de sucursal, no de organizacion -- ver
    constants/roles.py). Deduplicado por id de usuario, por si el
    Propietario tambien figura en la tabla con rol de sucursal.
    """
    vistos = {}

    propietario = usuario_propietario_de(gym_id)
    if propietario:
        vistos[propietario.id] = propietario

    staff_sucursal = (
        Usuario.query
        .join(Rol, Usuario.id_rol == Rol.id)
        .filter(Usuario.id_gimnasio == gym_id, Rol.nombre.in_([ROL_ADMINISTRADOR, ROL_RECEPCIONISTA]))
        .all()
    )
    for u in staff_sucursal:
        vistos[u.id] = u

    return list(vistos.values())


def _avisar_staff_reporte(gym_id: int, reporte: ReporteChat, reportante, reportado) -> None:
    """
    HU-12/AC2: escala un reporte de chat recien creado al staff que debe
    evaluar el caso -- aviso in-app + push (crear_notificacion ya hace
    ambos, ver routes/compartido/notificaciones.py) y ademas correo,
    porque el staff no siempre tiene la app abierta y un entrenador (o
    cualquier rol) senalado amerita que se enteren aunque no esten viendo
    el celular en ese momento.

    Se llama DESPUES de db.session.commit(): un fallo de push o de correo
    jamas debe tumbar la creacion del reporte en si, que ya quedo a salvo.
    Todo lo de aqui es best-effort (try/except silencioso).
    """
    staff = _destinatarios_staff(gym_id)
    if not staff:
        return

    motivo_legible    = (reporte.motivo or "").replace("_", " ")
    nombre_reportante = reportante.nombre if reportante else "Un usuario"
    titulo  = "Nuevo reporte en el chat"
    mensaje = f"{nombre_reportante} reporto a {reportado.nombre} ({motivo_legible}). Revisalo en Moderacion de chat."

    try:
        mdb = get_mongo_db()
        for u in staff:
            try:
                crear_notificacion(
                    mdb, u.id, gym_id, "chat_reporte", titulo, mensaje,
                    referencia_tipo="reporte_chat", referencia_id=str(reporte.id),
                )
            except Exception:
                pass  # un push fallido no debe impedir avisarle a los demas
    except Exception:
        pass  # sin Mongo disponible, el correo de abajo sigue siendo el respaldo

    destinatarios_email = [u.email for u in staff if u.email]
    if not destinatarios_email:
        return

    comentario_html = (
        f"<p><strong>Comentario:</strong> {reporte.comentario}</p>" if reporte.comentario else ""
    )
    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;background:#0f0f1a;color:#fff;padding:32px;border-radius:12px;">
      <h2 style="color:#f87171;margin-top:0;">GymPro — Reporte en el chat</h2>
      <p><strong>{nombre_reportante}</strong> reporto a <strong>{reportado.nombre}</strong>
         ({reportado.rol.nombre if reportado.rol else 'usuario'}) en el chat.</p>
      <p><strong>Motivo:</strong> {motivo_legible}</p>
      {comentario_html}
      <p>Entra a Moderacion de chat para revisar la conversacion y resolver el caso
         (desestimar, advertir o suspender el acceso al chat de la persona reportada).</p>
      <p style="margin-top:28px;font-size:12px;color:#888;">
        GymPro — Este mensaje es automatico, por favor no respondas.
      </p>
    </div>"""
    try:
        msg = Message(
            subject=f"Reporte en el chat — {reportado.nombre}",
            sender=remitente("MAIL_SENDER_NOTIFICACIONES"),
            recipients=destinatarios_email,
            html=html,
        )
        mail.send(msg)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/chat/reportes
# ─────────────────────────────────────────────────────────────────────────────
@chat_moderacion_bp.route("/reportes", methods=["POST"])
@jwt_required()
@require_tenant
def crear_reporte():
    """
    Denuncia un mensaje o a un usuario (AC5: respalda el botón "Reportar"
    visible en el chat, tanto en web como en móvil).

    Body: {
        "id_usuario_reportado": <int>,   requerido
        "motivo":               <str>,   requerido -- ver MOTIVOS_REPORTE_CHAT
        "comentario":           <str>,   opcional
        "id_mensaje":           <str>,   opcional -- ObjectId de mensajes_chat
    }
    """
    actor_id = _actor_id()
    gym_id   = g.tenant_id
    data     = request.get_json(silent=True) or {}

    try:
        reportado_id = int(data.get("id_usuario_reportado"))
    except (TypeError, ValueError):
        return jsonify({"msg": "id_usuario_reportado es requerido y debe ser numérico"}), 400

    motivo = (data.get("motivo") or "").strip()
    if motivo not in MOTIVOS_REPORTE_CHAT:
        return jsonify({"msg": f"motivo debe ser uno de: {', '.join(MOTIVOS_REPORTE_CHAT)}"}), 400

    if reportado_id == actor_id:
        return jsonify({"msg": "No puedes reportarte a ti mismo"}), 400

    reportado = Usuario.query.filter_by(id=reportado_id, id_gimnasio=gym_id).first()
    if not reportado:
        return jsonify({"msg": "Usuario no encontrado en este gimnasio"}), 404

    comentario = (data.get("comentario") or "").strip() or None
    id_mensaje = (data.get("id_mensaje") or "").strip() or None

    reporte = ReporteChat(
        id_gimnasio=gym_id,
        id_usuario_reportante=actor_id,
        id_usuario_reportado=reportado_id,
        id_mensaje_mongo=id_mensaje,
        motivo=motivo,
        comentario=comentario,
        estado="pendiente",
    )
    db.session.add(reporte)
    db.session.flush()  # para tener reporte.id en el detalle de la bitácora

    # AC2 (escalar al staff): el reporte ya queda filtrado por id_gimnasio,
    # así que la bandeja de moderación de ESTE gimnasio/sucursal
    # (routes/owner_gym/moderacion_chat.py) lo va a listar sin que un
    # reporte cruce nunca a otro gimnasio. Además de quedar ahí, se avisa
    # de inmediato (aviso in-app + push + correo) al Propietario, los
    # Administradores y los Recepcionistas de la sucursal, para que estén
    # atentos al usuario reportado y evalúen el caso sin tener que estar
    # revisando la bandeja a cada rato.
    registrar_evento(
        actor_id, "chat.reportar_usuario", id_gimnasio=gym_id,
        detalle={"id_reporte": reporte.id, "id_usuario_reportado": reportado_id, "motivo": motivo},
    )
    db.session.commit()

    reportante = Usuario.query.get(actor_id)
    _avisar_staff_reporte(gym_id, reporte, reportante, reportado)

    return jsonify({
        "msg": "Reporte enviado. El propietario del gimnasio lo revisará.",
        **reporte.to_dict(),
    }), 201
