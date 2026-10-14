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
