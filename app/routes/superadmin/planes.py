"""
superadmin/planes.py — CRUD de planes de suscripción SaaS.

Endpoints:
    GET    /api/superadmin/planes          catálogo completo (activos + inactivos)
    POST   /api/superadmin/planes          crear plan
    PUT    /api/superadmin/planes/<id>     editar plan
    PATCH  /api/superadmin/planes/<id>/toggle  activar / desactivar
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from app.utils.security import require_role

planes_admin_bp = Blueprint("planes_admin", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@planes_admin_bp.route("/planes", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_planes():
    """Devuelve todos los planes (activos e inactivos) con conteo de suscriptores."""
    planes = PlanSuscripcion.query.order_by(PlanSuscripcion.precio_mensual_mxn).all()
    result = []
    for p in planes:
        subs_activas = Suscripcion.query.filter(
            Suscripcion.id_plan == p.id,
            Suscripcion.estado.in_(["active", "trialing"]),
        ).count()
        d = p.to_dict()
        d["suscriptores_activos"] = subs_activas
        result.append(d)
    return jsonify(result), 200


@planes_admin_bp.route("/planes", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def crear_plan():
    """
    Crea un nuevo plan de suscripción.

    Body JSON:
        {
            "nombre":             "enterprise_plus",
            "precio_mensual_mxn": 199900,     ← centavos (1999.00 MXN)
            "max_miembros":       null,        ← null = ilimitado
            "descripcion":        "Plan enterprise plus con soporte prioritario",
            "stripe_price_id":    "price_xxx"  ← opcional
        }
    """
    data = request.get_json() or {}

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"msg": "nombre es requerido"}), 400
    if PlanSuscripcion.query.filter_by(nombre=nombre).first():
        return jsonify({"msg": f"Ya existe un plan con el nombre '{nombre}'"}), 409

    precio = data.get("precio_mensual_mxn")
    if precio is None or not isinstance(precio, int) or precio < 0:
        return jsonify({"msg": "precio_mensual_mxn debe ser un entero positivo en centavos"}), 400

    plan = PlanSuscripcion(
        nombre              = nombre,
        precio_mensual_mxn  = precio,
        max_miembros        = data.get("max_miembros"),   # None = ilimitado
        descripcion         = data.get("descripcion", ""),
        stripe_price_id     = data.get("stripe_price_id"),
        activo              = True,
    )
    db.session.add(plan)
    db.session.commit()
    return jsonify(plan.to_dict()), 201


@planes_admin_bp.route("/planes/<int:plan_id>", methods=["PUT"])
@jwt_required()
@require_role("superadmin")
def editar_plan(plan_id: int):
    """
    Actualiza un plan existente.
    No permite cambiar el nombre si ya hay suscriptores activos (evita inconsistencias).
    """
    plan = PlanSuscripcion.query.get_or_404(plan_id)
    data = request.get_json() or {}

    nuevo_nombre = data.get("nombre")
    if nuevo_nombre and nuevo_nombre != plan.nombre:
        subs_activas = Suscripcion.query.filter(
            Suscripcion.id_plan == plan_id,
            Suscripcion.estado.in_(["active", "trialing"]),
        ).count()
        if subs_activas > 0:
            return jsonify({
                "msg": f"No se puede cambiar el nombre del plan: tiene {subs_activas} suscriptores activos.",
            }), 409
        if PlanSuscripcion.query.filter(
            PlanSuscripcion.nombre == nuevo_nombre,
            PlanSuscripcion.id != plan_id
        ).first():
            return jsonify({"msg": f"Ya existe un plan con el nombre '{nuevo_nombre}'"}), 409
        plan.nombre = nuevo_nombre

    if "precio_mensual_mxn" in data:
        precio = data["precio_mensual_mxn"]
        if not isinstance(precio, int) or precio < 0:
            return jsonify({"msg": "precio_mensual_mxn debe ser un entero positivo en centavos"}), 400
        plan.precio_mensual_mxn = precio

    if "max_miembros" in data:
        plan.max_miembros = data["max_miembros"]   # None = ilimitado

    if "descripcion" in data:
        plan.descripcion = data["descripcion"]

    if "stripe_price_id" in data:
        plan.stripe_price_id = data["stripe_price_id"]

    db.session.commit()
    return jsonify(plan.to_dict()), 200


@planes_admin_bp.route("/planes/<int:plan_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("superadmin")
def toggle_plan(plan_id: int):
    """
    Activa o desactiva un plan.
    Los planes inactivos no aparecen en el catálogo público pero las
    suscripciones existentes NO se cancelan automáticamente.
    """
    plan = PlanSuscripcion.query.get_or_404(plan_id)

    if plan.activo:
        # Advertir si hay suscriptores activos antes de desactivar
        subs_activas = Suscripcion.query.filter(
            Suscripcion.id_plan == plan_id,
            Suscripcion.estado.in_(["active", "trialing"]),
        ).count()
        plan.activo = False
        db.session.commit()
        return jsonify({
            "msg":               "Plan desactivado.",
            "id":                plan.id,
            "activo":            False,
            "subs_activas_afectadas": subs_activas,
            "advertencia":       (
                f"{subs_activas} suscripción(es) activa(s) siguen en este plan. "
                "Considera migrarlas antes de eliminar el plan."
            ) if subs_activas else None,
        }), 200

    plan.activo = True
    db.session.commit()
    return jsonify({"msg": "Plan activado.", "id": plan.id, "activo": True}), 200
