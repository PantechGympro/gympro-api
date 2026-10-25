"""
utils/email_remitentes.py -- Remitente "Nombre <correo>" por categoría de
correo, centralizado para no repetir el patrón en cada ruta que manda mail.

Desde la migración de Gmail a Resend, GymPro ya no está limitado a una sola
cuenta de correo: Resend permite cualquier número de direcciones bajo el
dominio propio ya verificado (gympro.lat) sin costo ni límite adicional. Se
aprovecha para separar el remitente según el TIPO de correo:

    cuentas@gympro.lat        -- alta de cuenta, credenciales, recuperación
                                  de contraseña (todo lo que toca el acceso)
    facturacion@gympro.lat    -- recibos de venta/membresía, avisos de cambio
                                  de cuenta de cobro, reportes de corte de caja
    notificaciones@gympro.lat -- avisos automáticos sin intención de respuesta
                                  (vencimiento de membresía, citas, backups)
    soporte@gympro.lat        -- remitente por defecto (MAIL_DEFAULT_SENDER)
    contacto@gympro.lat       -- NO es remitente de ningún envío: es la
                                  dirección pública que se muestra en el pie
                                  de los correos ("si tienes dudas, escribe
                                  a...") para que las respuestas de los
                                  usuarios lleguen a un buzón que sí se
                                  revisa, en vez de a un remitente de solo
                                  envío.

Separar remitentes por categoría (en vez de un único MAIL_DEFAULT_SENDER)
también ayuda a la entregabilidad real: un proveedor de correo que ve que
"facturacion@" solo manda recibos y "notificaciones@" solo manda avisos
automáticos construye una reputación más limpia para cada uno que una sola
cuenta mezclando los dos tipos de tráfico.
"""
from flask import current_app

# Nombre visible en el "From:" del cliente de correo, por variable de config.
_NOMBRES = {
    "MAIL_DEFAULT_SENDER":        "GymPro Soporte",
    "MAIL_SENDER_CUENTAS":        "GymPro Cuentas",
    "MAIL_SENDER_FACTURACION":    "GymPro Facturación",
    "MAIL_SENDER_NOTIFICACIONES": "GymPro",
}


def _armar(correo, clave):
    if not correo:
        return None
    nombre = _NOMBRES.get(clave, "GymPro")
    return f"{nombre} <{correo}>"


def remitente(clave: str = "MAIL_DEFAULT_SENDER"):
    """
    Arma el remitente "Nombre <correo>" a partir de la variable de config
    `clave` (una de las MAIL_SENDER_* o MAIL_DEFAULT_SENDER), para usarse
    dentro de una request o de un job que ya empujó su propio app_context
    (ver chequear_vencimientos() en notifications.py). Si esa variable no
    está configurada, cae a MAIL_DEFAULT_SENDER -- nunca a MAIL_USERNAME,
    que con Resend es el literal "resend" del protocolo SMTP, no un correo.
    """
    correo = current_app.config.get(clave) or current_app.config.get("MAIL_DEFAULT_SENDER")
    return _armar(correo, clave)


def remitente_app(app, clave: str = "MAIL_DEFAULT_SENDER"):
    """
    Igual que remitente(), pero para código que recibe `app` de forma
    explícita en vez de depender de current_app (p. ej.
    send_email_notification() en backups/service.py, que ya se pasaba
    `app` como parámetro antes de esta migración -- se respeta ese patrón
    en vez de forzar un current_app que ese módulo nunca usó).
    """
    correo = app.config.get(clave) or app.config.get("MAIL_DEFAULT_SENDER")
    return _armar(correo, clave)


def correo_contacto() -> str:
    """Dirección pública de contacto para mostrar en el pie de los correos
    (no se usa como remitente -- ver docstring del módulo)."""
    return current_app.config.get("MAIL_SENDER_CONTACTO") or "contacto@gympro.lat"
