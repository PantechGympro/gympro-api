"""
superadmin/usuarios.py — Gestión de usuarios de la plataforma e impersonación.

Endpoints:
    GET  /api/superadmin/usuarios               todos los usuarios con filtros
    GET  /api/superadmin/usuarios/<id>          detalle de usuario
    POST /api/superadmin/usuarios/<id>/impersonate  genera token temporal como ese usuario
    PATCH /api/superadmin/usuarios/<id>/toggle  activar / desactivar usuario
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, create_access_token, get_jwt_identity
from datetime import timedelta

from app.extensions import db
from app.models.pg.usuario import Usuario
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.rol import Rol
from app.utils.security import require_role

usuarios_admin_bp = Blueprint("usuarios_admin", __name__)

# Duración máxima del token de impersonación — corto por seguridad
_IMPERSONATE_TTL = timedelta(hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@usuarios_admin_bp.route("/usuarios", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_usuarios():
    """
    Lista todos los usuarios de la plataforma con filtros y paginación.

    Query params:
        gym_id   int  — filtrar por gimnasio
        rol      str  — filtrar por nombre de rol
        activo   bool
        q        str  — buscar por nombre o email (ILIKE)
        page     int
        per_page int
    """
    gym_id_param = request.args.get("gym_id", type=int)
    rol_param    = request.args.get("rol")
    activo_param = request.args.get("activo")
    q_param      = request.args.get("q", "").strip()
    page         = max(1, int(request.args.get("page", 1)))
    per_page     = min(100, int(request.args.get("per_page", 25)))

    query = Usuario.query.join(Rol, isouter=True).join(Gimnasio, isouter=True)

    if gym_id_param is not None:
        query = query.filter(Usuario.id_gimnasio == gym_id_param)
    if rol_param:
        query = query.filter(Rol.nombre == rol_param)
    if activo_param is not None:
        query = query.filter(Usuario.activo == (activo_param.lower() == "true"))
    if q_param:
        like = f"%{q_param}%"
        query = query.filter(
            db.or_(
                Usuario.nombre.ilike(like),
                Usuario.email.ilike(like),
            )
        )

    query    = query.order_by(Usuario.created_at.desc())
    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    items = []
    for u in paginado.items:
        items.append({
            "id":          u.id,
            "nombre":      u.nombre,
            "email":       u.email,
            "activo":      u.activo,
            "rol":         u.rol.nombre if u.rol else None,
            "id_gimnasio": u.id_gimnasio,
            "gimnasio":    u.gimnasio.nombre if u.gimnasio else None,
            "created_at":  u.created_at.isoformat() if u.created_at else None,
        })

    return jsonify({
        "usuarios": items,
        "total":    paginado.total,
        "page":     page,
        "pages":    paginado.pages,
        "per_page": per_page,
    }), 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def detalle_usuario(user_id: int):
    """Devuelve perfil completo de un usuario."""
    u = Usuario.query.get_or_404(user_id)
    return jsonify({
        "id":          u.id,
        "nombre":      u.nombre,
        "email":       u.email,
        "activo":      u.activo,
        "rol":         u.rol.nombre if u.rol else None,
        "id_gimnasio": u.id_gimnasio,
        "gimnasio":    u.gimnasio.nombre if u.gimnasio else None,
        "plan":        u.gimnasio.plan if u.gimnasio else None,
        "created_at":  u.created_at.isoformat() if u.created_at else None,
    }), 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>/impersonate", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def impersonate(user_id: int):
    """
    Genera un JWT de corta duración (1 h) con la identidad del usuario indicado.
    Permite al superadmin diagnosticar problemas desde la perspectiva de ese usuario
    sin conocer su contraseña.

    El token generado incluye el claim 'impersonated_by' con el ID del superadmin
    para auditoría.

    Response:
        { "access_token": "...", "user": {...}, "expira_en": "1h" }
    """
    target = Usuario.query.get_or_404(user_id)
    if not target.activo:
        return jsonify({"msg": "No se puede impersonar un usuario inactivo."}), 403

    superadmin_id = get_jwt_identity()
    rol_nombre    = target.rol.nombre if target.rol else "Desconocido"

    _plan = target.gimnasio.plan if target.gimnasio else "basico"
    plan  = _plan.value if hasattr(_plan, "value") else str(_plan)

    claims = {
        "email":           target.email,
        "role":            rol_nombre,
        "id_gimnasio":     target.id_gimnasio,
        "plan":            plan,
        "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
        "perfil_completo": True,
        "peso_inicial":    None,
        "fuente":          "pg",
        "impersonated_by": superadmin_id,   # auditoría
    }

    token = create_access_token(
        identity=str(target.id),
        additional_claims=claims,
        expires_delta=_IMPERSONATE_TTL,
    )

    return jsonify({
        "access_token": token,
        "expira_en":    "1h",
        "user": {
            "id":          target.id,
            "nombre":      target.nombre,
            "email":       target.email,
            "role":        rol_nombre,
            "id_gimnasio": target.id_gimnasio,
            "gimnasio":    target.gimnasio.nombre if target.gimnasio else None,
            "plan":        plan,
        },
    }), 200


@usuarios_admin_bp.route("/usuarios/<int:user_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("superadmin")
def toggle_usuario(user_id: int):
    """
    Activa o desactiva un usuario individualmente.
    No permite desactivarse a sí mismo.
    """
    superadmin_id = int(get_jwt_identity())
    if user_id == superadmin_id:
        return jsonify({"msg": "No puedes desactivar tu propia cuenta."}), 403

    u = Usuario.query.get_or_404(user_id)
    u.activo = not u.activo
    db.session.commit()

    accion = "activado" if u.activo else "desactivado"
    return jsonify({
        "msg":    f"Usuario {accion}.",
        "id":     u.id,
        "email":  u.email,
        "activo": u.activo,
    }), 200
