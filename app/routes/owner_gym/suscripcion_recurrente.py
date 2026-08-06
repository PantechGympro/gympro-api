"""
suscripcion_recurrente.py — Cargo recurrente de la suscripción a la plataforma.

Cómo funciona el cobro automático
---------------------------------
GymPro no guarda tarjetas ni dispara cargos. El dueño autoriza UNA vez en la
pasarela y a partir de ahí es ella quien cobra cada 30 días, igual que Netflix
o Spotify. El sistema solo pregunta el estado del acuerdo y registra lo que la
pasarela reporta.

Esto es deliberado: almacenar medios de pago obligaría a cumplir PCI-DSS, y
disparar cargos desde nuestro servidor requiere permisos especiales que PayPal
concede caso por caso. Delegando el calendario a la pasarela, el sistema se
limita a leer.

    PayPal        subscription  (Subscriptions API v1)
    Mercado Pago  preapproval

Ciclo de vida
-------------
    1. POST /suscripcion/recurrente          crea el acuerdo -> url_autorizacion
    2. el dueño autoriza en la pasarela
    3. GET  /suscripcion/recurrente/estado   confirma que quedó activo
    4. la pasarela cobra sola cada periodo
    5. POST /suscripcion/recurrente/cancelar da de baja el acuerdo

El paso 3 existe porque en desarrollo los webhooks no llegan a localhost. Es la
misma reconciliación manual que ya usa el sistema para los pagos de membresías.

Endpoints:
    GET    /api/billing/suscripcion/recurrente          estado del acuerdo
    POST   /api/billing/suscripcion/recurrente          crear y obtener la URL
    POST   /api/billing/suscripcion/recurrente/sincronizar   reconciliar a mano
    DELETE /api/billing/suscripcion/recurrente          cancelar
"""
import logging
import os
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.suscripcion import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.services.payments import PasarelaError, pasarela_de_plataforma
from app.utils.tenant import require_tenant

logger = logging.getLogger(__name__)

suscripcion_recurrente_bp = Blueprint(
    "suscripcion_recurrente", __name__, url_prefix="/api/billing"
)

PROVEEDORES_VALIDOS = ("paypal", "mercadopago")


def _es_admin(claims: dict) -> bool:
    return claims.get("role") in ("owner_gym", "superadmin")


def _suscripcion_del_gimnasio(gym_id: int) -> Suscripcion | None:
    """La suscripción vigente del gimnasio, o la más reciente si ninguna lo está."""
    return (
        Suscripcion.query
        .filter(Suscripcion.id_gimnasio == gym_id,
                Suscripcion.estado.in_(["trialing", "active", "past_due"]))
        .order_by(Suscripcion.created_at.desc())
        .first()
        or Suscripcion.query.filter_by(id_gimnasio=gym_id)
        .order_by(Suscripcion.created_at.desc()).first()
    )


def _url_front() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:8080").rstrip("/")


def _aplicar_estado(sub: Suscripcion, resultado) -> None:
    """
    Vuelca en la suscripción lo que la pasarela reporta del acuerdo.

    La fecha de cobro la manda la pasarela, no se calcula aquí: ella es la dueña
    del calendario en este modelo, y estimarla llevaría a que el panel muestre
    una fecha y el cargo llegue en otra.
    """
    ahora = datetime.now(timezone.utc)
    sub.estado_recurrente = resultado.estado
    sub.updated_at = ahora

    if resultado.proximo_cobro:
        try:
            fecha = datetime.fromisoformat(str(resultado.proximo_cobro).replace("Z", "+00:00"))
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            sub.fecha_proximo_cobro = fecha
        except (ValueError, TypeError):
            logger.warning("Fecha de cobro ilegible de la pasarela: %r", resultado.proximo_cobro)

    if resultado.cobra_sola:
        # El acuerdo cobra: la suscripción está al corriente.
        sub.estado   = "active"
        sub.fecha_fin = None
    elif resultado.estado in ("cancelado", "vencido"):
        # Dejó de cobrar. No se corta el servicio aquí: se apaga la intención y
        # el ciclo diario decidirá según la fecha de vencimiento, para no dar de
        # baja a un gimnasio que aún tiene días pagados por delante.
        sub.auto_renovar = False


# ─────────────────────────────────────────────────────────────────────────────
# CONSULTAR
# ─────────────────────────────────────────────────────────────────────────────

