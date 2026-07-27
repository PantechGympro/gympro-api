"""
services/payments/base.py — Contrato común de las pasarelas de pago.

Cada proveedor (PayPal, Mercado Pago) implementa esta interfaz, de modo que las
rutas de checkout no conocen los detalles de cada API. Añadir un proveedor nuevo
consiste en crear una clase que herede de PasarelaBase y registrarla en factory.py.

Flujo unificado:
    1. crear_checkout(...)   -> devuelve {referencia_externa, url_pago}
    2. el pagador completa el pago en la pasarela
    3. confirmar_pago(ref)   -> consulta el estado real y, si procede, captura
    4. interpretar_webhook() -> traduce la notificación asíncrona a un estado
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResultadoCheckout:
    """Salida de crear_checkout: lo que el frontend necesita para redirigir."""
    referencia_externa: str
    url_pago: str
    datos: dict = field(default_factory=dict)


@dataclass
class ResultadoPago:
    """Estado consolidado de un pago consultado o notificado."""
    estado: str                 # pendiente | aprobado | rechazado | cancelado
    referencia_externa: str | None = None
    referencia_pago: str | None = None
    monto: float | None = None
    moneda: str | None = None
    datos: dict = field(default_factory=dict)


class PasarelaError(RuntimeError):
    """Error recuperable de una pasarela; el mensaje es apto para el usuario."""


class PasarelaBase:
    """Interfaz que implementan todos los proveedores de pago."""

    nombre = "base"

    def __init__(self, credenciales: dict, modo: str = "sandbox", moneda: str = "MXN"):
        self.credenciales = credenciales or {}
        self.modo = modo or "sandbox"
        self.moneda = (moneda or "MXN").upper()

    # ── API que deben implementar los proveedores ────────────────────────────

    def verificar_credenciales(self) -> tuple[bool, str]:
        """Comprueba que las credenciales sirven. Retorna (ok, mensaje)."""
        raise NotImplementedError

    def crear_checkout(self, *, monto: float, descripcion: str,
                       url_exito: str, url_cancelacion: str,
                       referencia: str, extra: dict | None = None) -> ResultadoCheckout:
        """Crea la orden/preferencia y devuelve la URL a la que enviar al pagador."""
        raise NotImplementedError

    def confirmar_pago(self, referencia_externa: str) -> ResultadoPago:
        """Consulta el estado del pago y lo captura si el proveedor lo requiere."""
        raise NotImplementedError

    def interpretar_webhook(self, payload: dict, headers: dict) -> ResultadoPago | None:
        """Traduce una notificación asíncrona; None si el evento es irrelevante."""
        raise NotImplementedError

    # ── Utilidades compartidas ───────────────────────────────────────────────

    @property
    def es_sandbox(self) -> bool:
        return self.modo != "live"

    def _requerir(self, *claves: str) -> None:
        faltantes = [c for c in claves if not self.credenciales.get(c)]
        if faltantes:
            raise PasarelaError(
                f"Faltan credenciales de {self.nombre}: {', '.join(faltantes)}"
            )
