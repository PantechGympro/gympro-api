"""
spark_mapreduce.py -- Analisis MapReduce de ingresos y asistencia por periodo.

Cache por gym_id con TTL configurable (ANALYTICS_CACHE_TTL_HOURS).
La SparkSession es el singleton de spark_config.get_spark().
F.to_timestamp() normaliza tanto strings ISO como ISODate nativos de MongoDB.
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import get_spark, cache_get, cache_set

spark_mapreduce_bp = Blueprint("spark_mapreduce", __name__)


def _cache_key(gym_id) -> str:
    return f"mapreduce_gym{gym_id}"


# ------------------------------------------------------------------------------
# LOGICA MAPREDUCE
# ------------------------------------------------------------------------------

def _mapreduce_ingresos(spark, gym_id=None):
    from pyspark.sql import functions as F
    from app.routes.ia.spark_config import leer_coleccion

    df = leer_coleccion(spark, "pagos")
    if gym_id is not None:
        df = df.filter(F.col("id_gimnasio") == int(gym_id))

    # F.to_timestamp normaliza tanto strings ISO-8601 como ISODate de MongoDB
    df_mapped = (
        df.withColumn("fecha_dt", F.to_timestamp(F.col("fecha_pago")))
        .select(
            F.date_format(F.col("fecha_dt"), "yyyy-MM").alias("periodo"),
            F.col("metodo_pago"),
            F.col("monto").cast("double").alias("monto"),
        )
        .filter(F.col("monto").isNotNull() & F.col("periodo").isNotNull())
    )

    # REDUCE: detalle por metodo de pago y periodo
    resultado = (
        df_mapped
        .groupBy("periodo", "metodo_pago")
        .agg(
            F.round(F.sum("monto"), 2).alias("total_ingresos"),
            F.count("*").alias("num_pagos"),
            F.round(F.avg("monto"), 2).alias("promedio_pago"),
        )
        .orderBy("periodo", "metodo_pago")
    )

    # REDUCE: resumen total por periodo (independiente del metodo)
    resumen_periodo = (
        df_mapped
        .groupBy("periodo")
        .agg(
            F.round(F.sum("monto"), 2).alias("total_periodo"),
            F.count("*").alias("total_transacciones"),
        )
        .orderBy("periodo")
    )

    return (
        [row.asDict() for row in resultado.collect()],
        [row.asDict() for row in resumen_periodo.collect()],
    )


def _mapreduce_asistencia(spark, gym_id=None):
    from pyspark.sql import functions as F
    from app.routes.ia.spark_config import leer_coleccion

    df = leer_coleccion(spark, "asistencias")
    if gym_id is not None:
        df = df.filter(F.col("id_gimnasio") == int(gym_id))

    # MAP: extraer dimensiones temporales normalizando la fecha
    df_mapped = (
        df.withColumn("fecha_dt", F.to_timestamp(F.col("fecha")))
        .select(
            F.date_format(F.col("fecha_dt"), "yyyy-MM").alias("periodo"),
            F.date_format(F.col("fecha_dt"), "EEEE").alias("dia_semana"),
        )
        .filter(F.col("periodo").isNotNull())
    )

    # REDUCE: volumen mensual
    por_mes = (
        df_mapped
        .groupBy("periodo")
        .agg(F.count("*").alias("total_visitas"))
        .orderBy("periodo")
    )

    # REDUCE: frecuencia por dia de la semana
    por_dia = (
        df_mapped
        .groupBy("dia_semana")
        .agg(F.count("*").alias("total_visitas"))
        .orderBy(F.count("*").desc())
    )

    return (
        [row.asDict() for row in por_mes.collect()],
        [row.asDict() for row in por_dia.collect()],
    )


def _clean(lst: list) -> list:
    """Sanitiza tipos Decimal/float de Spark antes de la serializacion JSON."""
    cleaned = []
    for row in lst:
        clean_row = {}
        for k, v in row.items():
            if hasattr(v, "to_decimal"):
                clean_row[k] = round(float(v.to_decimal()), 2)
            elif isinstance(v, float):
                clean_row[k] = round(v, 2)
            else:
                clean_row[k] = v
        cleaned.append(clean_row)
    return cleaned


def _ejecutar_y_construir_payload(spark, gym_id=None) -> dict:
    ingresos_detalle, resumen_ingresos = _mapreduce_ingresos(spark, gym_id)
    asistencia_mes,   asistencia_dia   = _mapreduce_asistencia(spark, gym_id)

    return {
        "algoritmo":                 "MapReduce",
        "descripcion":               "Agregacion distribuida de ingresos y asistencia por periodo",
        "ingresos_por_periodo":      _clean(ingresos_detalle),
        "resumen_ingresos":          _clean(resumen_ingresos),
        "asistencia_por_mes":        _clean(asistencia_mes),
        "asistencia_por_dia_semana": _clean(asistencia_dia),
        "ejecutado_en":              datetime.now().isoformat(),
    }


# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------

@spark_mapreduce_bp.route("/api/analytics/mapreduce", methods=["GET"])
@jwt_required()
def mapreduce_analytics():
    """
    Devuelve el resultado desde cache (TTL = ANALYTICS_CACHE_TTL_HOURS).
    Si expiro o no existe, re-ejecuta el MapReduce y actualiza.
    """
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark   = get_spark()
        payload = _ejecutar_y_construir_payload(spark, gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_mapreduce_bp.route("/api/analytics/mapreduce/train", methods=["POST"])
@jwt_required()
def mapreduce_train():
    """
    Fuerza re-ejecucion del MapReduce y actualiza cache para este gimnasio.
    No requiere body.
    """
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        spark   = get_spark()
        payload = _ejecutar_y_construir_payload(spark, gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)

        return jsonify({
            **payload,
            "mensaje": f"MapReduce re-ejecutado para gimnasio {gym_id}.",
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
