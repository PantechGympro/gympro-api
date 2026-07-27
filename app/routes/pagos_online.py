"""
routes/pagos_online.py — Checkout y webhooks de PayPal / Mercado Pago.

Cubre los tres contextos de cobro de GymPro:

  membresia    el miembro paga su membresía  -> cuenta del GIMNASIO
  producto     venta de productos (POS)      -> cuenta del GIMNASIO
  suscripcion  el gimnasio paga su plan SaaS -> cuenta de la PLATAFORMA

Endpoints:
  GET  /api/pagos/metodos                 métodos disponibles para el gimnasio
  POST /api/pagos/checkout                crea el cobro y devuelve la URL de pago
  GET  /api/pagos/estado/<id>             consulta y confirma una transacción
  POST /api/pagos/webhook/<proveedor>     notificación asíncrona de la pasarela

Los webhooks están exentos de JWT (ver utils/tenant.py): la pasarela no envía
token. Se identifican por la transacción referenciada en el payload.
"""
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from app.extensions import db
from app.models.pg.pasarela_pago import TransaccionPago
from app.utils.tenant import require_tenant
from app.services.payments import (
    PasarelaError, pasarela_de_gimnasio, pasarela_de_plataforma, proveedores_disponibles,
)

logger = logging.getLogger(__name__)

pagos_online_bp = Blueprint("pagos_online", __name__, url_prefix="/api/pagos")

_CONTEXTOS = ("membresia", "producto", "suscripcion")


def _url_base_front() -> str:
    """URL pública del frontend, usada para las páginas de retorno."""
    return os.getenv("FRONTEND_URL", "http://localhost:8080").rstrip("/")


def _url_base_api() -> str:
    """URL pública de la API, usada para las notificaciones (webhooks)."""
    return os.getenv("PUBLIC_API_URL", "").rstrip("/")


# ── Métodos disponibles ──────────────────────────────────────────────────────

@pagos_online_bp.route("/metodos", methods=["GET"])
@jwt_required()
@require_tenant
def metodos_disponibles():
    """Métodos de pago en línea activos en el gimnasio actual."""
    return jsonify({"metodos": proveedores_disponibles(g.tenant_id)}), 200


@pagos_online_bp.route("/metodos-plataforma", methods=["GET"])
@jwt_required()
def metodos_plataforma():
    """
    Métodos con los que la PLATAFORMA puede cobrar la suscripción SaaS.
    Se derivan de las variables de entorno, no de la configuración del gimnasio.
    """
    modo = os.getenv("PLATAFORMA_PAGOS_MODO", "sandbox")
    moneda = os.getenv("PLATAFORMA_PAGOS_MONEDA", "MXN")
    metodos = []
    if os.getenv("PLATAFORMA_PAYPAL_CLIENT_ID"):
        metodos.append({"proveedor": "paypal", "nombre": "PayPal",
                        "modo": modo, "moneda": moneda})
    if os.getenv("PLATAFORMA_MP_ACCESS_TOKEN"):
        metodos.append({"proveedor": "mercadopago", "nombre": "Mercado Pago",
                        "modo": modo, "moneda": moneda})
    return jsonify({"metodos": metodos}), 200


# ── Crear checkout ───────────────────────────────────────────────────────────

