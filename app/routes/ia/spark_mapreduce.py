"""
spark_mapreduce.py — Análisis MapReduce de ingresos y asistencia por período.

Motor: pymongo aggregation pipeline (en proceso, sin JVM, sin internet).
MongoDB ya implementa el patrón Map→Reduce via $group + $sort nativo.

Caché por gym_id con TTL configurable (ANALYTICS_CACHE_TTL_HOURS).
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_mapreduce_bp = Blueprint("spark_mapreduce", __name__)


def _cache_key(gym_id) -> str:
    return f"mapreduce_gym{gym_id}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_periodo(val) -> str | None:
    """Extrae 'YYYY-MM' de un valor datetime o string ISO."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m")
    if isinstance(val, str):
        s = val.strip()
        if len(s) >= 7:
            return s[:7]
    return None


# ── MapReduce de ingresos ─────────────────────────────────────────────────────

def _mapreduce_ingresos(db, gym_id=None):
    """
    MAP: (periodo, metodo_pago) → monto
    REDUCE: sum(monto), count, avg(monto)
    """
    match = {}
    if gym_id is not None:
        match["id_gimnasio"] = int(gym_id)

    # Pymongo aggregation — equivalente exacto al Spark groupBy + agg
    pipeline_detalle = [
        {"$match": match},
        {"$addFields": {
            "periodo": {"$dateToString": {"format": "%Y-%m", "date": {
                "$cond": [
                    {"$type": ["$fecha_pago"]},
                    "$fecha_pago",
                    {"$dateFromString": {"dateString": "$fecha_pago", "onError": None}},
                ]
            }}},
        }},
        {"$match": {"periodo": {"$ne": None}, "monto": {"$ne": None}}},
        {"$group": {
            "_id": {"periodo": "$periodo", "metodo": "$metodo_pago"},
            "total_ingresos":  {"$sum": "$monto"},
            "num_pagos":       {"$sum": 1},
            "promedio_pago":   {"$avg": "$monto"},
        }},
        {"$sort": {"_id.periodo": 1, "_id.metodo": 1}},
    ]

    pipeline_resumen = [
        {"$match": match},
        {"$addFields": {
            "periodo": {"$dateToString": {"format": "%Y-%m", "date": {
                "$cond": [
                    {"$type": ["$fecha_pago"]},
                    "$fecha_pago",
                    {"$dateFromString": {"dateString": "$fecha_pago", "onError": None}},
                ]
            }}},
        }},
        {"$match": {"periodo": {"$ne": None}}},
        {"$group": {
            "_id":                  "$periodo",
            "total_periodo":        {"$sum": "$monto"},
            "total_transacciones":  {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]

    detalle = [
        {
            "periodo":         r["_id"]["periodo"],
            "metodo_pago":     r["_id"]["metodo"] or "Sin especificar",
            "total_ingresos":  _safe_float(r["total_ingresos"]),
            "num_pagos":       int(r["num_pagos"]),
            "promedio_pago":   _safe_float(r["promedio_pago"]),
        }
        for r in db.pagos.aggregate(pipeline_detalle)
    ]

    resumen = [
        {
            "periodo":               r["_id"],
            "total_periodo":         _safe_float(r["total_periodo"]),
            "total_transacciones":   int(r["total_transacciones"]),
        }
        for r in db.pagos.aggregate(pipeline_resumen)
    ]

    return detalle, resumen


# ── MapReduce de asistencia ───────────────────────────────────────────────────

_DIA_ES = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo",
}


def _mapreduce_asistencia(db, gym_id=None):
    """
    MAP: fecha → (periodo, dia_semana)
    REDUCE: count por mes, count por día de la semana
    """
    match = {}
    if gym_id is not None:
        match["id_gimnasio"] = int(gym_id)

    # Recuperar todas las fechas y procesar en Python (más simple que $dayOfWeek en todos los formatos)
    registros = list(db.asistencias.find(match, {"fecha": 1, "_id": 0}))

    por_mes: dict[str, int] = {}
    por_dia: dict[str, int] = {}

    for r in registros:
        fecha = r.get("fecha")
        dt    = None
        if isinstance(fecha, datetime):
            dt = fecha.replace(tzinfo=None)
        elif isinstance(fecha, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(fecha[:len(fmt)], fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            continue
        mes = dt.strftime("%Y-%m")
        dia = _DIA_ES.get(dt.strftime("%A"), dt.strftime("%A"))
        por_mes[mes] = por_mes.get(mes, 0) + 1
        por_dia[dia] = por_dia.get(dia, 0) + 1

    asist_mes = sorted(
        [{"periodo": m, "total_visitas": c} for m, c in por_mes.items()],
        key=lambda x: x["periodo"]
    )
    asist_dia = sorted(
        [{"dia_semana": d, "total_visitas": c} for d, c in por_dia.items()],
        key=lambda x: x["total_visitas"], reverse=True
    )

    return asist_mes, asist_dia


# ── Construcción de payload ───────────────────────────────────────────────────

def _ejecutar_y_construir_payload(gym_id=None) -> dict:
    db = get_mongo_db()
    ingresos_detalle, resumen_ingresos = _mapreduce_ingresos(db, gym_id)
    asistencia_mes,   asistencia_dia   = _mapreduce_asistencia(db, gym_id)

    return {
        "algoritmo":                 "MapReduce (pymongo aggregation)",
        "descripcion":               "Agregación de ingresos y asistencia por período",
        "ingresos_por_periodo":      ingresos_detalle,
        "resumen_ingresos":          resumen_ingresos,
        "asistencia_por_mes":        asistencia_mes,
        "asistencia_por_dia_semana": asistencia_dia,
        "ejecutado_en":              datetime.now().isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@spark_mapreduce_bp.route("/api/analytics/mapreduce", methods=["GET"])
@jwt_required()
def mapreduce_analytics():
    """Devuelve resultado desde caché. Si expiró, re-ejecuta y actualiza."""
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        payload = _ejecutar_y_construir_payload(gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_mapreduce_bp.route("/api/analytics/mapreduce/train", methods=["POST"])
@jwt_required()
def mapreduce_train():
    """Fuerza re-ejecución del MapReduce y actualiza caché."""
    try:
        gym_id  = get_jwt().get("id_gimnasio")
        key     = _cache_key(gym_id)
        payload = _ejecutar_y_construir_payload(gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify({**payload,
                        "mensaje": f"MapReduce re-ejecutado para gimnasio {gym_id}."}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
