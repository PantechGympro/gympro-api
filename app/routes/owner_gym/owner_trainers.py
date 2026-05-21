"""
owner_gym/owner_trainers.py — Gestión de entrenadores y recepcionistas del gimnasio.

Endpoints:
    GET  /api/owner_gym/staff               Lista todos (entrenadores + recepcionistas)
    GET  /api/owner_gym/staff/<id>          Detalle de un usuario staff
    POST /api/owner_gym/staff               Crear usuario staff (entrenador o recepcionista)
    PATCH /api/owner_gym/staff/<id>/toggle  Activar / desactivar
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models.pg.usuario   import Usuario
from app.models.pg.rol        import Rol
from app.models.pg.gimnasio   import Gimnasio
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_trainers_bp = Blueprint("owner_trainers", __name__)

_STAFF_ROLES = ("Entrenador", "Recepcionista")


def _staff_filter(gym_id):
    """Roles válidos de staff para un gimnasio."""
    roles = Rol.query.filter(Rol.nombre.in_(_STAFF_ROLES)).all()
    role_ids = [r.id for r in roles]
    return role_ids


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/staff
# ─────────────────────────────────────────────────────────────────────────────
@owner_trainers_bp.route("/staff", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def listar_staff():
    """Lista entrenadores y recepcionistas del gimnasio con filtros opcionales."""
    gym_id = g.tenant_id
    rol_filter  = request.args.get("rol")          # "Entrenador" | "Recepcionista"
    solo_activos = request.args.get("activos", "true").lower() == "true"
    search       = request.args.get("q", "").strip()

    role_ids = _staff_filter(gym_id)
    if not role_ids:
        return jsonify([]), 200

    q = Usuario.query.filter(
        Usuario.id_gimnasio == gym_id,
        Usuario.id_rol.in_(role_ids),
    )
    if solo_activos:
        q = q.filter_by(activo=True)
    if rol_filter:
        rol_obj = Rol.query.filter_by(nombre=rol_filter).first()
        if rol_obj:
            q = q.filter_by(id_rol=rol_obj.id)
    if search:
        q = q.filter(
            db.or_(
                Usuario.nombre.ilike(f"%{search}%"),
                Usuario.email.ilike(f"%{search}%"),
            )
        )

    usuarios = q.order_by(Usuario.nombre).all()
    return jsonify([u.to_dict() for u in usuarios]), 200


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/staff/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_trainers_bp.route("/staff/<int:user_id>", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def get_staff_member(user_id: int):
    gym_id   = g.tenant_id
    role_ids = _staff_filter(gym_id)
    usuario  = Usuario.query.filter_by(id=user_id, id_gimnasio=gym_id).first()
    if not usuario or usuario.id_rol not in role_ids:
        return jsonify({"msg": "Usuario no encontrado"}), 404
    return jsonify(usuario.to_dict()), 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/owner_gym/staff
# ─────────────────────────────────────────────────────────────────────────────
@owner_trainers_bp.route("/staff", methods=["POST"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def crear_staff():
    """
    Crea un nuevo usuario staff (entrenador o recepcionista) en el gimnasio.
    Body JSON:
        {
            "nombre":   "Juan Pérez",
            "email":    "juan@gym.com",
            "password": "segura123",
            "rol":      "Entrenador"   // o "Recepcionista"
        }
    """
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    nombre   = (data.get("nombre")   or "").strip()
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()
    rol_name = (data.get("rol")      or "").strip()

    if not all([nombre, email, password, rol_name]):
        return jsonify({"msg": "nombre, email, password y rol son requeridos"}), 400
    if rol_name not in _STAFF_ROLES:
        return jsonify({"msg": f"rol debe ser uno de: {', '.join(_STAFF_ROLES)}"}), 400

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
    )
    nuevo.set_password(password)
    db.session.add(nuevo)
    db.session.commit()

    return jsonify({"msg": "Usuario creado", **nuevo.to_dict()}), 201


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/owner_gym/staff/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_trainers_bp.route("/staff/<int:user_id>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
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

    if rol_name:
        if rol_name not in _STAFF_ROLES:
            return jsonify({"msg": f"rol debe ser uno de: {', '.join(_STAFF_ROLES)}"}), 400
        rol_obj = Rol.query.filter_by(nombre=rol_name).first()
        if not rol_obj:
            return jsonify({"msg": f"Rol '{rol_name}' no existe en la base de datos"}), 500
        usuario.id_rol = rol_obj.id

    db.session.commit()
    return jsonify({"msg": "Usuario actualizado", **usuario.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/owner_gym/staff/<id>/toggle
# ─────────────────────────────────────────────────────────────────────────────
@owner_trainers_bp.route("/staff/<int:user_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym")
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