@pagos_online_bp.route("/checkout", methods=["POST"])
@jwt_required()
@require_tenant
def crear_checkout():
    """
    Crea un cobro en línea y devuelve la URL a la que redirigir al pagador.

    Body JSON:
      {
        "proveedor": "paypal" | "mercadopago",
        "contexto":  "membresia" | "producto" | "suscripcion",
        "monto": 499.00,
        "descripcion": "Membresía mensual",
        "referencia_local": "id del miembro / venta / suscripción"
      }
    """
    data = request.get_json() or {}
    proveedor = (data.get("proveedor") or "").lower()
    contexto  = (data.get("contexto") or "").lower()

    if contexto not in _CONTEXTOS:
        return jsonify({"msg": f"Contexto inválido. Usa uno de: {', '.join(_CONTEXTOS)}"}), 400

    try:
        monto = round(float(data.get("monto") or 0), 2)
    except (TypeError, ValueError):
        return jsonify({"msg": "Monto inválido"}), 400
    if monto <= 0:
        return jsonify({"msg": "El monto debe ser mayor que cero"}), 400

    descripcion = (data.get("descripcion") or "Pago GymPro").strip()
    referencia_local = str(data.get("referencia_local") or "") or None

    # Resolver de dónde salen las credenciales según el contexto
    try:
        if contexto == "suscripcion":
            pasarela = pasarela_de_plataforma(proveedor)
            moneda = pasarela.moneda
        else:
            pasarela, cfg = pasarela_de_gimnasio(g.tenant_id, proveedor)
            moneda = cfg.moneda or pasarela.moneda
    except PasarelaError as exc:
        return jsonify({"msg": str(exc)}), 400

    # Registrar la transacción antes de llamar a la pasarela
    try:
        id_usuario = int(get_jwt_identity())
    except (TypeError, ValueError):
        id_usuario = None

    tx = TransaccionPago(
        id_gimnasio=g.tenant_id,
        proveedor=proveedor,
        contexto=contexto,
        estado="pendiente",
        monto=Decimal(str(monto)),
        moneda=moneda,
        descripcion=descripcion[:255],
        referencia_local=referencia_local,
        id_usuario=id_usuario,
        metadatos={"origen": data.get("origen") or "web"},
    )
    db.session.add(tx)
    db.session.flush()   # obtiene tx.id sin cerrar la transacción

    front = _url_base_front()
    url_exito      = f"{front}/pago/exito?tx={tx.id}"
    url_cancelacion = f"{front}/pago/cancelado?tx={tx.id}"

    extra = {"brand_name": "GymPro"}
    api_publica = _url_base_api()
    if api_publica:
        extra["notification_url"] = f"{api_publica}/api/pagos/webhook/{proveedor}"
    if data.get("email_pagador"):
        extra["email_pagador"] = data["email_pagador"]

    try:
        resultado = pasarela.crear_checkout(
            monto=monto, descripcion=descripcion,
            url_exito=url_exito, url_cancelacion=url_cancelacion,
            referencia=str(tx.id), extra=extra,
        )
    except PasarelaError as exc:
        db.session.rollback()
        logger.warning("Checkout %s falló: %s", proveedor, exc)
        return jsonify({"msg": str(exc)}), 400
    except Exception as exc:
        db.session.rollback()
        logger.exception("Error inesperado creando checkout")
        return jsonify({"msg": f"No se pudo iniciar el pago: {exc}"}), 500

    tx.referencia_externa = resultado.referencia_externa
    db.session.commit()

    return jsonify({
        "transaccion_id": tx.id,
        "url_pago":       resultado.url_pago,
        "proveedor":      proveedor,
        "monto":          monto,
        "moneda":         moneda,
    }), 201


# ── Consultar / confirmar estado ─────────────────────────────────────────────

@pagos_online_bp.route("/estado/<int:tx_id>", methods=["GET"])
@jwt_required()
@require_tenant
def estado_transaccion(tx_id):
    """
    Consulta el estado real del pago en la pasarela y actualiza la transacción.
    Se llama desde la página de retorno tras completar el checkout.
    """
    tx = TransaccionPago.query.filter_by(id=tx_id, id_gimnasio=g.tenant_id).first()
    if not tx:
        return jsonify({"msg": "Transacción no encontrada"}), 404

    estado_actual = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
    if estado_actual in ("aprobado", "reembolsado"):
        return jsonify({"transaccion": tx.to_dict()}), 200

    proveedor = tx.proveedor.value if hasattr(tx.proveedor, "value") else tx.proveedor
    contexto  = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto

    try:
        pasarela = (pasarela_de_plataforma(proveedor) if contexto == "suscripcion"
                    else pasarela_de_gimnasio(tx.id_gimnasio, proveedor)[0])
        resultado = pasarela.confirmar_pago(tx.referencia_externa)
    except PasarelaError as exc:
        return jsonify({"msg": str(exc), "transaccion": tx.to_dict()}), 400
    except Exception as exc:
        logger.exception("Error consultando pago")
        return jsonify({"msg": f"No se pudo consultar el pago: {exc}"}), 500

    _aplicar_resultado(tx, resultado)
    db.session.commit()
    return jsonify({"transaccion": tx.to_dict()}), 200


# ── Webhooks ─────────────────────────────────────────────────────────────────

