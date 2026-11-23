"""
utils/web_push.py -- Envio de notificaciones push al NAVEGADOR (Web Push /
VAPID), complemento del push a movil que ya existe via Expo
(compartido/notificaciones._enviar_push).

Hasta ahora el unico canal push real era Expo (solo movil instalado): un
usuario que solo usa la web nunca recibia push, nada mas la fila en la
campana in-app y, para ciertos eventos, un correo. Este modulo cierra ese
hueco reusando el MISMO estandar que ya habla el navegador (la Push API +
Service Worker, ver web/public/sw.js), sin inventar infraestructura nueva:
Flask + pywebpush del lado del servidor.

No requiere un servicio externo (FCM/APNs de por medio los maneja el propio
navegador vendor-side); solo el par de llaves VAPID (ver app/config.py) y
las suscripciones que cada navegador entrega una vez al aceptar el permiso
(coleccion Mongo `push_subscriptions_web`, ver compartido/notificaciones.py).
"""
import json
import logging

logger = logging.getLogger(__name__)


def enviar_web_push(app, subscription_info: dict, payload: dict) -> str:
    """
    Manda un push a UNA suscripcion de navegador.

    Devuelve:
      "ok"       -- entregado (o aceptado por el servicio push del navegador)
      "vencida"  -- la suscripcion ya no es valida (404/410): el llamador
                    debe borrarla, el navegador la revoco (desinstalo el PWA,
                    borro datos del sitio, etc.)
      "error"    -- fallo transitorio (red, limites, etc.); no se borra la
                    suscripcion, se reintenta en el siguiente evento.
    """
    public_key  = app.config.get("VAPID_PUBLIC_KEY")
    private_key = app.config.get("VAPID_PRIVATE_KEY")
    if not private_key or not public_key:
        # Entorno sin llaves VAPID configuradas (ej. una copia local recien
        # clonada): no es un error del evento que disparo el push, solo no
        # hay como mandarlo todavia.
        return "error"

    try:
        from pywebpush import WebPushException, webpush

        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims={"sub": f"mailto:{app.config.get('VAPID_CLAIMS_EMAIL', 'soporte@gympro.lat')}"},
            ttl=60 * 60 * 24,  # 24h: si el navegador estuvo offline, aun vale la pena entregarlo
        )
        return "ok"
    except WebPushException as ex:
        status = getattr(ex.response, "status_code", None)
        if status in (404, 410):
            return "vencida"
        logger.warning("[web-push] fallo no fatal (status=%s): %s", status, ex)
        return "error"
    except Exception as ex:  # pragma: no cover -- red/parsing, no debe tumbar el llamador
        logger.warning("[web-push] error inesperado: %s", ex)
        return "error"
