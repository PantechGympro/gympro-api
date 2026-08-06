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

from .base import (
    PasarelaBase, ResultadoCheckout, ResultadoPago, ResultadoSuscripcion, PasarelaError,
)

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

        # ── Cobro recurrente ────────────────────────────────────────────────
        # Cada renovación llega como PAYMENT.SALE.COMPLETED con `billing_agreement_id`
        # apuntando al acuerdo. Ese campo es lo que permite saber a qué gimnasio
        # corresponde el cargo sin haberlo iniciado nosotros.
        if evento == "PAYMENT.SALE.COMPLETED" and recurso.get("billing_agreement_id"):
            monto = None
            importe = recurso.get("amount") or {}
            try:
                monto = float(importe.get("total") or importe.get("value"))
            except (TypeError, ValueError):
                pass
            return ResultadoPago(
                estado="aprobado",
                referencia_externa=recurso.get("billing_agreement_id"),
                referencia_pago=recurso.get("id"),
                monto=monto,
                moneda=importe.get("currency") or importe.get("currency_code"),
                datos=payload,
            )

        # El acuerdo dejó de cobrar: se avisa como rechazo para que el ciclo
        # diario marque la suscripción como vencida.
        if evento in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED",
                      "BILLING.SUBSCRIPTION.EXPIRED", "BILLING.SUBSCRIPTION.PAYMENT.FAILED"):
            return ResultadoPago(estado="rechazado",
                                 referencia_externa=recurso.get("id"),
                                 datos=payload)

        return None

    # ── Cobro recurrente (Subscriptions API v1) ──────────────────────────────
    #
    # PayPal exige tres objetos encadenados para cobrar de forma recurrente:
    #
    #     producto  ->  plan (define precio y periodicidad)  ->  suscripción
    #
    # El producto y el plan se crean una sola vez por combinación de importe y
    # periodicidad; la suscripción es lo que autoriza cada gimnasio. Se usa la
    # cabecera `PayPal-Request-Id` como clave de idempotencia para que reintentar
    # no genere planes duplicados en la cuenta.
    #
    # Documentación: https://developer.paypal.com/docs/api/subscriptions/v1/

    soporta_suscripciones = True

    def _crear_producto(self, nombre: str, referencia: str) -> str:
        cuerpo = {
            "name": (nombre or "Suscripción GymPro")[:127],
            "description": "Plan de la plataforma GymPro",
            "type": "SERVICE",
            "category": "SOFTWARE",
        }
        headers = self._headers()
        # Idempotencia: mismo id de petición => PayPal devuelve el producto ya
        # creado en lugar de duplicarlo.
        headers["PayPal-Request-Id"] = f"prod-{referencia}"[:108]
        try:
            r = requests.post(f"{self._base_url}/v1/catalogs/products",
                              json=cuerpo, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear el producto en PayPal: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("PayPal crear producto %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("PayPal no pudo registrar el producto de la suscripción.")
        return r.json().get("id", "")

    def _crear_plan(self, producto_id: str, monto: float, descripcion: str,
                    dias_periodo: int, referencia: str) -> str:
        # PayPal razona en unidades de tiempo, no en días sueltos. Se traduce el
        # periodo al equivalente más cercano que la API acepta.
        if dias_periodo % 365 == 0:
            unidad, cantidad = "YEAR", dias_periodo // 365
        elif dias_periodo % 30 == 0:
            unidad, cantidad = "MONTH", dias_periodo // 30
        elif dias_periodo % 7 == 0:
            unidad, cantidad = "WEEK", dias_periodo // 7
        else:
            unidad, cantidad = "DAY", dias_periodo

        cuerpo = {
            "product_id": producto_id,
            "name": (descripcion or "Plan GymPro")[:127],
            "status": "ACTIVE",
            "billing_cycles": [{
                "frequency": {"interval_unit": unidad, "interval_count": cantidad},
                "tenure_type": "REGULAR",
                "sequence": 1,
                # 0 = sin fin: se cobra hasta que alguien cancele.
                "total_cycles": 0,
                "pricing_scheme": {
                    "fixed_price": {
                        "currency_code": self.moneda,
                        "value": f"{float(monto):.2f}",
                    },
                },
            }],
            "payment_preferences": {
                "auto_bill_outstanding": True,
                "setup_fee_failure_action": "CONTINUE",
                # Tras 3 intentos fallidos PayPal suspende el acuerdo; el ciclo
                # diario lo detecta al consultar el estado y marca past_due.
                "payment_failure_threshold": 3,
            },
        }
        headers = self._headers()
        headers["PayPal-Request-Id"] = f"plan-{referencia}"[:108]
        try:
            r = requests.post(f"{self._base_url}/v1/billing/plans",
                              json=cuerpo, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear el plan en PayPal: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("PayPal crear plan %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("PayPal no pudo registrar el plan de cobro recurrente.")
        return r.json().get("id", "")

    def crear_suscripcion(self, *, monto: float, descripcion: str,
                          url_retorno: str, url_cancelacion: str,
                          referencia: str, dias_periodo: int = 30,
                          extra: dict | None = None) -> ResultadoSuscripcion:
        producto_id = self._crear_producto(descripcion, referencia)
        plan_id     = self._crear_plan(producto_id, monto, descripcion, dias_periodo, referencia)

        cuerpo = {
            "plan_id": plan_id,
            "custom_id": referencia,
            "application_context": {
                "brand_name": (extra or {}).get("brand_name", "GymPro"),
                "user_action": "SUBSCRIBE_NOW",
                "shipping_preference": "NO_SHIPPING",
                "return_url": url_retorno,
                "cancel_url": url_cancelacion,
            },
        }
        try:
            r = requests.post(f"{self._base_url}/v1/billing/subscriptions",
                              json=cuerpo, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear la suscripción en PayPal: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("PayPal crear suscripcion %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("PayPal no pudo crear el acuerdo de cobro recurrente.")

        data = r.json()
        url = next((l.get("href") for l in data.get("links", [])
                    if l.get("rel") == "approve"), None)
        if not url:
            raise PasarelaError("PayPal no devolvió la URL para autorizar el cargo recurrente.")

        return ResultadoSuscripcion(
            estado=self._estado_suscripcion(data.get("status")),
            referencia_externa=data.get("id", ""),
            url_autorizacion=url,
            monto=float(monto),
            moneda=self.moneda,
            datos=data,
        )

    def consultar_suscripcion(self, referencia_externa: str) -> ResultadoSuscripcion:
        try:
            r = requests.get(f"{self._base_url}/v1/billing/subscriptions/{referencia_externa}",
                             headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo consultar la suscripción en PayPal: {exc}") from exc

        if r.status_code == 404:
            raise PasarelaError("PayPal no encontró el acuerdo de cobro recurrente.")
        if r.status_code != 200:
            logger.error("PayPal consultar suscripcion %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("PayPal no devolvió el estado del acuerdo.")

        data = r.json()
        facturacion = data.get("billing_info") or {}
        importe = (facturacion.get("last_payment") or {}).get("amount") or {}
        monto = None
        try:
            monto = float(importe.get("value"))
        except (TypeError, ValueError):
            pass

        return ResultadoSuscripcion(
            estado=self._estado_suscripcion(data.get("status")),
            referencia_externa=referencia_externa,
            proximo_cobro=facturacion.get("next_billing_time"),
            monto=monto,
            moneda=importe.get("currency_code") or self.moneda,
            datos=data,
        )

    def cancelar_suscripcion(self, referencia_externa: str,
                             motivo: str = "Cancelado por el usuario") -> bool:
        try:
            r = requests.post(
                f"{self._base_url}/v1/billing/subscriptions/{referencia_externa}/cancel",
                json={"reason": (motivo or "Cancelado")[:127]},
                headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo cancelar la suscripción en PayPal: {exc}") from exc

        # 204 = cancelado. 422 suele significar que ya estaba cancelado, que para
        # quien llama es el mismo resultado deseado.
        if r.status_code in (204, 200):
            return True
        if r.status_code == 422:
            logger.info("PayPal: la suscripcion %s ya no estaba activa", referencia_externa)
            return True
        logger.error("PayPal cancelar suscripcion %s: %s", r.status_code, r.text[:400])
        return False

    @staticmethod
    def _estado_suscripcion(status: str | None) -> str:
        return {
            "APPROVAL_PENDING": "pendiente",
            "APPROVED":         "pendiente",   # autorizado pero aún sin activar
            "ACTIVE":           "activo",
            "SUSPENDED":        "pausado",
            "CANCELLED":        "cancelado",
            "EXPIRED":          "vencido",
        }.get((status or "").upper(), "pendiente")

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
