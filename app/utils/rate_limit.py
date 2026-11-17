"""
utils/rate_limit.py -- Rate limiting centralizado (anti abuso y fuerza bruta).

Antes los topes vivían en 6 decoradores sueltos y todo lo demás compartía el
límite global por IP ("300 per day; 60 per hour"). Tres problemas reales:

1. Detrás de nginx la IP que ve Flask era la del proxy: TODOS los usuarios
   compartían un solo contador (ver el comentario histórico de /api/auth/me,
   "no me deja entrar"). Ahora `configurar_proxy()` aplica ProxyFix para leer
   la IP real del cliente (X-Forwarded-For).
2. El contador era por IP aunque la sesión ya identificara al usuario: un
   gimnasio entero (misma IP de WiFi) se bloqueaba entre sí. Ahora un usuario
   autenticado cuenta por usuario (`clave_limite`) y solo el tráfico anónimo
   cuenta por IP.
3. Solo 6 endpoints tenían límite propio; pagos, check-in, entrenamientos de
   modelos, backups, IA, PDFs y subidas dependían del global. Ahora hay una
   tabla de políticas (REGLAS) auditable en un solo archivo, aplicada por
   `aplicar_rate_limits(app)` al final de create_app().

Reglas de diseño
----------------
* Fuerza bruta de login/reset: además del límite por IP hay uno POR CUENTA
  (`clave_cuenta`) que solo descuenta intentos fallidos, para frenar ataques
  distribuidos contra un mismo correo sin castigar a quien sí entra bien.
* Los webhooks de pasarelas y el health check están exentos (son tráfico de
  máquina con ráfagas legítimas; se autentican con firma).
* Las peticiones OPTIONS (preflight CORS) no consumen cuota.
* Almacén: Redis compartido entre workers (ver config.RATELIMIT_*); si Redis
  cae, Flask-Limiter degrada a memoria por worker (config) en vez de abrir.
* Respuesta 429 en español con `Retry-After` y un `codigo` estable para que
  web y móvil muestren "espera N segundos" sin parsear texto.
"""
import os
import re

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_limiter.errors import RateLimitExceeded
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

# ── Políticas con nombre ─────────────────────────────────────────────────────
POLITICAS = {
    # Dinero: crear cobros/checkout o reconciliar. Un humano hace pocos.
    "pago":            "10 per minute; 60 per hour",
    # Check-in del propio miembro por QR (1 por día en la práctica).
    "checkin_miembro": "6 per minute; 30 per hour",
    # Check-in registrado por personal (puede haber ráfagas en hora pico).
    "checkin_staff":   "40 per minute; 600 per hour",
    # Recalcular modelos / respaldos / restauraciones: caros en CPU y disco.
    "pesado":          "3 per minute; 20 per hour",
    # Generación/importación con IA (Ollama): la más cara del servidor.
    "ia":              "5 per minute; 40 per hour",
    # PDFs y reportes pesados.
    "reporte":         "10 per minute; 100 per hour",
    # Subida de archivos.
    "subida":          "10 per minute; 60 per hour",
    # Alta de staff y reenvío de credenciales (correos).
    "alta_staff":      "10 per minute; 60 per hour",
    # Consultas de analítica (lecturas que agregan en Mongo).
    "analitica":       "30 per minute; 600 per hour",
    # Búsquedas/lookup que pegan a terceros.
    "busqueda":        "30 per minute; 600 per hour",
    # Mensajería (chat, reportes de chat, bloqueos).
    "mensajeria":      "30 per minute; 600 per hour",
    # Cualquier otra escritura (POST/PUT/PATCH/DELETE) autenticada.
    "escritura":       "120 per minute; 1500 per hour",
    # Endpoints públicos sin sesión (catálogo, planes, testimonios).
    "publico":         "60 per minute; 600 per hour",
    # Refresco de sesión.
    "refresco":        "30 per minute; 300 per hour",
}

_ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}

# (regex sobre la ruta, métodos afectados o None=todos, política). Gana la primera.
REGLAS = [
    (r"^/api/(pagos/checkout|pagos/reconciliar|billing/stripe/checkout|billing/facturas)$", {"POST"}, "pago"),
    (r"^/api/user/checkin/qr$", {"POST"}, "checkin_miembro"),
    (r"^/api/(checkins/manual|recepcionista/checkins)$", {"POST"}, "checkin_staff"),
    (r"/train$", {"POST"}, "pesado"),
    (r"^/api/(owner_gym/|superadmin/)?backups/(trigger|restore|restore-upload|schedule)$", {"POST"}, "pesado"),
    (r"/(import-ai|confirm-import)$", {"POST"}, "ia"),
    (r"/pdf$", {"GET"}, "reporte"),
    (r"(/cert-upload|/perfil-publico/imagenes|/profile/photo)$", {"POST", "PUT"}, "subida"),
    (r"^/api/owner_gym/staff(/\d+/reenviar-credenciales|/trasladar)?$", {"POST"}, "alta_staff"),
    (r"^/api/(miembros/verificar|nutricion/barcode/)", {"GET"}, "busqueda"),
    (r"/chat(/|$)", _ESCRITURA, "mensajeria"),
    (r"^/api/analytics/", {"GET"}, "analitica"),
    (r"^/api/(publico|onboarding/planes)", {"GET"}, "publico"),
    (r"^/api/auth/refresh$", {"POST"}, "refresco"),
    (r"^/api/", _ESCRITURA, "escritura"),
]
_REGLAS_COMPILADAS = [(re.compile(rx), metodos, pol) for rx, metodos, pol in REGLAS]

