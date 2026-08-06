"""
routes/billing.py — Gestión de suscripciones y facturas de la plataforma.

Sprint 3 / US12: operaciones CRUD sobre las entidades financieras en PostgreSQL.
Sprint 3 / US13 (pendiente): integración real con Stripe (checkout session,
    customer portal, webhook). Las rutas de Stripe se añadirán en billing_stripe.py
    para mantener separación de concerns.

Roles permitidos:
  - Administrador del gimnasio → lectura + modificación de su propia suscripción.
  - SuperAdmin (role="superadmin") → acceso total a cualquier gimnasio.
  - Otros roles → solo GET de su plan actual.

Endpoints:
  GET  /api/billing/planes               → catálogo de planes disponibles (público autenticado)
  GET  /api/billing/suscripcion          → suscripción activa del gimnasio en JWT
  POST /api/billing/suscripcion          → crear suscripción (admin del gimnasio)
  PUT  /api/billing/suscripcion/<id>     → cambiar plan o estado (admin del gimnasio)
  GET  /api/billing/facturas             → historial de facturas del gimnasio
  POST /api/billing/facturas             → registrar pago manual (admin / test local)
"""
import logging
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.plan_suscripcion   import PlanSuscripcion
from app.models.pg.suscripcion        import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.utils.tenant import require_tenant

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__, url_prefix="/api/billing")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _es_admin(claims: dict) -> bool:
    return claims.get("role") in ("owner_gym", "superadmin")


def _suscripcion_activa(gym_id: int) -> Suscripcion | None:
    """Devuelve la suscripción trialing/active del gimnasio o None."""
    return (
        Suscripcion.query
        .filter(
            Suscripcion.id_gimnasio == gym_id,
            Suscripcion.estado.in_(["trialing", "active"]),
        )
        .order_by(Suscripcion.created_at.desc())
        .first()
    )


# ─────────────────────────────────────────────────────────────────────────────
# CATÁLOGO DE PLANES
# ─────────────────────────────────────────────────────────────────────────────

@billing_bp.route("/planes", methods=["GET"])
@jwt_required()
def listar_planes():
    """
    Devuelve todos los planes activos de la plataforma.
    Disponible para cualquier usuario autenticado (sin restricción de rol).
    """
    # Se ordena por el campo 'orden' definido en el catálogo comercial y, como
    # criterio secundario, por precio (planes antiguos con orden = 0).
    planes = (PlanSuscripcion.query
              .filter_by(activo=True)
              .order_by(PlanSuscripcion.orden, PlanSuscripcion.precio_mensual_mxn)
              .all())
    return jsonify([p.to_dict() for p in planes]), 200


# ─────────────────────────────────────────────────────────────────────────────
# SUSCRIPCIÓN DEL GIMNASIO
# ─────────────────────────────────────────────────────────────────────────────

@billing_bp.route("/suscripcion", methods=["GET"])
@jwt_required()
@require_tenant
def get_suscripcion():
    """Suscripción activa del gimnasio extraído del JWT."""
    gym_id = g.tenant_id
    sub = _suscripcion_activa(gym_id)
    if not sub:
        return jsonify({"suscripcion": None, "msg": "Sin suscripción activa"}), 200
    return jsonify({"suscripcion": sub.to_dict()}), 200


