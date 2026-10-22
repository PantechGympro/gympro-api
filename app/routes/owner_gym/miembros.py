import re
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required
from datetime import datetime
from bson.objectid import ObjectId

from app.mongo import get_db
from app.models.user import User
from app.models.miembro import Miembro
from app.utils.tenant import get_tenant_filter, require_tenant
from app.utils.alcance import crear_asignacion
from app.utils.capacidad import verificar_capacidad_miembro, LimiteDePlanExcedido
from app.utils.busqueda import patron_insensible
# PG — fuente de verdad desde Sprint 2
from app.extensions import db as pg_db
from app.models.pg.usuario import Usuario as UsuarioPG
from app.models.pg.rol import Rol as RolPG
from app.utils.credenciales_acceso import (
    generar_password_temporal, enviar_credenciales_acceso, reenviar_credenciales_acceso,
)  # SCRUM-224
from app.utils.security import require_role
from app.constants.roles import ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA

miembros_bp = Blueprint("miembros", __name__)

VALID_BASE64_PREFIXES = ("data:image/jpeg;base64,", "data:image/png;base64,",
                         "data:image/webp;base64,", "data:image/gif;base64,")

def _valid_foto(value):
    """Devuelve la cadena base64 si es válida, None en caso contrario."""
    return value if (isinstance(value, str) and value.startswith(VALID_BASE64_PREFIXES)) else None

# ==============================================================================
# 1. LISTAR MIEMBROS (CON BUSCADOR + PAGINACIÓN)
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["GET"])
@jwt_required()
@require_tenant
def listar_miembros():
    import math
    db        = get_db()
    gym_id    = g.tenant_id
    page      = request.args.get('page', 1, type=int)
    per_page  = 6
    mostrar_inactivos = request.args.get('inactivos', 'false') == 'true'
    search    = request.args.get('search', '', type=str)

    estado_filtro = "Inactivo" if mostrar_inactivos else "Activo"

    # Aislamiento estricto: sólo miembros del gimnasio del token
    filtro_miembros = {
        "estado":         estado_filtro,
        "id_gimnasio_pg": gym_id,
    }

    if search:
        # SCRUM-105: el nombre se compara ignorando mayúsculas/acentos
        # (patron_insensible, única fuente de esa lógica -- ver
        # utils/busqueda.py; MongoDB no tiene un equivalente a unaccent,
        # por eso el patrón se construye en Python en vez de delegarlo a
        # la base). El email queda fuera de alcance a propósito
        # (identificador técnico) y se sigue comparando tal cual.
        regex_email = re.compile(re.escape(search), re.IGNORECASE)
        filtro_miembros["$or"] = [
            {"nombre": {"$regex": patron_insensible(search), "$options": "i"}},
            {"email":  regex_email},
        ]
        # Búsqueda exacta por ID (id_usuario_pg / "número de socio") cuando
        # el texto es solo dígitos -- mismo criterio que ya usa
        # recepcionista_routes.py::get_members().
        if search.strip().isdigit():
            filtro_miembros["$or"].append({"id_usuario_pg": int(search.strip())})

    skip            = (page - 1) * per_page
    total_miembros  = db.miembros.count_documents(filtro_miembros)
    miembros_cursor = db.miembros.find(filtro_miembros).sort("fecha_registro", -1).skip(skip).limit(per_page)
    pages           = math.ceil(total_miembros / per_page) if total_miembros > 0 else 0

    miembros_docs = list(miembros_cursor)

    # Un solo round-trip a Postgres para marcar quienes siguen con la
    # contraseña temporal del alta (para que el frontend pueda ofrecer
    # "Reenviar correo" solo donde de verdad aplica) -- evita N+1 queries.
    ids_pg = [int(d["id_usuario_pg"]) for d in miembros_docs if d.get("id_usuario_pg")]
    pendientes_ids = set()
    if ids_pg:
        pendientes_ids = {
            u.id for u in UsuarioPG.query.filter(
                UsuarioPG.id.in_(ids_pg),
                UsuarioPG.debe_cambiar_password.is_(True),
            ).all()
        }

    miembros_lista = []
    for d in miembros_docs:
        item = Miembro(**d).to_dict_full(include_stats=False)
        id_usuario_pg = d.get("id_usuario_pg")
        item["id_usuario_pg"] = id_usuario_pg
        try:
            item["debe_cambiar_password"] = int(id_usuario_pg) in pendientes_ids
        except (TypeError, ValueError):
            item["debe_cambiar_password"] = False
        miembros_lista.append(item)

    return jsonify({
        "miembros":     miembros_lista,
        "total":        total_miembros,
        "pages":        pages,
        "current_page": page,
    }), 200