# Rutas que nunca se limitan (tráfico de máquina con firma propia).
_EXENTAS = re.compile(r"/webhook(/|$)|^/api/health")


# ── Claves de conteo ─────────────────────────────────────────────────────────
def clave_limite():
    """Usuario autenticado -> 'u:<id>'; anónimo -> 'ip:<ip real>'."""
    try:
        verify_jwt_in_request(optional=True)
        identidad = get_jwt_identity()
    except Exception:  # token vencido/inválido: cuenta como anónimo
        identidad = None
    return f"u:{identidad}" if identidad else f"ip:{get_remote_address()}"


def clave_cuenta():
    """Para login/reset: cuenta por correo (ataques distribuidos a una cuenta)."""
    datos = request.get_json(silent=True) or {}
    correo = str(datos.get("email") or datos.get("correo") or "").strip().lower()[:254]
    return f"cuenta:{correo}" if correo else f"ip:{get_remote_address()}"


def solo_fallos(respuesta):
    """deduct_when para límites por cuenta: solo cuentan intentos que fallaron."""
    return respuesta.status_code in (400, 401, 403, 404, 422)


# ── Proxy ────────────────────────────────────────────────────────────────────
def configurar_proxy(app):
    """
    Lee la IP real del cliente detrás de nginx. PROXY_HOPS = nº de proxies de
    confianza delante de Flask (nginx del compose = 1; 0 si el API se expone
    directo). Con 0 NO se confía en X-Forwarded-For: un atacante podría
    falsearlo para saltarse los límites.
    """
    saltos = int(os.getenv("PROXY_HOPS", "1"))
    if saltos > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=saltos, x_proto=saltos, x_host=saltos)


# ── Aplicación de políticas ──────────────────────────────────────────────────
def politica_para(ruta, metodos):
    """Política que corresponde a (ruta, métodos), o None. Útil para pruebas."""
    for rx, ms, pol in _REGLAS_COMPILADAS:
        if rx.search(ruta) and (ms is None or ms & set(metodos)):
            return pol
    return None


def aplicar_rate_limits(app, limiter):
    """
    Envuelve cada vista según REGLAS. Se llama UNA vez al final de create_app(),
    cuando ya están registrados todos los blueprints. Las vistas que ya traen
    @limiter.limit propio (login, register, me...) se respetan tal cual.
    """
    # Nombre calificado de las vistas ya decoradas a mano.
    ya_marcadas = set(getattr(limiter, "_marked_for_limiting", set()))
    aplicadas = {}

    for regla in app.url_map.iter_rules():
        vista = app.view_functions.get(regla.endpoint)
        if vista is None or regla.endpoint.endswith("static"):
            continue
        nombre = f"{vista.__module__}.{vista.__name__}"
        if nombre in ya_marcadas or _EXENTAS.search(regla.rule):
            continue
        metodos = regla.methods - {"HEAD", "OPTIONS"}
        politica = politica_para(regla.rule, metodos)
        if politica is None:
            continue
        # Una vista puede colgar de varias reglas (rutas alternativas): la
        # primera política asignada manda para toda la vista.
        if regla.endpoint in aplicadas:
            continue
        afectados = sorted(metodos & _ESCRITURA) if politica == "escritura" else sorted(metodos)
        app.view_functions[regla.endpoint] = limiter.limit(
            POLITICAS[politica], methods=afectados, key_func=clave_limite,
        )(vista)
        aplicadas[regla.endpoint] = politica

    app.logger.info("Rate limit: %d endpoints con política propia", len(aplicadas))
    return aplicadas


def registrar_manejador_429(app):
    """429 en español, con Retry-After y código estable."""
    @app.errorhandler(RateLimitExceeded)
    def _demasiadas(exc):
        espera = None
        try:
            from app.extensions import limiter
            limite = limiter.current_limit
            if limite is not None and getattr(limite, "reset_at", None):
                import time
                espera = max(1, int(limite.reset_at - time.time()))
        except Exception:
            espera = None
        mensaje = "Hiciste demasiados intentos en poco tiempo. "
        mensaje += (f"Espera {espera} segundos y vuelve a intentarlo."
                    if espera else "Espera un momento y vuelve a intentarlo.")
        cuerpo = {"error": mensaje, "msg": mensaje, "codigo": "demasiadas_solicitudes"}
        if espera:
            cuerpo["reintentar_en"] = espera
        respuesta = jsonify(cuerpo)
        respuesta.status_code = 429
        if espera:
            respuesta.headers["Retry-After"] = str(espera)
        return respuesta
