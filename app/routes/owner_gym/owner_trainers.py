"""
owner_gym/owner_trainers.py - Gestión de entrenadores y recepcionistas del gimnasio.

Endpoints:
    GET  /api/owner_gym/staff               Lista todos (entrenadores + recepcionistas)
    GET  /api/owner_gym/staff/<id>          Detalle de un usuario staff
    POST /api/owner_gym/staff               Crear usuario staff (entrenador o recepcionista)
    PATCH /api/owner_gym/staff/<id>/toggle  Activar / desactivar
    POST /api/owner_gym/staff/trasladar     Mover staff a otra sucursal
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models.pg.usuario   import Usuario
from app.models.pg.rol        import Rol
from app.models.pg.gimnasio   import Gimnasio
from app.models.pg.asignacion_usuario import AsignacionUsuario
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import crear_asignacion, usuario_tiene_alcance, registrar_evento
from app.utils.capacidad import verificar_capacidad_staff, LimiteDePlanExcedido
from app.utils.busqueda import normalizar_columna
from app.utils.credenciales_acceso import (
    generar_password_temporal, enviar_credenciales_acceso, reenviar_credenciales_acceso,
)  # SCRUM-224
from app.constants.roles import normalizar_rol, ROL_ADMINISTRADOR

owner_trainers_bp = Blueprint("owner_trainers", __name__)

# HU-45: un Administrador da de alta y gestiona el Staff (Entrenador /
# Recepcionista) de SU sucursal -- el formulario de alta (OwnerStaff.jsx)
# no ofrece el rol Administrador como opción, así que no hay riesgo de que
# un Administrador cree a otro Administrador por esta vía (eso sigue siendo
# exclusivo de Propietario, ver sucursales.py). El tope de capacidad de
# staff (utils/capacidad.py) ya es por Gimnasio, así que sigue aplicando
# igual sin importar quién da de alta.

_STAFF_ROLES         = ("Entrenador", "Recepcionista")
_VALID_FOTO_PREFIXES = ("data:image/jpeg;base64,", "data:image/png;base64,",
                        "data:image/webp;base64,", "data:image/gif;base64,")

def _valid_foto(value):
    return value if (isinstance(value, str) and value.startswith(_VALID_FOTO_PREFIXES)) else None


def _staff_filter(gym_id):
    """Roles válidos de staff para un gimnasio."""
    roles = Rol.query.filter(Rol.nombre.in_(_STAFF_ROLES)).all()
    role_ids = [r.id for r in roles]
    return role_ids


# ---------------------------------------------------------------------------
# GET /api/owner_gym/staff
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def listar_staff():
    """Lista entrenadores y recepcionistas del gimnasio con filtros opcionales."""
    gym_id = g.tenant_id
    rol_filter    = request.args.get("rol")          # "Entrenador" | "Recepcionista"
    activos_param = request.args.get("activos", "true").strip().lower()
    search        = request.args.get("q", "").strip()

    role_ids = _staff_filter(gym_id)
    if not role_ids:
        return jsonify([]), 200

    q = Usuario.query.filter(
        Usuario.id_gimnasio == gym_id,
        Usuario.id_rol.in_(role_ids),
    )
    # "true"/"false" filtran explícitamente por el estatus solicitado; cualquier
    # otro valor (p. ej. "" o "all") devuelve activos e inactivos sin filtrar.
    if activos_param == "true":
        q = q.filter_by(activo=True)
    elif activos_param == "false":
        q = q.filter_by(activo=False)
    if rol_filter:
        rol_obj = Rol.query.filter_by(nombre=rol_filter).first()
        if rol_obj:
            q = q.filter_by(id_rol=rol_obj.id)
    if search:
        # SCRUM-105: el nombre se compara ignorando mayúsculas/acentos
        # (normalizar_columna, única fuente de esa lógica -- ver
        # utils/busqueda.py); el email queda fuera de alcance a propósito
        # (identificador técnico) y se sigue comparando tal cual.
        q = q.filter(
            db.or_(
                normalizar_columna(Usuario.nombre).ilike(normalizar_columna(f"%{search}%")),
                Usuario.email.ilike(f"%{search}%"),
            )
        )

    usuarios = q.order_by(Usuario.nombre).all()
    return jsonify([u.to_dict() for u in usuarios]), 200


# ---------------------------------------------------------------------------
# GET /api/owner_gym/staff/<id>
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff/<int:user_id>", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def get_staff_member(user_id: int):
    gym_id   = g.tenant_id
    role_ids = _staff_filter(gym_id)
    usuario  = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario or usuario.id_rol not in role_ids:
        return jsonify({"msg": "Usuario no encontrado"}), 404
    return jsonify(usuario.to_dict()), 200


# ---------------------------------------------------------------------------
# POST /api/owner_gym/staff
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def crear_staff():
    """
    Crea un nuevo usuario staff (entrenador o recepcionista) en el gimnasio.
    Body JSON:
        {
            "nombre":   "Juan Pérez",
            "email":    "juan@gym.com",
            "rol":      "Entrenador"   // o "Recepcionista"
        }

    SCRUM-224: ya no se manda "password" -- se genera aquí y se le envía
    por correo al propio entrenador/recepcionista, que la cambia en su
    primer login (ver Usuario.debe_cambiar_password).
    """
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    nombre   = (data.get("nombre")   or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    rol_name = (data.get("rol")      or "").strip()

    if not all([nombre, email, rol_name]):
        return jsonify({"msg": "nombre, email y rol son requeridos"}), 400
    if rol_name not in _STAFF_ROLES:
        return jsonify({"msg": f"rol debe ser uno de: {', '.join(_STAFF_ROLES)}"}), 400

    # SCRUM-222 (AC2): tope de staff del plan, verificado en el alta.
    try:
        verificar_capacidad_staff(gym_id)
    except LimiteDePlanExcedido as e:
        return jsonify({"msg": str(e)}), 409

    if Usuario.query.filter_by(email=email).first():
        return jsonify({"msg": "El email ya está registrado"}), 409

    rol_obj = Rol.query.filter_by(nombre=rol_name).first()
    if not rol_obj:
        return jsonify({"msg": f"Rol '{rol_name}' no existe en la base de datos"}), 500

    nuevo = Usuario(
        nombre      = nombre,
        email       = email,
        id_rol      = rol_obj.id,
        id_gimnasio = gym_id,
        activo      = True,
        foto_perfil = _valid_foto(data.get("foto_base64")),
    )
    password_generada = generar_password_temporal()
    nuevo.set_password(password_generada)
    nuevo.debe_cambiar_password = True
    db.session.add(nuevo)
    db.session.flush()

    # Asignación de alcance del nuevo staff (HU-41 / SCRUM-212) -- no
    # cambia nada del comportamiento actual de este endpoint.
    crear_asignacion(nuevo)

    db.session.commit()

    enviar_credenciales_acceso(email, nombre, password_generada, rol_name)

    return jsonify({"msg": "Usuario creado", **nuevo.to_dict()}), 201


# ---------------------------------------------------------------------------
# POST /api/owner_gym/staff/<id>/reenviar-credenciales
# ---------------------------------------------------------------------------
# Mismo criterio que miembros.py::reenviar_credenciales_miembro -- genera una
# contrasena NUEVA (no reenvia la original) y la reenvia por correo, para
# cuando el correo de bienvenida nunca llego (caso real: @hotmail).
@owner_trainers_bp.route("/staff/<int:user_id>/reenviar-credenciales", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def reenviar_credenciales_staff(user_id: int):
    gym_id   = g.tenant_id
    role_ids = _staff_filter(gym_id)
    usuario  = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario or usuario.id_rol not in role_ids:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    rol_label = usuario.rol.nombre if usuario.rol else "Staff"
    enviado = reenviar_credenciales_acceso(usuario, rol_label)
    db.session.commit()

    if enviado:
        return jsonify({"msg": "Se reenviaron las credenciales por correo."}), 200
    return jsonify({
        "msg": "Se generó una contraseña nueva, pero el correo no pudo enviarse. "
               "Intenta de nuevo más tarde o contacta a soporte.",
        "aviso": True,
    }), 200


# ---------------------------------------------------------------------------
# PUT /api/owner_gym/staff/<id>
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff/<int:user_id>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def actualizar_staff(user_id: int):
    """
    Edita datos de un miembro del staff (entrenador o recepcionista).
    Body JSON (todos opcionales):
        { "nombre": "...", "email": "...", "password": "...", "rol": "Entrenador" }
    """
    gym_id   = g.tenant_id
    role_ids = _staff_filter(gym_id)
    usuario  = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario or usuario.id_rol not in role_ids:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    data     = request.get_json() or {}
    nombre   = (data.get("nombre")   or "").strip() or None
    email    = (data.get("email")    or "").strip().lower() or None
    password = (data.get("password") or "").strip() or None
    rol_name = (data.get("rol")      or "").strip() or None

    if nombre:
        usuario.nombre = nombre

    if email and email != usuario.email:
        if Usuario.query.filter_by(email=email).first():
            return jsonify({"msg": "El email ya está registrado"}), 409
        usuario.email = email

    if password:
        usuario.set_password(password)
        usuario.debe_cambiar_password = True  # SCRUM-224: la acaba de fijar el admin, no él mismo

    if rol_name:
        if rol_name not in _STAFF_ROLES:
            return jsonify({"msg": f"rol debe ser uno de: {', '.join(_STAFF_ROLES)}"}), 400
        rol_obj = Rol.query.filter_by(nombre=rol_name).first()
        if not rol_obj:
            return jsonify({"msg": f"Rol '{rol_name}' no existe en la base de datos"}), 500
        usuario.id_rol = rol_obj.id

    foto_nueva = _valid_foto(data.get("foto_base64"))
    if foto_nueva:
        usuario.foto_perfil = foto_nueva

    db.session.commit()
    return jsonify({"msg": "Usuario actualizado", **usuario.to_dict()}), 200


# ---------------------------------------------------------------------------
# PATCH /api/owner_gym/staff/<id>/toggle
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff/<int:user_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def toggle_staff(user_id: int):
    gym_id   = g.tenant_id
    role_ids = _staff_filter(gym_id)
    usuario  = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario or usuario.id_rol not in role_ids:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    usuario.activo = not usuario.activo
    db.session.commit()
    estado = "activado" if usuario.activo else "desactivado"
    return jsonify({"msg": f"Usuario {estado}", **usuario.to_dict()}), 200


# ---------------------------------------------------------------------------
# POST /api/owner_gym/staff/trasladar
# ---------------------------------------------------------------------------
@owner_trainers_bp.route("/staff/trasladar", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
def trasladar_staff():
    """
    Traslada uno o más integrantes del staff (Entrenador/Recepcionista) de
    ESTA sucursal a otra de la misma organización -- mismo criterio de
    alcance que traslado_sucursal.py::trasladar_miembros (HU-48), pero
    más simple: el staff vive solo en PostgreSQL (Usuario.id_gimnasio +
    AsignacionUsuario), no tiene contraparte en Mongo que sincronizar, así
    que no hace falta la compensación manual de aquel endpoint -- basta
    con el rollback normal de la transacción si algo falla.
    """
    gym_id = g.tenant_id
    data   = request.get_json(silent=True) or {}
    ids_crudos = data.get("ids_usuario")
    if not isinstance(ids_crudos, list) or not ids_crudos:
        return jsonify({"msg": "ids_usuario debe ser una lista con al menos un elemento"}), 400
    try:
        ids_usuario = [int(i) for i in ids_crudos]
    except (TypeError, ValueError):
        return jsonify({"msg": "ids_usuario contiene un id inválido"}), 400

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
        return jsonify({"msg": "Ningún integrante requería traslado", "movidos": 0}), 200

    role_ids = _staff_filter(gym_id)
    usuarios = Usuario.query.filter(
        Usuario.id.in_(ids_usuario),
        Usuario.id_gimnasio == gym_id,
        Usuario.id_rol.in_(role_ids),
    ).all()
    if len(usuarios) != len(set(ids_usuario)):
        return jsonify({"msg": "Uno o más integrantes del staff no existen en esta sucursal"}), 404

    # AC8 (igual que para miembros): Administrador necesita alcance sobre
    # AMBAS sucursales (origen y destino); Propietario puede mover entre
    # cualquier par de su organización (ya garantizado arriba por
    # id_organizacion).
    claims = get_jwt()
    if normalizar_rol(claims.get("role")) == ROL_ADMINISTRADOR:
        actor_id = int(get_jwt_identity())
        for id_gym in (gym_id, destino):
            if not usuario_tiene_alcance(actor_id, [ROL_ADMINISTRADOR], id_gimnasio_objetivo=id_gym):
                return jsonify({"msg": "No tienes asignada una de las sucursales involucradas"}), 403

    try:
        verificar_capacidad_staff(destino, incremento=len(usuarios))
    except LimiteDePlanExcedido as e:
        caben = max(e.tope - e.actual, 0)
        return jsonify({"msg": str(e), "caben": caben, "solicitados": len(usuarios)}), 409

    for u in usuarios:
        u.id_gimnasio = destino
        asignacion = AsignacionUsuario.query.filter_by(id_usuario=u.id, id_gimnasio=gym_id).first()
        if asignacion:
            asignacion.id_gimnasio = destino
        else:
            # No debería faltar (crear_staff siempre llama crear_asignacion),
            # pero si falta por datos legados, se crea ahora en vez de dejar
            # al staff sin asignación tras el traslado.
            db.session.add(AsignacionUsuario(
                id_usuario=u.id, id_organizacion=gym_destino.id_organizacion,
                id_gimnasio=destino, id_rol=u.id_rol,
            ))

    registrar_evento(
        int(get_jwt_identity()), "staff.trasladar_sucursal",
        id_organizacion=gym_destino.id_organizacion, id_gimnasio=destino,
        detalle={"ids_usuario": ids_usuario, "id_gimnasio_destino": destino, "origen": gym_id},
    )
    db.session.commit()

    return jsonify({
        "movidos": len(usuarios),
        "ids_usuario": [u.id for u in usuarios],
        "sucursal_destino": {"id": gym_destino.id, "nombre": gym_destino.nombre},
    }), 200
