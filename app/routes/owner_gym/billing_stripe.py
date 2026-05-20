"""
routes/billing_stripe.py -- Integracion con Stripe (Sprint 3 / US13).

Modo local (STRIPE_ENABLED=false en .env): los endpoints de checkout y portal
devuelven respuestas mock para poder desarrollar y probar el flujo completo
sin incurrir en costos reales.

Modo produccion (STRIPE_ENABLED=true + claves reales en .env):
  - /api/billing/stripe/checkout  -> crea Stripe Checkout Session
  - /api/billing/stripe/portal    -> crea Stripe Billing Portal Session
  - /api/billing/webhook          -> recibe eventos de Stripe y sincroniza PG

Variables de entorno requeridas en produccion:
  STRIPE_SECRET_KEY      sk_live_... o sk_test_...
  STRIPE_WEBHOOK_SECRET  whsec_...  (del dashboard de Stripe)
  STRIPE_ENABLED         true

El webhook ya esta exento de autenticacion JWT en utils/tenant.py.
"""
import os
import logging
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.gimnasio            import Gimnasio
from app.models.pg.plan_suscripcion    import PlanSuscripcion
from app.models.pg.suscripcion         import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.utils.tenant import require_tenant

logger = logging.getLogger(__name__)

billing_stripe_bp = Blueprint("billing_stripe", __name__, url_prefix="/api/billing/stripe")

_STRIPE_ENABLED = os.getenv("STRIPE_ENABLED", "false").lower() == "true"


def _get_stripe():
    """
    Importa y configura stripe solo si STRIPE_ENABLED=true.
    Lanza RuntimeError descriptivo si las claves no estan configuradas.
    """
    if not _STRIPE_ENABLED:
        raise RuntimeError("Stripe no habilitado (STRIPE_ENABLED=false)")
    import stripe as _stripe
    _stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not _stripe.api_key:
        raise RuntimeError("STRIPE_SECRET_KEY no configurado")
    return _stripe


def _es_admin(claims: dict) -> bool:
    return claims.get("role") in ("owner_gym", "superadmin")


# ─────────────────────────────────────────────────────────────────────────────
# CHECKOUT SESSION
# ─────────────────────────────────────────────────────────────────────────────