@billing_bp.route("/suscripcion", methods=["POST"])
@jwt_required()
@require_tenant
def crear_suscripcion():
    """
    Crea una nueva suscripción para el gimnasio del JWT.
    Solo Administrador o SuperAdmin pueden invocar este endpoint.

    Body JSON:
        { "id_plan": 1, "dias_prueba": 14 }   (dias_prueba opcional, default 14)
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede gestionar suscripciones"}), 403

    gym_id = g.tenant_id

    # Evitar doble suscripción activa
    if _suscripcion_activa(gym_id):
        return jsonify({"msg": "Ya existe una suscripción activa. Usa PUT para modificarla."}), 409

    data = request.get_json() or {}
    id_plan = data.get("id_plan")
    if not id_plan:
        return jsonify({"msg": "id_plan es requerido"}), 400

    plan = PlanSuscripcion.query.filter_by(id=id_plan, activo=True).first()
    if not plan:
        return jsonify({"msg": "Plan no encontrado o inactivo"}), 404

    dias_prueba = int(data.get("dias_prueba", 14))
    ahora       = datetime.now(timezone.utc)

    nueva_sub = Suscripcion(
        id_gimnasio          = gym_id,
        id_plan              = plan.id,
        estado               = "trialing",
        fecha_inicio         = ahora,
        fecha_proximo_cobro  = ahora + timedelta(days=dias_prueba),
    )
    db.session.add(nueva_sub)
    db.session.flush()  # obtener nueva_sub.id antes del commit

    # Factura del primer período (estado pendiente durante prueba)
    factura = FacturaSuscripcion(
        id_suscripcion    = nueva_sub.id,
        monto             = plan.precio_mensual_mxn,
        moneda            = "MXN",
        estado            = "pendiente",
        fecha_emision     = ahora,
        fecha_vencimiento = ahora + timedelta(days=dias_prueba),
    )
    db.session.add(factura)
    db.session.commit()

    return jsonify({
        "msg":         "Suscripción creada en período de prueba",
        "suscripcion": nueva_sub.to_dict(),
        "factura":     factura.to_dict(),
    }), 201


@billing_bp.route("/suscripcion/<int:sub_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_suscripcion(sub_id: int):
    """
    Actualiza plan o estado de una suscripción.
    Campos permitidos: id_plan, estado, fecha_fin, fecha_proximo_cobro.

    - Cambio de plan: genera una nueva factura por la diferencia.
    - Cancelación: estado → cancelled, fecha_fin → ahora.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede modificar suscripciones"}), 403

    gym_id = g.tenant_id
    sub    = Suscripcion.query.filter_by(id=sub_id, id_gimnasio=gym_id).first()
    if not sub:
        return jsonify({"msg": "Suscripción no encontrada"}), 404

    data   = request.get_json() or {}
    ahora  = datetime.now(timezone.utc)

    # Cambio de plan
    if "id_plan" in data:
        nuevo_plan = PlanSuscripcion.query.filter_by(id=data["id_plan"], activo=True).first()
        if not nuevo_plan:
            return jsonify({"msg": "Plan no encontrado o inactivo"}), 404
        sub.id_plan = nuevo_plan.id

        # Factura prorrateada del nuevo plan
        factura = FacturaSuscripcion(
            id_suscripcion    = sub.id,
            monto             = nuevo_plan.precio_mensual_mxn,
            moneda            = "MXN",
            estado            = "pendiente",
            fecha_emision     = ahora,
            fecha_vencimiento = ahora + timedelta(days=30),
        )
        db.session.add(factura)

    # Cambio de estado
    if "estado" in data:
        estados_validos = {"trialing", "active", "past_due", "unpaid", "cancelled", "paused"}
        if data["estado"] not in estados_validos:
            return jsonify({"msg": f"Estado inválido. Opciones: {sorted(estados_validos)}"}), 400
        sub.estado = data["estado"]
        if data["estado"] == "cancelled" and not sub.fecha_fin:
            sub.fecha_fin = ahora

    if "fecha_proximo_cobro" in data:
        try:
            sub.fecha_proximo_cobro = datetime.fromisoformat(data["fecha_proximo_cobro"])
        except ValueError:
            return jsonify({"msg": "fecha_proximo_cobro debe ser ISO-8601"}), 400

    if "auto_renovar" in data:
        sub.auto_renovar = bool(data["auto_renovar"])

    sub.updated_at = ahora
    db.session.commit()

    return jsonify({"msg": "Suscripción actualizada", "suscripcion": sub.to_dict()}), 200


