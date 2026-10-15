"""
utils/security.py — Decoradores de autorización para endpoints Flask.

Uso:
    from app.utils.security import require_role

    @backups_bp.route("/trigger", methods=["POST"])
    @jwt_required()
    @require_role("owner_gym", "superadmin")
    def trigger_backup():
        ...

    # Múltiples roles permitidos:
    @jwt_required()
    @require_role("owner_gym", "superadmin")
    def endpoint():
        ...

IMPORTANTE: @jwt_required() siempre debe ir ANTES de @require_role().
jwt_required valida y carga el token; require_role lee los claims del token ya validado.
"""
from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt
from app.constants.roles import normalizar_rol


def require_role(*roles: str):
    """
    Decorador factory que restringe el acceso a usuarios con alguno de los roles indicados.

    El claim 'role' es incluido en el token por auth/routes.py al hacer login.
    Retorna 403 si el rol del usuario no está en la lista de roles permitidos.

    La comparación se hace vía normalizar_rol() (ver app.constants.roles),
    no con un "in" literal sobre cadenas: el catálogo real mezcla
    mayúsculas ("Entrenador", "Recepcionista", "Miembro" vienen así desde
    seed_pg.py) con minúsculas ("owner_gym", "superadmin"), y una
    comparación exacta sensible a mayúsculas puede negar acceso de forma
    silenciosa si alguien escribe el rol con otra convención (SCRUM-107).

    Args:
        *roles: nombres o alias de rol permitidos — ver ROL_* y los alias
                documentados en app.constants.roles.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            claims = get_jwt()
            user_role = claims.get("role", "")
            permitidos = {normalizar_rol(r) for r in roles}

            if normalizar_rol(user_role) not in permitidos:
                return jsonify({
                    "msg": "Acceso denegado: no tienes permisos para realizar esta acción",
                    "required_roles": list(roles),
                    "your_role": user_role
                }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# SCRUM-224 — @require_funcion_plan(): bandera de función del plan contratado
# ─────────────────────────────────────────────────────────────────────────────
from flask import g as _g
from app.constants.roles import ROL_SUPERADMIN
from app.utils.capacidad import permite_funcion


def require_funcion_plan(nombre_funcion: str):
    """
    Decorador factory que bloquea el endpoint si el plan de la organización
    no habilita `nombre_funcion` -- ver PlanSuscripcion.limites y
    utils/capacidad.py::permite_funcion() (las banderas reales: ver
    migración 012 -- analiticas_ia, rutinas_ia, reportes_pdf, app_movil).

    A diferencia de require_role()/require_tenant() (quién eres / de qué
    gimnasio eres), este decorador pregunta algo distinto: tu gimnasio SÍ
    es el tuyo, pero ¿tu plan incluye esto? Por eso vive junto al resto de
    decoradores de autorización aunque la pregunta real la resuelva
    utils/capacidad.py (el mismo módulo que ya resuelve los topes
    numéricos del plan, SCRUM-222/223) -- se evita duplicar ahí la
    resolución Gimnasio -> Organizacion -> PlanSuscripcion.

    Resolución del gimnasio: usa g.tenant_id si @require_tenant ya corrió
    antes en la cadena (el caso normal en los paneles de propietario/
    entrenador); si no está disponible, cae al claim id_gimnasio del
    propio JWT -- algunos endpoints de IA (ver routes/ia/spark_*.py) no
    pasan por @require_tenant y resuelven el gimnasio directo del token.

    Responde 402 (Payment Required: la acción es válida, el plan
    contratado no la incluye -- distinto del 409 que ya usa
    LimiteDePlanExcedido para "sí la incluye, pero ya topaste") con
    `funcion_requerida` para que el frontend pueda mostrar un aviso de
    mejora de plan en vez de un error genérico.

    IMPORTANTE: debe ir DESPUÉS de @jwt_required() (y de @require_tenant
    si el endpoint lo usa) en la cadena de decoradores, igual que
    @require_role().

    superadmin NO tiene gimnasio propio (claims id_gimnasio/tenant_id
    siempre None -- ver _build_token_pg en auth/routes.py) y al menos un
    endpoint gateado aquí (spark_cancelaciones.py) trata ese None como
    "vista de toda la plataforma", no como "sin plan": bloquearlo le
    quitaría esa vista. Mismo criterio que @requiere() en este archivo --
    superadmin pasa siempre, sin consultar ningún plan.

    Uso:
        @trainer_bp.route("/routines/import-ai", methods=["POST"])
        @jwt_required()
        @require_tenant
        @require_funcion_plan("rutinas_ia")
        def import_routines_ai():
            ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if normalizar_rol(get_jwt().get("role", "")) == ROL_SUPERADMIN:
                return fn(*args, **kwargs)
            id_gimnasio = getattr(_g, "tenant_id", None)
            if id_gimnasio is None:
                id_gimnasio = get_jwt().get("id_gimnasio")
            if not permite_funcion(id_gimnasio, nombre_funcion):
                return jsonify({
                    "msg": "Esta función no está disponible en tu plan actual. Mejora tu plan para usarla.",
                    "funcion_requerida": nombre_funcion,
                    "upgrade_requerido": True,
                }), 402
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────────────────────────────────────
# SCRUM-213 (HU-42) — @requiere(): alcance real sobre AsignacionUsuario
# ─────────────────────────────────────────────────────────────────────────────
from flask import request, g, current_app
from flask_jwt_extended import get_jwt_identity
from app.constants.roles import ROL_SUPERADMIN
from app.utils.alcance import usuario_tiene_alcance


