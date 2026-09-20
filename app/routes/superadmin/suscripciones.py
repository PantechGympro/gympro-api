"""
superadmin/suscripciones.py — Gestión de suscripciones SaaS de la plataforma.

Endpoints:
    GET    /api/superadmin/suscripciones              todas las suscripciones con filtros
    GET    /api/superadmin/suscripciones/<id>         detalle con historial de facturas

Plan y estado ya NO se pueden cambiar manualmente desde el superadmin: ambos
se actualizan automáticamente a partir de los eventos de pago de Stripe
(ver app/routes/owner_gym/billing_stripe.py::_dispatch_event). Esto evita que
un cambio manual quede desincronizado con lo que Stripe realmente cobró.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.models.pg.suscripcion import Suscripcion, EstadoSuscripcionEnum
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.utils.security import require_role

suscripciones_admin_bp = Blueprint("suscripciones_admin", __name__)

_ESTADOS_VALIDOS = [e.value for e in EstadoSuscripcionEnum]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _sub_to_dict(sub: Suscripcion, include_facturas: bool = False) -> dict:
    estado = sub.estado if isinstance(sub.estado, str) else sub.estado.value
    data = {
        "id":                     sub.id,
        "id_gimnasio":            sub.id_gimnasio,
        "gimnasio":               sub.gimnasio.nombre if sub.gimnasio else None,
        "plan_id":                sub.id_plan,
        "plan":                   sub.plan.nombre if sub.plan else None,
        "precio_mensual_mxn":     sub.plan.precio_mensual_mxn if sub.plan else None,
        "estado":                 estado,
        "activa":                 sub.activa,
        "fecha_inicio":           sub.fecha_inicio.isoformat() if sub.fecha_inicio else None,
        "fecha_fin":              sub.fecha_fin.isoformat() if sub.fecha_fin else None,
        "fecha_proximo_cobro":    sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
        "stripe_subscription_id": sub.stripe_subscription_id,
        "created_at":             sub.created_at.isoformat() if sub.created_at else None,
    }
    if include_facturas:
        facturas = (
            FacturaSuscripcion.query
            .filter_by(id_suscripcion=sub.id)
            .order_by(FacturaSuscripcion.fecha_emision.desc())
            .all()
        )
        data["facturas"] = [f.to_dict() for f in facturas]
    return data


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@suscripciones_admin_bp.route("/suscripciones", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_suscripciones():
    """
    Devuelve todas las suscripciones de la plataforma.

    Query params:
        estado   str  — active | trialing | past_due | unpaid | cancelled | paused
        plan_id  int  — filtrar por plan
        gym_id   int  — filtrar por gimnasio
        page     int
        per_page int
    """
    estado_param  = request.args.get("estado")
    plan_id_param = request.args.get("plan_id", type=int)
    gym_id_param  = request.args.get("gym_id", type=int)
    page          = max(1, int(request.args.get("page", 1)))
    per_page      = min(100, int(request.args.get("per_page", 20)))

    query = (
        Suscripcion.query
        .join(Gimnasio)
        .join(PlanSuscripcion)
        .order_by(Suscripcion.created_at.desc())
    )

    if estado_param:
        query = query.filter(Suscripcion.estado == estado_param)
    if plan_id_param:
        query = query.filter(Suscripcion.id_plan == plan_id_param)
    if gym_id_param:
        query = query.filter(Suscripcion.id_gimnasio == gym_id_param)

    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "suscripciones": [_sub_to_dict(s) for s in paginado.items],
        "total":         paginado.total,
        "page":          page,
        "pages":         paginado.pages,
        "per_page":      per_page,
        # resumen de estados para el dashboard
        "resumen_estados": {
            estado: Suscripcion.query.filter(Suscripcion.estado == estado).count()
            for estado in _ESTADOS_VALIDOS
        },
    }), 200


@suscripciones_admin_bp.route("/suscripciones/<int:sub_id>", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def detalle_suscripcion(sub_id: int):
    """Detalle completo con historial de facturas."""
    sub = Suscripcion.query.get_or_404(sub_id)
    return jsonify(_sub_to_dict(sub, include_facturas=True)), 200
