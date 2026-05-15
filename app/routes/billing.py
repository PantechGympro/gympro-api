"""
routes/billing.py — Gestión de suscripciones y facturas de la plataforma.

Sprint 3 / US12: operaciones CRUD sobre las entidades financieras en PostgreSQL.
Sprint 3 / US13 (pendiente): integración real con Stripe (checkout session,
    customer portal, webhook). Las rutas de Stripe se añadirán en billing_stripe.py
    para mantener separación de concerns.

Roles permitidos:
  - Administrador del gimnasio → lectura + modificación de su propia suscripción.
  - SuperAdmin (role="SuperAdmin") → acceso total a cualquier gimnasio.
  - Otros roles → solo GET de su plan actual.

Endpoints:
  GET  /api/billing/planes               → catálogo de planes disponibles (público autenticado)
  GET  /api/billing/suscripcion          → suscripción activa del gimnasio en JWT
  POST /api/billing/suscripcion          → crear suscripción (admin del gimnasio)
  PUT  /api/billing/suscripcion/<id>     → cambiar plan o estado (admin del gimnasio)
  GET  /api/billing/facturas             → historial de facturas del gimnasio
  POST /api/billing/facturas             → registrar pago manual (admin / test local)
"""
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.plan_suscripcion   import PlanSuscripcion
from app.models.pg.suscripcion        import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.utils.tenant import require_tenant

billing_bp = Blueprint("billing", __name__, url_prefix="/api/billing")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _es_admin(claims: dict) -> bool:
    return claims.get("role") in ("Administrador", "SuperAdmin")


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
    planes = PlanSuscripcion.query.filter_by(activo=True).order_by(PlanSuscripcion.precio_mensual_mxn).all()
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

    sub.updated_at = ahora
    db.session.commit()

    return jsonify({"msg": "Suscripción actualizada", "suscripcion": sub.to_dict()}), 200


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