def requiere(*roles: str, sobre_sucursal: str = None):
    """
    Decorador factory que resuelve la pregunta única de HU-42: ¿existe una
    asignación (AsignacionUsuario) que le dé al usuario del token alguno
    de `roles` sobre la sucursal objetivo?

    A diferencia de require_role()/require_tenant() (que siguen siendo
    correctos para Propietario/Entrenador/Recepcionista/Miembro, con una
    sola sucursal de por vida hoy -- ver utils/alcance.py para el porqué),
    @requiere() SÍ puede autorizar sobre una sucursal DISTINTA de la
    propia del usuario: lo necesita cualquier endpoint que HU-45 en
    adelante deje tocar más de una sucursal (un Administrador con varias
    asignadas, o un Propietario operando sobre una sucursal que no es la
    de su usuarios.id_gimnasio original).

    Args:
        *roles: nombres o alias de rol permitidos (ver normalizar_rol()).
        sobre_sucursal: nombre del parámetro de la URL o del query string
            que lleva el id de la sucursal objetivo (p. ej. "id_gimnasio"
            en una ruta "/sucursales/<int:id_gimnasio>/..."). Si se omite,
            el alcance exigido es sobre el propio gimnasio del token
            (g.tenant_id) -- el mismo criterio de siempre, solo que
            verificado contra AsignacionUsuario en vez de contra el claim.

    Uso:
        @sucursales_bp.route("/sucursales/<int:id_gimnasio>/staff", methods=["POST"])
        @jwt_required()
        @requiere("Propietario", "Administrador", sobre_sucursal="id_gimnasio")
        def asignar_staff(id_gimnasio):
            ...

    IMPORTANTE: @jwt_required() siempre debe ir ANTES de @requiere(), igual
    que con require_role().
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            claims     = get_jwt()
            rol_actual = normalizar_rol(claims.get("role", ""))

            if rol_actual == ROL_SUPERADMIN:
                return fn(*args, **kwargs)

            id_gimnasio_objetivo = None
            if sobre_sucursal is not None:
                crudo = (request.view_args or {}).get(sobre_sucursal)
                if crudo is None:
                    crudo = request.args.get(sobre_sucursal)
                if crudo is not None:
                    try:
                        id_gimnasio_objetivo = int(crudo)
                    except (TypeError, ValueError):
                        id_gimnasio_objetivo = None
            else:
                id_gimnasio_objetivo = getattr(g, "tenant_id", None)

            # Compatibilidad con usuarios legacy de Mongo (auth/routes.py,
            # _build_token_mongo): el fallback nunca llamó crear_asignacion(),
            # así que no tienen fila en asignacion_usuario. Se conserva el
            # criterio anterior (claims) para no negarles acceso de golpe
            # -- AC6, "todo lo que funcionaba sigue funcionando igual".
            if claims.get("fuente") == "mongo":
                autorizado = rol_actual in {normalizar_rol(r) for r in roles}
                if autorizado and id_gimnasio_objetivo is not None:
                    autorizado = claims.get("id_gimnasio") == id_gimnasio_objetivo
            else:
                try:
                    user_id = int(get_jwt_identity())
                except (TypeError, ValueError):
                    user_id = None
                autorizado = user_id is not None and usuario_tiene_alcance(
                    user_id, roles, id_gimnasio_objetivo
                )

            if not autorizado:
                current_app.logger.warning(
                    "Acceso denegado por alcance: usuario=%s ruta=%s %s roles_requeridos=%s sucursal_objetivo=%s",
                    get_jwt_identity(), request.method, request.path, roles, id_gimnasio_objetivo,
                )
                return jsonify({
                    "msg": "Acceso denegado: no tienes alcance sobre este recurso",
                }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator
