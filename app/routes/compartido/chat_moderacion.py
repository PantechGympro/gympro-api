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

from app.extensions import db
from app.models.pg.usuario import Usuario
from app.models.pg.bloqueo_chat import BloqueoChat
from app.models.pg.reporte_chat import ReporteChat, MOTIVOS_REPORTE_CHAT
from app.utils.tenant import require_tenant
from app.utils.alcance import registrar_evento

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

    # AC2 (escalar al propietario): no hace falta "enviar" nada aparte --
    # el reporte ya queda filtrado por id_gimnasio, así que la bandeja del
    # propietario de ESTE gimnasio (routes/owner_gym/moderacion_chat.py)
    # lo va a listar sin que un reporte cruce nunca a otro gimnasio.
    registrar_evento(
        actor_id, "chat.reportar_usuario", id_gimnasio=gym_id,
        detalle={"id_reporte": reporte.id, "id_usuario_reportado": reportado_id, "motivo": motivo},
    )
    db.session.commit()

    return jsonify({
        "msg": "Reporte enviado. El propietario del gimnasio lo revisará.",
        **reporte.to_dict(),
    }), 201
