"""
services/payments/mercadopago.py — Proveedor Mercado Pago (Checkout Pro).

Usa el access token del propio gimnasio, de modo que el dinero se acredita
directamente en su cuenta de Mercado Pago. La plataforma solo crea la
preferencia de pago y consulta el resultado.

Credenciales esperadas:
    { "access_token": "APP_USR-...", "public_key": "APP_USR-..." }
El access token de prueba comienza con TEST- y el productivo con APP_USR-.

Endpoints usados:
    POST /checkout/preferences   crear preferencia (devuelve init_point)
    GET  /v1/payments/{id}       consultar un pago
    GET  /merchant_orders/{id}   consultar una orden

Documentación: https://www.mercadopago.com.mx/developers/es/reference
"""
from __future__ import annotations

import logging
import requests

from .base import (
    PasarelaBase, ResultadoCheckout, ResultadoPago, ResultadoSuscripcion, PasarelaError,
)

logger = logging.getLogger(__name__)

_API = "https://api.mercadopago.com"
_TIMEOUT = 25

# Mercado Pago devuelve estados propios; se traducen al vocabulario interno.
_MAPA_ESTADOS = {
    "approved":     "aprobado",
    "authorized":   "aprobado",
    "pending":      "pendiente",
    "in_process":   "pendiente",
    "in_mediation": "pendiente",
    "rejected":     "rechazado",
    "cancelled":    "cancelado",
    "refunded":     "reembolsado",
    "charged_back": "reembolsado",
}


