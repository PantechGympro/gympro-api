"""
auth/routes.py — Autenticación y registro de usuarios.

Sprint 2: modo dual de autenticación.
  1. Login busca primero en PostgreSQL (nuevos usuarios).
  2. Si no encuentra, hace fallback a MongoDB (usuarios legacy).
  El JWT ahora incluye id_gimnasio como claim multi-tenant.

Sprint 3: una vez migrados todos los usuarios a PG, eliminar el bloque
  "_login_mongo_fallback" y el import de modelos Mongo.
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from app.extensions import db, limiter
from app.models.pg.usuario import Usuario as UsuarioPG
from app.models.pg.rol     import Rol     as RolPG

# ── Modelos Mongo (fallback legacy — se eliminarán en Sprint 3) ───────────────
from app.mongo import get_db
from app.models.user import User as UserMongo
from app.models.role import Role as RoleMongo
from app.models.miembro import Miembro
from app.models.miembro_membresia import MiembroMembresia
from bson.objectid import ObjectId

auth_bp = Blueprint("auth", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_token_pg(usuario: UsuarioPG) -> dict:
    """Construye el payload JWT para un usuario de PostgreSQL."""
    rol_nombre = usuario.rol.nombre if usuario.rol else "Desconocido"

    # superadmin no tiene gimnasio — se le asigna plan "enterprise" de plataforma
    if rol_nombre == "superadmin":
        return {
            "identity": str(usuario.id),
            "claims": {
                "email":        usuario.email,
                "role":         "superadmin",
                "id_gimnasio":  None,
                "plan":         "enterprise",
                "access_level": "premium",
                "perfil_completo": True,
                "peso_inicial": None,
                "fuente":       "pg",
            },
            "user_response": {
                "id":           str(usuario.id),
                "nombre":       usuario.nombre,
                "email":        usuario.email,
                "role":         "superadmin",
                "id_gimnasio":  None,
                "plan":         "enterprise",
                "access_level": "premium",
                "perfil_completo": True,
            },
        }

    # plan es str con PGEnum(create_type=False); con db.Enum(PlanEnum) sería .value
    _plan = usuario.gimnasio.plan if usuario.gimnasio else "basico"
    plan  = _plan.value if hasattr(_plan, "value") else str(_plan)

    primer_login = getattr(usuario, "primer_login", False)

    return {
        "identity": str(usuario.id),
        "claims": {
            "email":           usuario.email,
            "role":            rol_nombre,
            "id_gimnasio":     usuario.id_gimnasio,
            "plan":            plan,
            "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
            "perfil_completo": True,
            "peso_inicial":    None,
            "primer_login":    primer_login,
            "fuente":          "pg",
        },
        "user_response": {
            "id":              str(usuario.id),
            "nombre":          usuario.nombre,
            "email":           usuario.email,
            "role":            rol_nombre,
            "id_gimnasio":     usuario.id_gimnasio,
            "plan":            plan,
            "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
            "perfil_completo": True,
            "primer_login":    primer_login,
        },
    }


def _build_token_mongo(user, nombre_rol: str) -> dict:
    """
    Construye el payload JWT para un usuario legacy de MongoDB.
    DEPRECADO — se elimina en Sprint 3.
    """
    mongo_db          = get_db()
    nombre_membresia  = "Sin Plan"
    access_level      = "premium"
    perfil_completo   = True
    peso_inicial      = None

    if nombre_rol == "Miembro":
        access_level   = "basico"
        miembro_data   = mongo_db.miembros.find_one({"id_usuario": user._id})
        if miembro_data:
            miembro       = Miembro(**miembro_data)
            peso_inicial  = float(miembro.peso_inicial) if miembro.peso_inicial else None
            perfil_completo = peso_inicial is not None

            mm_activa = list(
                mongo_db.miembro_membresia.find(
                    {"id_miembro": miembro._id, "estado": "Activa"}
                ).sort("fecha_fin", -1).limit(1)
            )
            if mm_activa:
                id_mem = mm_activa[0].get("id_membresia")
                _nombre = None
                # PG path (integer id — Sprint 3+)
                try:
                    from app.models.pg.tipo_membresia import TipoMembresia
                    tm = TipoMembresia.query.get(int(id_mem))
                    if tm:
                        _nombre = tm.nombre
                except (TypeError, ValueError, Exception):
                    pass
                # Legacy Mongo path
                if _nombre is None:
                    mem_info = mongo_db.membresias.find_one({"_id": id_mem})
                    if mem_info:
                        _nombre = mem_info.get("nombre", "Sin Plan")
                if _nombre:
                    nombre_membresia = _nombre
                    if any(p in nombre_membresia for p in ["Premium", "VIP"]):
                        access_level = "premium"

    return {
        "identity": str(user._id),
        "claims": {
            "email":           user.email,
            "role":            nombre_rol,
            "id_gimnasio":     None,   # legacy: sin gimnasio asignado
            "plan":            nombre_membresia,
            "access_level":    access_level,
            "perfil_completo": perfil_completo,
            "peso_inicial":    peso_inicial,
            "fuente":          "mongo",  # quitar en Sprint 3
        },
        "user_response": {
            "id":              str(user._id),
            "nombre":          user.nombre,
            "email":           user.email,
            "role":            nombre_rol,
            "id_gimnasio":     None,
            "membership_plan": nombre_membresia,
            "access_level":    access_level,
            "perfil_completo": perfil_completo,
            "peso_inicial":    peso_inicial,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute; 30 per hour")
def login():
    data = request.get_json(silent=True)
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"msg": "Datos incompletos"}), 400

    email    = data["email"].strip().lower()
    password = data["password"]

    # ── 1. Buscar en PostgreSQL (usuarios nuevos) ─────────────────────────────
    usuario_pg = UsuarioPG.query.filter_by(email=email).first()
    if usuario_pg:
        if not usuario_pg.check_password(password):
            return jsonify({"msg": "Credenciales inválidas"}), 401
        if not usuario_pg.activo:
            return jsonify({"msg": "Usuario inactivo"}), 403
        if usuario_pg.gimnasio and not usuario_pg.gimnasio.activo:
            return jsonify({"msg": "El gimnasio no está activo. Contacta al administrador."}), 403

        payload = _build_token_pg(usuario_pg)
        token   = create_access_token(
            identity=payload["identity"],
            additional_claims=payload["claims"],
        )
        return jsonify({"access_token": token, "user": payload["user_response"]}), 200

    # ── 2. Fallback MongoDB (usuarios legacy — Sprint 2 transitorio) ──────────
    user_mongo = UserMongo.find_by_email(email)
    if user_mongo:
        if not user_mongo.check_password(password):
            return jsonify({"msg": "Credenciales inválidas"}), 401
        if not user_mongo.activo:
            return jsonify({"msg": "Usuario inactivo"}), 403

        role       = RoleMongo.find_by_id(user_mongo.id_role)
        nombre_rol = role.nombre if role else "Desconocido"

        payload = _build_token_mongo(user_mongo, nombre_rol)
        token   = create_access_token(
            identity=payload["identity"],
            additional_claims=payload["claims"],
        )
        return jsonify({"access_token": token, "user": payload["user_response"]}), 200

    # ── Usuario no encontrado en ninguna fuente ───────────────────────────────
    return jsonify({"msg": "Credenciales inválidas"}), 401


# ─────────────────────────────────────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per minute; 10 per hour")
def register():
    """
    Registro de nuevos usuarios — escribe directamente en PostgreSQL.

    Body JSON esperado:
        {
            "nombre":      "Juan Pérez",
            "email":       "juan@example.com",
            "password":    "Segura1234!",
            "id_gimnasio": 1          (opcional — si se omite, queda sin gimnasio asignado)
        }
    """
    data  = request.get_json()
    email = (data.get("email") or "").strip().lower()

    if not email or not data.get("password") or not data.get("nombre"):
        return jsonify({"msg": "nombre, email y password son requeridos"}), 400

    # Verificar duplicado en PG
    if UsuarioPG.query.filter_by(email=email).first():
        return jsonify({"msg": "El correo ya está registrado"}), 400

    # Verificar duplicado en Mongo (compatibilidad durante migración)
    if UserMongo.find_by_email(email):
        return jsonify({"msg": "El correo ya está registrado"}), 400

    # Buscar rol Miembro en PG
    rol_miembro = RolPG.query.filter_by(nombre="Miembro").first()
    if not rol_miembro:
        return jsonify({"msg": "Error interno: rol 'Miembro' no encontrado en la base de datos"}), 500

    try:
        nuevo = UsuarioPG(
            nombre=data["nombre"],
            email=email,
            id_rol=rol_miembro.id,
            id_gimnasio=data.get("id_gimnasio"),  # None si no se especifica
            activo=True,
        )
        nuevo.set_password(data["password"])
        db.session.add(nuevo)
        db.session.commit()

        return jsonify({"msg": "Usuario registrado exitosamente", "id": nuevo.id}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": str(e)}), 500
