"""
superadmin/spark_platform.py — Analytics de plataforma para el superadmin.

Motor: pymongo aggregation + scikit-learn Ridge (en proceso, sin JVM, sin internet).
Opera SIN filtro de id_gimnasio — agrega datos de TODOS los gimnasios.

Endpoints:
    GET  /api/superadmin/analytics/plataforma      ingresos y miembros por gimnasio
    POST /api/superadmin/analytics/plataforma      forzar re-cálculo
    GET  /api/superadmin/analytics/proyeccion      proyección de ingresos futuros (regresión)
    GET  /api/superadmin/analytics/churn-gimnasios gimnasios con riesgo de churn SaaS
    GET  /api/superadmin/analytics/crecimiento     crecimiento de miembros por gimnasio
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from datetime import datetime, timedelta

from app.utils.security import require_role
from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_platform_bp = Blueprint("spark_platform", __name__)

_CACHE_TTL_PLATFORM = 6   # horas


# ── Analytics de plataforma ───────────────────────────────────────────────────

def _analytics_plataforma() -> dict:
    """
    Agrega ingresos totales y miembros por gimnasio via pymongo.
    Enriquece nombres de gimnasio desde PostgreSQL.
    """
    from collections import defaultdict
    from app.models.pg.gimnasio import Gimnasio

    db = get_mongo_db()

    # Ingresos por (id_gimnasio, periodo)
    pipeline_ingresos = [
        {"$match": {"monto": {"$ne": None}}},
        {"$addFields": {
            "fecha_dt": {"$cond": [
                {"$eq": [{"$type": "$fecha_pago"}, "date"]},
                "$fecha_pago",
                {"$dateFromString": {"dateString": {"$toString": "$fecha_pago"}, "onError": None}},
            ]},
        }},
        {"$match": {"fecha_dt": {"$ne": None}}},
        {"$group": {
            "_id": {
                "gym":     "$id_gimnasio",
                "periodo": {"$dateToString": {"format": "%Y-%m", "date": "$fecha_dt"}},
            },
            "ingresos":  {"$sum": "$monto"},
            "num_pagos": {"$sum": 1},
        }},
        {"$sort": {"_id.gym": 1, "_id.periodo": 1}},
    ]
    ingresos_periodo = [
        {
            "id_gimnasio": r["_id"]["gym"],
            "periodo":     r["_id"]["periodo"],
            "ingresos":    round(float(r["ingresos"] or 0), 2),
            "num_pagos":   int(r["num_pagos"]),
        }
        for r in db.pagos.aggregate(pipeline_ingresos)
        if r["_id"].get("periodo")
    ]

    # Totales por gimnasio
    pipeline_total = [
        {"$match": {"monto": {"$ne": None}}},
        {"$group": {
            "_id":                  "$id_gimnasio",
            "ingresos_totales":     {"$sum": "$monto"},
            "total_transacciones":  {"$sum": 1},
            "ticket_promedio":      {"$avg": "$monto"},
        }},
    ]
    totales_map = {
        str(r["_id"]): {
            "ingresos_totales":    round(float(r["ingresos_totales"] or 0), 2),
            "total_transacciones": int(r["total_transacciones"]),
            "ticket_promedio":     round(float(r["ticket_promedio"] or 0), 2),
        }
        for r in db.pagos.aggregate(pipeline_total)
    }

    # Miembros por gimnasio
    pipeline_miembros = [
        {"$group": {
            "_id":           "$id_gimnasio_pg",
            "total":         {"$sum": 1},
            "activos":       {"$sum": {"$cond": [{"$eq": ["$estado", "Activo"]}, 1, 0]}},
        }},
    ]
    miembros_map = {
        str(r["_id"]): {"total": int(r["total"]), "activos": int(r["activos"])}
        for r in db.miembros.aggregate(pipeline_miembros)
    }

    # Enriquecer con nombre de gimnasio desde PG
    gym_ids = set(totales_map.keys()) | set(miembros_map.keys())
    try:
        gym_int_ids = [int(g) for g in gym_ids if g and str(g).isdigit()]
        gyms = Gimnasio.query.filter(Gimnasio.id.in_(gym_int_ids)).all()
        gym_name_map = {str(g.id): g.nombre for g in gyms}
        gym_plan_map = {
            str(g.id): (g.plan.value if hasattr(g.plan, "value") else str(g.plan))
            for g in gyms
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Error enriqueciendo nombres de gym: %s", e)
        gym_name_map = {}
        gym_plan_map = {}

    resumen = []
    for gym_id in sorted(gym_ids):
        t = totales_map.get(gym_id, {})
        m = miembros_map.get(gym_id, {})
        resumen.append({
            "id_gimnasio":         gym_id,
            "gimnasio":            gym_name_map.get(gym_id, f"Gimnasio {gym_id}"),
            "plan":                gym_plan_map.get(gym_id),
            "ingresos_totales":    t.get("ingresos_totales", 0.0),
            "total_transacciones": t.get("total_transacciones", 0),
            "ticket_promedio":     t.get("ticket_promedio", 0.0),
            "total_miembros":      m.get("total", 0),
            "miembros_activos":    m.get("activos", 0),
        })
    resumen.sort(key=lambda x: x["ingresos_totales"], reverse=True)

    return {
        "algoritmo":                "MapReduce Plataforma",
        "ingresos_por_periodo_gym": ingresos_periodo,
        "resumen_por_gimnasio":     resumen,
        "ejecutado_en":             datetime.now().isoformat(),
    }


# ── Proyección de ingresos ────────────────────────────────────────────────────

def _proyeccion_ingresos() -> dict:
    """
    Ridge Regression sobre ingresos mensuales totales de la plataforma.
    Proyecta los próximos 6 meses.
    """
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.metrics import r2_score, mean_squared_error

    db = get_mongo_db()

    pipeline = [
        {"$match": {"monto": {"$ne": None}}},
        {"$addFields": {
            "fecha_dt": {"$cond": [
                {"$eq": [{"$type": "$fecha_pago"}, "date"]},
                "$fecha_pago",
                {"$dateFromString": {"dateString": {"$toString": "$fecha_pago"}, "onError": None}},
            ]},
        }},
        {"$match": {"fecha_dt": {"$ne": None}}},
        {"$group": {
            "_id":      {"$dateToString": {"format": "%Y-%m", "date": "$fecha_dt"}},
            "ingresos": {"$sum": "$monto"},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = [r for r in db.pagos.aggregate(pipeline) if r["_id"]]

    if len(rows) < 3:
        return {"error": "Datos insuficientes para proyección (mínimo 3 períodos)",
                "datos_historicos": []}

    periodos = [r["_id"] for r in rows]
    y = np.array([float(r["ingresos"]) for r in rows])
    X = np.arange(1, len(y) + 1, dtype=float).reshape(-1, 1)

    model  = Ridge(alpha=0.01).fit(X, y)
    y_pred = model.predict(X)

    metricas = {
        "r2":   round(float(r2_score(y, y_pred)), 4),
        "rmse": round(float(mean_squared_error(y, y_pred) ** 0.5), 2),
    }

    # Proyectar 6 meses siguientes
    ultimo = periodos[-1]
    anio, mes = int(ultimo[:4]), int(ultimo[5:7])
    proyecciones = []
    for i in range(1, 7):
        mes_sig  = mes + i
        anio_sig = anio + (mes_sig - 1) // 12
        mes_sig  = ((mes_sig - 1) % 12) + 1
        periodo_str = f"{anio_sig:04d}-{mes_sig:02d}"
        pred = float(model.predict([[len(y) + i]])[0])
        proyecciones.append({"periodo": periodo_str, "proyectado": round(max(0.0, pred), 2)})

    return {
        "algoritmo":        "Regresión Lineal (ingresos plataforma)",
        **metricas,
        "datos_historicos": [{"periodo": r["_id"], "ingresos": float(r["ingresos"])} for r in rows],
        "proyeccion_6m":    proyecciones,
        "ejecutado_en":     datetime.now().isoformat(),
    }


# ── Churn de gimnasios (sin ML — consulta directa PG + Mongo) ────────────────

def _churn_gimnasios() -> dict:
    from app.models.pg.suscripcion import Suscripcion
    from app.models.pg.gimnasio import Gimnasio
    from app.mongo import get_db

    mdb   = get_db()
    ahora = datetime.utcnow()

    subs_riesgo = (
        Suscripcion.query
        .join(Gimnasio)
        .filter(Suscripcion.estado.in_(["past_due", "unpaid", "paused"]))
        .all()
    )

    resultado = []
    for sub in subs_riesgo:
        gym = sub.gimnasio
        if not gym:
            continue

        ultima = mdb.asistencias.find_one(
            {"id_gimnasio": gym.id}, sort=[("fecha", -1)]
        )
        ultima_str    = None
        dias_inactivo = None
        if ultima:
            fecha_raw = ultima.get("fecha")
            if isinstance(fecha_raw, datetime):
                ultima_str    = fecha_raw.isoformat()
                dias_inactivo = (ahora - fecha_raw.replace(tzinfo=None)).days
            elif isinstance(fecha_raw, str):
                try:
                    dt = datetime.fromisoformat(fecha_raw[:10])
                    ultima_str    = dt.isoformat()
                    dias_inactivo = (ahora - dt).days
                except Exception:
                    pass

        estado_str = sub.estado if isinstance(sub.estado, str) else sub.estado.value
        resultado.append({
            "gym_id":              gym.id,
            "gimnasio":            gym.nombre,
            "plan":                sub.plan.nombre if sub.plan else None,
            "estado_sub":          estado_str,
            "fecha_proximo_cobro": sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
            "ultima_actividad":    ultima_str,
            "dias_inactivo":       dias_inactivo,
            "nivel_riesgo":        "ALTO" if estado_str == "unpaid" else "MEDIO",
        })

    return {
        "algoritmo":        "Churn Detection (SaaS)",
        "gimnasios_riesgo": resultado,
        "total_riesgo":     len(resultado),
        "ejecutado_en":     ahora.isoformat(),
    }


# ── Crecimiento mensual de miembros por gimnasio ──────────────────────────────

def _crecimiento_miembros() -> dict:
    from app.models.pg.gimnasio import Gimnasio

    db = get_mongo_db()

    pipeline = [
        {"$addFields": {
            "fecha_dt": {"$cond": [
                {"$eq": [{"$type": "$fecha_registro"}, "date"]},
                "$fecha_registro",
                {"$dateFromString": {"dateString": {"$toString": "$fecha_registro"}, "onError": None}},
            ]},
        }},
        {"$match": {"fecha_dt": {"$ne": None}}},
        {"$group": {
            "_id": {
                "gym":     "$id_gimnasio_pg",
                "periodo": {"$dateToString": {"format": "%Y-%m", "date": "$fecha_dt"}},
            },
            "nuevos_miembros": {"$sum": 1},
        }},
        {"$sort": {"_id.gym": 1, "_id.periodo": 1}},
    ]
    rows = [r for r in db.miembros.aggregate(pipeline) if r["_id"].get("periodo")]

    # Enriquecer nombres
    gym_ids = {r["_id"]["gym"] for r in rows if r["_id"]["gym"]}
    try:
        gym_int_ids = [int(g) for g in gym_ids if g is not None]
        gyms        = Gimnasio.query.filter(Gimnasio.id.in_(gym_int_ids)).all()
        gym_map     = {str(g.id): g.nombre for g in gyms}
    except Exception:
        gym_map = {}

    crecimiento = [
        {
            "gym_id":          r["_id"]["gym"],
            "gimnasio":        gym_map.get(str(r["_id"]["gym"]), f"Gimnasio {r['_id']['gym']}"),
            "periodo":         r["_id"]["periodo"],
            "nuevos_miembros": int(r["nuevos_miembros"]),
        }
        for r in rows
    ]

    return {
        "algoritmo":    "Crecimiento Mensual por Gimnasio",
        "crecimiento":  crecimiento,
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@spark_platform_bp.route("/analytics/plataforma", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def analytics_plataforma():
    key    = "platform_analytics_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200
    try:
        payload = _analytics_plataforma()
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/plataforma", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def analytics_plataforma_refresh():
    try:
        payload = _analytics_plataforma()
        payload["desde_cache"] = False
        cache_set("platform_analytics_v1", payload)
        return jsonify({**payload, "msg": "Analytics de plataforma actualizados."}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/proyeccion", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def proyeccion_ingresos():
    key    = "platform_proyeccion_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200
    try:
        payload = _proyeccion_ingresos()
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/churn-gimnasios", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def churn_gimnasios():
    try:
        return jsonify(_churn_gimnasios()), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/crecimiento", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def crecimiento_miembros():
    key    = "platform_crecimiento_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200
    try:
        payload = _crecimiento_miembros()
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
