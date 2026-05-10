from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from datetime import datetime

spark_mapreduce_bp = Blueprint("spark_mapreduce", __name__)

_spark_instance = None
def _get_spark():
    global _spark_instance
    if _spark_instance is None:
        import sys, os
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)
        from spark_config import crear_spark_session
        _spark_instance = crear_spark_session()
    return _spark_instance


# ──────────────────────────────────────────────────────────────────────────────
# CACHÉ
# ──────────────────────────────────────────────────────────────────────────────

CACHE_COLLECTION = "analytics_cache"
CACHE_KEY        = "mapreduce_resultado"


def _get_cached_result():
    try:
        import sys, os
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)
        from spark_config import get_mongo_db
        db  = get_mongo_db()
        doc = db[CACHE_COLLECTION].find_one({"_id": CACHE_KEY})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as e:
        print(f"[mapreduce cache] Error leyendo caché: {e}")
    return None


def _save_cached_result(payload: dict):
    try:
        import sys, os
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)
        from spark_config import get_mongo_db
        db = get_mongo_db()
        db[CACHE_COLLECTION].replace_one(
            {"_id": CACHE_KEY},
            {"_id": CACHE_KEY, **payload},
            upsert=True
        )
    except Exception as e:
        print(f"[mapreduce cache] Error guardando caché: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA MAPREDUCE: INGRESOS Y ASISTENCIA
# ──────────────────────────────────────────────────────────────────────────────

def _mapreduce_ingresos(spark):
    import sys, os
    # Asegura que el entorno reconozca módulos locales para la configuración de Spark
    directorio_actual = os.path.dirname(__file__)
    if directorio_actual not in sys.path:
        sys.path.insert(0, directorio_actual)

    from spark_config import leer_coleccion
    from pyspark.sql import functions as F

    # Carga de datos crudos desde la colección de pagos
    df = leer_coleccion(spark, "pagos")

    # FASE DE MAPPING:
    # Seleccionamos y transformamos las columnas necesarias.
    # Se formatea la fecha a "año-mes" para crear las claves de agrupación.
    df_mapped = df.select(
        F.date_format(F.col("fecha_pago"), "yyyy-MM").alias("periodo"),
        F.col("metodo_pago"),
        F.col("monto").cast("double").alias("monto")
    ).filter(F.col("monto").isNotNull()) # Limpieza de registros sin valor monetario

    # FASE DE REDUCING (Detalle por Método):
    # Agrupamos por periodo y método para calcular métricas financieras distribuidas.
    resultado = (
        df_mapped
        .groupBy("periodo", "metodo_pago")
        .agg(
            F.sum("monto").alias("total_ingresos"),
            F.count("*").alias("num_pagos"),
            F.avg("monto").alias("promedio_pago")
        )
        .orderBy("periodo", "metodo_pago")
    )

    # FASE DE REDUCING (Resumen Mensual):
    # Segunda agregación para obtener el gran total por mes, independientemente del método.
    resumen_periodo = (
        df_mapped
        .groupBy("periodo")
        .agg(
            F.sum("monto").alias("total_periodo"),
            F.count("*").alias("total_transacciones")
        )
        .orderBy("periodo")
    )

    # Recolección de resultados al driver para su posterior procesamiento
    return (
        [row.asDict() for row in resultado.collect()],
        [row.asDict() for row in resumen_periodo.collect()]
    )


def _mapreduce_asistencia(spark):
    import sys, os
    directorio_actual = os.path.dirname(__file__)
    if directorio_actual not in sys.path:
        sys.path.insert(0, directorio_actual)

    from spark_config import leer_coleccion
    from pyspark.sql import functions as F

    df = leer_coleccion(spark, "asistencias")

    # FASE DE MAPPING:
    # Extraemos dimensiones temporales (mes y nombre del día) de la marca de tiempo.
    df_mapped = df.select(
        F.col("fecha"),
        F.date_format(F.col("fecha"), "yyyy-MM").alias("periodo"),
        F.date_format(F.col("fecha"), "EEEE").alias("dia_semana")
    ).filter(F.col("fecha").isNotNull())

    # FASE DE REDUCING (Volumen Mensual):
    # Conteo de concurrencia total por mes.
    por_mes = (
        df_mapped
        .groupBy("periodo")
        .agg(F.count("*").alias("total_visitas"))
        .orderBy("periodo")
    )

    # FASE DE REDUCING (Frecuencia por Día):
    # Identificación de los días de la semana con mayor afluencia.
    por_dia = (
        df_mapped
        .groupBy("dia_semana")
        .agg(F.count("*").alias("total_visitas"))
        .orderBy(F.count("*").desc()) # Orden descendente para ver picos de asistencia
    )

    return (
        [row.asDict() for row in por_mes.collect()],
        [row.asDict() for row in por_dia.collect()]
    )


def _clean(lst):
    """
    Función de utilidad para sanitizar tipos de datos antes de la serialización JSON.
    Convierte objetos Decimal de Spark/BSON a floats estándar y redondea a 2 decimales.
    """
    cleaned = []
    for row in lst:
        clean_row = {}
        for k, v in row.items():
            if hasattr(v, 'to_decimal'): # Manejo específico para tipos Decimal de Spark
                clean_row[k] = float(v.to_decimal())
            elif isinstance(v, float):
                clean_row[k] = round(v, 2)
            else:
                clean_row[k] = v
        cleaned.append(clean_row)
    return cleaned


def _ejecutar_y_construir_payload(spark):
    """
    Orquestador principal: Ejecuta las tareas de MapReduce y construye el objeto de respuesta.
    Consolidando los ingresos, tendencias de asistencia y metadatos de ejecución.
    """
    from datetime import datetime

    # Disparo de las tareas de análisis
    ingresos_detalle, resumen_ingresos = _mapreduce_ingresos(spark)
    asistencia_mes,   asistencia_dia   = _mapreduce_asistencia(spark)

    # Construcción de la estructura final (Payload)
    return {
        "algoritmo":   "MapReduce",
        "descripcion": "Agregación distribuida de ingresos y asistencia por periodo",
        "ingresos_por_periodo":      _clean(ingresos_detalle),
        "resumen_ingresos":          _clean(resumen_ingresos),
        "asistencia_por_mes":        _clean(asistencia_mes),
        "asistencia_por_dia_semana": _clean(asistencia_dia),
        "ejecutado_en":              datetime.now().isoformat()
    }

# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@spark_mapreduce_bp.route("/api/analytics/mapreduce", methods=["GET"])
@jwt_required()
def mapreduce_analytics():
    """
    Devuelve el resultado desde caché. Si no existe, lo calcula por primera vez.
    """
    try:
        cached = _get_cached_result()
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark   = _get_spark()
        payload = _ejecutar_y_construir_payload(spark)
        payload["desde_cache"] = False
        _save_cached_result(payload)
        return jsonify(payload), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_mapreduce_bp.route("/api/analytics/mapreduce/train", methods=["POST"])
@jwt_required()
def mapreduce_train():
    """
    Fuerza la re-ejecución del MapReduce y actualiza la caché.
    No requiere body.
    """
    try:
        spark   = _get_spark()
        payload = _ejecutar_y_construir_payload(spark)
        payload["desde_cache"] = False
        _save_cached_result(payload)

        return jsonify({
            **payload,
            "mensaje": "MapReduce re-ejecutado y caché actualizada."
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500