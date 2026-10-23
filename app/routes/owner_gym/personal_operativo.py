"""
owner_gym/personal_operativo.py — Directorio de personal SIN acceso al
sistema (mantenimiento, limpieza, seguridad, etc.) -- ver
models/personal_operativo.py para el por qué de un modelo aparte del
staff con login de owner_trainers.py.

Endpoints:
    GET    /api/owner_gym/personal/puestos     Catálogo sugerido de puestos
    GET    /api/owner_gym/personal              Lista (filtros: activos, puesto, q)
    POST   /api/owner_gym/personal               Crear
    PUT    /api/owner_gym/personal/<id>           Editar
    PATCH  /api/owner_gym/personal/<id>/toggle    Activar / desactivar
    POST   /api/owner_gym/personal/trasladar      Mover a otra sucursal
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from bson.objectid import ObjectId
from bson.errors import InvalidId

from app.extensions import db as pg_db
from app.mongo import get_db
from app.models.personal_operativo import PersonalOperativo, PUESTOS_SUGERIDOS
from app.models.pg.gimnasio import Gimnasio
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import usuario_tiene_alcance, registrar_evento
from app.constants.roles import normalizar_rol, ROL_ADMINISTRADOR

personal_bp = Blueprint("personal_operativo", __name__)


@personal_bp.route("/personal/puestos", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
def listar_puestos_sugeridos():
    """Catálogo para el selector del formulario de alta -- el frontend
    siempre deja además un campo "Otro" con texto libre."""
    return jsonify(list(PUESTOS_SUGERIDOS)), 200


# ---------------------------------------------------------------------------
# GET /api/owner_gym/personal
# ---------------------------------------------------------------------------
@personal_bp.route("/personal", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def listar_personal():
    mdb    = get_db()
    gym_id = g.tenant_id
    activos_param = request.args.get("activos", "true").strip().lower()
    puesto = (request.args.get("puesto") or "").strip()
    search = (request.args.get("q") or "").strip()

    query = {"id_gimnasio": gym_id}
    if activos_param == "true":
        query["activo"] = True
    elif activos_param == "false":
        query["activo"] = False
    if puesto:
        query["puesto"] = puesto
    if search:
        query["nombre"] = {"$regex": search, "$options": "i"}

    docs = list(mdb.personal_operativo.find(query).sort("nombre", 1))
    return jsonify([PersonalOperativo(**d).to_dict() for d in docs]), 200


# ---------------------------------------------------------------------------
# POST /api/owner_gym/personal
# ---------------------------------------------------------------------------
@personal_bp.route("/personal", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def crear_personal():
    gym_id = g.tenant_id
    data   = request.get_json() or {}
    nombre   = (data.get("nombre")   or "").strip()
    puesto   = (data.get("puesto")   or "").strip()
    telefono = (data.get("telefono") or "").strip() or None
    notas    = (data.get("notas")    or "").strip() or None

    if not nombre or not puesto:
        return jsonify({"msg": "nombre y puesto son requeridos"}), 400

    nuevo = PersonalOperativo(
        nombre=nombre, puesto=puesto, id_gimnasio=gym_id,
        telefono=telefono, notas=notas, activo=True,
    )
    nuevo.save()
    return jsonify(nuevo.to_dict()), 201


# ---------------------------------------------------------------------------
# PUT /api/owner_gym/personal/<id>
# ---------------------------------------------------------------------------
@personal_bp.route("/personal/<id_personal>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def editar_personal(id_personal):
    gym_id  = g.tenant_id
    persona = PersonalOperativo.find_by_id(id_personal, id_gimnasio=gym_id)
    if not persona:
        return jsonify({"msg": "No encontrado"}), 404

    data = request.get_json() or {}
    if "nombre" in data:
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"msg": "El nombre no puede quedar vacío"}), 400
        persona.nombre = nombre
    if "puesto" in data:
        puesto = (data.get("puesto") or "").strip()
        if not puesto:
            return jsonify({"msg": "El puesto no puede quedar vacío"}), 400
        persona.puesto = puesto
    if "telefono" in data:
        persona.telefono = (data.get("telefono") or "").strip() or None
    if "notas" in data:
        persona.notas = (data.get("notas") or "").strip() or None

    persona.save()
    return jsonify(persona.to_dict()), 200


# ---------------------------------------------------------------------------
# PATCH /api/owner_gym/personal/<id>/toggle
# ---------------------------------------------------------------------------
@personal_bp.route("/personal/<id_personal>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def toggle_personal(id_personal):
    gym_id  = g.tenant_id
    persona = PersonalOperativo.find_by_id(id_personal, id_gimnasio=gym_id)
    if not persona:
        return jsonify({"msg": "No encontrado"}), 404
    persona.activo = not persona.activo
    persona.save()
    estado = "activado" if persona.activo else "desactivado"
    return jsonify({"msg": f"Personal {estado}", **persona.to_dict()}), 200


# ---------------------------------------------------------------------------
# POST /api/owner_gym/personal/trasladar
# ---------------------------------------------------------------------------
@personal_bp.route("/personal/trasladar", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def trasladar_personal():
    """
    Mueve uno o más integrantes del personal operativo de ESTA sucursal a
    otra de la misma organización -- mismo criterio de alcance que
    traslado_sucursal.py::trasladar_miembros (HU-48), pero sin tocar
    PostgreSQL: este personal no tiene Usuario ni AsignacionUsuario, solo
    el documento en Mongo. Tampoco pasa por el tope de staff del plan
    (max_staff): ese límite cuenta Usuarios con login (Entrenador,
    Recepcionista, Administrador), y este personal no lo es.
    """
    gym_id = g.tenant_id
    data   = request.get_json(silent=True) or {}
    ids_crudos = data.get("ids_personal")
    if not isinstance(ids_crudos, list) or not ids_crudos:
        return jsonify({"msg": "ids_personal debe ser una lista con al menos un elemento"}), 400
    try:
        ids_personal = [ObjectId(str(i)) for i in ids_crudos]
    except (InvalidId, TypeError):
        return jsonify({"msg": "ids_personal contiene un id inválido"}), 400

    destino_pedido = data.get("id_gimnasio_destino")
    try:
        destino = int(destino_pedido) if destino_pedido else gym_id
    except (TypeError, ValueError):
        return jsonify({"msg": "id_gimnasio_destino inválido"}), 400

    mi_gym      = Gimnasio.query.get(gym_id)
    gym_destino = Gimnasio.query.get(destino)
    if not mi_gym or not gym_destino or gym_destino.id_organizacion != mi_gym.id_organizacion:
        return jsonify({"msg": "Sucursal destino no encontrada"}), 404

    if destino == gym_id:
        return jsonify({"msg": "Nadie requería traslado", "movidos": 0}), 200

    mdb  = get_db()
    docs = list(mdb.personal_operativo.find({"_id": {"$in": ids_personal}, "id_gimnasio": gym_id}))
    if len(docs) != len(ids_personal):
        return jsonify({"msg": "Uno o más registros no existen en esta sucursal"}), 404

    # AC8 (igual que para miembros): Administrador necesita alcance sobre
    # AMBAS sucursales; Propietario puede mover entre cualquier par de su
    # organización (ya garantizado arriba por id_organizacion).
    claims = get_jwt()
    if normalizar_rol(claims.get("role")) == ROL_ADMINISTRADOR:
        actor_id = int(get_jwt_identity())
        for id_gym in (gym_id, destino):
            if not usuario_tiene_alcance(actor_id, [ROL_ADMINISTRADOR], id_gimnasio_objetivo=id_gym):
                return jsonify({"msg": "No tienes asignada una de las sucursales involucradas"}), 403

    mdb.personal_operativo.update_many(
        {"_id": {"$in": ids_personal}}, {"$set": {"id_gimnasio": destino}}
    )

    registrar_evento(
        int(get_jwt_identity()), "personal.trasladar_sucursal",
        id_organizacion=gym_destino.id_organizacion, id_gimnasio=destino,
        detalle={
            "ids_personal": [str(i) for i in ids_personal],
            "id_gimnasio_destino": destino, "origen": gym_id,
        },
    )
    pg_db.session.commit()

    return jsonify({
        "movidos": len(docs),
        "sucursal_destino": {"id": gym_destino.id, "nombre": gym_destino.nombre},
    }), 200
