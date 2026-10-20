"""
utils/checkin_qr.py -- Token de corta vigencia para el check-in por QR
general del gimnasio (SCRUM: reemplaza QR por-persona + boton de
autocheckin sin verificacion).

Flujo: el recepcionista pide un token nuevo cada ~45-60s y lo muestra como
QR en pantalla (ver recepcionista_routes.py::get_checkin_qr_token); el
MIEMBRO lo escanea con la camara de SU PROPIA app movil y lo envia junto
con su propio JWT (ver user_dashboard.py::checkin_por_qr). La identidad del
check-in la pone el JWT del miembro, igual que cualquier otro endpoint de
/api/user/* -- este token no identifica a nadie, solo certifica "alguien
miro la pantalla de recepcion de ESTE gimnasio hace menos de
QR_TTL_SEGUNDOS segundos".

Vida corta a proposito: es la unica defensa contra que alguien le tome foto
al codigo y lo comparta para que otros hagan check-in "desde su casa"
(justo lo que este cambio busca evitar) -- pasados unos segundos la foto ya
no sirve. No es de un solo uso: varios miembros formados en recepcion deben
poder escanear el MISMO codigo dentro de su ventana de vigencia.
"""
import time
import jwt
from flask import current_app

QR_TTL_SEGUNDOS = 60
_ALGO = "HS256"
_TYP = "checkin_qr"


class QrTokenInvalido(Exception):
    """El token no tiene el formato/firma/tipo de un QR de check-in valido."""


class QrTokenExpirado(Exception):
    """El token era valido pero ya vencio (ver QR_TTL_SEGUNDOS)."""


def emitir_token(gym_id: int) -> dict:
    """Devuelve {"token": str, "expira_en": int} para el gimnasio dado."""
    ahora = int(time.time())
    payload = {
        "typ":    _TYP,
        "gym_id": int(gym_id),
        "iat":    ahora,
        "exp":    ahora + QR_TTL_SEGUNDOS,
    }
    token = jwt.encode(payload, current_app.config["JWT_SECRET_KEY"], algorithm=_ALGO)
    return {"token": token, "expira_en": QR_TTL_SEGUNDOS}


def validar_token(token: str, gym_id: int) -> None:
    """
    No devuelve nada si `token` sirve para hacer check-in en `gym_id`.
    Lanza QrTokenExpirado o QrTokenInvalido en caso contrario.
    """
    try:
        payload = jwt.decode(token, current_app.config["JWT_SECRET_KEY"], algorithms=[_ALGO])
    except jwt.ExpiredSignatureError:
        raise QrTokenExpirado("El codigo QR expiro. Pide uno nuevo en recepcion.")
    except jwt.InvalidTokenError:
        raise QrTokenInvalido("Codigo QR invalido.")

    if payload.get("typ") != _TYP:
        raise QrTokenInvalido("Codigo QR invalido.")
    if int(payload.get("gym_id") or -1) != int(gym_id):
        raise QrTokenInvalido("Este codigo QR es de otro gimnasio.")
