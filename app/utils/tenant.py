"""
utils/tenant.py — Middleware multi-tenant para GymPro.

Propaga el id_gimnasio del JWT al contexto de la request (flask.g.tenant_id)
para que cualquier blueprint pueda filtrar datos por gimnasio sin repetir
la lógica de extracción del token.

Uso en blueprints:
    from flask import g
    from app.mongo import get_db

    # MongoDB
    db = get_db()
    registros = db.miembros.find({"id_gimnasio": g.tenant_id})

    # PostgreSQL (SQLAlchemy)
    from app.models.pg.usuario import Usuario
    usuarios = Usuario.query.filter_by(id_gimnasio=g.tenant_id).all()

Rutas exentas (no requieren tenant):
    /api/auth/login
    /api/auth/register
    /api/health
    /api/billing/webhook  (futuro Sprint 3 — Stripe firma el payload directamente)

IMPORTANTE: Este middleware se registra DESPUÉS de jwt.init_app() para que
el contexto JWT esté disponible cuando se llame a get_jwt().
"""
from flask import g, request, jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt
from flask_jwt_extended.exceptions import NoAuthorizationError, InvalidHeaderError
from jwt.exceptions import ExpiredSignatureError, DecodeError

# Rutas que no necesitan autenticación ni tenant
_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/health",
    "/api/billing/webhook",
    "/api/billing/stripe/webhook",
    "/api/onboarding",
    # Webhooks de PayPal y Mercado Pago: la pasarela notifica sin token JWT.
    # La transacción se identifica por la referencia incluida en el payload.
    "/api/pagos/webhook",
)


def init_tenant_middleware(app):
    """
    Registra el before_request de tenant en la app Flask.
    Llamar desde create_app() después de inicializar las extensiones.
    """

    @app.before_request
    def _resolve_tenant():
        # Saltar rutas exentas
        if any(request.path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            g.tenant_id = None
            return

        # Intentar extraer el JWT sin propagar excepciones al cliente aún
        try:
            verify_jwt_in_request()
            claims = get_jwt()
        except (NoAuthorizationError, InvalidHeaderError):
            # Sin token → solo aplica si la ruta es protegida
            # Los decoradores @jwt_required() en cada endpoint se encargan
            # de rechazar la request si falta el token.
            g.tenant_id = None
            return
        except (ExpiredSignatureError, DecodeError):
            g.tenant_id = None
            return
        except Exception:
            g.tenant_id = None
            return

        # superadmin opera a nivel de plataforma — no está ligado a ningún gimnasio.
        # Se le asigna tenant_id=None y g.is_superadmin=True para que los endpoints
        # puedan optar por mostrar datos de todos los gimnasios.
        if claims.get("role") == "superadmin":
            g.tenant_id    = None
            g.is_superadmin = True
            return

        g.is_superadmin = False

        # Extraer id_gimnasio del claim JWT
        tenant_id = claims.get("id_gimnasio")
        g.tenant_id = tenant_id

        # Log de debug (solo en FLASK_DEBUG=1)
        if current_app.debug and tenant_id:
            current_app.logger.debug(
                f"[Tenant] Request {request.method} {request.path} → gimnasio {tenant_id}"
            )


def get_tenant_filter():
    """
    Retorna el filtro de tenant para usar en queries.

    Para MongoDB:
        db.collection.find(get_tenant_filter())

    Para SQLAlchemy:
        Model.query.filter_by(**get_tenant_filter()).all()
        # o con id_gimnasio directamente:
        Model.query.filter_by(id_gimnasio=g.tenant_id).all()

    Si tenant_id es None (usuario legacy sin gimnasio asignado), retorna
    diccionario vacío para no filtrar — comportamiento permisivo durante
    la migración. Quitar este fallback al cortar el soporte legacy.
    """
    tenant_id = getattr(g, "tenant_id", None)
    if tenant_id is None:
        return {}
    return {"id_gimnasio": tenant_id}


def require_tenant(fn):
    """
    Decorador que garantiza que g.tenant_id está presente.
    Usar en endpoints que exigen aislamiento estricto de tenant.

    Uso:
        @blueprint.route("/data")
        @jwt_required()
        @require_tenant
        def get_data():
            ...
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "tenant_id", None):
            return jsonify({
                "msg": "Token sin gimnasio asignado. Contacta al administrador."
            }), 403
        return fn(*args, **kwargs)
    return wrapper
