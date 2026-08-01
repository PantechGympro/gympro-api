"""
auth/routes.py — Autenticación y registro de usuarios.

Sprint 2: modo dual de autenticación.
  1. Login busca primero en PostgreSQL (nuevos usuarios).
  2. Si no encuentra, hace fallback a MongoDB (usuarios legacy).
  El JWT ahora incluye id_gimnasio como claim multi-tenant.

Sprint 3: una vez migrados todos los usuarios a PG, eliminar el bloque
  "_login_mongo_fallback" y el import de modelos Mongo.
"""
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import (
    create_access_token, create_refresh_token, jwt_required, get_jwt_identity,
)
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
from app.extensions import db, limiter, mail
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

    # Para miembros PG, resolver peso_inicial y perfil_completo desde el doc Mongo
    # (el perfil físico sigue viviendo en MongoDB — colección miembros)
    peso_inicial    = None
    perfil_completo = True
    miembro_doc = None
    if rol_nombre in ("Miembro", "user"):
        try:
            mdb         = get_db()
            miembro_doc = mdb.miembros.find_one({"id_usuario_pg": usuario.id})
            if miembro_doc:
                pi           = miembro_doc.get("peso_inicial")
                peso_inicial = float(pi) if pi else None
            perfil_completo = peso_inicial is not None
        except Exception:
            pass  # si Mongo no está disponible no bloquear el login

    # Foto de perfil: del doc del miembro o del Usuario (staff/owner). Solo base64.
    foto_login = getattr(usuario, "foto_perfil", None)
    if miembro_doc and miembro_doc.get("foto_perfil"):
        foto_login = miembro_doc.get("foto_perfil")
    foto_login = foto_login if (isinstance(foto_login, str) and foto_login.startswith("data:image")) else None

    return {
        "identity": str(usuario.id),
        "claims": {
            "email":           usuario.email,
            "role":            rol_nombre,
            "id_gimnasio":     usuario.id_gimnasio,
            "plan":            plan,
            "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
            "perfil_completo": perfil_completo,
            "peso_inicial":    peso_inicial,
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
            "perfil_completo": perfil_completo,
            "peso_inicial":    peso_inicial,
            "primer_login":    primer_login,
            "foto_perfil":     foto_login,
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
        # Token de refresco de larga duración: permite que la sesión sobreviva
        # al cierre de la aplicación sin tener que alargar el access token, que
        # es el que viaja en cada petición y por eso conviene que caduque pronto.
        refresh = create_refresh_token(
            identity=payload["identity"],
            additional_claims=payload["claims"],
        )
        return jsonify({
            "access_token":  token,
            "refresh_token": refresh,
            "user":          payload["user_response"],
        }), 200

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
        refresh = create_refresh_token(
            identity=payload["identity"],
            additional_claims=payload["claims"],
        )
        return jsonify({
            "access_token":  token,
            "refresh_token": refresh,
            "user":          payload["user_response"],
        }), 200

    # ── Usuario no encontrado en ninguna fuente ───────────────────────────────
    return jsonify({"msg": "Credenciales inválidas"}), 401


# ─────────────────────────────────────────────────────────────────────────────
# REFRESCO DE SESIÓN
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh_token():
    """
    Entrega un access token nuevo a partir del token de refresco.

    Es lo que permite que la sesión se sienta permanente: la aplicación guarda
    el refresco y, cuando el access token caduca, pide uno nuevo en silencio en
    vez de mandar al usuario a la pantalla de acceso.

    Se vuelven a leer el rol y el gimnasio desde la base en lugar de copiarlos
    del token viejo: así, si al usuario le cambiaron el rol o lo dieron de baja,
    el cambio surte efecto en el siguiente refresco y no dentro de dos meses.
    """
    user_id = get_jwt_identity()

    usuario = UsuarioPG.query.get(int(user_id)) if str(user_id).isdigit() else None
    if usuario:
        if not usuario.activo:
            return jsonify({"msg": "Usuario inactivo"}), 403
        if usuario.gimnasio and not usuario.gimnasio.activo:
            return jsonify({"msg": "El gimnasio no está activo"}), 403

        payload = _build_token_pg(usuario)
        return jsonify({
            "access_token": create_access_token(
                identity=payload["identity"],
                additional_claims=payload["claims"],
            ),
            "user": payload["user_response"],
        }), 200

    # Usuarios legacy de Mongo: se conservan los claims del token de refresco.
    from flask_jwt_extended import get_jwt
    claims = {k: v for k, v in get_jwt().items()
              if k not in ("exp", "iat", "jti", "type", "sub", "nbf", "fresh")}
    return jsonify({
        "access_token": create_access_token(identity=user_id, additional_claims=claims),
    }), 200


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


# ─────────────────────────────────────────────────────────────────────────────
# RECUPERACIÓN DE CONTRASEÑA (código de 6 dígitos por correo)
# ─────────────────────────────────────────────────────────────────────────────

_RESET_TTL_MIN   = 15   # vigencia del código en minutos
_RESET_MAX_TRIES = 5    # intentos permitidos por código


def _send_reset_email(email: str, code: str) -> None:
    """Envía el código de restablecimiento por correo (flask-mail)."""
    sender = current_app.config.get("MAIL_DEFAULT_SENDER") or current_app.config.get("MAIL_USERNAME")
    body = (
        "Hola,\n\n"
        f"Tu código para restablecer la contraseña de GymPro es:\n\n    {code}\n\n"
        f"El código vence en {_RESET_TTL_MIN} minutos. Si no solicitaste este cambio, "
        "ignora este correo; tu contraseña seguirá igual.\n\n— Equipo GymPro"
    )
    msg = Message(
        subject="GymPro — Código para restablecer tu contraseña",
        sender=sender,
        recipients=[email],
        body=body,
    )
    mail.send(msg)


@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("3 per minute; 10 per hour")
def forgot_password():
    """
    Paso 1: el usuario pide un código. Genera un código de 6 dígitos, lo guarda
    hasheado con expiración y lo envía por correo. Responde SIEMPRE de forma
    genérica para no revelar si el correo existe (enumeration-safe).
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"msg": "El correo es requerido"}), 400

    existe = UsuarioPG.query.filter_by(email=email).first() or UserMongo.find_by_email(email)
    if existe:
        code = f"{random.randint(0, 999999):06d}"
        mdb  = get_db()
        mdb.password_resets.insert_one({
            "email":      email,
            "code_hash":  generate_password_hash(code),
            "expires_at": datetime.utcnow() + timedelta(minutes=_RESET_TTL_MIN),
            "used":       False,
            "attempts":   0,
            "created_at": datetime.utcnow(),
        })
        try:
            _send_reset_email(email, code)
        except Exception as e:
            current_app.logger.error(f"[forgot-password] fallo al enviar correo: {e}")

    return jsonify({
        "msg": "Si el correo está registrado, te enviamos un código para restablecer tu contraseña."
    }), 200


@auth_bp.route("/reset-password", methods=["POST"])
@limiter.limit("5 per minute; 20 per hour")
def reset_password():
    """
    Paso 2: valida el código y actualiza la contraseña (PG o Mongo legacy).
    Body: { email, code, new_password }.
    """
    data         = request.get_json(silent=True) or {}
    email        = (data.get("email") or "").strip().lower()
    code         = (data.get("code") or "").strip()
    new_password = data.get("new_password") or data.get("password") or ""

    if not email or not code or not new_password:
        return jsonify({"msg": "Correo, código y nueva contraseña son requeridos"}), 400
    if len(new_password) < 8:
        return jsonify({"msg": "La contraseña debe tener al menos 8 caracteres"}), 400

    mdb   = get_db()
    reset = mdb.password_resets.find_one({"email": email, "used": False}, sort=[("created_at", -1)])
    if not reset:
        return jsonify({"msg": "Código inválido o expirado. Solicita uno nuevo."}), 400
    if reset.get("expires_at") and reset["expires_at"] < datetime.utcnow():
        return jsonify({"msg": "El código expiró. Solicita uno nuevo."}), 400
    if reset.get("attempts", 0) >= _RESET_MAX_TRIES:
        return jsonify({"msg": "Demasiados intentos. Solicita un código nuevo."}), 429
    if not check_password_hash(reset["code_hash"], code):
        mdb.password_resets.update_one({"_id": reset["_id"]}, {"$inc": {"attempts": 1}})
        return jsonify({"msg": "Código incorrecto"}), 400

    # Código válido → invalidar y actualizar contraseña
    mdb.password_resets.update_one(
        {"_id": reset["_id"]},
        {"$set": {"used": True, "used_at": datetime.utcnow()}},
    )

    usuario_pg = UsuarioPG.query.filter_by(email=email).first()
    if usuario_pg:
        try:
            usuario_pg.set_password(new_password)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({"msg": f"No se pudo actualizar la contraseña: {e}"}), 500
        return jsonify({"msg": "Contraseña actualizada. Ya puedes iniciar sesión."}), 200

    user_mongo = UserMongo.find_by_email(email)
    if user_mongo:
        user_mongo.set_password(new_password)
        user_mongo.save()
        return jsonify({"msg": "Contraseña actualizada. Ya puedes iniciar sesión."}), 200

    return jsonify({"msg": "No se pudo actualizar la contraseña"}), 400
