"""
utils/security.py — Decoradores de autorización para endpoints Flask.

Uso:
    from app.utils.security import require_role

    @backups_bp.route("/trigger", methods=["POST"])
    @jwt_required()
    @require_role("Administrador")
    def trigger_backup():
        ...

    # Múltiples roles permitidos:
    @jwt_required()
    @require_role("Administrador", "Recepcionista")
    def endpoint():
        ...

IMPORTANTE: @jwt_required() siempre debe ir ANTES de @require_role().
jwt_required valida y carga el token; require_role lee los claims del token ya validado.
"""
from functools import wraps
from flask import jsonify
from flask_jwt_extended import get_jwt


def require_role(*roles: str):
    """
    Decorador factory que restringe el acceso a usuarios con alguno de los roles indicados.

    El claim 'role' es incluido en el token por auth/routes.py al hacer login.
    Retorna 403 si el rol del usuario no está en la lista de roles permitidos.

    Args:
        *roles: nombres de rol permitidos (deben coincidir exactamente con los
                valores almacenados en la colección 'roles' de MongoDB).
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            claims = get_jwt()
            user_role = claims.get("role", "")

            if user_role not in roles:
                return jsonify({
                    "msg": "Acceso denegado: no tienes permisos para realizar esta acción",
                    "required_roles": list(roles),
                    "your_role": user_role
                }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorator
