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
