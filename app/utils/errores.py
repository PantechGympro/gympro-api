"""
utils/errores.py — Manejo centralizado de errores y cabeceras de seguridad.

Auditoría nov/2026. Antes de este módulo:

  * 130 endpoints respondían `jsonify({"error": str(e)}), 500`. El texto de
    una excepción cruda (pymongo, psycopg2, KeyError, rutas de archivos...)
    viajaba tal cual al navegador/app: filtra detalles internos (OWASP
    A05/A09) y, además, es ilegible para quien usa el sistema.
  * No había ningún errorhandler global: una excepción no atrapada devolvía
    la página HTML de error de Flask, que los clientes (que esperan JSON)
    mostraban como "Unexpected token <".
  * Los errores de sesión de Flask-JWT-Extended salían en inglés
    ("Token has expired", "Missing Authorization Header").

En vez de editar a mano cada uno de esos endpoints (y depender de que el
siguiente que se escriba recuerde hacerlo bien), el saneado vive en UN solo
lugar: un `after_request` que intercepta toda respuesta 5xx.

  - El detalle técnico se registra en el log del servidor con un folio.
  - Al cliente solo llega un mensaje claro + ese folio (`ref`), para que
    soporte pueda encontrar el error exacto sin que el usuario vea jerga.

Uso: `init_errores(app)` desde create_app().

Para que un 5xx conserve un mensaje redactado a propósito:
    return jsonify({"error": "El servicio de IA está saturado...", "publico": True}), 503
"""
import uuid

from flask import jsonify, request
from werkzeug.exceptions import HTTPException

MSG_GENERICO = (
    "Tuvimos un problema de nuestro lado y no pudimos completar la acción. "
    "Intenta de nuevo en unos minutos; si sigue pasando, avísale a soporte."
)

# Mensajes por código HTTP para errores que Flask genera por su cuenta
# (ruta inexistente, archivo demasiado grande, límite de peticiones...).
_MSG_HTTP = {
    400: "No pudimos procesar la solicitud. Revisa los datos e intenta de nuevo.",
    404: "No encontramos lo que buscabas. Puede que ya no exista o que el enlace esté incompleto.",
    405: "Esta acción no está disponible aquí.",
    413: "El archivo es demasiado grande. El máximo permitido es 15 MB.",
    415: "Ese tipo de archivo no es compatible.",
    429: "Hiciste demasiados intentos en poco tiempo. Espera un momento y vuelve a intentarlo.",
}

# Claves donde los endpoints ponen el texto que ve el usuario...
_CLAVES_MENSAJE = ("error", "msg", "message", "mensaje")
# ...y claves que solo llevan detalle interno: nunca deben salir en un 5xx.
_CLAVES_INTERNAS = ("detalle", "detail", "trace", "traceback", "stack", "exception")

# Textos "humanos" pero vacíos de contenido: se reemplazan por el genérico.
_FRASES_VACIAS = ("error interno", "internal server error", "error desconocido")


def _folio() -> str:
    """Folio corto para cruzar lo que ve el usuario con el log del servidor."""
    return uuid.uuid4().hex[:8].upper()


def _es_mensaje_propio(texto) -> bool:
    """
    True si el texto lo escribió una persona del equipo para el usuario
    (p. ej. "No se pudo completar el traslado") y no es una frase vacía.
    Solo se confía en él cuando el endpoint lo mandó JUNTO a un campo de
    detalle interno: ese patrón (`{"msg": "...", "detalle": str(e)}`)
    indica que el mensaje y la excepción iban separados a propósito.
    """
    if not isinstance(texto, str) or not texto.strip():
        return False
    bajo = texto.strip().lower().rstrip(".")
    return not any(bajo.startswith(f) for f in _FRASES_VACIAS)


