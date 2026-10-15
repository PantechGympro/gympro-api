"""
routes/compartido/traslado_sucursal.py — Traslado de miembro(s) entre
sucursales de la misma organización (HU-48 / SCRUM-219).

El destino es `id_gimnasio_destino` si se manda explícito, o si se omite,
la sucursal del propio token (g.tenant_id). Dos flujos distintos lo usan:
  - Verificar Visitante (HU-47/SCRUM-218), UN miembro: "cambiar ESTE
    visitante A mi sucursal" -- no manda destino, es implícito.
  - Miembros (HU-48/SCRUM-219), selección múltiple: "mover a los
    seleccionados A otra sucursal que yo elijo" -- manda destino
    explícito, porque ahí la dirección es la inversa (empujar miembros
    de ESTA sucursal hacia otra, no traer visitantes hacia la propia).
En ambos casos, sea implícito o explícito, el destino se valida contra la
MISMA organización que la sucursal del token (nunca una organización
ajena), y el AC8 de abajo exige alcance real sobre él para un
Administrador -- no basta con mandarlo en el body.

Reglas de negocio (AC de HU-48), todas aplicadas aquí:
  AC1  Selección múltiple en una sola confirmación -- `ids_miembro` es
       siempre una lista, de 1 o más elementos.
  AC2  Capacidad del destino se valida ANTES de ejecutar; si no caben
       todos, se avisa cuántos sí caben y NO se ejecuta nada.
  AC3  La membresía vigente no se toca (ni se prorratea ni se devuelve):
       este endpoint nunca escribe en miembro_membresia.
  AC4  La renovación se cobra en la sucursal nueva -- ya es automático en
       cuanto id_gimnasio_pg cambia (ver owner_gym/pagos.py::registrar_pago,
       que usa g.tenant_id, no el gimnasio del miembro).
  AC5  El ingreso original no se mueve: no se toca la colección `pagos`.
  AC6  El expediente (salud, progreso, rutinas) viaja con el miembro
       porque esas colecciones se indexan por id_miembro (ObjectId), no
       por id_gimnasio_pg -- no hay nada que mover ahí.
  AC7  Las asistencias históricas no se mueven: no se toca `asistencias`.
  AC8  Lo ejecuta Propietario, o un Administrador con alcance sobre AMBAS
       sucursales (origen Y destino) -- ver usuario_tiene_alcance().
       Recepcionista no puede (@require_role no lo incluye).
  AC9  Bitácora con quién, cuándo y qué miembros (registrar_evento()).
  AC10 Atómico: todo o nada. Mongo no participa de la transacción de PG
       (sin replica set no hay transacciones multidocumento aquí), así
       que si falla a medio camino se compensa a mano revirtiendo los
       documentos Mongo que sí llegaron a cambiar, y se hace rollback de
       la sesión PG -- ver el except de _ejecutar más abajo.
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from bson import ObjectId
from bson.errors import InvalidId

from app.mongo import get_db
from app.extensions import db as pg_db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario as UsuarioPG
from app.models.pg.asignacion_usuario import AsignacionUsuario
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.capacidad import verificar_capacidad_miembro, LimiteDePlanExcedido
from app.utils.alcance import usuario_tiene_alcance, registrar_evento, sucursales_de_administrador
from app.constants.roles import normalizar_rol, ROL_ADMINISTRADOR

traslado_bp = Blueprint("traslado_sucursal", __name__)


@traslado_bp.route("/miembros/sucursales-destino", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def sucursales_destino():
    """
    Sucursales de la organización hacia las que el usuario actual puede
    trasladar miembros: todas, si es Propietario; solo las que tiene
    asignadas, si es Administrador (AC8 -- las mismas que puede usar como
    destino en /miembros/trasladar). Alimenta el selector de destino de la
    pantalla de Miembros; el frontend oculta el traslado masivo por
    completo si esto devuelve 1 sola fila (nada a donde mover).
    """
    mi_gym = Gimnasio.query.get(g.tenant_id)
    if not mi_gym:
        return jsonify([]), 200

    claims = get_jwt()
    if normalizar_rol(claims.get("role")) == ROL_ADMINISTRADOR:
        ids = sucursales_de_administrador(int(get_jwt_identity()))
        q = Gimnasio.query.filter(Gimnasio.id.in_(ids))
    else:
        q = Gimnasio.query.filter_by(id_organizacion=mi_gym.id_organizacion)

    sucursales = q.order_by(Gimnasio.nombre).all()
    return jsonify([
        {"id": s.id, "nombre": s.nombre, "es_actual": s.id == g.tenant_id}
        for s in sucursales
    ]), 200


@traslado_bp.route("/miembros/trasladar", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def trasladar_miembros():
    data = request.get_json(silent=True) or {}
    ids_crudos = data.get("ids_miembro")
    if not isinstance(ids_crudos, list) or not ids_crudos:
        return jsonify({"msg": "ids_miembro debe ser una lista con al menos un elemento"}), 400

    try:
        ids_miembro = [ObjectId(str(i)) for i in ids_crudos]
    except (InvalidId, TypeError):
        return jsonify({"msg": "ids_miembro contiene un id inválido"}), 400

    destino_pedido = data.get("id_gimnasio_destino")
    try:
        destino = int(destino_pedido) if destino_pedido else g.tenant_id
    except (TypeError, ValueError):
        return jsonify({"msg": "id_gimnasio_destino inválido"}), 400

    mi_gym = Gimnasio.query.get(g.tenant_id)
    gym_destino = Gimnasio.query.get(destino)
    if not mi_gym or not gym_destino or gym_destino.id_organizacion != mi_gym.id_organizacion:
        return jsonify({"msg": "Sucursal destino no encontrada"}), 404

    mdb = get_db()
    docs = list(mdb.miembros.find({"_id": {"$in": ids_miembro}}))
    if len(docs) != len(ids_miembro):
        return jsonify({"msg": "Uno o más miembros no existen"}), 404

    # Todos deben pertenecer a la misma organización que el destino.
    ids_org = {
        row.id for row in
        Gimnasio.query.filter_by(id_organizacion=gym_destino.id_organizacion)
        .with_entities(Gimnasio.id).all()
    }
    a_mover = []
    for d in docs:
        origen = d.get("id_gimnasio_pg")
        if origen not in ids_org:
            return jsonify({"msg": "Un miembro no pertenece a esta organización"}), 400
        if origen != destino:
            a_mover.append(d)

    # Si ya estaban todos en el destino no hay nada que hacer -- no es un
    # error (evita que un reintento accidental dé una falla confusa).
    if not a_mover:
        return jsonify({"msg": "Ningún miembro requería traslado", "movidos": 0}), 200

    # AC8: Administrador necesita alcance sobre AMBAS sucursales (cada
    # origen Y el destino). Propietario puede mover entre cualquier par
    # de sucursales de SU organización (ya garantizado arriba por ids_org).
    claims = get_jwt()
    es_administrador = normalizar_rol(claims.get("role")) == ROL_ADMINISTRADOR
    if es_administrador:
        actor_id = int(get_jwt_identity())
        sucursales_involucradas = {d["id_gimnasio_pg"] for d in a_mover} | {destino}
        for id_gym in sucursales_involucradas:
            if not usuario_tiene_alcance(actor_id, [ROL_ADMINISTRADOR], id_gimnasio_objetivo=id_gym):
                return jsonify({"msg": "No tienes asignada una de las sucursales involucradas"}), 403

    # AC2: capacidad del destino ANTES de ejecutar -- todo o nada.
    try:
        verificar_capacidad_miembro(destino, incremento=len(a_mover))
    except LimiteDePlanExcedido as e:
        caben = max(e.tope - e.actual, 0)
        return jsonify({"msg": str(e), "caben": caben, "solicitados": len(a_mover)}), 409

    aplicados = []
    try:
        for d in a_mover:
            origen = d["id_gimnasio_pg"]

            # 1. PG: Usuario.id_gimnasio -- el tenant real del propio
            # miembro (su login, su JWT la próxima vez que entre).
            if d.get("id_usuario_pg"):
                usuario_pg = UsuarioPG.query.get(d["id_usuario_pg"])
                if usuario_pg:
                    usuario_pg.id_gimnasio = destino

                # 1b. AsignacionUsuario del miembro (HU-41/42): se
                # actualiza en el lugar en vez de dejarla apuntando a la
                # sucursal vieja -- ver utils/alcance.py.
                asignacion = AsignacionUsuario.query.filter_by(
                    id_usuario=d["id_usuario_pg"], id_gimnasio=origen
                ).first()
                if asignacion:
                    asignacion.id_gimnasio = destino

            # 2. Mongo: miembros.id_gimnasio_pg -- la fuente real para el
            # resto del sistema (dashboards, listados, capacidad).
            mdb.miembros.update_one({"_id": d["_id"]}, {"$set": {"id_gimnasio_pg": destino}})
            aplicados.append(d)

        registrar_evento(
            int(get_jwt_identity()), "miembros.trasladar_sucursal",
            id_organizacion=gym_destino.id_organizacion, id_gimnasio=destino,
            detalle={
                "ids_miembro": [str(d["_id"]) for d in a_mover],
                "id_gimnasio_destino": destino,
                "origenes": {str(d["_id"]): d["id_gimnasio_pg"] for d in a_mover},
            },
        )
        pg_db.session.commit()
    except Exception as e:
        # AC10: compensación manual -- Mongo no participa de la
        # transacción de PG, así que los documentos que sí llegaron a
        # cambiar antes del fallo se revierten a su sucursal de origen.
        pg_db.session.rollback()
        for d in aplicados:
            mdb.miembros.update_one(
                {"_id": d["_id"]}, {"$set": {"id_gimnasio_pg": d["id_gimnasio_pg"]}}
            )
        return jsonify({"msg": "No se pudo completar el traslado", "detalle": str(e)}), 500

    return jsonify({
        "movidos": len(a_mover),
        "ids_miembro": [str(d["_id"]) for d in a_mover],
        "sucursal_destino": {"id": gym_destino.id, "nombre": gym_destino.nombre},
    }), 200
