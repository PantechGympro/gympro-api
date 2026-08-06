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


@dataclass
class ResultadoSuscripcion:
    """
    Estado de un acuerdo de cobro recurrente.

    `estado` se normaliza a un vocabulario común porque PayPal y Mercado Pago
    nombran lo mismo de forma distinta (APPROVAL_PENDING / pending, ACTIVE /
    authorized...). Quien consume esta capa no debería tener que saber cuál de
    las dos pasarelas respondió.

        pendiente   creado, falta que el pagador lo autorice
        activo      autorizado y cobrando
        pausado     suspendido temporalmente, sin cobrar
        cancelado   dado de baja, no volverá a cobrar
        vencido     terminó su vigencia

    `proximo_cobro` es la fecha que reporta la pasarela, no una que calculemos:
    la pasarela es la dueña del calendario en este modelo, y adivinarlo llevaría
    a que el panel muestre una fecha y el cargo llegue en otra.
    """
    estado: str
    referencia_externa: str | None = None
    url_autorizacion: str | None = None
    proximo_cobro: str | None = None        # ISO 8601, tal como lo da la pasarela
    monto: float | None = None
    moneda: str | None = None
    datos: dict = field(default_factory=dict)

    @property
    def cobra_sola(self) -> bool:
        """True si el acuerdo está en condiciones de generar cargos."""
        return self.estado == "activo"


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

    # ── Cobro recurrente ─────────────────────────────────────────────────────
    #
    # Modelo de suscripción: el pagador autoriza UNA vez y a partir de ahí la
    # pasarela cobra sola cada periodo. GymPro no guarda tarjetas ni dispara
    # cargos; solo pregunta el estado del acuerdo y registra lo que la pasarela
    # reporta. Es el mismo esquema con el que cobran Netflix o Spotify, y evita
    # que el sistema tenga que cumplir PCI-DSS por almacenar medios de pago.
    #
    # Los proveedores que no lo soporten pueden dejar estos métodos sin
    # implementar: `soporta_suscripciones` avisa a quien los llame.

    soporta_suscripciones = False

    def crear_suscripcion(self, *, monto: float, descripcion: str,
                          url_retorno: str, url_cancelacion: str,
                          referencia: str, dias_periodo: int = 30,
                          extra: dict | None = None) -> ResultadoSuscripcion:
        """Crea el acuerdo y devuelve la URL donde el pagador debe autorizarlo."""
        raise NotImplementedError

    def consultar_suscripcion(self, referencia_externa: str) -> ResultadoSuscripcion:
        """Estado actual del acuerdo según la pasarela."""
        raise NotImplementedError

    def cancelar_suscripcion(self, referencia_externa: str,
                             motivo: str = "Cancelado por el usuario") -> bool:
        """Da de baja el acuerdo. True si la pasarela lo aceptó."""
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