@billing_bp.route("/suscripcion/renovar", methods=["POST"])
@jwt_required()
@require_tenant
def renovar_suscripcion_demo():
    """
    Renueva o mejora el plan del gimnasio con PAGO SIMULADO (flujo demo).
    Crea una factura ya PAGADA, activa la suscripción y extiende la fecha de
    próximo cobro 30 días. En producción esto lo confirmaría el webhook de la
    pasarela (Stripe / PayPal / Mercado Pago).

    Body JSON (todos opcionales):
        { "id_plan": <int>, "auto_renovar": <bool> }
        - id_plan presente  → mejora/cambia de plan.
        - id_plan ausente   → renueva el plan actual.
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede renovar la suscripción"}), 403

    gym_id = g.tenant_id
    ahora  = datetime.now(timezone.utc)
    data   = request.get_json() or {}

    sub = _suscripcion_activa(gym_id) or (
        Suscripcion.query.filter_by(id_gimnasio=gym_id)
        .order_by(Suscripcion.created_at.desc()).first()
    )

    id_plan = data.get("id_plan")
    if id_plan:
        plan = PlanSuscripcion.query.filter_by(id=id_plan, activo=True).first()
        if not plan:
            return jsonify({"msg": "Plan no encontrado o inactivo"}), 404
    elif sub and sub.plan:
        plan = sub.plan
    else:
        return jsonify({"msg": "No hay suscripción; indica id_plan para crear una."}), 400

    # Punto de partida para extender: renovar apila 30 días sobre lo que quede.
    base = sub.fecha_proximo_cobro if (sub and sub.fecha_proximo_cobro) else ahora
    try:
        if base < ahora:
            base = ahora
    except TypeError:
        base = ahora
    proximo = base + timedelta(days=30)

    if not sub:
        sub = Suscripcion(
            id_gimnasio=gym_id, id_plan=plan.id, estado="active",
            fecha_inicio=ahora, fecha_proximo_cobro=proximo,
        )
        db.session.add(sub)
        db.session.flush()
    else:
        sub.id_plan             = plan.id
        sub.estado              = "active"
        sub.fecha_proximo_cobro = proximo
        sub.fecha_fin           = None
        sub.updated_at          = ahora

    if "auto_renovar" in data:
        sub.auto_renovar = bool(data["auto_renovar"])

    factura = FacturaSuscripcion(
        id_suscripcion    = sub.id,
        monto             = plan.precio_mensual_mxn,
        moneda            = "MXN",
        estado            = "pagada",
        fecha_emision     = ahora,
        fecha_pago        = ahora,
        fecha_vencimiento = proximo,
    )
    db.session.add(factura)
    db.session.commit()

    return jsonify({
        "msg":         "Pago simulado exitoso. Tu suscripción quedó activa.",
        "demo":        True,
        "suscripcion": sub.to_dict(),
        "factura":     factura.to_dict(),
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# AUTO-RENOVACIÓN
# ─────────────────────────────────────────────────────────────────────────────

def procesar_auto_renovaciones(app=None) -> dict:
    """
    Reconcilia las suscripciones vencidas contra su pasarela.

    Este proceso NO cobra. En el modelo de suscripciones de PayPal y Mercado
    Pago, el dueño autoriza una vez y la pasarela cobra sola cada periodo; el
    papel de GymPro es preguntar cómo quedó el acuerdo y registrar lo que la
    pasarela reporta. Que el sistema disparase el cargo obligaría a guardar
    medios de pago, con las exigencias de PCI-DSS que eso implica.

    Por cada suscripción cuya fecha de cobro ya pasó:

      - Con acuerdo activo en la pasarela → se lee de ella la nueva fecha de
        cobro y la suscripción sigue vigente. El cargo en sí lo registra el
        webhook, o la reconciliación manual si el webhook no llegó.
      - Con acuerdo caído (cancelado, suspendido, sin autorizar) → se marca
        'past_due' y se apaga `auto_renovar`, porque prometía algo que ya no
        ocurre.
      - Sin acuerdo → 'past_due'. Nunca hubo cobro automático.

    Si la pasarela no responde, la suscripción se deja intacta: cortarle el
    servicio a un gimnasio que sí pagó por un fallo de red sería peor que
    esperar al día siguiente.

    La ejecuta el scheduler una vez al día y también puede llamarse a mano desde
    POST /api/billing/suscripcion/auto-renovar.
    """
    from flask import current_app
    from app.services.payments import PasarelaError, pasarela_de_plataforma

    contexto = app.app_context() if app is not None else current_app.app_context()
    with contexto:
        ahora = datetime.now(timezone.utc)
        resumen = {"al_corriente": 0, "vencidas": 0, "sin_respuesta": 0,
                   "errores": 0, "revisadas": 0}

        pendientes = (
            Suscripcion.query
            .filter(
                Suscripcion.estado.in_(["trialing", "active"]),
                Suscripcion.fecha_proximo_cobro.isnot(None),
                Suscripcion.fecha_proximo_cobro <= ahora,
            )
            .all()
        )
        resumen["revisadas"] = len(pendientes)

        def _vencer(sub, motivo: str):
            sub.estado       = "past_due"
            sub.auto_renovar = False
            sub.updated_at   = ahora
            resumen["vencidas"] += 1
            logger.info("Suscripción %s marcada past_due: %s", sub.id, motivo)

        for sub in pendientes:
            try:
                if not (sub.auto_renovar and sub.referencia_recurrente):
                    _vencer(sub, "sin acuerdo de cobro recurrente")
                    continue

                try:
                    pasarela  = pasarela_de_plataforma(sub.pasarela_recurrente)
                    resultado = pasarela.consultar_suscripcion(sub.referencia_recurrente)
                except PasarelaError as exc:
                    # La pasarela no contestó o rechazó las credenciales. No se
                    # toca la suscripción: se reintenta mañana.
                    resumen["sin_respuesta"] += 1
                    logger.warning("Suscripción %s: la pasarela no respondió (%s)", sub.id, exc)
                    continue

                sub.estado_recurrente = resultado.estado
                sub.updated_at        = ahora

                if not resultado.cobra_sola:
                    _vencer(sub, f"acuerdo en estado '{resultado.estado}'")
                    continue

                # El acuerdo sigue vivo. La fecha la manda la pasarela; solo si
                # no la reporta se avanza un ciclo para no dejarla en el pasado
                # y reprocesar la misma suscripción cada día.
                if resultado.proximo_cobro:
                    try:
                        fecha = datetime.fromisoformat(
                            str(resultado.proximo_cobro).replace("Z", "+00:00"))
                        if fecha.tzinfo is None:
                            fecha = fecha.replace(tzinfo=timezone.utc)
                        sub.fecha_proximo_cobro = fecha
                    except (ValueError, TypeError):
                        logger.warning("Suscripción %s: fecha de cobro ilegible %r",
                                       sub.id, resultado.proximo_cobro)
                        sub.fecha_proximo_cobro = _avanzar_ciclo(sub.fecha_proximo_cobro, ahora)
                else:
                    sub.fecha_proximo_cobro = _avanzar_ciclo(sub.fecha_proximo_cobro, ahora)

                sub.estado    = "active"
                sub.fecha_fin = None
                resumen["al_corriente"] += 1

            except Exception as exc:
                resumen["errores"] += 1
                logger.exception("Suscripción %s falló en la reconciliación: %s", sub.id, exc)

        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("No se pudo guardar la reconciliación de suscripciones: %s", exc)
            resumen["errores"] += 1

        return resumen


def _avanzar_ciclo(desde, ahora):
    """
    Siguiente fecha de cobro partiendo de la anterior, no de hoy.

    Se parte de la fecha previa para no regalar días cuando el proceso corre con
    retraso; si el atraso supera un ciclo, se avanza hasta rebasar el presente.
    """
    proximo = desde or ahora
    # Una fila antigua puede venir sin zona horaria; compararla con un datetime
    # consciente lanzaría TypeError y abortaría el ciclo para los demás.
    if proximo.tzinfo is None:
        proximo = proximo.replace(tzinfo=timezone.utc)
    while proximo <= ahora:
        proximo = proximo + timedelta(days=30)
    return proximo


@billing_bp.route("/suscripcion/auto-renovar", methods=["POST"])
@jwt_required()
def trigger_auto_renovacion():
    """Ejecuta el ciclo de auto-renovación a mano. Solo superadmin."""
    claims = get_jwt()
    if claims.get("role") != "superadmin":
        return jsonify({"msg": "Solo el superadministrador puede ejecutar esta acción"}), 403

    from flask import current_app
    resultado = procesar_auto_renovaciones(app=current_app._get_current_object())
    return jsonify({"msg": "Ciclo de auto-renovación ejecutado", **resultado}), 200


# ─────────────────────────────────────────────────────────────────────────────
# FACTURAS
# ─────────────────────────────────────────────────────────────────────────────

@billing_bp.route("/facturas", methods=["GET"])
@jwt_required()
@require_tenant
def listar_facturas():
    """
    Historial de facturas del gimnasio.
    Query params: estado (pendiente|pagada|vencida|fallida), limit (default 20).
    """
    gym_id = g.tenant_id

    # Obtener IDs de suscripciones del gimnasio
    sub_ids = [
        s.id for s in
        Suscripcion.query.filter_by(id_gimnasio=gym_id).with_entities(Suscripcion.id).all()
    ]
    if not sub_ids:
        return jsonify({"facturas": [], "total": 0}), 200

    q = FacturaSuscripcion.query.filter(FacturaSuscripcion.id_suscripcion.in_(sub_ids))

    if estado := request.args.get("estado"):
        q = q.filter(FacturaSuscripcion.estado == estado)

    limit    = min(int(request.args.get("limit", 20)), 100)
    facturas = q.order_by(FacturaSuscripcion.fecha_emision.desc()).limit(limit).all()

    return jsonify({
        "facturas": [f.to_dict() for f in facturas],
        "total":    q.count(),
    }), 200


@billing_bp.route("/facturas", methods=["POST"])
@jwt_required()
@require_tenant
def registrar_pago_manual():
    """
    Marca una factura como pagada (flujo local sin Stripe).
    Solo Administrador. En producción con Stripe esto lo hace el webhook.

    Body JSON: { "id_factura": 1 }
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede registrar pagos"}), 403

    gym_id = g.tenant_id
    data   = request.get_json() or {}
    fid    = data.get("id_factura")
    if not fid:
        return jsonify({"msg": "id_factura es requerido"}), 400

    # Verificar que la factura pertenece a este gimnasio
    sub_ids = [
        s.id for s in
        Suscripcion.query.filter_by(id_gimnasio=gym_id).with_entities(Suscripcion.id).all()
    ]
    factura = FacturaSuscripcion.query.filter(
        FacturaSuscripcion.id == fid,
        FacturaSuscripcion.id_suscripcion.in_(sub_ids),
    ).first()

    if not factura:
        return jsonify({"msg": "Factura no encontrada"}), 404
    if factura.estado == "pagada":
        return jsonify({"msg": "La factura ya está pagada"}), 409

    ahora           = datetime.now(timezone.utc)
    factura.estado  = "pagada"
    factura.fecha_pago = ahora

    # Activar la suscripción si estaba en trialing/past_due
    sub = factura.suscripcion
    if sub and sub.estado in ("trialing", "past_due"):
        sub.estado              = "active"
        sub.fecha_proximo_cobro = ahora + timedelta(days=30)
        sub.updated_at          = ahora

    db.session.commit()

    return jsonify({
        "msg":     "Pago registrado",
        "factura": factura.to_dict(),
    }), 200