# ==============================================================================
# 2. CREAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA)
@require_tenant
def crear_miembro():
    db     = get_db()
    gym_id = g.tenant_id
    data   = request.get_json(silent=True) or {}

    nombre   = data.get('nombre', '').strip()
    email    = data.get('email', '').strip().lower()

    if not nombre or not email:
        return jsonify({"error": "Nombre y Email son obligatorios"}), 400

    # SCRUM-224: la contraseña ya no la escribe quien da de alta al miembro
    # (antes incluso caía a un fallback fijo "gym123" si se dejaba en
    # blanco) -- se genera aquí y se le envía por correo al propio
    # miembro, que la cambia en su primer login (debe_cambiar_password).
    password_generada = generar_password_temporal()

    # SCRUM-222 (AC1): el tope de plan se verifica en el alta, antes de
    # tocar la base -- nunca en una consulta de lectura (ver utils/capacidad.py).
    try:
        verificar_capacidad_miembro(gym_id)
    except LimiteDePlanExcedido as e:
        return jsonify({"error": str(e)}), 409

    # Verificar duplicado en PG y en Mongo
    if UsuarioPG.query.filter_by(email=email).first():
        return jsonify({"error": "El email ya está registrado"}), 400
    if User.find_by_email(email):
        return jsonify({"error": "El email ya está registrado"}), 400

    try:
        # 1. Crear Usuario en PostgreSQL
        rol_pg = RolPG.query.filter_by(nombre="Miembro").first()
        if not rol_pg:
            return jsonify({"error": "Rol 'Miembro' no encontrado en base de datos"}), 500

        pg_usuario = UsuarioPG(
            nombre      = nombre,
            email       = email,
            id_rol      = rol_pg.id,
            id_gimnasio = gym_id,
            activo      = True,
        )
        pg_usuario.set_password(password_generada)
        pg_usuario.debe_cambiar_password = True
        pg_db.session.add(pg_usuario)
        pg_db.session.flush()

        # 1b. Asignación de alcance del miembro (HU-41 / SCRUM-212). El
        # gimnasio ya existe (es el tenant del token) -- crear_asignacion
        # resuelve su organización con una consulta, sin cambiar nada del
        # comportamiento actual de este endpoint.
        crear_asignacion(pg_usuario)

        # 2. Foto: base64 almacenada directamente en MongoDB (sin filesystem)
        foto_perfil = _valid_foto(data.get("foto_base64"))

        # 3. Crear documento Miembro en MongoDB
        nuevo_miembro = Miembro(
            id_usuario_pg  = pg_usuario.id,
            nombre         = nombre,
            email          = email,
            id_gimnasio_pg = gym_id,
            telefono       = data.get("telefono"),
            sexo           = data.get("sexo"),
            peso_inicial   = data.get("peso_inicial"),
            estatura       = data.get("estatura"),
            fecha_registro = datetime.now(),
            estado         = "Activo",
            foto_perfil    = foto_perfil,
        )
        miembro_id        = nuevo_miembro.save()
        nuevo_miembro._id = miembro_id

        pg_db.session.commit()

        # El correo se manda ya con la fila comprometida (commit arriba) --
        # si falla el envío (ver enviar_credenciales_acceso: falla
        # silenciosamente), el miembro queda creado igual, solo que
        # alguien tendrá que resetearle la contraseña a mano.
        enviar_credenciales_acceso(email, nombre, password_generada, "Miembro")

        return jsonify(nuevo_miembro.to_dict_full()), 201

    except Exception as e:
        pg_db.session.rollback()
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 2b. REENVIAR CREDENCIALES (correo de bienvenida con contraseña nueva)
# ==============================================================================
# Para cuando el correo original nunca llego -- genera una contrasena
# temporal NUEVA (no reenvia la vieja) y la reenvia por correo. No bloquea
# el alta ni afecta el estado del miembro si el envio vuelve a fallar; solo
# informa si el SMTP acepto o no el mensaje (ver reenviar_credenciales_acceso
# y su nota sobre por que un "True" no garantiza que llegue a la bandeja de
# entrada -- Hotmail/Outlook filtran agresivo el correo con contrasenas).
@miembros_bp.route("/api/miembros/<id>/reenviar-credenciales", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_RECEPCIONISTA)
@require_tenant
def reenviar_credenciales_miembro(id):
    db     = get_db()
    gym_id = g.tenant_id

    try:
        miembro = db.miembros.find_one({"_id": ObjectId(id), "id_gimnasio_pg": gym_id})
    except Exception:
        return jsonify({"error": "Id de miembro invalido"}), 400
    if not miembro:
        return jsonify({"error": "Miembro no encontrado"}), 404

    id_usuario_pg = miembro.get("id_usuario_pg")
    usuario = UsuarioPG.query.get(int(id_usuario_pg)) if id_usuario_pg else None
    if not usuario:
        return jsonify({"error": "Este miembro no tiene una cuenta de acceso asociada"}), 400

    enviado = reenviar_credenciales_acceso(usuario, "Miembro")
    pg_db.session.commit()

    if enviado:
        return jsonify({"msg": "Se reenviaron las credenciales por correo."}), 200
    return jsonify({
        "msg": "Se generó una contraseña nueva, pero el correo no pudo enviarse. "
               "Intenta de nuevo más tarde o contacta a soporte.",
        "aviso": True,
    }), 200

