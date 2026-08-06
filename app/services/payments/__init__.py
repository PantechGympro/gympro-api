"""
services/payments — Capa de pasarelas de pago de GymPro.

Exporta la interfaz común y la fábrica que resuelve qué credenciales usar
según el contexto del cobro (gimnasio o plataforma).
"""
from .base import (
    PasarelaBase,
    PasarelaError,
    ResultadoCheckout,
    ResultadoPago,
    ResultadoSuscripcion,
)
from .factory import (
    PROVEEDORES,
    PROVEEDORES_INFO,
    construir_pasarela,
    pasarela_de_gimnasio,
    pasarela_de_plataforma,
    proveedores_disponibles,
)

__all__ = [
    "PasarelaBase",
    "PasarelaError",
    "ResultadoCheckout",
    "ResultadoPago",
    "ResultadoSuscripcion",
    "PROVEEDORES",
    "PROVEEDORES_INFO",
    "construir_pasarela",
    "pasarela_de_gimnasio",
    "pasarela_de_plataforma",
    "proveedores_disponibles",
]
