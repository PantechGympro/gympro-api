"""
services/payments/paypal.py — Proveedor PayPal (REST Orders API v2).

Usa las credenciales del propio gimnasio (client_id / client_secret), por lo que
el dinero llega directamente a su cuenta de PayPal. La plataforma solo orquesta
la creación de la orden y su captura.

Credenciales esperadas:
    { "client_id": "...", "client_secret": "..." }

Endpoints usados:
    POST /v1/oauth2/token           obtener access token
    POST /v2/checkout/orders        crear orden
    GET  /v2/checkout/orders/{id}   consultar estado
    POST /v2/checkout/orders/{id}/capture   capturar el pago aprobado

Documentación: https://developer.paypal.com/docs/api/orders/v2/
"""
from __future__ import annotations

import logging
import requests

from .base import PasarelaBase, ResultadoCheckout, ResultadoPago, PasarelaError

logger = logging.getLogger(__name__)

_API_SANDBOX = "https://api-m.sandbox.paypal.com"
_API_LIVE    = "https://api-m.paypal.com"
_TIMEOUT     = 25


class PayPalPasarela(PasarelaBase):
    nombre = "paypal"

    @property
    def _base_url(self) -> str:
        return _API_SANDBOX if self.es_sandbox else _API_LIVE

    # ── Autenticación ────────────────────────────────────────────────────────

    def _token(self) -> str:
        self._requerir("client_id", "client_secret")
        try:
            r = requests.post(
                f"{self._base_url}/v1/oauth2/token",
                auth=(self.credenciales["client_id"], self.credenciales["client_secret"]),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo contactar a PayPal: {exc}") from exc

        if r.status_code != 200:
            raise PasarelaError(
                "PayPal rechazó las credenciales. Verifica el Client ID y el Secret "
                f"y que correspondan al modo {'sandbox' if self.es_sandbox else 'live'}."
            )
        return r.json().get("access_token", "")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    # ── Interfaz ─────────────────────────────────────────────────────────────

    def verificar_credenciales(self) -> tuple[bool, str]:
        try:
            self._token()
            modo = "sandbox (pruebas)" if self.es_sandbox else "producción"
            return True, f"Conexión con PayPal correcta en modo {modo}."
        except PasarelaError as exc:
            return False, str(exc)

    def crear_checkout(self, *, monto: float, descripcion: str,
                       url_exito: str, url_cancelacion: str,
                       referencia: str, extra: dict | None = None) -> ResultadoCheckout:
        cuerpo = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": referencia,
                "description": (descripcion or "Pago GymPro")[:127],
                "amount": {
                    "currency_code": self.moneda,
                    "value": f"{float(monto):.2f}",
                },
            }],
            "application_context": {
                "brand_name": (extra or {}).get("brand_name", "GymPro"),
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": url_exito,
                "cancel_url": url_cancelacion,
            },
        }
        try:
            r = requests.post(f"{self._base_url}/v2/checkout/orders",
                              json=cuerpo, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear la orden en PayPal: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("PayPal crear orden %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("PayPal no pudo crear la orden de pago.")

        data = r.json()
        url = next((l.get("href") for l in data.get("links", []) if l.get("rel") == "approve"), None)
        if not url:
            raise PasarelaError("PayPal no devolvió la URL de aprobación.")
        return ResultadoCheckout(referencia_externa=data.get("id", ""), url_pago=url, datos=data)

    def confirmar_pago(self, referencia_externa: str) -> ResultadoPago:
        headers = self._headers()
        try:
            r = requests.get(f"{self._base_url}/v2/checkout/orders/{referencia_externa}",
                             headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo consultar la orden en PayPal: {exc}") from exc

        if r.status_code != 200:
            raise PasarelaError("PayPal no encontró la orden indicada.")
        data = r.status_code == 200 and r.json() or {}
        status = (data.get("status") or "").upper()

        # Si el pagador aprobó pero aún no se cobra, se captura ahora.
        if status == "APPROVED":
            try:
                cap = requests.post(
                    f"{self._base_url}/v2/checkout/orders/{referencia_externa}/capture",
                    headers=headers, timeout=_TIMEOUT)
                if cap.status_code in (200, 201):
                    data = cap.json()
                    status = (data.get("status") or "").upper()
            except requests.RequestException as exc:
                raise PasarelaError(f"No se pudo capturar el pago en PayPal: {exc}") from exc

        return self._a_resultado(data, status, referencia_externa)

    def interpretar_webhook(self, payload: dict, headers: dict) -> ResultadoPago | None:
        evento = (payload or {}).get("event_type", "")
        recurso = (payload or {}).get("resource", {}) or {}

        if evento in ("CHECKOUT.ORDER.APPROVED", "PAYMENT.CAPTURE.COMPLETED"):
            ref = recurso.get("id") or (recurso.get("supplementary_data", {})
                                        .get("related_ids", {}).get("order_id"))
            estado = "aprobado" if evento == "PAYMENT.CAPTURE.COMPLETED" else "pendiente"
            monto = None
            try:
                monto = float(recurso.get("amount", {}).get("value"))
            except (TypeError, ValueError):
                pass
            return ResultadoPago(estado=estado, referencia_externa=ref,
                                 referencia_pago=recurso.get("id"), monto=monto,
                                 moneda=recurso.get("amount", {}).get("currency_code"),
                                 datos=payload)

        if evento in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.REVERSED"):
            return ResultadoPago(estado="rechazado", referencia_pago=recurso.get("id"), datos=payload)

        if evento == "PAYMENT.CAPTURE.REFUNDED":
            return ResultadoPago(estado="reembolsado", referencia_pago=recurso.get("id"), datos=payload)

        return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _a_resultado(data: dict, status: str, referencia: str) -> ResultadoPago:
        mapa = {
            "COMPLETED": "aprobado",
            "APPROVED":  "pendiente",
            "CREATED":   "pendiente",
            "SAVED":     "pendiente",
            "VOIDED":    "cancelado",
            "PAYER_ACTION_REQUIRED": "pendiente",
        }
        estado = mapa.get(status, "pendiente")
        monto = moneda = None
        pago_id = None
        try:
            unidad = (data.get("purchase_units") or [{}])[0]
            capturas = (unidad.get("payments", {}) or {}).get("captures") or []
            if capturas:
                pago_id = capturas[0].get("id")
                monto = float(capturas[0].get("amount", {}).get("value"))
                moneda = capturas[0].get("amount", {}).get("currency_code")
            else:
                monto = float(unidad.get("amount", {}).get("value"))
                moneda = unidad.get("amount", {}).get("currency_code")
        except (TypeError, ValueError, IndexError, AttributeError):
            pass
        return ResultadoPago(estado=estado, referencia_externa=referencia,
                             referencia_pago=pago_id, monto=monto, moneda=moneda, datos=data)