# ==============================================================================
# 3. ACTUALIZAR MIEMBRO (PUT)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_miembro(id):
    db      = get_db()
    gym_id  = g.tenant_id
    miembro = Miembro.find_by_id(id)
    if not miembro:
        return jsonify({"error": "Miembro no encontrado"}), 404
    if miembro.id_gimnasio_pg != gym_id:
        return jsonify({"error": "No autorizado"}), 403

    # Resolver usuario: PG (nuevo) o Mongo legacy (pre-Sprint 2)
    usuario_pg    = UsuarioPG.query.get(miembro.id_usuario_pg) if miembro.id_usuario_pg else None
    usuario_mongo = User.find_by_id(miembro.id_usuario) if miembro.id_usuario else None

    data = request.get_json(silent=True) or {}

    try:
        nuevo_nombre = (data.get('nombre') or '').strip() or None
        nuevo_email  = (data.get('email') or '').strip().lower() or None

        # Actualizar usuario en PG si existe
        if usuario_pg:
            if nuevo_nombre:
                usuario_pg.nombre = nuevo_nombre
            if nuevo_email:
                conflicto = UsuarioPG.query.filter_by(email=nuevo_email).first()
                if conflicto and conflicto.id != usuario_pg.id:
                    return jsonify({"error": "El email ya está en uso por otro usuario"}), 400
                usuario_pg.email = nuevo_email
            pg_db.session.commit()

        # Actualizar usuario en Mongo si existe (legacy)
        elif usuario_mongo:
            if nuevo_nombre:
                usuario_mongo.nombre = nuevo_nombre
            if nuevo_email:
                existente = User.find_by_email(nuevo_email)
                if existente and existente._id != usuario_mongo._id:
                    return jsonify({"error": "El email ya está en uso por otro usuario"}), 400
                usuario_mongo.email = nuevo_email
            usuario_mongo.save()

        # Actualizar campos desnormalizados en Miembro
        if nuevo_nombre:    miembro.nombre       = nuevo_nombre
        if nuevo_email:     miembro.email        = nuevo_email
        if data.get('telefono'):     miembro.telefono     = data['telefono']
        if data.get('sexo'):         miembro.sexo         = data['sexo']
        if data.get('peso_inicial'): miembro.peso_inicial = data['peso_inicial']
        if data.get('estatura'):     miembro.estatura     = data['estatura']

        # Foto: sólo actualizar si se envía una base64 válida
        foto_nueva = _valid_foto(data.get("foto_base64"))
        if foto_nueva:
            miembro.foto_perfil = foto_nueva

        miembro.save()
        return jsonify(miembro.to_dict_full()), 200

    except Exception as e:
        pg_db.session.rollback()
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 4. ELIMINAR MIEMBRO (Lógico)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_miembro(id):
    try:
        gym_id  = g.tenant_id
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404
        if miembro.id_gimnasio_pg != gym_id:
            return jsonify({"error": "No autorizado"}), 403

        miembro.estado = "Inactivo"
        miembro.save()

        # Desactivar en PG (nuevo) o Mongo (legacy)
        if miembro.id_usuario_pg:
            u_pg = UsuarioPG.query.get(miembro.id_usuario_pg)
            if u_pg:
                u_pg.activo = False
                pg_db.session.commit()
        elif miembro.id_usuario:
            u_mongo = User.find_by_id(miembro.id_usuario)
            if u_mongo:
                u_mongo.activo = False
                u_mongo.save()

        return jsonify({"message": "Miembro desactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 5. REACTIVAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>/reactivar", methods=["PUT"])
@jwt_required()
@require_tenant
def reactivar_miembro(id):
    try:
        gym_id  = g.tenant_id
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404
        if miembro.id_gimnasio_pg != gym_id:
            return jsonify({"error": "No autorizado"}), 403

        miembro.estado = "Activo"
        miembro.save()

        # Reactivar en PG (nuevo) o Mongo (legacy)
        if miembro.id_usuario_pg:
            u_pg = UsuarioPG.query.get(miembro.id_usuario_pg)
            if u_pg:
                u_pg.activo = True
                pg_db.session.commit()
        elif miembro.id_usuario:
            u_mongo = User.find_by_id(miembro.id_usuario)
            if u_mongo:
                u_mongo.activo = True
                u_mongo.save()

        return jsonify({"message": "Miembro reactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==============================================================================
# 6. HISTORIAL DE ASISTENCIAS DE UN MIEMBRO (PAGINADO)
# ==============================================================================
def _fecha_iso(valor):
    """Normaliza la fecha de una asistencia a 'YYYY-MM-DD'.

    La colección mezcla documentos legacy (fecha como string) con los nuevos
    (datetime), así que se normaliza aquí para que el cliente reciba siempre
    el mismo formato y pueda darle formato local sin adivinar.
    """
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, str):
        return valor[:10]
    return None


@miembros_bp.route("/api/miembros/<id>/asistencias", methods=["GET"])
@jwt_required()
@require_tenant
def listar_asistencias_miembro(id):
    """Días en los que el miembro asistió al gimnasio, del más reciente al más antiguo.

    Query params:
        page      (int) — página, base 1. Default 1.
        per_page  (int) — registros por página, máximo 50. Default 10.

    Aislamiento: se valida que el miembro pertenezca al gimnasio del token antes
    de consultar sus asistencias; nunca se filtra sólo por id_miembro sin ese
    chequeo previo.
    """
    import math
    try:
        db     = get_db()
        gym_id = g.tenant_id

        try:
            oid = ObjectId(id)
        except Exception:
            return jsonify({"error": "Id de miembro inválido"}), 400

        miembro = db.miembros.find_one({"_id": oid, "id_gimnasio_pg": gym_id}, {"_id": 1})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        page     = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 10, type=int), 1), 50)
        skip     = (page - 1) * per_page

        filtro = {"id_miembro": oid}
        total  = db.asistencias.count_documents(filtro)
        cursor = (db.asistencias.find(filtro)
                  .sort("fecha", -1)
                  .skip(skip)
                  .limit(per_page))

        asistencias_raw = list(cursor)

        # HU-47/SCRUM-218: si alguna asistencia fue una visita a OTRA
        # sucursal (id_gimnasio_visita != esta, la de origen del
        # miembro), se resuelve su nombre para mostrarlo -- en lote, para
        # no hacer una consulta a PG por cada fila de la página.
        ids_visita = {
            a["id_gimnasio_visita"] for a in asistencias_raw
            if a.get("id_gimnasio_visita") and a["id_gimnasio_visita"] != gym_id
        }
        nombres_sucursal = {}
        if ids_visita:
            from app.models.pg.gimnasio import Gimnasio
            nombres_sucursal = {
                gimn.id: gimn.nombre
                for gimn in Gimnasio.query.filter(Gimnasio.id.in_(ids_visita)).all()
            }

        asistencias = [{
            "id":              str(a["_id"]),
            "fecha":           _fecha_iso(a.get("fecha")),
            "hora_entrada":    a.get("hora_entrada"),
            "hora_salida":     a.get("hora_salida"),
            "origen":          a.get("origen", "checkin"),
            "sucursal_visita": nombres_sucursal.get(a.get("id_gimnasio_visita")),
        } for a in asistencias_raw]

        return jsonify({
            "asistencias":  asistencias,
            "total":        total,
            "pages":        math.ceil(total / per_page) if total else 0,
            "current_page": page,
            "per_page":     per_page,
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
