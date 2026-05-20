"""
superadmin/gimnasios.py — Gestión de gimnasios registrados en la plataforma.

Endpoints:
    GET    /api/superadmin/gimnasios              listar con filtros y paginación
    GET    /api/superadmin/gimnasios/<id>         detalle + métricas MongoDB
    PATCH  /api/superadmin/gimnasios/<id>/toggle  activar / desactivar
    GET    /api/superadmin/gimnasios/<id>/metricas métricas operativas detalladas
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from datetime import datetime, timedelta

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario
from app.models.pg.suscripcion import Suscripcion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.mongo import get_db
from app.utils.security import require_role

gimnasios_admin_bp = Blueprint("gimnasios_admin", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _metricas_mongo(gym_id: int) -> dict:
    """Consulta métricas operativas del gimnasio desde MongoDB."""
    mdb = get_db()
    ahora = datetime.utcnow()
    mes_actual_inicio = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    filtro = {"id_gimnasio": gym_id}

    total_miembros  = mdb.miembros.count_documents(filtro)
    activos         = mdb.miembros.count_documents({**filtro, "estado": "Activo"})
    asistencias_mes = mdb.asistencias.count_documents({
        **filtro,
        "$expr": {"$gte": [
            {"$toDate": "$fecha"},
            mes_actual_inicio
        ]}
    })

    # Ingresos del mes actual
    pipeline_ingresos = [
        {"$match": {
            **filtro,
            "$expr": {"$gte": [{"$toDate": "$fecha_pago"}, mes_actual_inicio]}
        }},
        {"$group": {"_id": None, "total": {"$sum": "$monto"}}},
    ]
    res_ingresos = list(mdb.pagos.aggregate(pipeline_ingresos))
    ingresos_mes = round(res_ingresos[0]["total"], 2) if res_ingresos else 0.0

    # Última asistencia registrada
    ultima_asist = mdb.asistencias.find_one(filtro, sort=[("fecha", -1)])
    ultima_actividad = None
    if ultima_asist:
        ultima_actividad = ultima_asist.get("fecha") or ultima_asist.get("created_at")
        if hasattr(ultima_actividad, "isoformat"):
            ultima_actividad = ultima_actividad.isoformat()

    return {
        "total_miembros":    total_miembros,
        "miembros_activos":  activos,
        "asistencias_mes":   asistencias_mes,
        "ingresos_mes_mxn":  ingresos_mes,
        "ultima_actividad":  ultima_actividad,
    }


def _suscripcion_activa(gym_id: int) -> dict | None:
    sub = (
        Suscripcion.query
        .join(PlanSuscripcion)
        .filter(Suscripcion.id_gimnasio == gym_id)
        .order_by(Suscripcion.created_at.desc())
        .first()
    )
    if not sub:
        return None
    return {
        "id":                   sub.id,
        "estado":               sub.estado if isinstance(sub.estado, str) else sub.estado.value,
        "plan":                 sub.plan.nombre if sub.plan else None,
        "precio_mensual_mxn":   sub.plan.precio_mensual_mxn if sub.plan else None,
        "fecha_inicio":         sub.fecha_inicio.isoformat() if sub.fecha_inicio else None,
        "fecha_proximo_cobro":  sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
        "stripe_subscription_id": sub.stripe_subscription_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@gimnasios_admin_bp.route("/gimnasios", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def listar_gimnasios():
    """
    Devuelve todos los gimnasios registrados en la plataforma.

    Query params:
        activo   bool  — filtrar por estado activo/inactivo
        plan     str   — basico | pro | enterprise
        page     int   — página (default 1)
        per_page int   — resultados por página (default 20, max 100)
    """
    activo_param = request.args.get("activo")
    plan_param   = request.args.get("plan")
    page         = max(1, int(request.args.get("page", 1)))
    per_page     = min(100, int(request.args.get("per_page", 20)))

    query = Gimnasio.query

    if activo_param is not None:
        query = query.filter(Gimnasio.activo == (activo_param.lower() == "true"))
    if plan_param:
        query = query.filter(Gimnasio.plan == plan_param)

    query = query.order_by(Gimnasio.created_at.desc())
    paginado = query.paginate(page=page, per_page=per_page, error_out=False)

    items = []
    for gym in paginado.items:
        sub = _suscripcion_activa(gym.id)
        items.append({
            **gym.to_dict(),
            "total_usuarios": Usuario.query.filter_by(id_gimnasio=gym.id).count(),
            "suscripcion":    sub,
        })

    return jsonify({
        "gimnasios": items,
        "total":     paginado.total,
        "activos":   Gimnasio.query.filter_by(activo=True).count(),
        "page":      page,
        "pages":     paginado.pages,
        "per_page":  per_page,
    }), 200


@gimnasios_admin_bp.route("/gimnasios/<int:gym_id>", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def detalle_gimnasio(gym_id: int):
    """Devuelve detalle completo de un gimnasio: datos PG + métricas MongoDB."""
    gym = Gimnasio.query.get_or_404(gym_id)

    usuarios = Usuario.query.filter_by(id_gimnasio=gym_id).all()
    staff    = [
        {"id": u.id, "nombre": u.nombre, "email": u.email,
         "rol": u.rol.nombre if u.rol else None, "activo": u.activo}
        for u in usuarios
    ]

    return jsonify({
        **gym.to_dict(),
        "staff":       staff,
        "suscripcion": _suscripcion_activa(gym_id),
        "metricas":    _metricas_mongo(gym_id),
    }), 200


@gimnasios_admin_bp.route("/gimnasios/<int:gym_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("superadmin")
def toggle_gimnasio(gym_id: int):
    """
    Activa o desactiva un gimnasio.
    Desactivar bloquea el login de todos sus usuarios (check en auth/routes.py).
    """
    gym = Gimnasio.query.get_or_404(gym_id)
    gym.activo = not gym.activo
    db.session.commit()

    accion = "activado" if gym.activo else "desactivado"
    return jsonify({
        "msg":    f"Gimnasio {accion} correctamente.",
        "id":     gym.id,
        "nombre": gym.nombre,
        "activo": gym.activo,
    }), 200


@gimnasios_admin_bp.route("/gimnasios/<int:gym_id>/metricas", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def metricas_gimnasio(gym_id: int):
    """
    Devuelve métricas operativas extendidas del gimnasio desde MongoDB.
    Útil para el dashboard de plataforma del superadmin.
    """
    Gimnasio.query.get_or_404(gym_id)   # 404 si no existe
    mdb    = get_db()
    filtro = {"id_gimnasio": gym_id}

    # Asistencias últimos 12 meses
    hace_12_meses = datetime.utcnow() - timedelta(days=365)
    pipeline_asist = [
        {"$match": {
            **filtro,
            "$expr": {"$gte": [{"$toDate": "$fecha"}, hace_12_meses]}
        }},
        {"$group": {
            "_id": {"$substr": [{"$toString": {"$toDate": "$fecha"}}, 0, 7]},
            "total": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}},
    ]
    asistencias_12m = [
        {"periodo": r["_id"], "total": r["total"]}
        for r in mdb.asistencias.aggregate(pipeline_asist)
    ]

    # Ingresos últimos 12 meses por método de pago
    pipeline_ingresos = [
        {"$match": {
            **filtro,
            "$expr": {"$gte": [{"$toDate": "$fecha_pago"}, hace_12_meses]}
        }},
        {"$group": {
            "_id": {
                "periodo":     {"$substr": [{"$toString": {"$toDate": "$fecha_pago"}}, 0, 7]},
                "metodo_pago": "$metodo_pago",
            },
            "total":      {"$sum": "$monto"},
            "num_pagos":  {"$sum": 1},
        }},
        {"$sort": {"_id.periodo": 1}},
    ]
    ingresos_12m = [
        {
            "periodo":     r["_id"]["periodo"],
            "metodo_pago": r["_id"]["metodo_pago"],
            "total":       round(r["total"], 2),
            "num_pagos":   r["num_pagos"],
        }
        for r in mdb.pagos.aggregate(pipeline_ingresos)
    ]

    # Distribución de membresías activas
    membresias_activas = mdb.miembro_membresia.count_documents(
        {**filtro, "estado": "Activa"}
    ) if filtro.get("id_gimnasio") else mdb.miembro_membresia.count_documents({"estado": "Activa"})

    return jsonify({
        "gym_id":              gym_id,
        "asistencias_12m":     asistencias_12m,
        "ingresos_12m":        ingresos_12m,
        "membresias_activas":  membresias_activas,
        "generado_en":         datetime.utcnow().isoformat(),
    }), 200
