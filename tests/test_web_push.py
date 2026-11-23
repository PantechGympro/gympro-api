"""app/utils/web_push.py -- envío de Web Push a una suscripción del
navegador. Pruebas unitarias (sin red real: se mockea pywebpush.webpush)."""
from types import SimpleNamespace
from unittest.mock import patch

from app.utils.web_push import enviar_web_push


class _AppFalso:
    """Evita depender de variables de entorno VAPID_* para estas pruebas:
    construye un config mínimo con las llaves que necesita enviar_web_push."""
    def __init__(self, **config):
        self.config = config


SUBSCRIPTION = {"endpoint": "https://fcm.example/x", "keys": {"p256dh": "a", "auth": "b"}}
PAYLOAD = {"title": "Hola", "body": "Mensaje de prueba"}


def test_sin_llaves_vapid_configuradas_devuelve_error_sin_llamar_a_pywebpush():
    app = _AppFalso()  # sin VAPID_PRIVATE_KEY ni VAPID_PUBLIC_KEY
    with patch("pywebpush.webpush") as mock_webpush:
        resultado = enviar_web_push(app, SUBSCRIPTION, PAYLOAD)
    assert resultado == "error"
    mock_webpush.assert_not_called()


def test_envio_exitoso_devuelve_ok():
    app = _AppFalso(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv", VAPID_CLAIMS_EMAIL="soporte@gympro.lat")
    with patch("pywebpush.webpush") as mock_webpush:
        resultado = enviar_web_push(app, SUBSCRIPTION, PAYLOAD)
    assert resultado == "ok"
    mock_webpush.assert_called_once()
    _, kwargs = mock_webpush.call_args
    assert kwargs["subscription_info"] == SUBSCRIPTION
    assert kwargs["vapid_private_key"] == "priv"
    assert kwargs["vapid_claims"] == {"sub": "mailto:soporte@gympro.lat"}


def test_suscripcion_vencida_404_o_410_devuelve_vencida():
    from pywebpush import WebPushException
    app = _AppFalso(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
    for status in (404, 410):
        with patch("pywebpush.webpush", side_effect=WebPushException("x", response=SimpleNamespace(status_code=status))):
            assert enviar_web_push(app, SUBSCRIPTION, PAYLOAD) == "vencida"


def test_fallo_transitorio_no_borra_la_suscripcion_devuelve_error():
    from pywebpush import WebPushException
    app = _AppFalso(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
    with patch("pywebpush.webpush", side_effect=WebPushException("x", response=SimpleNamespace(status_code=500))):
        assert enviar_web_push(app, SUBSCRIPTION, PAYLOAD) == "error"


def test_excepcion_inesperada_no_se_propaga():
    app = _AppFalso(VAPID_PUBLIC_KEY="pub", VAPID_PRIVATE_KEY="priv")
    with patch("pywebpush.webpush", side_effect=RuntimeError("red caída")):
        assert enviar_web_push(app, SUBSCRIPTION, PAYLOAD) == "error"