class MercadoPagoPasarela(PasarelaBase):
    nombre = "mercadopago"

    def _headers(self) -> dict:
        self._requerir("access_token")
        return {
            "Authorization": f"Bearer {self.credenciales['access_token']}",
            "Content-Type": "application/json",
        }

    # ── Interfaz ─────────────────────────────────────────────────────────────

    def verificar_credenciales(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{_API}/users/me", headers=self._headers(), timeout=_TIMEOUT)
        except PasarelaError as exc:
            return False, str(exc)
        except requests.RequestException as exc:
            return False, f"No se pudo contactar a Mercado Pago: {exc}"

        if r.status_code == 200:
            data = r.json()
            nick = data.get("nickname") or data.get("email") or "cuenta"
            token = str(self.credenciales.get("access_token", ""))
            # Nota: las credenciales de prueba actuales de Mercado Pago (p. ej. en
            # México) también empiezan con APP_USR-, igual que las productivas; el
            # prefijo TEST- solo aparece en integraciones antiguas. Por eso no se
            # puede inferir el entorno del token: solo se avisa cuando es seguro.
            aviso = ""
            if token.startswith("TEST-") and not self.es_sandbox:
                aviso = (" Atención: el token es de prueba (TEST-) pero el modo "
                         "está en producción.")
            modo_cfg = "pruebas" if self.es_sandbox else "producción"
            return True, (f"Conexión correcta con Mercado Pago ({nick}). "
                          f"Configurado en modo {modo_cfg}.{aviso}")

        if r.status_code in (401, 403):
            return False, "Mercado Pago rechazó el Access Token. Verifica que sea correcto y vigente."
        return False, f"Mercado Pago respondió con código {r.status_code}."

    def crear_checkout(self, *, monto: float, descripcion: str,
                       url_exito: str, url_cancelacion: str,
                       referencia: str, extra: dict | None = None) -> ResultadoCheckout:
        extra = extra or {}
        cuerpo = {
            "items": [{
                "title": (descripcion or "Pago GymPro")[:250],
                "quantity": 1,
                "unit_price": round(float(monto), 2),
                "currency_id": self.moneda,
            }],
            "external_reference": referencia,
            "back_urls": {
                "success": url_exito,
                "failure": url_cancelacion,
                "pending": url_exito,
            },
            "statement_descriptor": extra.get("brand_name", "GymPro")[:22],
        }
        # auto_return solo es válido con URLs públicas (no localhost)
        if not str(url_exito).startswith("http://localhost"):
            cuerpo["auto_return"] = "approved"
        if extra.get("notification_url"):
            cuerpo["notification_url"] = extra["notification_url"]
        if extra.get("email_pagador"):
            cuerpo["payer"] = {"email": extra["email_pagador"]}

        try:
            r = requests.post(f"{_API}/checkout/preferences", json=cuerpo,
                              headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear la preferencia en Mercado Pago: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("MP crear preferencia %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("Mercado Pago no pudo crear el cobro. Revisa las credenciales y la moneda.")

        data = r.json()
        # init_point es la URL correcta en ambos entornos: con credenciales de
        # prueba (las de "Credenciales de prueba" del panel) ya apunta al entorno
        # de test, sin mover dinero real. sandbox_init_point solo se usa como
        # respaldo para integraciones antiguas que aún lo devuelven.
        url = data.get("init_point") or data.get("sandbox_init_point")
        if not url:
            raise PasarelaError("Mercado Pago no devolvió la URL de pago.")
        return ResultadoCheckout(referencia_externa=str(data.get("id", "")), url_pago=url, datos=data)

    def confirmar_pago(self, referencia_externa: str) -> ResultadoPago:
        """
        Busca el pago asociado a la preferencia. Mercado Pago no permite consultar
        una preferencia por estado, así que se buscan los pagos por referencia externa.
        """
        try:
            r = requests.get(f"{_API}/v1/payments/search",
                             params={"external_reference": referencia_externa},
                             headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo consultar el pago en Mercado Pago: {exc}") from exc

        if r.status_code != 200:
            return ResultadoPago(estado="pendiente", referencia_externa=referencia_externa)

        resultados = (r.json() or {}).get("results") or []
        if not resultados:
            return ResultadoPago(estado="pendiente", referencia_externa=referencia_externa)

        # Se prioriza un pago aprobado si existe más de un intento
        pago = next((p for p in resultados if p.get("status") == "approved"), resultados[0])
        return self._a_resultado(pago, referencia_externa)

    def interpretar_webhook(self, payload: dict, headers: dict) -> ResultadoPago | None:
        tipo = (payload or {}).get("type") or (payload or {}).get("topic")

        # Cobro recurrente: MP avisa con el tipo `subscription_preapproval`
        # cuando el acuerdo cambia de estado (autorizado, pausado, cancelado).
        # No trae el importe, así que se consulta el acuerdo para saber cómo
        # quedó y se traduce a aprobado/rechazado.
        if tipo in ("subscription_preapproval", "preapproval"):
            preapproval_id = ((payload or {}).get("data") or {}).get("id") or (payload or {}).get("id")
            if not preapproval_id:
                return None
            try:
                estado = self.consultar_suscripcion(str(preapproval_id))
            except PasarelaError as exc:
                logger.error("MP webhook preapproval %s: %s", preapproval_id, exc)
                return None
            return ResultadoPago(
                estado="aprobado" if estado.cobra_sola else "rechazado",
                referencia_externa=str(preapproval_id),
                monto=estado.monto,
                moneda=estado.moneda,
                datos=estado.datos,
            )

        if tipo not in ("payment", "merchant_order"):
            return None

        dato_id = ((payload or {}).get("data") or {}).get("id") or (payload or {}).get("id")
        if not dato_id:
            return None

        if tipo == "payment":
            try:
                r = requests.get(f"{_API}/v1/payments/{dato_id}",
                                 headers=self._headers(), timeout=_TIMEOUT)
            except requests.RequestException as exc:
                logger.error("MP webhook consulta pago: %s", exc)
                return None
            if r.status_code != 200:
                return None
            return self._a_resultado(r.json(), None)

        return None

    # ── Cobro recurrente (Preapproval) ───────────────────────────────────────
    #
    # Mercado Pago lo resuelve en un solo objeto: el "preapproval" lleva dentro
    # el importe y la periodicidad, sin el encadenado producto/plan que exige
    # PayPal. El pagador autoriza en `init_point` y a partir de ahí MP cobra solo.
    #
    # Documentación: https://www.mercadopago.com.mx/developers/es/reference/subscriptions/_preapproval/post

    soporta_suscripciones = True

    def crear_suscripcion(self, *, monto: float, descripcion: str,
                          url_retorno: str, url_cancelacion: str,
                          referencia: str, dias_periodo: int = 30,
                          extra: dict | None = None) -> ResultadoSuscripcion:
        # MP solo admite "days" o "months" como unidad. 30 días es exactamente
        # un mes para su calendario de cobro, y expresarlo en meses evita el
        # desfase que acumularía contar de 30 en 30 a lo largo del año.
        if dias_periodo % 30 == 0:
            frecuencia, tipo_frecuencia = dias_periodo // 30, "months"
        else:
            frecuencia, tipo_frecuencia = dias_periodo, "days"

        cuerpo = {
            "reason": (descripcion or "Suscripción GymPro")[:255],
            "external_reference": referencia,
            "payer_email": (extra or {}).get("payer_email") or "",
            "back_url": url_retorno,
            "status": "pending",
            "auto_recurring": {
                "frequency": frecuencia,
                "frequency_type": tipo_frecuencia,
                "transaction_amount": round(float(monto), 2),
                "currency_id": self.moneda,
            },
        }
        # MP rechaza el campo si va vacío, así que se omite cuando no se conoce
        # el correo del pagador; en ese caso lo pedirá durante la autorización.
        if not cuerpo["payer_email"]:
            cuerpo.pop("payer_email")

        try:
            r = requests.post(f"{_API}/preapproval", json=cuerpo,
                              headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo crear la suscripción en Mercado Pago: {exc}") from exc

        if r.status_code not in (200, 201):
            logger.error("MP crear preapproval %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("Mercado Pago no pudo crear el acuerdo de cobro recurrente.")

        data = r.json()
        url = data.get("init_point") or data.get("sandbox_init_point")
        if not url:
            raise PasarelaError("Mercado Pago no devolvió la URL para autorizar el cargo recurrente.")

        return ResultadoSuscripcion(
            estado=self._estado_suscripcion(data.get("status")),
            referencia_externa=str(data.get("id") or ""),
            url_autorizacion=url,
            proximo_cobro=(data.get("auto_recurring") or {}).get("start_date"),
            monto=float(monto),
            moneda=self.moneda,
            datos=data,
        )

    def consultar_suscripcion(self, referencia_externa: str) -> ResultadoSuscripcion:
        try:
            r = requests.get(f"{_API}/preapproval/{referencia_externa}",
                             headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo consultar la suscripción en Mercado Pago: {exc}") from exc

        if r.status_code == 404:
            raise PasarelaError("Mercado Pago no encontró el acuerdo de cobro recurrente.")
        if r.status_code != 200:
            logger.error("MP consultar preapproval %s: %s", r.status_code, r.text[:400])
            raise PasarelaError("Mercado Pago no devolvió el estado del acuerdo.")

        data = r.json()
        recurrente = data.get("auto_recurring") or {}
        monto = None
        try:
            monto = float(recurrente.get("transaction_amount"))
        except (TypeError, ValueError):
            pass

        return ResultadoSuscripcion(
            estado=self._estado_suscripcion(data.get("status")),
            referencia_externa=referencia_externa,
            # MP nombra `next_payment_date` a lo que PayPal llama next_billing_time.
            proximo_cobro=data.get("next_payment_date") or recurrente.get("end_date"),
            monto=monto,
            moneda=recurrente.get("currency_id") or self.moneda,
            datos=data,
        )

    def cancelar_suscripcion(self, referencia_externa: str,
                             motivo: str = "Cancelado por el usuario") -> bool:
        try:
            r = requests.put(f"{_API}/preapproval/{referencia_externa}",
                             json={"status": "cancelled"},
                             headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise PasarelaError(f"No se pudo cancelar la suscripción en Mercado Pago: {exc}") from exc

        if r.status_code == 200:
            return True
        logger.error("MP cancelar preapproval %s: %s", r.status_code, r.text[:400])
        return False

    @staticmethod
    def _estado_suscripcion(status: str | None) -> str:
        return {
            "pending":   "pendiente",
            "authorized": "activo",
            "paused":    "pausado",
            "cancelled": "cancelado",
            "finished":  "vencido",
        }.get((status or "").lower(), "pendiente")

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _a_resultado(pago: dict, referencia_externa: str | None) -> ResultadoPago:
        estado = _MAPA_ESTADOS.get((pago.get("status") or "").lower(), "pendiente")
        monto = None
        try:
            monto = float(pago.get("transaction_amount"))
        except (TypeError, ValueError):
            pass
        return ResultadoPago(
            estado=estado,
            referencia_externa=referencia_externa or pago.get("external_reference"),
            referencia_pago=str(pago.get("id")) if pago.get("id") else None,
            monto=monto,
            moneda=pago.get("currency_id"),
            datos=pago,
        )