@billing_stripe_bp.route("/checkout", methods=["POST"])
@jwt_required()
@require_tenant
def crear_checkout_session():
    """
    Crea una Stripe Checkout Session para el plan indicado.

    Modo local (STRIPE_ENABLED=false):
        Devuelve un objeto mock con url simulada para no romper el frontend.

    Body JSON: { "id_plan": 1, "success_url": "...", "cancel_url": "..." }
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede iniciar el checkout"}), 403

    gym_id = g.tenant_id
    data   = request.get_json() or {}
    id_plan = data.get("id_plan")
    if not id_plan:
        return jsonify({"msg": "id_plan es requerido"}), 400

    plan = PlanSuscripcion.query.filter_by(id=id_plan, activo=True).first()
    if not plan:
        return jsonify({"msg": "Plan no encontrado"}), 404

    gym = Gimnasio.query.get(gym_id)
    if not gym:
        return jsonify({"msg": "Gimnasio no encontrado"}), 404

    # Modo local: mock response
    if not _STRIPE_ENABLED:
        return jsonify({
            "modo":        "local",
            "msg":         "Stripe en modo local. Usa POST /api/billing/facturas para registrar pagos manualmente.",
            "checkout_url": None,
            "session_id":   "mock_session_local",
            "plan":         plan.to_dict(),
        }), 200

    # Modo produccion: Stripe real
    try:
        stripe = _get_stripe()
        success_url = data.get("success_url", "http://localhost:3000/dashboard?checkout=success")
        cancel_url  = data.get("cancel_url",  "http://localhost:3000/dashboard?checkout=cancel")

        # Crear o reutilizar Stripe Customer
        if not gym.stripe_customer_id:
            customer = stripe.Customer.create(
                email=gym.email_contacto,
                name=gym.nombre,
                metadata={"gym_id": str(gym_id)},
            )
            gym.stripe_customer_id = customer["id"]
            db.session.commit()

        session = stripe.checkout.Session.create(
            customer   = gym.stripe_customer_id,
            mode       = "subscription",
            line_items = [{"price": plan.stripe_price_id, "quantity": 1}],
            success_url= success_url,
            cancel_url = cancel_url,
            metadata   = {"gym_id": str(gym_id), "plan_id": str(plan.id)},
        )
        return jsonify({"checkout_url": session.url, "session_id": session.id}), 200

    except Exception as e:
        logger.exception("Stripe checkout error")
        return jsonify({"msg": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# BILLING PORTAL
# ─────────────────────────────────────────────────────────────────────────────

@billing_stripe_bp.route("/portal", methods=["POST"])
@jwt_required()
@require_tenant
def crear_portal_session():
    """
    Genera un link al Billing Portal de Stripe para que el gym gestione
    su suscripcion, metodos de pago y facturas directamente.

    Modo local: devuelve mock.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede acceder al portal"}), 403

    gym_id = g.tenant_id

    if not _STRIPE_ENABLED:
        return jsonify({
            "modo":       "local",
            "msg":        "Stripe en modo local. Gestiona la suscripcion via /api/billing/suscripcion",
            "portal_url": None,
        }), 200

    try:
        stripe  = _get_stripe()
        gym     = Gimnasio.query.get(gym_id)
        if not gym or not gym.stripe_customer_id:
            return jsonify({"msg": "El gimnasio no tiene cuenta Stripe. Inicia un checkout primero."}), 400

        return_url = request.get_json(silent=True, force=True) or {}
        return_url = return_url.get("return_url", "http://localhost:3000/dashboard")

        session = stripe.billing_portal.Session.create(
            customer  = gym.stripe_customer_id,
            return_url= return_url,
        )
        return jsonify({"portal_url": session.url}), 200

    except Exception as e:
        logger.exception("Stripe portal error")
        return jsonify({"msg": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK (ruta exenta de JWT en tenant.py)
# ─────────────────────────────────────────────────────────────────────────────

@billing_stripe_bp.route("/webhook", methods=["POST"])
def stripe_webhook():
    """
    Recibe eventos de Stripe y sincroniza el estado de suscripciones y facturas en PG.

    Stripe firma cada payload con STRIPE_WEBHOOK_SECRET. Si la firma no coincide
    devuelve 400. En modo local este endpoint no se usa pero esta disponible para
    pruebas con Stripe CLI: stripe listen --forward-to localhost:3000/api/billing/stripe/webhook

    Eventos manejados:
      invoice.paid                      -> factura pagada, suscripcion -> active
      invoice.payment_failed            -> factura fallida, suscripcion -> past_due
      customer.subscription.updated     -> sincroniza estado y fechas
      customer.subscription.deleted     -> suscripcion -> cancelled
      checkout.session.completed        -> crea Suscripcion en PG al completar checkout
    """
    if not _STRIPE_ENABLED:
        return jsonify({"msg": "Stripe deshabilitado"}), 200

    try:
        stripe  = _get_stripe()
        payload = request.get_data()
        sig     = request.headers.get("Stripe-Signature", "")
        secret  = os.getenv("STRIPE_WEBHOOK_SECRET", "")

        try:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed")
            return jsonify({"msg": "Invalid signature"}), 400

        _dispatch_event(event)
        return jsonify({"received": True}), 200

    except Exception as e:
        logger.exception("Stripe webhook error")
        return jsonify({"msg": str(e)}), 500


def _dispatch_event(event: dict) -> None:
    """Enruta el evento Stripe al handler correspondiente."""
    handlers = {
        "checkout.session.completed":    _handle_checkout_completed,
        "invoice.paid":                  _handle_invoice_paid,
        "invoice.payment_failed":        _handle_invoice_failed,
        "customer.subscription.updated": _handle_subscription_updated,
        "customer.subscription.deleted": _handle_subscription_deleted,
    }
    handler = handlers.get(event["type"])
    if handler:
        handler(event["data"]["object"])
    else:
        logger.debug("Evento Stripe no manejado: %s", event["type"])


def _handle_checkout_completed(session: dict) -> None:
    """
    Cuando el checkout termina con exito, crea la Suscripcion en PG
    vinculada al stripe_subscription_id del objeto session.
    """
    gym_id  = int(session.get("metadata", {}).get("gym_id", 0))
    plan_id = int(session.get("metadata", {}).get("plan_id", 0))
    stripe_sub_id = session.get("subscription")

    if not gym_id or not plan_id or not stripe_sub_id:
        return

    existing = Suscripcion.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
    if existing:
        return

    plan = PlanSuscripcion.query.get(plan_id)
    if not plan:
        return

    ahora = datetime.now(timezone.utc)
    sub = Suscripcion(
        id_gimnasio           = gym_id,
        id_plan               = plan.id,
        estado                = "active",
        fecha_inicio          = ahora,
        fecha_proximo_cobro   = ahora + timedelta(days=30),
        stripe_subscription_id= stripe_sub_id,
    )
    db.session.add(sub)
    db.session.commit()
    logger.info("Suscripcion creada para gym %s via Stripe checkout", gym_id)


def _handle_invoice_paid(invoice: dict) -> None:
    stripe_sub_id = invoice.get("subscription")
    stripe_inv_id = invoice.get("id")

    sub = Suscripcion.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
    if not sub:
        return

    ahora = datetime.now(timezone.utc)

    # Marcar o crear factura
    factura = FacturaSuscripcion.query.filter_by(stripe_invoice_id=stripe_inv_id).first()
    if not factura:
        factura = FacturaSuscripcion(
            id_suscripcion    = sub.id,
            monto             = invoice.get("amount_paid", 0),
            moneda            = invoice.get("currency", "mxn").upper(),
            stripe_invoice_id = stripe_inv_id,
        )
        db.session.add(factura)

    factura.estado     = "pagada"
    factura.fecha_pago = ahora

    sub.estado             = "active"
    sub.fecha_proximo_cobro = ahora + timedelta(days=30)
    sub.updated_at         = ahora
    db.session.commit()
    logger.info("Factura pagada (Stripe) para suscripcion %s", sub.id)


def _handle_invoice_failed(invoice: dict) -> None:
    stripe_sub_id = invoice.get("subscription")
    sub = Suscripcion.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
    if not sub:
        return

    ahora       = datetime.now(timezone.utc)
    sub.estado  = "past_due"
    sub.updated_at = ahora

    factura = FacturaSuscripcion.query.filter_by(
        stripe_invoice_id=invoice.get("id")
    ).first()
    if factura:
        factura.estado = "fallida"

    db.session.commit()
    logger.warning("Pago fallido para suscripcion %s", sub.id)


def _handle_subscription_updated(stripe_sub: dict) -> None:
    sub = Suscripcion.query.filter_by(
        stripe_subscription_id=stripe_sub["id"]
    ).first()
    if not sub:
        return

    estado_map = {
        "trialing":  "trialing",
        "active":    "active",
        "past_due":  "past_due",
        "unpaid":    "unpaid",
        "canceled":  "cancelled",
        "paused":    "paused",
    }
    ahora       = datetime.now(timezone.utc)
    sub.estado  = estado_map.get(stripe_sub.get("status"), "active")
    sub.updated_at = ahora

    period_end = stripe_sub.get("current_period_end")
    if period_end:
        sub.fecha_proximo_cobro = datetime.fromtimestamp(period_end, tz=timezone.utc)

    db.session.commit()
    logger.info("Suscripcion %s actualizada a %s", sub.id, sub.estado)


def _handle_subscription_deleted(stripe_sub: dict) -> None:
    sub = Suscripcion.query.filter_by(
        stripe_subscription_id=stripe_sub["id"]
    ).first()
    if not sub:
        return

    ahora      = datetime.now(timezone.utc)
    sub.estado = "cancelled"
    if not sub.fecha_fin:
        sub.fecha_fin = ahora
    sub.updated_at = ahora
    db.session.commit()
    logger.info("Suscripcion %s cancelada via Stripe", sub.id)
