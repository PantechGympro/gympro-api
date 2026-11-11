"""
owner_gym/moderacion_chat.py — Bandeja de reportes de chat y moderación (HU-12).

AC2: los reportes sobre personal del gimnasio (entrenador o recepción)
llegan a una bandeja del propietario -- en la práctica, CUALQUIER reporte
de este gimnasio llega aquí (@require_tenant ya garantiza que nunca se
lista el de otro gimnasio, RNF-03), sin importar si el reportado es
staff o un miembro: el propietario es la única autoridad de moderación
que existe hoy sobre el chat de su sucursal.

AC4 (trazabilidad): cada resolución queda en reportes_chat.resuelto_por/
resuelto_en/nota_resolucion Y además en bitacora_eventos vía
registrar_evento() -- la misma tabla de auditoría genérica que ya usa el
resto del panel de Propietario/Administrador (ver utils/alcance.py).

Endpoints (Propietario / Administrador de la sucursal):
  GET   /api/owner_gym/reportes-chat                — bandeja, filtrable por estado
  GET   /api/owner_gym/reportes-chat/<id>            — detalle + historial de mensajes
  PATCH /api/owner_gym/reportes-chat/<id>/resolver   — desestimar | advertir | suspender
  PATCH /api/owner_gym/staff/<id>/reactivar-chat     — levantar una suspensión de chat
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from bson import ObjectId

from app.extensions import db
from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.models.pg.reporte_chat import ReporteChat, ESTADOS_REPORTE_CHAT
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import registrar_evento
from app.routes.compartido.notificaciones import crear_notificacion

moderacion_chat_bp = Blueprint("moderacion_chat", __name__)

# accion del body -> estado final del reporte (ver ReporteChat.estado / migración 031)
_ACCIONES_VALIDAS = {
    "desestimar": "desestimado",
    "advertir":   "advertencia",
    "suspender":  "suspendido",
}


def _serializar_mensaje(doc: dict) -> dict:
    """Mismo criterio de serialización que notificaciones.py/training.py:
    _id → id (str), ObjectId → str, datetime naive de Mongo → ISO con +00:00."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/reportes-chat