@pagos_online_bp.route("/webhook/<proveedor>", methods=["POST"])
def webhook(proveedor):
    """
    Recibe notificaciones asíncronas de la pasarela. Sin JWT: la petición
    proviene del proveedor, no del navegador del usuario.
    """
    proveedor = (proveedor or "").lower()
    payload = request.get_json(silent=True) or {}
    logger.info("Webhook %s recibido: %s", proveedor, str(payload)[:300])

    # La transacción se localiza por la referencia externa del proveedor
    tx = _localizar_transaccion(proveedor, payload)
    if not tx:
        # Se responde 200 para que la pasarela no reintente indefinidamente
        return jsonify({"msg": "Sin transacción asociada"}), 200

    contexto = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto
    try:
        pasarela = (pasarela_de_plataforma(proveedor) if contexto == "suscripcion"
                    else pasarela_de_gimnasio(tx.id_gimnasio, proveedor)[0])
        resultado = pasarela.interpretar_webhook(payload, dict(request.headers))
    except Exception as exc:
        logger.error("Webhook %s no procesado: %s", proveedor, exc)
        return jsonify({"msg": "Error procesando webhook"}), 200

    if resultado:
        _aplicar_resultado(tx, resultado)
        db.session.commit()

    return jsonify({"msg": "ok"}), 200


# ── Helpers ──────────────────────────────────────────────────────────────────

def _localizar_transaccion(proveedor: str, payload: dict) -> TransaccionPago | None:
    """Encuentra la transacción local referida por el payload del webhook."""
    recurso = (payload or {}).get("resource", {}) or {}
    candidatos = [
        recurso.get("id"),
        (recurso.get("supplementary_data", {}) or {}).get("related_ids", {}).get("order_id"),
        ((payload or {}).get("data") or {}).get("id"),
        (payload or {}).get("id"),
    ]
    # PayPal envía reference_id = id de nuestra transacción
    ref_local = None
    try:
        ref_local = (recurso.get("purchase_units") or [{}])[0].get("reference_id")
    except (IndexError, AttributeError):
        pass
    if ref_local and str(ref_local).isdigit():
        tx = TransaccionPago.query.get(int(ref_local))
        if tx:
            return tx
    for c in candidatos:
        if not c:
            continue
        tx = TransaccionPago.query.filter_by(referencia_externa=str(c)).first()
        if tx:
            return tx
    # Mercado Pago: external_reference lleva el id de nuestra transacción
    ext = (payload or {}).get("external_reference")
    if ext and str(ext).isdigit():
        return TransaccionPago.query.get(int(ext))
    return None


def _aplicar_resultado(tx: TransaccionPago, resultado) -> None:
    """Vuelca el resultado de la pasarela sobre la transacción local."""
    if not resultado or not resultado.estado:
        return
    anterior = tx.estado.value if hasattr(tx.estado, "value") else tx.estado
    tx.estado = resultado.estado
    if resultado.referencia_pago:
        tx.referencia_pago = str(resultado.referencia_pago)[:120]
    if resultado.estado == "aprobado" and not tx.fecha_pago:
        tx.fecha_pago = datetime.now(timezone.utc)

    meta = dict(tx.metadatos or {})
    meta["ultimo_estado_pasarela"] = resultado.datos.get("status") if resultado.datos else None
    tx.metadatos = meta

    if anterior != "aprobado" and resultado.estado == "aprobado":
        _registrar_pago_aprobado(tx)


def _registrar_pago_aprobado(tx: TransaccionPago) -> None:
    """
    Efecto de negocio al aprobarse un pago. Se aísla aquí para que el resto del
    flujo no dependa de los módulos concretos de membresías, ventas o suscripciones.
    """
    contexto = tx.contexto.value if hasattr(tx.contexto, "value") else tx.contexto
    try:
        if contexto == "suscripcion" and tx.referencia_local:
            from app.models.pg.suscripcion import Suscripcion
            sus = Suscripcion.query.get(int(tx.referencia_local))
            if sus:
                sus.estado = "active"
        # Membresías y productos viven en MongoDB; el registro operativo lo
        # realizan sus propios módulos al consultar el estado de la transacción.
    except Exception as exc:
        logger.error("No se pudo aplicar el efecto del pago %s: %s", tx.id, exc)