def _sanear_5xx(app, respuesta):
    """Quita el detalle técnico de una respuesta 5xx y deja un mensaje claro."""
    cuerpo = respuesta.get_json(silent=True) if respuesta.is_json else None
    folio = _folio()

    app.logger.error(
        "[%s] %s %s -> %s | cuerpo original: %s",
        folio, request.method, request.path, respuesta.status_code,
        cuerpo if cuerpo is not None else respuesta.get_data(as_text=True)[:500],
    )

    if not isinstance(cuerpo, dict):
        nueva = jsonify({"error": MSG_GENERICO, "msg": MSG_GENERICO, "ref": folio})
        nueva.status_code = respuesta.status_code
        return nueva

    # Solo "detalle"/"detail" marcan el patrón del equipo (mensaje propio +
    # excepción aparte). Un "trace" suelto acompaña casi siempre a un
    # `"error": str(e)`, que NO es un mensaje de fiar.
    traia_detalle = any(k in cuerpo for k in ("detalle", "detail"))
    # Opt-in explícito: un endpoint que redacta su propio mensaje para un 5xx
    # (p. ej. "El servicio de IA está saturado") lo marca con "publico": True
    # y se respeta tal cual. La marca no viaja al cliente.
    es_publico = cuerpo.get("publico") is True
    limpio = {k: v for k, v in cuerpo.items() if k not in _CLAVES_INTERNAS and k != "publico"}

    claves_presentes = [k for k in _CLAVES_MENSAJE if k in limpio]
    for clave in claves_presentes:
        if not ((traia_detalle or es_publico) and _es_mensaje_propio(limpio[clave])):
            limpio[clave] = MSG_GENERICO
    if not claves_presentes:
        limpio["error"] = MSG_GENERICO

    limpio["ref"] = folio
    nueva = jsonify(limpio)
    nueva.status_code = respuesta.status_code
    return nueva


def init_errores(app):
    """Registra errorhandlers, saneado de 5xx y cabeceras de seguridad."""
    from app.extensions import jwt

    # ── Errores HTTP generados por Flask/Werkzeug (404, 405, 413, 429...) ────
    @app.errorhandler(HTTPException)
    def _http(exc):
        codigo = exc.code or 500
        if codigo >= 500:
            mensaje = MSG_GENERICO
        else:
            mensaje = _MSG_HTTP.get(codigo, _MSG_HTTP[400])
        return jsonify({"error": mensaje, "msg": mensaje}), codigo

    # ── Cualquier excepción que ningún endpoint atrapó ───────────────────────
    @app.errorhandler(Exception)
    def _inesperado(exc):
        # El traceback completo queda en el log; el after_request de abajo
        # le pone folio y mensaje a la respuesta.
        app.logger.exception("Excepción no controlada en %s %s", request.method, request.path)
        return jsonify({"error": MSG_GENERICO}), 500

    # ── Sesión (Flask-JWT-Extended): mismos códigos de siempre, en español ───
    # Los clientes reaccionan al código 401, no al texto, así que cambiar el
    # mensaje no altera el flujo de renovación de sesión de web ni de móvil.
    @jwt.expired_token_loader
    def _expirado(_cabecera, _carga):
        return jsonify({"msg": "Tu sesión caducó. Vuelve a iniciar sesión para continuar."}), 401

    @jwt.invalid_token_loader
    def _invalido(_motivo):
        return jsonify({"msg": "No pudimos validar tu sesión. Inicia sesión de nuevo."}), 422

    @jwt.unauthorized_loader
    def _sin_sesion(_motivo):
        return jsonify({"msg": "Necesitas iniciar sesión para ver esto."}), 401

    @jwt.revoked_token_loader
    def _revocado(_cabecera, _carga):
        return jsonify({"msg": "Tu sesión se cerró. Vuelve a iniciar sesión."}), 401

    @app.after_request
    def _post(respuesta):
        # Cabeceras de seguridad baratas y sin efectos secundarios para una
        # API JSON: evitan sniffing de tipo, que la API se incruste en un
        # iframe ajeno, y que el navegador mande la URL completa como referer.
        # (Se aplican DESPUÉS del saneado: _sanear_5xx devuelve una respuesta
        # nueva y las cabeceras puestas antes se perderían.)
        if respuesta.status_code >= 500 and not respuesta.direct_passthrough:
            respuesta = _sanear_5xx(app, respuesta)

        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("X-Frame-Options", "DENY")
        respuesta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return respuesta
