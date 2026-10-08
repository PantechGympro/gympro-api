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

class TenantNoResuelto(PermissionError):
    """No se pudo determinar a qué gimnasio pertenece la petición.

    Antes, get_tenant_filter() devolvía {} en este caso: un filtro vacío
    no restringe nada y la consulta entregaba los registros de TODOS los
    gimnasios (PR-01 — Actividad 09, F. Pérez Medina). Ahora la ausencia
    de inquilino se señala con esta excepción y se traduce a 403 en el
    errorhandler registrado más abajo.
    """


# Rutas EXACTAS que no requieren resolver un inquilino. Se comparan por
# igualdad y no por prefijo: antes "/api/onboarding" (prefijo) eximía
# también a PUT /api/onboarding/complete-setup -- que exige sesión y
# nunca debió quedar exenta -- y cualquier endpoint nuevo que empezara
# igual quedaba exento sin que nadie lo decidiera (PR-02).
_RUTAS_PUBLICAS = frozenset({
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/health",
    "/api/onboarding/gym-types",     # catálogo público del selector
    "/api/onboarding/planes",        # catálogo público de planes
    "/api/onboarding/register-gym",  # alta inicial: aún no existe el gimnasio
})

# Webhooks sin parámetro en la ruta: la pasarela notifica sin JWT y se
# autentica por la firma del propio proveedor dentro de cada handler.
_RUTAS_WEBHOOK = frozenset({
    "/api/billing/webhook",
    "/api/billing/stripe/webhook",
})

# Único caso que se conserva por PREFIJO, porque la ruta real lleva un
# parámetro variable (<proveedor>): /api/pagos/webhook/paypal,
# /api/pagos/webhook/mercadopago. Queda aparte y comentado a propósito,
# para que cualquier adición futura sea una decisión visible y no un
# efecto colateral de "empezar igual".
_PREFIJOS_WEBHOOK = (
    "/api/pagos/webhook",
)


def init_tenant_middleware(app):
    """
    Registra el before_request de tenant en la app Flask.
    Llamar desde create_app() después de inicializar las extensiones.
    """

    @app.before_request
    def _resolve_tenant():
        ruta = request.path.rstrip("/") or "/"

        # Saltar rutas exentas: exactas, o por prefijo solo para el
        # webhook con parámetro variable (ver _PREFIJOS_WEBHOOK arriba).
        if (
            ruta in _RUTAS_PUBLICAS
            or ruta in _RUTAS_WEBHOOK
            or any(ruta.startswith(p) for p in _PREFIJOS_WEBHOOK)
        ):
            g.tenant_id     = None
            g.is_superadmin = False
            return

        # Intentar extraer el JWT sin propagar excepciones al cliente aún
        try:
            verify_jwt_in_request()
            claims = get_jwt()
        except (NoAuthorizationError, InvalidHeaderError):
            # Sin token → solo aplica si la ruta es protegida
            # Los decoradores @jwt_required() en cada endpoint se encargan
            # de rechazar la request si falta el token.
            g.tenant_id     = None
            g.is_superadmin = False
            return
        except (ExpiredSignatureError, DecodeError):
            g.tenant_id     = None
            g.is_superadmin = False
            return
        except Exception:
            g.tenant_id     = None
            g.is_superadmin = False
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

    @app.errorhandler(TenantNoResuelto)
    def _sin_inquilino(exc):
        current_app.logger.warning(
            "Petición rechazada sin inquilino resuelto: %s %s",
            request.method, request.path,
        )
        return jsonify({"msg": "Token sin gimnasio asignado."}), 403


def get_tenant_filter():
    """
    Retorna el filtro de tenant para usar en queries.

    Para MongoDB:
        db.collection.find(get_tenant_filter())

    Para SQLAlchemy:
        Model.query.filter_by(**get_tenant_filter()).all()
        # o con id_gimnasio directamente:
        Model.query.filter_by(id_gimnasio=g.tenant_id).all()

    Falla en CERRADO (PR-01): antes, si no había inquilino resuelto, se
    devolvía {} y un filtro vacío no restringe nada -- la consulta
    entregaba los registros de TODOS los gimnasios. Ahora la ausencia de
    inquilino es un error explícito, nunca un permiso implícito.
    """
    if getattr(g, "is_superadmin", False):
        # Vista transversal del superadministrador: decisión explícita,
        # no un efecto colateral de tenant_id = None.
        return {}

    tenant_id = getattr(g, "tenant_id", None)
    if tenant_id is None:
        raise TenantNoResuelto(
            "La petición no tiene gimnasio asignado; se rechaza por "
            "aislamiento de datos entre inquilinos (RNF-03)."
        )
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