# ─────────────────────────────────────────────────────────────────────────────
@moderacion_chat_bp.route("/reportes-chat", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def listar_reportes():
    """
    Bandeja de reportes de ESTE gimnasio.
    Query: estado=pendiente|desestimado|advertencia|suspendido (default: todos)
    """
    gym_id = g.tenant_id
    estado = request.args.get("estado", "").strip()

    q = ReporteChat.query.filter_by(id_gimnasio=gym_id)
    if estado:
        if estado not in ESTADOS_REPORTE_CHAT:
            return jsonify({"msg": f"estado debe ser uno de: {', '.join(ESTADOS_REPORTE_CHAT)}"}), 400
        q = q.filter_by(estado=estado)

    reportes = q.order_by(ReporteChat.creado_en.desc()).all()
    return jsonify([r.to_dict() for r in reportes]), 200


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/reportes-chat/<id>
# ─────────────────────────────────────────────────────────────────────────────
@moderacion_chat_bp.route("/reportes-chat/<int:reporte_id>", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def detalle_reporte(reporte_id: int):
    """Detalle del reporte + últimos 100 mensajes entre ambos usuarios --
    el "revisa el reporte con el historial de la conversación" de AC4."""
    gym_id  = g.tenant_id
    reporte = ReporteChat.query.filter_by(id=reporte_id, id_gimnasio=gym_id).first()
    if not reporte:
        return jsonify({"msg": "Reporte no encontrado"}), 404

    historial = []
    if reporte.id_usuario_reportante and reporte.id_usuario_reportado:
        mdb = get_db()
        # mensajes_chat no distingue "quién reportó a quién" -- solo
        # miembro/entrenador -- así que se busca en ambos sentidos del par.
        a, b = reporte.id_usuario_reportante, reporte.id_usuario_reportado
        docs = list(mdb.mensajes_chat.find({
            "id_gimnasio_pg": gym_id,
            "$or": [
                {"id_miembro_pg": a, "id_entrenador_pg": b},
                {"id_miembro_pg": b, "id_entrenador_pg": a},
            ],
        }).sort("fecha", 1).limit(100))
        historial = [_serializar_mensaje(d) for d in docs]

    return jsonify({**reporte.to_dict(), "historial_mensajes": historial}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/owner_gym/reportes-chat/<id>/resolver
# ─────────────────────────────────────────────────────────────────────────────
@moderacion_chat_bp.route("/reportes-chat/<int:reporte_id>/resolver", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def resolver_reporte(reporte_id: int):
    """
    Body: { "accion": "desestimar"|"advertir"|"suspender", "nota_resolucion": str? }

    - desestimar: cierra el reporte sin tocar la cuenta del reportado.
    - advertir:   cierra el reporte y manda una notificación in-app al
                  reportado (no cambia nada de su acceso).
    - suspender:  cierra el reporte y corta el CHAT del reportado -- AC3:
                  Usuario.activo no se toca, solo chat_suspendido.
    """
    gym_id   = g.tenant_id
    actor_id = int(get_jwt_identity())
    data     = request.get_json(silent=True) or {}
    accion   = (data.get("accion") or "").strip()
    nota     = (data.get("nota_resolucion") or "").strip() or None

    if accion not in _ACCIONES_VALIDAS:
        return jsonify({"msg": f"accion debe ser una de: {', '.join(_ACCIONES_VALIDAS)}"}), 400

    reporte = ReporteChat.query.filter_by(id=reporte_id, id_gimnasio=gym_id).first()
    if not reporte:
        return jsonify({"msg": "Reporte no encontrado"}), 404
    if reporte.estado != "pendiente":
        return jsonify({"msg": f"Este reporte ya se resolvió como '{reporte.estado}'"}), 409

    nuevo_estado = _ACCIONES_VALIDAS[accion]
    reportado = (
        Usuario.query.filter_by(id=reporte.id_usuario_reportado, id_gimnasio=gym_id).first()
        if reporte.id_usuario_reportado else None
    )

    if accion == "advertir" and reportado:
        mdb = get_db()
        crear_notificacion(
            mdb, reportado.id, gym_id, "moderacion_advertencia",
            "Advertencia por uso del chat",
            "Recibiste una advertencia del propietario del gimnasio por el uso del chat. "
            "Revisa las normas de convivencia para evitar una suspensión.",
            referencia_tipo="reporte_chat", referencia_id=str(reporte.id),
        )

    if accion == "suspender" and reportado:
        reportado.chat_suspendido        = True
        reportado.chat_suspendido_motivo = nota or f"Reporte #{reporte.id} ({reporte.motivo})"
        reportado.chat_suspendido_en     = datetime.now(timezone.utc)
        reportado.chat_suspendido_por    = actor_id

    reporte.estado          = nuevo_estado
    reporte.resuelto_por    = actor_id
    reporte.resuelto_en     = datetime.now(timezone.utc)
    reporte.nota_resolucion = nota

    registrar_evento(
        actor_id, f"chat.reporte.{accion}", id_gimnasio=gym_id,
        detalle={
            "id_reporte":            reporte.id,
            "id_usuario_reportado":  reporte.id_usuario_reportado,
            "nota_resolucion":       nota,
        },
    )
    db.session.commit()

    return jsonify({"msg": "Reporte resuelto", **reporte.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/owner_gym/staff/<id>/reactivar-chat
# ─────────────────────────────────────────────────────────────────────────────
@moderacion_chat_bp.route("/staff/<int:user_id>/reactivar-chat", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def reactivar_chat(user_id: int):
    """Levanta una suspensión de chat. No toca Usuario.activo (sigue siendo
    el inverso exacto de AC3) ni reabre el reporte que la originó."""
    gym_id  = g.tenant_id
    usuario = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario:
        return jsonify({"msg": "Usuario no encontrado"}), 404
    if not usuario.chat_suspendido:
        return jsonify({"msg": "Este usuario no tiene el chat suspendido"}), 409

    usuario.chat_suspendido        = False
    usuario.chat_suspendido_motivo = None
    usuario.chat_suspendido_en     = None
    usuario.chat_suspendido_por    = None

    registrar_evento(
        int(get_jwt_identity()), "chat.reactivar", id_gimnasio=gym_id,
        detalle={"id_usuario": user_id},
    )
    db.session.commit()

    return jsonify({"msg": "Acceso al chat reactivado", **usuario.to_dict()}), 200
