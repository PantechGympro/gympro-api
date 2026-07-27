"""
services/payments/factory.py — Resolución de la pasarela a usar en cada cobro.

Dos orígenes de credenciales según el contexto del pago:

  membresia / producto  → credenciales DEL GIMNASIO (tabla configuracion_pasarela).
                          El dinero llega directo a la cuenta del gimnasio.

  suscripcion           → credenciales DE LA PLATAFORMA (variables de entorno),
                          porque es el gimnasio quien paga su plan a GymPro.

Variables de entorno de la plataforma (solo para el contexto 'suscripcion'):
    PLATAFORMA_PAYPAL_CLIENT_ID / PLATAFORMA_PAYPAL_SECRET
    PLATAFORMA_MP_ACCESS_TOKEN
    PLATAFORMA_PAGOS_MODO      sandbox | live   (default sandbox)
    PLATAFORMA_PAGOS_MONEDA    default MXN
"""
from __future__ import annotations

import os

from app.models.pg.pasarela_pago import ConfiguracionPasarela
from app.utils.crypto import descifrar_dict

from .base import PasarelaBase, PasarelaError
from .paypal import PayPalPasarela
from .mercadopago import MercadoPagoPasarela

PROVEEDORES = {
    "paypal": PayPalPasarela,
    "mercadopago": MercadoPagoPasarela,
}

PROVEEDORES_INFO = {
    "paypal": {
        "nombre": "PayPal",
        "campos": [
            {"clave": "client_id",     "etiqueta": "Client ID",     "secreto": False},
            {"clave": "client_secret", "etiqueta": "Client Secret", "secreto": True},
        ],
        "ayuda": "Obtén estas credenciales en developer.paypal.com → Apps & Credentials.",
    },
    "mercadopago": {
        "nombre": "Mercado Pago",
        "campos": [
            {"clave": "access_token", "etiqueta": "Access Token", "secreto": True},
            {"clave": "public_key",   "etiqueta": "Public Key",   "secreto": False},
        ],
        "ayuda": "Obtén estas credenciales en mercadopago.com.mx/developers → Tus integraciones.",
    },
}


def construir_pasarela(proveedor: str, credenciales: dict,
                       modo: str = "sandbox", moneda: str = "MXN") -> PasarelaBase:
    """Instancia el proveedor indicado con credenciales explícitas."""
    clase = PROVEEDORES.get((proveedor or "").lower())
    if not clase:
        raise PasarelaError(f"Proveedor de pago no soportado: {proveedor}")
    return clase(credenciales=credenciales, modo=modo, moneda=moneda)


def pasarela_de_gimnasio(gym_id: int, proveedor: str) -> tuple[PasarelaBase, ConfiguracionPasarela]:
    """
    Devuelve la pasarela configurada por un gimnasio y su registro de configuración.
    Lanza PasarelaError con un mensaje claro si no está lista para cobrar.
    """
    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=gym_id, proveedor=(proveedor or "").lower()
    ).first()

    if not cfg:
        raise PasarelaError(
            f"El gimnasio no tiene configurado {PROVEEDORES_INFO.get(proveedor, {}).get('nombre', proveedor)}. "
            "Configúralo en Configuración → Pagos."
        )
    if not cfg.activo:
        raise PasarelaError("Este método de pago está desactivado para el gimnasio.")

    credenciales = descifrar_dict(cfg.credenciales)
    if not credenciales:
        raise PasarelaError(
            "Las credenciales guardadas no se pudieron leer. Vuelve a capturarlas "
            "en Configuración → Pagos."
        )

    modo = cfg.modo.value if hasattr(cfg.modo, "value") else cfg.modo
    pasarela = construir_pasarela(proveedor, credenciales, modo=modo, moneda=cfg.moneda or "MXN")
    return pasarela, cfg


def pasarela_de_plataforma(proveedor: str) -> PasarelaBase:
    """Pasarela con las credenciales de GymPro, para cobrar suscripciones SaaS."""
    proveedor = (proveedor or "").lower()
    modo   = os.getenv("PLATAFORMA_PAGOS_MODO", "sandbox")
    moneda = os.getenv("PLATAFORMA_PAGOS_MONEDA", "MXN")

    if proveedor == "paypal":
        credenciales = {
            "client_id":     os.getenv("PLATAFORMA_PAYPAL_CLIENT_ID", ""),
            "client_secret": os.getenv("PLATAFORMA_PAYPAL_SECRET", ""),
        }
        if not credenciales["client_id"]:
            raise PasarelaError(
                "La plataforma no tiene configurado PayPal "
                "(PLATAFORMA_PAYPAL_CLIENT_ID en api/.env)."
            )
    elif proveedor == "mercadopago":
        credenciales = {"access_token": os.getenv("PLATAFORMA_MP_ACCESS_TOKEN", "")}
        if not credenciales["access_token"]:
            raise PasarelaError(
                "La plataforma no tiene configurado Mercado Pago "
                "(PLATAFORMA_MP_ACCESS_TOKEN en api/.env)."
            )
    else:
        raise PasarelaError(f"Proveedor de pago no soportado: {proveedor}")

    return construir_pasarela(proveedor, credenciales, modo=modo, moneda=moneda)


def proveedores_disponibles(gym_id: int) -> list[dict]:
    """Lista de métodos de pago listos para cobrar en un gimnasio (para el frontend)."""
    filas = ConfiguracionPasarela.query.filter_by(id_gimnasio=gym_id, activo=True).all()
    salida = []
    for f in filas:
        prov = f.proveedor.value if hasattr(f.proveedor, "value") else f.proveedor
        if not f.credenciales:
            continue
        salida.append({
            "proveedor": prov,
            "nombre":    PROVEEDORES_INFO.get(prov, {}).get("nombre", prov),
            "modo":      f.modo.value if hasattr(f.modo, "value") else f.modo,
            "moneda":    f.moneda,
        })
    return salida
