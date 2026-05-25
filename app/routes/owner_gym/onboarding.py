"""
routes/onboarding.py -- Registro de nuevo gimnasio en la plataforma (Sprint 3 / US16).

Flujo completo en un solo endpoint:
  1. Valida datos del gimnasio y del administrador.
  2. Crea Gimnasio en PG.
  3. Crea Usuario administrador vinculado al gimnasio.
  4. Crea Suscripcion en estado trialing (14 dias) con plan basico por defecto.
  5. Genera factura pendiente por el primer periodo.
  6. Envia email de bienvenida al admin.
  7. Devuelve JWT listo para usar (el admin queda logueado).

Ruta: POST /api/onboarding/register-gym
      (exenta de autenticacion JWT -- es la puerta de entrada de nuevos clientes)
"""
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt, get_jwt_identity
from flask_mail import Message

from app.extensions import db, mail, limiter
from app.models.pg.rol                 import Rol
from app.models.pg.gimnasio            import Gimnasio
from app.models.pg.usuario             import Usuario
from app.models.pg.plan_suscripcion    import PlanSuscripcion
from app.models.pg.suscripcion         import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.utils.gym_types               import GYM_TYPES, get_gym_type_config, seed_default_memberships

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/api/onboarding")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _enviar_bienvenida(email: str, nombre_gym: str, nombre_admin: str) -> None:
    """Envia email de bienvenida. Falla silenciosamente si Mail no esta configurado."""
    try:
        msg = Message(
            subject  = f"Bienvenido a GymPro SaaS — {nombre_gym}",
            recipients = [email],
            html     = f"""
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;padding:32px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0">GYM<span style="color:#6c63ff">PRO</span></h1>
    <p style="color:#aaa;margin-top:8px">Plataforma de gestion de gimnasios</p>
  </div>
  <div style="background:#f9f9f9;padding:32px;border-radius:0 0 12px 12px">
    <h2>Bienvenido, {nombre_admin}!</h2>
    <p>Tu gimnasio <strong>{nombre_gym}</strong> ha sido registrado exitosamente en GymPro SaaS.</p>
    <p>Tu cuenta esta en <strong>periodo de prueba de 14 dias</strong> con el plan Basico.
       Durante este tiempo podras explorar todas las funciones sin costo alguno.</p>
    <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:20px;margin:24px 0">
      <h3 style="margin-top:0">Proximos pasos</h3>
      <ol style="line-height:2">
        <li>Inicia sesion con tu correo y la contrasena que elegiste</li>
        <li>Completa el perfil de tu gimnasio en el panel de administracion</li>
        <li>Agrega a tu equipo (entrenadores, recepcionistas)</li>
        <li>Registra tus primeros miembros</li>
      </ol>
    </div>
    <p style="color:#888;font-size:13px">
      Si tienes dudas escribe a soporte@gympro.mx<br>
      Equipo GymPro SaaS
    </p>
  </div>
</body></html>
""",
        )
        mail.send(msg)
        logger.info("Email de bienvenida enviado a %s", email)
    except Exception as exc:
        logger.warning("No se pudo enviar email de bienvenida: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────

@onboarding_bp.route("/gym-types", methods=["GET"])
def list_gym_types():
    """Devuelve el catálogo de tipos de gimnasio para poblar el selector en el onboarding."""
    tipos = [
        {
            "id":          key,
            "label":       cfg["label"],
            "description": cfg["description"],
            "icon":        cfg["icon"],
        }
        for key, cfg in GYM_TYPES.items()
    ]
    return jsonify({"tipos": tipos}), 200


@onboarding_bp.route("/register-gym", methods=["POST"])
@limiter.limit("3 per hour; 10 per day")
def register_gym():
    """
    Registra un nuevo gimnasio y su administrador en un solo request.

    Body JSON:
    {
      "gym": {
        "nombre":         "FitZone Monterrey",
        "email_contacto": "contacto@fitzone.mx",
        "telefono":       "+52-81-1234-5678"
      },
      "admin": {
        "nombre":   "Juan Perez",
        "email":    "juan@fitzone.mx",
        "password": "Segura1234!"
      },
      "id_plan": 1   (opcional -- default: plan basico)
    }

    Response 201:
    {
      "msg":          "Gimnasio registrado exitosamente",
      "access_token": "eyJ...",
      "gym":          { ...to_dict() },
      "admin":        { id, nombre, email, role },
      "suscripcion":  { ...to_dict() }
    }
    """
    data = request.get_json() or {}

    # Validar estructura
    gym_data   = data.get("gym",   {})
    admin_data = data.get("admin", {})

    gym_nombre = (gym_data.get("nombre") or "").strip()
    gym_email  = (gym_data.get("email_contacto") or "").strip().lower()
    gym_tipo   = (gym_data.get("tipo_gimnasio") or "gimnasio_tradicional").strip()
    adm_nombre = (admin_data.get("nombre") or "").strip()
    adm_email  = (admin_data.get("email") or "").strip().lower()
    adm_pass   = admin_data.get("password", "")

    # Normalizar tipo: si viene uno no reconocido, default a tradicional
    if gym_tipo not in GYM_TYPES:
        gym_tipo = "gimnasio_tradicional"
    tipo_config = get_gym_type_config(gym_tipo)

    errores = []
    if not gym_nombre:          errores.append("gym.nombre es requerido")
    if not gym_email:           errores.append("gym.email_contacto es requerido")
    if not adm_nombre:          errores.append("admin.nombre es requerido")
    if not adm_email:           errores.append("admin.email es requerido")
    if len(adm_pass) < 8:       errores.append("admin.password debe tener al menos 8 caracteres")
    if errores:
        return jsonify({"msg": "Datos incompletos", "errores": errores}), 400

    # Verificar duplicados
    if Usuario.query.filter_by(email=adm_email).first():
        return jsonify({"msg": "El correo del administrador ya esta registrado"}), 409

    # Obtener rol owner_gym
    rol_admin = Rol.query.filter_by(nombre="owner_gym").first()
    if not rol_admin:
        return jsonify({"msg": "Error interno: rol owner_gym no encontrado"}), 500

    # Obtener plan (default: basico)
    id_plan = data.get("id_plan")
    if id_plan:
        plan = PlanSuscripcion.query.filter_by(id=id_plan, activo=True).first()
        if not plan:
            return jsonify({"msg": "Plan no encontrado"}), 404
    else:
        plan = PlanSuscripcion.query.filter_by(nombre="basico", activo=True).first()
        if not plan:
            return jsonify({"msg": "Plan basico no configurado en la plataforma"}), 500

    try:
        ahora = datetime.now(timezone.utc)

        # 1. Crear Gimnasio
        nuevo_gym = Gimnasio(
            nombre         = gym_nombre,
            plan           = plan.nombre,
            activo         = True,
            email_contacto = gym_email,
            telefono       = gym_data.get("telefono", ""),
            tipo_gimnasio  = gym_tipo,
            configuracion  = {
                "etiqueta_sesion": tipo_config["etiqueta_sesion"],
                "modulos":         tipo_config["modulos"],
                "label":           tipo_config["label"],
            },
        )
        db.session.add(nuevo_gym)
        db.session.flush()  # obtener nuevo_gym.id

        # 2. Crear Usuario administrador (primer_login=True para forzar onboarding)
        nuevo_admin = Usuario(
            nombre       = adm_nombre,
            email        = adm_email,
            id_rol       = rol_admin.id,
            id_gimnasio  = nuevo_gym.id,
            activo       = True,
            primer_login = True,
        )
        nuevo_admin.set_password(adm_pass)
        db.session.add(nuevo_admin)
        db.session.flush()

        # 3. Crear Suscripcion trialing
        suscripcion = Suscripcion(
            id_gimnasio         = nuevo_gym.id,
            id_plan             = plan.id,
            estado              = "trialing",
            fecha_inicio        = ahora,
            fecha_proximo_cobro = ahora + timedelta(days=14),
        )
        db.session.add(suscripcion)
        db.session.flush()

        # 3b. Sembrar TipoMembresia por defecto según tipo de gimnasio
        seed_default_memberships(db.session, nuevo_gym.id, gym_tipo)

        # 4. Factura inicial pendiente
        factura = FacturaSuscripcion(
            id_suscripcion    = suscripcion.id,
            monto             = plan.precio_mensual_mxn,
            moneda            = "MXN",
            estado            = "pendiente",
            fecha_emision     = ahora,
            fecha_vencimiento = ahora + timedelta(days=14),
        )
        db.session.add(factura)
        db.session.commit()

        # 5. Email de bienvenida (no bloquea si falla)
        _enviar_bienvenida(adm_email, gym_nombre, adm_nombre)

        # 6. Generar JWT para login inmediato (primer_login=True → fuerza onboarding)
        _plan_val = nuevo_gym.plan if isinstance(nuevo_gym.plan, str) else nuevo_gym.plan.value
        token = create_access_token(
            identity = str(nuevo_admin.id),
            additional_claims = {
                "email":           adm_email,
                "role":            "owner_gym",
                "id_gimnasio":     nuevo_gym.id,
                "plan":            _plan_val,
                "access_level":    "basico",
                "perfil_completo": True,
                "primer_login":    True,
                "fuente":          "pg",
            },
        )

        return jsonify({
            "msg":          "Gimnasio registrado exitosamente",
            "access_token": token,
            "gym":          nuevo_gym.to_dict(),
            "admin": {
                "id":          nuevo_admin.id,
                "nombre":      nuevo_admin.nombre,
                "email":       nuevo_admin.email,
                "role":        "owner_gym",
                "primer_login": True,
            },
            "suscripcion": suscripcion.to_dict(),
            "dias_prueba": 14,
        }), 201

    except Exception as exc:
        db.session.rollback()
        logger.exception("Error en onboarding de gimnasio")
        return jsonify({"msg": "Error interno al registrar el gimnasio", "detalle": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE SETUP — primer login del dueño
# ─────────────────────────────────────────────────────────────────────────────

@onboarding_bp.route("/complete-setup", methods=["PUT"])
@jwt_required()
def complete_setup():
    """
    Completa el onboarding del dueño en su primer inicio de sesión.

    Body JSON:
    {
      "nueva_password": "NuevaSegura1234!",   (requerido)
      "gym": {
        "tipo_gimnasio":    "crossfit_functional",
        "descripcion":      "El mejor gym de la ciudad",
        "direccion":        "Av. Principal 123, Monterrey",
        "horario_apertura": "06:00",
        "horario_cierre":   "22:00",
        "capacidad_maxima": 80,
        "redes": {
          "instagram": "@migym",
          "facebook":  "fb.com/migym",
          "website":   "https://migym.mx"
        }
      }
    }
    """
    claims      = get_jwt()
    user_id     = get_jwt_identity()
    id_gimnasio = claims.get("id_gimnasio")

    if claims.get("role") not in ("owner_gym", "admin", "Administrador"):
        return jsonify({"msg": "Acceso denegado"}), 403

    data          = request.get_json() or {}
    nueva_pass    = (data.get("nueva_password") or "").strip()
    gym_info      = data.get("gym", {})

    # nueva_password es opcional: el admin pudo haber establecido su contraseña
    # directamente en el formulario de registro del gimnasio.
    if nueva_pass and len(nueva_pass) < 8:
        return jsonify({"msg": "La contraseña debe tener al menos 8 caracteres"}), 400

    usuario = Usuario.query.get(int(user_id))
    if not usuario:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    gym = Gimnasio.query.get(id_gimnasio) if id_gimnasio else None

    try:
        # 1. Cambiar contraseña (solo si se envió)
        if nueva_pass:
            usuario.set_password(nueva_pass)
        usuario.primer_login = False

        # 2. Actualizar configuración del gimnasio
        if gym and gym_info:
            # Tipo de gimnasio (y reconfigurar módulos si cambió)
            nuevo_tipo = (gym_info.get("tipo_gimnasio") or "").strip()
            if nuevo_tipo and nuevo_tipo in GYM_TYPES:
                cfg_tipo = get_gym_type_config(nuevo_tipo)
                gym.tipo_gimnasio = nuevo_tipo
                config_base = {
                    "etiqueta_sesion": cfg_tipo["etiqueta_sesion"],
                    "modulos":         cfg_tipo["modulos"],
                    "label":           cfg_tipo["label"],
                }
            else:
                config_base = dict(gym.configuracion or {})

            # Mezclar campos de preferencias en configuracion JSON
            config_base.update({
                "descripcion":      gym_info.get("descripcion", config_base.get("descripcion", "")),
                "direccion":        gym_info.get("direccion",   config_base.get("direccion", "")),
                "horario_apertura": gym_info.get("horario_apertura", config_base.get("horario_apertura", "")),
                "horario_cierre":   gym_info.get("horario_cierre",   config_base.get("horario_cierre", "")),
                "capacidad_maxima": gym_info.get("capacidad_maxima", config_base.get("capacidad_maxima", 0)),
                "redes":            gym_info.get("redes",       config_base.get("redes", {})),
            })
            gym.configuracion = config_base

        db.session.commit()

        # 3. Emitir nuevo JWT con primer_login=False
        _plan = gym.plan if gym else "basico"
        plan  = _plan.value if hasattr(_plan, "value") else str(_plan)
        nuevo_token = create_access_token(
            identity = str(usuario.id),
            additional_claims = {
                "email":           usuario.email,
                "role":            claims.get("role"),
                "id_gimnasio":     id_gimnasio,
                "plan":            plan,
                "access_level":    "premium" if plan in ("pro", "enterprise") else "basico",
                "perfil_completo": True,
                "primer_login":    False,
                "fuente":          "pg",
            },
        )

        return jsonify({
            "msg":          "Configuración completada",
            "access_token": nuevo_token,
            "gym":          gym.to_dict() if gym else None,
        }), 200

    except Exception as exc:
        db.session.rollback()
        logger.exception("Error en complete-setup")
        return jsonify({"msg": "Error interno", "detalle": str(exc)}), 500
