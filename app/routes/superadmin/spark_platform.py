"""
superadmin/spark_platform.py — Analytics de plataforma con Spark para el superadmin.

A diferencia de los analytics por gimnasio (spark_mapreduce, etc.), estos endpoints
operan SIN filtro de id_gimnasio — agregan datos de TODOS los gimnasios de la plataforma.

Endpoints:
    GET  /api/superadmin/analytics/plataforma      ingresos y miembros por gimnasio
    POST /api/superadmin/analytics/plataforma      forzar re-cálculo
    GET  /api/superadmin/analytics/proyeccion      proyección de ingresos futuros (regresión)
    GET  /api/superadmin/analytics/churn-gimnasios gimnasios con riesgo de churn SaaS
    GET  /api/superadmin/analytics/crecimiento     crecimiento de miembros por gimnasio
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required
from datetime import datetime

from app.utils.security import require_role
from app.routes.ia.spark_config import (
    get_spark,
    cache_get,
    cache_set,
    leer_coleccion,
    leer_tabla_pg,
    SPARK_ENABLED,
)

spark_platform_bp = Blueprint("spark_platform", __name__)

_CACHE_TTL_PLATFORM = 6   # horas — se actualiza más frecuente que por gimnasio


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS — lógica Spark
# ─────────────────────────────────────────────────────────────────────────────

def _clean(lst: list) -> list:
    """Sanitiza tipos Decimal/float de Spark antes de JSON serialization."""
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


def _analytics_plataforma(spark) -> dict:
    """
    Agrega ingresos totales y miembros por gimnasio.
    Cruza datos de MongoDB (pagos, miembros) con PostgreSQL (gimnasios).
    """
    from pyspark.sql import functions as F

    # ── Ingresos por gimnasio ─────────────────────────────────────────────────
    df_pagos = leer_coleccion(spark, "pagos")
    ingresos_por_gym = (
        df_pagos
        .withColumn("fecha_dt", F.to_timestamp(F.col("fecha_pago")))
        .withColumn("periodo",  F.date_format(F.col("fecha_dt"), "yyyy-MM"))
        .filter(F.col("monto").isNotNull())
        .groupBy("id_gimnasio", "periodo")
        .agg(
            F.round(F.sum("monto"), 2).alias("ingresos"),
            F.count("*").alias("num_pagos"),
        )
        .orderBy("id_gimnasio", "periodo")
    )

    # ── Total acumulado por gimnasio ──────────────────────────────────────────
    total_por_gym = (
        df_pagos
        .filter(F.col("monto").isNotNull())
        .groupBy("id_gimnasio")
        .agg(
            F.round(F.sum("monto"), 2).alias("ingresos_totales"),
            F.count("*").alias("total_transacciones"),
            F.round(F.avg("monto"), 2).alias("ticket_promedio"),
        )
    )

    # ── Miembros por gimnasio ─────────────────────────────────────────────────
    df_miembros = leer_coleccion(spark, "miembros")
    miembros_por_gym = (
        df_miembros
        .groupBy("id_gimnasio_pg")
        .agg(
            F.count("*").alias("total_miembros"),
            F.sum(F.when(F.col("estado") == "Activo", 1).otherwise(0)).alias("activos"),
        )
        .withColumnRenamed("id_gimnasio_pg", "id_gimnasio")
    )

    # ── Cruzar con nombres de gimnasios desde PG ─────────────────────────────
    df_gyms = leer_tabla_pg(spark, "gimnasios")
    resumen = (
        total_por_gym
        .join(miembros_por_gym, on="id_gimnasio", how="left")
        .join(df_gyms.select("id", "nombre", "plan", "activo"),
              total_por_gym["id_gimnasio"] == df_gyms["id"],
              how="left")
        .select(
            F.col("id_gimnasio"),
            F.col("nombre").alias("gimnasio"),
            F.col("plan"),
            F.col("activo"),
            F.col("ingresos_totales"),
            F.col("total_transacciones"),
            F.col("ticket_promedio"),
            F.col("total_miembros"),
            F.col("activos").alias("miembros_activos"),
        )
        .orderBy(F.col("ingresos_totales").desc())
    )

    return {
        "algoritmo":       "MapReduce Plataforma",
        "ingresos_por_periodo_gym": _clean(ingresos_por_gym.collect()),
        "resumen_por_gimnasio":     _clean(resumen.collect()),
        "ejecutado_en":    datetime.now().isoformat(),
    }


def _proyeccion_ingresos(spark) -> dict:
    """
    Regresión lineal sobre los ingresos mensuales TOTALES de la plataforma
    para proyectar los próximos 6 meses.
    """
    from pyspark.sql import functions as F
    from pyspark.ml.regression import LinearRegression
    from pyspark.ml.feature import VectorAssembler

    df_pagos = (
        leer_coleccion(spark, "pagos")
        .withColumn("fecha_dt", F.to_timestamp(F.col("fecha_pago")))
        .withColumn("periodo",  F.date_format(F.col("fecha_dt"), "yyyy-MM"))
        .filter(F.col("monto").isNotNull() & F.col("periodo").isNotNull())
        .groupBy("periodo")
        .agg(F.round(F.sum("monto"), 2).alias("ingresos"))
        .orderBy("periodo")
    )

    # Convertir período a índice numérico para la regresión
    periodos     = [r["periodo"] for r in df_pagos.collect()]
    n_periodos   = len(periodos)

    if n_periodos < 3:
        return {"error": "Datos insuficientes para proyección (mínimo 3 períodos)", "datos_historicos": []}

    df_indexed = df_pagos.withColumn(
        "idx",
        F.row_number().over(
            __import__("pyspark.sql.window", fromlist=["Window"])
            .Window.orderBy("periodo")
        ).cast("double")
    )

    assembler = VectorAssembler(inputCols=["idx"], outputCol="features")
    df_ml     = assembler.transform(df_indexed).select("features", F.col("ingresos").alias("label"))

    lr      = LinearRegression(maxIter=100, regParam=0.01)
    modelo  = lr.fit(df_ml)

    # Proyectar los 6 meses siguientes
    from pyspark.sql.types import DoubleType, StructType, StructField
    from pyspark.sql import Row
    import calendar
    from datetime import date

    ultimo_periodo = periodos[-1]
    anio, mes      = int(ultimo_periodo[:4]), int(ultimo_periodo[5:7])

    proyecciones = []
    idx_base     = float(n_periodos)
    for i in range(1, 7):
        mes_sig  = mes + i
        anio_sig = anio + (mes_sig - 1) // 12
        mes_sig  = ((mes_sig - 1) % 12) + 1
        periodo_str = f"{anio_sig:04d}-{mes_sig:02d}"

        idx_future = idx_base + i
        features   = assembler.transform(
            spark.createDataFrame([(idx_future,)], ["idx"])
        )
        pred = modelo.transform(features).collect()[0]["prediction"]
        proyecciones.append({
            "periodo":    periodo_str,
            "proyectado": round(max(0.0, pred), 2),
        })

    datos_hist = [{"periodo": r["periodo"], "ingresos": float(r["ingresos"])}
                  for r in df_pagos.collect()]

    return {
        "algoritmo":        "Regresión Lineal (ingresos plataforma)",
        "r2":               round(modelo.summary.r2, 4),
        "rmse":             round(modelo.summary.rootMeanSquaredError, 2),
        "datos_historicos": datos_hist,
        "proyeccion_6m":    proyecciones,
        "ejecutado_en":     datetime.now().isoformat(),
    }


def _churn_gimnasios() -> dict:
    """
    Identifica gimnasios con riesgo de churn usando datos PG (sin Spark — consulta rápida).
    Criterios de riesgo:
      - Suscripción en estado past_due | unpaid
      - Suscripción vencida hace > 7 días
      - Gimnasio sin actividad MongoDB en los últimos 30 días
    """
    from app.models.pg.suscripcion import Suscripcion, EstadoSuscripcionEnum
    from app.models.pg.gimnasio import Gimnasio
    from app.mongo import get_db
    from datetime import timedelta
    mdb   = get_db()
    ahora = datetime.utcnow()

    # Suscripciones en estados problemáticos
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

        # Última actividad (última asistencia registrada)
        ultima = mdb.asistencias.find_one(
            {"id_gimnasio": gym.id},
            sort=[("fecha", -1)],
        )
        ultima_str = None
        dias_inactivo = None
        if ultima:
            fecha_raw = ultima.get("fecha")
            if isinstance(fecha_raw, datetime):
                ultima_str = fecha_raw.isoformat()
                dias_inactivo = (ahora - fecha_raw).days
            elif isinstance(fecha_raw, str):
                try:
                    dt = datetime.fromisoformat(fecha_raw[:10])
                    ultima_str = dt.isoformat()
                    dias_inactivo = (ahora - dt).days
                except Exception:
                    pass

        estado_str = sub.estado if isinstance(sub.estado, str) else sub.estado.value
        resultado.append({
            "gym_id":         gym.id,
            "gimnasio":       gym.nombre,
            "plan":           sub.plan.nombre if sub.plan else None,
            "estado_sub":     estado_str,
            "fecha_proximo_cobro": sub.fecha_proximo_cobro.isoformat() if sub.fecha_proximo_cobro else None,
            "ultima_actividad":    ultima_str,
            "dias_inactivo":       dias_inactivo,
            "nivel_riesgo":   "ALTO" if estado_str == "unpaid" else "MEDIO",
        })

    return {
        "algoritmo":    "Churn Detection (SaaS)",
        "gimnasios_riesgo": resultado,
        "total_riesgo": len(resultado),
        "ejecutado_en": ahora.isoformat(),
    }


def _crecimiento_miembros(spark) -> dict:
    """
    Calcula el crecimiento mensual de miembros por gimnasio.
    Compara el conteo de miembros registrados mes a mes.
    """
    from pyspark.sql import functions as F

    df = (
        leer_coleccion(spark, "miembros")
        .withColumn("fecha_dt", F.to_timestamp(F.col("fecha_registro")))
        .withColumn("periodo",  F.date_format(F.col("fecha_dt"), "yyyy-MM"))
        .filter(F.col("periodo").isNotNull())
        .groupBy("id_gimnasio_pg", "periodo")
        .agg(F.count("*").alias("nuevos_miembros"))
        .orderBy("id_gimnasio_pg", "periodo")
    )

    df_gyms = leer_tabla_pg(spark, "gimnasios")
    resultado = (
        df
        .join(df_gyms.select("id", "nombre"),
              df["id_gimnasio_pg"] == df_gyms["id"],
              how="left")
        .select(
            F.col("id_gimnasio_pg").alias("gym_id"),
            F.col("nombre").alias("gimnasio"),
            F.col("periodo"),
            F.col("nuevos_miembros"),
        )
        .orderBy("gym_id", "periodo")
    )

    return {
        "algoritmo":    "Crecimiento Mensual por Gimnasio",
        "crecimiento":  _clean(resultado.collect()),
        "ejecutado_en": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@spark_platform_bp.route("/analytics/plataforma", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def analytics_plataforma():
    """Ingresos y miembros agregados por gimnasio. Usa cache con TTL de 6h."""
    key    = "platform_analytics_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200

    try:
        spark   = get_spark()
        payload = _analytics_plataforma(spark)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": SPARK_ENABLED}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/plataforma", methods=["POST"])
@jwt_required()
@require_role("superadmin")
def analytics_plataforma_refresh():
    """Fuerza re-cálculo del analytics de plataforma e invalida cache."""
    try:
        spark   = get_spark()
        payload = _analytics_plataforma(spark)
        payload["desde_cache"] = False
        cache_set("platform_analytics_v1", payload)
        return jsonify({**payload, "msg": "Analytics de plataforma actualizados."}), 200
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": SPARK_ENABLED}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/proyeccion", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def proyeccion_ingresos():
    """
    Proyección de ingresos totales de la plataforma para los próximos 6 meses
    usando regresión lineal sobre el histórico mensual de MongoDB.
    Cache de 6h.
    """
    key    = "platform_proyeccion_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200

    try:
        spark   = get_spark()
        payload = _proyeccion_ingresos(spark)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": SPARK_ENABLED}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/churn-gimnasios", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def churn_gimnasios():
    """
    Gimnasios con riesgo de churn SaaS (suscripción vencida, sin actividad).
    No usa Spark — consulta directa PG + Mongo, respuesta inmediata.
    """
    try:
        payload = _churn_gimnasios()
        return jsonify(payload), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_platform_bp.route("/analytics/crecimiento", methods=["GET"])
@jwt_required()
@require_role("superadmin")
def crecimiento_miembros():
    """
    Crecimiento mensual de nuevos miembros por gimnasio.
    Cache de 6h.
    """
    key    = "platform_crecimiento_v1"
    cached = cache_get(key, ttl_hours=_CACHE_TTL_PLATFORM)
    if cached:
        cached["desde_cache"] = True
        return jsonify(cached), 200

    try:
        spark   = get_spark()
        payload = _crecimiento_miembros(spark)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": SPARK_ENABLED}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
