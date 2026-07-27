"""
utils/crypto.py — Cifrado simétrico de secretos guardados en base de datos.

Se usa para las credenciales de pasarelas de pago de cada gimnasio
(ver models/pg/pasarela_pago.py). Las credenciales NUNCA se guardan en claro:
se serializan a JSON y se cifran con Fernet (AES-128-CBC + HMAC-SHA256).

Variable de entorno requerida:
    PAYMENTS_ENCRYPTION_KEY   clave Fernet en base64 (44 caracteres)

Generar una clave nueva:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

IMPORTANTE: si se pierde o se cambia la clave, las credenciales ya guardadas
dejan de poder descifrarse y los gimnasios deberán volver a capturarlas.
"""
from __future__ import annotations

import json
import os
import logging

logger = logging.getLogger(__name__)

_ENV_KEY = "PAYMENTS_ENCRYPTION_KEY"


class CryptoNoConfigurado(RuntimeError):
    """La clave de cifrado no está configurada o es inválida."""


def _fernet():
    from cryptography.fernet import Fernet  # import diferido: solo si se usa

    raw = os.getenv(_ENV_KEY, "").strip()
    if not raw:
        raise CryptoNoConfigurado(
            f"{_ENV_KEY} no está configurada. Genera una clave con: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" y agrégala a api/.env"
        )
    try:
        return Fernet(raw.encode())
    except Exception as exc:
        raise CryptoNoConfigurado(f"{_ENV_KEY} inválida: {exc}") from exc


def cifrar_dict(datos: dict) -> str:
    """Serializa un dict a JSON y lo devuelve cifrado en texto (base64)."""
    payload = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(payload).decode("utf-8")


def descifrar_dict(token: str | None) -> dict:
    """
    Descifra un texto producido por cifrar_dict y devuelve el dict original.
    Devuelve {} si el token es None o vacío.
    """
    if not token:
        return {}
    try:
        claro = _fernet().decrypt(token.encode("utf-8"))
        return json.loads(claro.decode("utf-8"))
    except CryptoNoConfigurado:
        raise
    except Exception as exc:
        # Token corrupto o cifrado con otra clave: no reventar la petición
        logger.error("No se pudieron descifrar credenciales: %s", exc)
        return {}


def enmascarar(valor: str | None, visibles: int = 4) -> str | None:
    """
    Devuelve una pista del secreto para mostrar en la interfaz sin exponerlo:
    'sk_live_9f3a2b7c'  ->  '············b7c'  (solo los últimos caracteres).
    """
    if not valor:
        return None
    valor = str(valor)
    if len(valor) <= visibles:
        return "·" * len(valor)
    return "·" * 8 + valor[-visibles:]


def crypto_disponible() -> bool:
    """True si la clave de cifrado está configurada y es válida."""
    try:
        _fernet()
        return True
    except Exception:
        return False