@suscripcion_recurrente_bp.route("/suscripcion/recurrente", methods=["GET"])
@jwt_required()
@require_tenant
def estado_recurrente():
    """Estado del acuerdo de cobro recurrente y métodos disponibles."""
    sub = _suscripcion_del_gimnasio(g.tenant_id)

    disponibles = []
    for prov in PROVEEDORES_VALIDOS:
        try:
            pasarela = pasarela_de_plataforma(prov)
        except PasarelaError:
            continue   # la plataforma no tiene ese proveedor configurado
        if getattr(pasarela, "soporta_suscripciones", False):
            disponibles.append({
                "proveedor": prov,
                "nombre": "PayPal" if prov == "paypal" else "Mercado Pago",
                "modo": os.getenv("PLATAFORMA_PAGOS_MODO", "sandbox"),
            })

    if not sub:
        return jsonify({"acuerdo": None, "metodos": disponibles}), 200

    return jsonify({
        "acuerdo": {
            "pasarela":         sub.pasarela_recurrente,
            "estado":           sub.estado_recurrente,
            "activo":           sub.cobro_automatico,
            "auto_renovar":     bool(sub.auto_renovar),
            "proximo_cobro":    sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
        },
        "metodos": disponibles,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# CREAR
# ─────────────────────────────────────────────────────────────────────────────

@suscripcion_recurrente_bp.route("/suscripcion/recurrente", methods=["POST"])
@jwt_required()
@require_tenant
def crear_recurrente():
    """
    Crea el acuerdo de cobro recurrente y devuelve la URL donde autorizarlo.

    Body JSON:
        { "proveedor": "paypal" | "mercadopago", "origen": "web" | "mobile" }
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede activar el cargo recurrente"}), 403

    data      = request.get_json() or {}
    proveedor = (data.get("proveedor") or "").lower()
    if proveedor not in PROVEEDORES_VALIDOS:
        return jsonify({"msg": f"Proveedor inválido. Usa uno de: {', '.join(PROVEEDORES_VALIDOS)}"}), 400

    sub = _suscripcion_del_gimnasio(g.tenant_id)
    if not sub:
        return jsonify({"msg": "No hay una suscripción a la que asociar el cargo recurrente."}), 404
    if not sub.plan:
        return jsonify({"msg": "La suscripción no tiene un plan asignado."}), 400

    # Si ya hay un acuerdo vivo, no se crea otro: dos acuerdos activos sobre la
    # misma suscripción cobrarían dos veces.
    if sub.referencia_recurrente and sub.estado_recurrente == "activo":
        return jsonify({
            "msg": "Ya tienes el cargo recurrente activo. Cancélalo antes de crear uno nuevo.",
            "estado": sub.estado_recurrente,
        }), 409

    try:
        pasarela = pasarela_de_plataforma(proveedor)
    except PasarelaError as exc:
        return jsonify({"msg": str(exc)}), 400

    if not getattr(pasarela, "soporta_suscripciones", False):
        return jsonify({"msg": "Este método de pago no admite cargos recurrentes."}), 400

    # El importe viaja en centavos en la base y en pesos hacia la pasarela.
    monto = round((sub.plan.precio_mensual_mxn or 0) / 100, 2)
    if monto <= 0:
        return jsonify({"msg": "El plan no tiene un precio válido."}), 400

    # Desde el móvil, FRONTEND_URL apunta a localhost y en el teléfono eso es el
    # propio teléfono. Se usa el host con el que la app llamó a la API, igual
    # que hace el checkout de pagos.
    if (data.get("origen") or "").lower() == "mobile":
        front, sufijo = request.host_url.rstrip("/"), "&app=1"
    else:
        front, sufijo = _url_front(), ""

    try:
        resultado = pasarela.crear_suscripcion(
            monto=monto,
            descripcion=f"GymPro — Plan {sub.plan.nombre}",
            url_retorno=f"{front}/suscripcion/recurrente/exito?sub={sub.id}{sufijo}",
            url_cancelacion=f"{front}/suscripcion/recurrente/cancelado?sub={sub.id}{sufijo}",
            referencia=str(sub.id),
            dias_periodo=30,
            extra={
                "brand_name": "GymPro",
                "payer_email": data.get("email_pagador") or "",
            },
        )
    except PasarelaError as exc:
        logger.warning("Alta de cargo recurrente en %s falló: %s", proveedor, exc)
        return jsonify({"msg": str(exc)}), 400
    except Exception as exc:
        logger.exception("Error inesperado creando el cargo recurrente")
        return jsonify({"msg": f"No se pudo crear el cargo recurrente: {exc}"}), 500

    sub.pasarela_recurrente   = proveedor
    sub.referencia_recurrente = resultado.referencia_externa
    sub.estado_recurrente     = resultado.estado
    # Todavía NO se activa auto_renovar: la intención se confirma cuando el
    # dueño termina de autorizar y la pasarela reporta el acuerdo como activo.
    db.session.commit()

    return jsonify({
        "msg":               "Acuerdo creado. Autorízalo para activar el cargo recurrente.",
        "url_autorizacion":  resultado.url_autorizacion,
        "proveedor":         proveedor,
        "monto":             monto,
        "estado":            resultado.estado,
    }), 201


# ─────────────────────────────────────────────────────────────────────────────
# SINCRONIZAR (reconciliación manual)
# ─────────────────────────────────────────────────────────────────────────────

@suscripcion_recurrente_bp.route("/suscripcion/recurrente/sincronizar", methods=["POST"])
@jwt_required()
@require_tenant
def sincronizar_recurrente():
    """
    Pregunta a la pasarela cómo quedó el acuerdo y actualiza la suscripción.

    Es lo que el dueño usa al volver de autorizar: en desarrollo los webhooks no
    llegan a localhost, así que sin esto el acuerdo quedaría "pendiente" para
    siempre aunque la pasarela ya lo tuviera activo.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede sincronizar la suscripción"}), 403

    sub = _suscripcion_del_gimnasio(g.tenant_id)
    if not sub or not sub.referencia_recurrente:
        return jsonify({"msg": "No hay un acuerdo de cobro recurrente que sincronizar."}), 404

    try:
        pasarela  = pasarela_de_plataforma(sub.pasarela_recurrente)
        resultado = pasarela.consultar_suscripcion(sub.referencia_recurrente)
    except PasarelaError as exc:
        return jsonify({"msg": str(exc)}), 400

    _aplicar_estado(sub, resultado)
    # Al confirmarse activo, se enciende la intención: el dueño completó el
    # trámite y la pasarela lo respalda.
    if resultado.cobra_sola:
        sub.auto_renovar = True
    db.session.commit()

    return jsonify({
        "msg":           "Estado actualizado desde la pasarela.",
        "estado":        sub.estado_recurrente,
        "activo":        sub.cobro_automatico,
        "proximo_cobro": sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# CANCELAR
# ─────────────────────────────────────────────────────────────────────────────

@suscripcion_recurrente_bp.route("/suscripcion/recurrente", methods=["DELETE"])
@jwt_required()
@require_tenant
def cancelar_recurrente():
    """
    Da de baja el acuerdo en la pasarela.

    No cancela la suscripción: el gimnasio conserva el servicio hasta la fecha
    ya pagada, simplemente deja de renovarse solo.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede cancelar el cargo recurrente"}), 403

    sub = _suscripcion_del_gimnasio(g.tenant_id)
    if not sub or not sub.referencia_recurrente:
        return jsonify({"msg": "No hay un cargo recurrente activo."}), 404

    try:
        pasarela = pasarela_de_plataforma(sub.pasarela_recurrente)
        ok = pasarela.cancelar_suscripcion(
            sub.referencia_recurrente,
            motivo="El gimnasio desactivó el cargo recurrente desde GymPro",
        )
    except PasarelaError as exc:
        return jsonify({"msg": str(exc)}), 400

    if not ok:
        return jsonify({"msg": "La pasarela no pudo cancelar el acuerdo. Inténtalo de nuevo."}), 502

    sub.auto_renovar          = False
    sub.estado_recurrente     = "cancelado"
    sub.referencia_recurrente = None
    sub.pasarela_recurrente   = None
    sub.updated_at            = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        "msg": "Cargo recurrente cancelado. Tu plan sigue activo hasta la fecha ya pagada.",
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRO DE UN COBRO RECURRENTE
# ─────────────────────────────────────────────────────────────────────────────

def registrar_cobro_recurrente(referencia_acuerdo: str, monto_mxn: float | None,
                               referencia_pago: str | None = None) -> bool:
    """
    Registra un cargo que la pasarela hizo por su cuenta.

    La llama el webhook al recibir la notificación de una renovación. Localiza
    la suscripción por la referencia del acuerdo —el webhook no sabe de
    gimnasios— y emite la factura correspondiente.

    Devuelve True si se registró algo.
    """
    sub = Suscripcion.query.filter_by(referencia_recurrente=referencia_acuerdo).first()
    if not sub:
        logger.warning("Cobro recurrente sin suscripción asociada: %s", referencia_acuerdo)
        return False

    ahora = datetime.now(timezone.utc)

    # Idempotencia: las pasarelas reenvían la misma notificación si no reciben
    # respuesta. Sin esta comprobación, un reintento cobraría dos veces en el
    # historial.
    if referencia_pago and FacturaSuscripcion.query.filter_by(
            referencia_externa=referencia_pago).first():
        logger.info("Cobro recurrente %s ya estaba registrado; se ignora", referencia_pago)
        return False

    # El importe del plan manda: es el contrato. El de la pasarela solo se usa
    # si el plan no lo tiene, para no registrar una factura en cero.
    centavos = sub.plan.precio_mensual_mxn if sub.plan else None
    if not centavos and monto_mxn:
        centavos = int(round(monto_mxn * 100))

    proximo = (sub.fecha_proximo_cobro or ahora) + timedelta(days=30)

    factura = FacturaSuscripcion(
        id_suscripcion     = sub.id,
        monto              = centavos or 0,
        moneda             = "MXN",
        estado             = "pagada",
        fecha_emision      = ahora,
        fecha_pago         = ahora,
        fecha_vencimiento  = proximo,
        referencia_externa = referencia_pago,
    )

    sub.estado              = "active"
    sub.estado_recurrente   = "activo"
    sub.fecha_proximo_cobro = proximo
    sub.fecha_fin           = None
    sub.updated_at          = ahora

    db.session.add(factura)
    db.session.commit()

    logger.info("Cobro recurrente registrado: suscripcion %s, acuerdo %s",
                sub.id, referencia_acuerdo)
    return True
