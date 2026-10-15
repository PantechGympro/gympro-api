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
    get_jwt, set_access_cookies, set_refresh_cookies, unset_jwt_cookies,
)
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
import random
import uuid
from app.extensions import db, limiter, mail, redis_cliente
from app.models.pg.usuario import Usuario as UsuarioPG
from app.models.pg.rol     import Rol     as RolPG
from app.utils.alcance     import crear_asignacion, sucursales_de_administrador, usuario_tiene_alcance
from app.constants.roles  import ROL_ADMINISTRADOR, ROL_SUPERADMIN, normalizar_rol
from app.utils.capacidad  import permite_funcion

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
                # HU-41 (SCRUM-212): claims adicionales de alcance. superadmin
                # opera a nivel de plataforma -- sin organización ni sucursales.
                # Aditivos: nada lee todavía estos dos campos para decidir
                # permisos (eso es HU-42), así que no cambian ningún comportamiento.
                "id_organizacion": None,
                "sucursales":      [],
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

    # SCRUM-223: el plan que se muestra/gatea en el frontend es el de la
    # ORGANIZACIÓN (ver utils/capacidad.py), no el de Gimnasio.plan --
    # desde que el billing se sincroniza a Organizacion.plan, Gimnasio.plan
    # ya no se actualiza y quedaría mostrando un valor viejo tras cualquier
    # cambio de plan.
    # plan es str con PGEnum(create_type=False); con db.Enum(PlanEnum) sería .value
    if usuario.gimnasio and usuario.gimnasio.organizacion:
        _plan = usuario.gimnasio.organizacion.plan
    else:
        _plan = "basico"
    plan  = _plan.value if hasattr(_plan, "value") else str(_plan)

    primer_login = getattr(usuario, "primer_login", False)
    debe_cambiar_password = getattr(usuario, "debe_cambiar_password", False)  # SCRUM-224

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
            "debe_cambiar_password": debe_cambiar_password,  # SCRUM-224
            "fuente":          "pg",
            # HU-41 (SCRUM-212) + HU-45 (SCRUM-216): "sucursales" ya deja de
            # ser aditivo para un Administrador -- es la lista real de
            # sucursales que puede operar (ver sucursales_de_administrador(),
            # resuelta sobre AsignacionUsuario, no sobre id_gimnasio). Para
            # el resto de roles se mantiene igual que antes: una sola
            # sucursal de por vida, sin consulta extra.
            "id_organizacion": usuario.gimnasio.id_organizacion if usuario.gimnasio else None,
            "sucursales":      (
                sucursales_de_administrador(usuario.id)
                if normalizar_rol(rol_nombre) == ROL_ADMINISTRADOR
                else ([usuario.id_gimnasio] if usuario.id_gimnasio else [])
            ),
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
            "debe_cambiar_password": debe_cambiar_password,  # SCRUM-224
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
        #
        # "fam" (Actividad 09, PR-02 — J. C. Pérez Nava): identifica la cadena
        # de renovaciones. Cada refresco emite un refresh_token nuevo que
        # conserva la misma familia; si un token ya canjeado vuelve a
        # presentarse, se revoca la familia completa (ver /auth/refresh).
        familia = str(uuid.uuid4())
        refresh = create_refresh_token(
            identity=payload["identity"],
            additional_claims={**payload["claims"], "fam": familia},
        )
        respuesta = jsonify({
            "access_token":  token,
            "refresh_token": refresh,
            "user":          payload["user_response"],
        })
        # Cookies HttpOnly (Actividad 09, PR-01 — M. Arriaga Mora): el portal
        # web ya no necesita guardar el token en localStorage para mantener
        # la sesión -- ver web/src/api/cliente.js. El cuerpo JSON de arriba
        # se conserva para no romper al cliente móvil, que autentica por
        # cabecera Authorization y no por cookies.
        set_access_cookies(respuesta, token)
        set_refresh_cookies(respuesta, refresh)
        return respuesta, 200

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
        respuesta = jsonify({
            "access_token":  token,
            "refresh_token": refresh,
            "user":          payload["user_response"],
        })
        set_access_cookies(respuesta, token)
        set_refresh_cookies(respuesta, refresh)
        return respuesta, 200

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

    Actividad 09, PR-02 (J. C. Pérez Nava): el token de refresco se ROTA en
    cada renovación. Antes, un mismo token de refresco servía un número
    ilimitado de veces durante sus 90 días completos -- ni caducaba al
    usarse ni el servidor distinguía entre el dispositivo legítimo y una
    copia robada. Ahora cada canje invalida el token presentado, y si ese
    mismo token vuelve a presentarse (señal de que hay dos copias en
    circulación), se revoca la familia completa y se fuerza a iniciar
    sesión de nuevo.
    """
    claims_viejos = get_jwt()
    jti_viejo     = claims_viejos.get("jti")
    familia       = claims_viejos.get("fam") or jti_viejo
    user_id       = get_jwt_identity()

    if jti_viejo and redis_cliente.get(f"revocado:{jti_viejo}"):
        if familia:
            redis_cliente.setex(f"familia_revocada:{familia}", 90 * 86400, "1")
        return jsonify({"msg": "Sesión revocada por seguridad."}), 401

    restante = claims_viejos["exp"] - int(datetime.now(timezone.utc).timestamp())
    if restante > 0 and jti_viejo:
        redis_cliente.setex(f"revocado:{jti_viejo}", restante, "1")

    usuario = UsuarioPG.query.get(int(user_id)) if str(user_id).isdigit() else None
    if usuario:
        if not usuario.activo:
            return jsonify({"msg": "Usuario inactivo"}), 403
        if usuario.gimnasio and not usuario.gimnasio.activo:
            return jsonify({"msg": "El gimnasio no está activo"}), 403

        payload       = _build_token_pg(usuario)
        access_nuevo  = create_access_token(
            identity=payload["identity"], additional_claims=payload["claims"],
        )
        refresh_nuevo = create_refresh_token(
            identity=payload["identity"],
            additional_claims={**payload["claims"], "fam": familia},
        )
        respuesta = jsonify({
            "access_token":  access_nuevo,
            "refresh_token": refresh_nuevo,
            "user":          payload["user_response"],
        })
        set_access_cookies(respuesta, access_nuevo)
        set_refresh_cookies(respuesta, refresh_nuevo)
        return respuesta, 200

    # Usuarios legacy de Mongo: se conservan los claims del token de refresco.
    claims_legacy = {k: v for k, v in claims_viejos.items()
                      if k not in ("exp", "iat", "jti", "type", "sub", "nbf", "fresh", "fam")}
    access_nuevo  = create_access_token(identity=user_id, additional_claims=claims_legacy)
    refresh_nuevo = create_refresh_token(
        identity=user_id, additional_claims={**claims_legacy, "fam": familia},
    )
    respuesta = jsonify({
        "access_token":  access_nuevo,
        "refresh_token": refresh_nuevo,
    })
    set_access_cookies(respuesta, access_nuevo)
    set_refresh_cookies(respuesta, refresh_nuevo)
    return respuesta, 200


# ─────────────────────────────────────────────────────────────────────────────
# SESIÓN ACTUAL (rol verificado por el servidor)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/me", methods=["GET"])
@jwt_required()
# Límite propio, muy por encima del global (ver default_limits en
# extensions.py: 60/hora) -- Layout.jsx llama este endpoint en CADA cambio
# de ruta (useEffect con [location.pathname, role]) para re-verificar el rol
# en cada navegación, así que una sesión normal de uso (clicks por el
# sidebar, probar varias pantallas) agota el límite global en minutos y dejó
# a un usuario real fuera de su propia sesión ("no me deja entrar", 429 en
# /api/auth/me). No tiene sentido limitarlo como a /login: ya exige un JWT
# válido, así que no es superficie de fuerza bruta -- lo único que previene
# un límite aquí es un bucle descontrolado del cliente, no un ataque.
@limiter.limit("120 per minute")
def me():
    """
    Devuelve el rol y los datos mínimos de la sesión actual, leídos del JWT
    firmado por el servidor -- nunca de lo que el cliente diga tener
    guardado localmente.

    Actividad 09, PR-02 (M. Arriaga Mora): Layout.jsx decidía qué panel
    mostrar comparando contra el campo "role" del objeto "user" guardado en
    localStorage, que el propio usuario puede editar desde la consola del
    navegador. Este endpoint es la fuente de verdad que reemplaza esa
    lectura: la guarda de navegación en Layout.jsx ahora espera esta
    respuesta antes de montar cualquier panel.
    """
    claims = get_jwt()

    # HU-47/SCRUM-218: la pantalla de "verificar miembro de otra sucursal"
    # no debe aparecer (AC7) si la organización tiene una sola sucursal --
    # distinto de "sucursales" arriba (las que ESTE usuario tiene asignadas
    # como Administrador): aquí se pregunta si la ORGANIZACIÓN completa
    # tiene más de una, sin importar a cuántas tenga alcance quien pregunta.
    id_organizacion = claims.get("id_organizacion")
    multi_sucursal = False
    if id_organizacion is not None:
        from app.models.pg.gimnasio import Gimnasio
        multi_sucursal = (
            Gimnasio.query.filter_by(id_organizacion=id_organizacion).count() >= 2
        )

    # SCRUM-224: banderas de función del plan contratado, para que el
    # frontend pueda mostrar el aviso de "bloqueado por tu plan" de forma
    # PROACTIVA al montar la pantalla, en vez de solo después de que el
    # usuario intente la acción y el backend responda 402 -- ver
    # @require_funcion_plan() en utils/security.py, que hace este mismo
    # chequeo (y es la fuente de verdad real) en cada endpoint protegido;
    # esto es solo para que la UI se anticipe. Layout.jsx ya llama a este
    # endpoint en cada cambio de ruta, así que cualquier pantalla puede
    # leer esto sin una petición adicional.
    FUNCIONES_PLAN = (
        "analiticas_ia", "rutinas_ia", "reportes_pdf", "app_movil",
        "pos", "cobros_en_linea", "respaldos", "soporte_prioritario",
    )
    if normalizar_rol(claims.get("role", "")) == ROL_SUPERADMIN:
        # El superadmin no tiene plan propio (id_gimnasio es None salvo que
        # esté impersonando) y @require_funcion_plan() lo exenta siempre
        # en el backend -- el frontend debe ver exactamente lo mismo: nada
        # bloqueado, nunca el aviso de "mejora tu plan".
        funciones_plan = {f: True for f in FUNCIONES_PLAN}
    else:
        id_gimnasio_actual = claims.get("id_gimnasio")
        funciones_plan = {f: permite_funcion(id_gimnasio_actual, f) for f in FUNCIONES_PLAN}

    return jsonify({
        "role":            claims.get("role"),
        "id_gimnasio":     claims.get("id_gimnasio"),
        "email":           claims.get("email"),
        # HU-45: el selector de sucursal activa del frontend los necesita
        # para saber si debe mostrarse y con qué opciones -- ver
        # POST /auth/sucursal-activa más abajo.
        "id_organizacion": id_organizacion,
        "sucursales":      claims.get("sucursales", []),
        "organizacion_multi_sucursal": multi_sucursal,
        "funciones_plan":  funciones_plan,
        "debe_cambiar_password": claims.get("debe_cambiar_password", False),  # SCRUM-224
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# CAMBIO DE CONTRASEÑA OBLIGATORIO (SCRUM-224)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/cambiar-password-inicial", methods=["PUT"])
@jwt_required()
def cambiar_password_inicial():
    """
    Cambia la contraseña de la cuenta actual y apaga debe_cambiar_password.

    Lo usa la pasarela obligatoria que ve cualquier miembro/staff/
    administrador (no el Propietario -- ese pasa por
    PUT /api/onboarding/complete-setup, que ya hace lo mismo para su propio
    flujo) la primera vez que entra con una contraseña que no eligió él
    mismo: generada al crear su cuenta (ver utils/credenciales_acceso.py)
    o reseteada por un Propietario/Administrador (ver
    owner_gym/owner_trainers.py::actualizar_staff()).

    No pide la contraseña actual: igual que complete-setup, el JWT ya
    prueba que la sesión es legítima -- exigir la contraseña generada de
    vuelta no añade seguridad real aquí y sí fricción al único paso que
    existe precisamente para reemplazarla.

    Body: { "nueva_password": "..." }
    """
    user_id = get_jwt_identity()
    data    = request.get_json(silent=True) or {}
    nueva_password = (data.get("nueva_password") or data.get("password") or "").strip()

    if len(nueva_password) < 8:
        return jsonify({"msg": "La contraseña debe tener al menos 8 caracteres"}), 400

    try:
        usuario = UsuarioPG.query.get(int(user_id))
    except (TypeError, ValueError):
        usuario = None
    if not usuario:
        return jsonify({"msg": "Usuario no encontrado"}), 404

    try:
        usuario.set_password(nueva_password)
        usuario.debe_cambiar_password = False
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"No se pudo actualizar la contraseña: {e}"}), 500

    # Reemitir el JWT ya con debe_cambiar_password=False -- mismo patrón que
    # onboarding.py::complete-setup: sin esto, el frontend seguiría viendo
    # la bandera vieja (true) hasta el próximo login y la pasarela de
    # cambio de contraseña nunca se cerraría sola.
    payload = _build_token_pg(usuario)
    token   = create_access_token(identity=payload["identity"], additional_claims=payload["claims"])

    return jsonify({
        "msg":          "Contraseña actualizada",
        "access_token": token,
        "user":         payload["user_response"],
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# SUCURSAL ACTIVA (HU-45 / SCRUM-216)
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/sucursal-activa", methods=["POST"])
@jwt_required()
def cambiar_sucursal_activa():
    """
    Cambia la sucursal activa de un Administrador multi-sucursal y reemite
    los tokens para que el cambio surta efecto de inmediato -- sin esto,
    el usuario tendría que cerrar sesión y volver a entrar cada vez que su
    propietario lo mueve de sucursal o le asigna una nueva.

    Diseño "sucursal activa": usuarios.id_gimnasio sigue siendo la única
    fuente que lee require_tenant/require_role (utils/tenant.py,
    utils/security.py) en los 267 endpoints existentes -- aquí solo se
    actualiza ese valor, validado contra AsignacionUsuario (la fuente de
    verdad de qué sucursales puede operar), y se reconstruye el JWT con
    _build_token_pg() exactamente como hace /auth/refresh. Cero endpoints
    existentes necesitan saber que esto existe.

    Deliberadamente limitado a Administrador: Propietario, Entrenador,
    Recepcionista y Miembro siguen con una sola sucursal de por vida (ver
    utils/alcance.py), así que no tienen nada que "activar".
    """
    data = request.get_json(silent=True) or {}
    id_gimnasio_nuevo = data.get("id_gimnasio")
    if not isinstance(id_gimnasio_nuevo, int):
        return jsonify({"msg": "id_gimnasio es obligatorio y debe ser un entero"}), 400

    user_id = get_jwt_identity()
    usuario = UsuarioPG.query.get(int(user_id)) if str(user_id).isdigit() else None
    if usuario is None or not usuario.activo:
        return jsonify({"msg": "Usuario inválido"}), 403

    if normalizar_rol(usuario.rol.nombre if usuario.rol else "") != ROL_ADMINISTRADOR:
        return jsonify({"msg": "Solo un Administrador de sucursal puede cambiar su sucursal activa"}), 403

    if not usuario_tiene_alcance(usuario.id, [ROL_ADMINISTRADOR], id_gimnasio_nuevo):
        return jsonify({"msg": "No tienes una asignación como Administrador sobre esa sucursal"}), 403

    usuario.id_gimnasio = id_gimnasio_nuevo
    db.session.commit()

    payload = _build_token_pg(usuario)
    access_nuevo  = create_access_token(identity=payload["identity"], additional_claims=payload["claims"])
    refresh_nuevo = create_refresh_token(
        identity=payload["identity"],
        additional_claims={**payload["claims"], "fam": str(uuid.uuid4())},
    )
    respuesta = jsonify({
        "access_token":  access_nuevo,
        "refresh_token": refresh_nuevo,
        "user":          payload["user_response"],
    })
    set_access_cookies(respuesta, access_nuevo)
    set_refresh_cookies(respuesta, refresh_nuevo)
    return respuesta, 200


# ─────────────────────────────────────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout", methods=["POST"])
@jwt_required(refresh=True)
def logout():
    """
    Revoca el token de refresco presentado.

    Actividad 09, PR-01 (J. C. Pérez Nava): antes, cerrar sesión solo
    limpiaba el almacén del dispositivo -- no existía ninguna llamada a un
    endpoint de cierre de sesión ni lista de revocación en el servidor, de
    modo que el token de refresco seguía siendo válido durante sus 90 días
    completos después de que el usuario creía haber cerrado la sesión.
    """
    claims   = get_jwt()
    jti      = claims.get("jti")
    restante = claims["exp"] - int(datetime.now(timezone.utc).timestamp())
    if restante > 0 and jti:
        redis_cliente.setex(f"revocado:{jti}", restante, "1")

    respuesta = jsonify({"msg": "Sesión cerrada"})
    unset_jwt_cookies(respuesta)
    return respuesta, 200


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
        db.session.flush()

        # Asignación de alcance del usuario (HU-41 / SCRUM-212) -- si no
        # se especificó id_gimnasio, queda sin organización ni sucursal,
        # igual que hoy queda sin gimnasio asignado.
        crear_asignacion(nuevo)

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
