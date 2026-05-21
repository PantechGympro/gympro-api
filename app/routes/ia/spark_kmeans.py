"""
spark_kmeans.py -- Clustering K-Means de miembros por composicion corporal.

Cache por (gym_id, k) con TTL configurable (ANALYTICS_CACHE_TTL_HOURS).
La SparkSession es el singleton de spark_config.get_spark().
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import get_spark, cache_get, cache_set

spark_kmeans_bp = Blueprint("spark_kmeans", __name__)

# Etiquetas dinamicas: el indice dentro de la lista = id de cluster ordenado
# por IMC promedio ascendente (se asignan en _build_payload despues de ordenar)
_LABEL_POOL = [
    "Principiante / Alta Prioridad",
    "Intermedio / Mantenimiento",
    "Avanzado / Optimizacion",
    "Elite / Rendimiento",
    "Senior / Bajo Impacto",
    "Recuperacion Activa",
    "Alto Volumen / Hipertrofia",
    "Definicion / Corte",
]


def _cache_key(gym_id, k: int) -> str:
    return f"kmeans_gym{gym_id}_k{k}"


# ------------------------------------------------------------------------------
# LOGICA K-MEANS
# ------------------------------------------------------------------------------

def _ejecutar_kmeans(spark, k: int = 3, max_iter: int = 20,
                     seed: int = 42, gym_id=None):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window
    from pyspark.sql.types import StringType
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import VectorAssembler, StandardScaler
    from pyspark.ml.evaluation import ClusteringEvaluator
    from app.routes.ia.spark_config import leer_coleccion
    import re as _re

    # 1. CARGA Y LIMPIEZA DE MIEMBROS (filtrado por gimnasio si aplica)
    df_miembros = leer_coleccion(spark, "miembros").select(
        F.col("_id").alias("id_miembro"),
        F.col("nombre"),
        F.col("peso_inicial").cast("double"),
        F.col("estatura").cast("double"),
        F.col("sexo"),
        F.col("id_gimnasio_pg").cast("integer"),
    ).filter(
        F.col("peso_inicial").isNotNull() &
        F.col("estatura").isNotNull() &
        (F.col("estatura") > 0)
    )
    if gym_id is not None:
        df_miembros = df_miembros.filter(F.col("id_gimnasio_pg") == int(gym_id))

    # 2. PROGRESO FISICO -- solo el registro mas reciente por miembro
    w = Window.partitionBy("id_miembro_prog").orderBy(F.col("fecha_registro").desc())
    df_progreso = (
        leer_coleccion(spark, "progreso_fisico")
        .select(
            F.col("id_miembro").alias("id_miembro_prog"),
            F.col("peso").cast("double"),
            F.col("imc").cast("double").alias("bmi"),
            F.col("grasa_corporal").cast("double"),
            F.col("masa_muscular").cast("double"),
            F.col("fecha_registro"),
        )
        .withColumn("rn", F.row_number().over(w))
        .filter(F.col("rn") == 1)
        .drop("rn", "fecha_registro")
    )

    # 3. JOIN miembros + progreso
    df = df_miembros.join(
        df_progreso,
        df_miembros["id_miembro"] == df_progreso["id_miembro_prog"],
        "left",
    )

    # 4. INGENIERIA DE CARACTERISTICAS -- IMC calculado + imputacion de nulos
    df = (
        df
        .withColumn("imc_calculado",
            F.when(F.col("bmi").isNotNull(), F.col("bmi"))
             .otherwise(F.col("peso_inicial") / (F.col("estatura") * F.col("estatura"))))
        .withColumn("peso_final",    F.coalesce(F.col("peso"),           F.col("peso_inicial")))
        .withColumn("grasa_final",   F.coalesce(F.col("grasa_corporal"), F.lit(20.0)))
        .withColumn("musculo_final", F.coalesce(F.col("masa_muscular"),  F.lit(30.0)))
    )

    df_features = df.select(
        F.col("id_miembro"),
        F.col("nombre"),
        F.col("peso_final").alias("peso"),
        F.col("imc_calculado").alias("imc"),
        F.col("grasa_final").alias("grasa"),
        F.col("musculo_final").alias("musculo"),
        F.col("sexo"),
    ).filter(F.col("peso").isNotNull() & F.col("imc").isNotNull())

    n = df_features.count()
    if n < k:
        raise ValueError(f"Datos insuficientes: {n} miembros con datos, se necesitan al menos {k}.")

    # 5. VECTORIZACION + ESCALADO
    assembler = VectorAssembler(
        inputCols=["peso", "imc", "grasa", "musculo"], outputCol="features_raw"
    )
    scaler = StandardScaler(
        inputCol="features_raw", outputCol="features", withStd=True, withMean=True
    )
    df_assembled = assembler.transform(df_features)
    df_scaled    = scaler.fit(df_assembled).transform(df_assembled)

    # 6. ENTRENAMIENTO
    model     = KMeans(featuresCol="features", predictionCol="cluster",
                       k=k, maxIter=max_iter, seed=seed).fit(df_scaled)
    df_result = model.transform(df_scaled)

    # 7. EVALUACION -- Coeficiente de Silueta
    silhouette = ClusteringEvaluator(
        featuresCol="features", predictionCol="cluster", metricName="silhouette"
    ).evaluate(df_result)

    # Centroides en espacio escalado (para inspeccion tecnica)
    centroides = [
        {"cluster": i, "peso_norm": round(float(c[0]), 4), "imc_norm": round(float(c[1]), 4),
         "grasa_norm": round(float(c[2]), 4), "musculo_norm": round(float(c[3]), 4)}
        for i, c in enumerate(model.clusterCenters())
    ]

    # 8. RESUMEN por cluster (valores reales sin escalar)
    resumen = (
        df_result.groupBy("cluster")
        .agg(
            F.count("*").alias("num_miembros"),
            F.round(F.avg("peso"),    2).alias("peso_promedio"),
            F.round(F.avg("imc"),     2).alias("imc_promedio"),
            F.round(F.avg("grasa"),   2).alias("grasa_promedio"),
            F.round(F.avg("musculo"), 2).alias("musculo_promedio"),
        )
        .orderBy("imc_promedio")   # ordena por IMC para asignar etiquetas consistentemente
    )

    # Convertir ObjectId -> hex
    def _oid_hex(val):
        if val is None:
            return None
        m = _re.search(r"[0-9a-fA-F]{24}", str(val))
        return m.group(0) if m else str(val)

    oid_udf = F.udf(_oid_hex, StringType())

    asignaciones = (
        df_result.select(
            oid_udf(F.col("id_miembro")).alias("id_miembro"),
            "nombre",
            "cluster", "sexo",
            F.round("peso",    1).alias("peso"),
            F.round("imc",     2).alias("imc"),
            F.round("grasa",   1).alias("grasa"),
            F.round("musculo", 1).alias("musculo"),
        ).orderBy("cluster")
    )

    return (
        [row.asDict() for row in resumen.collect()],
        [row.asDict() for row in asignaciones.collect()],
        centroides,
        round(silhouette, 4),
    )


def _build_payload(k: int, max_iter: int, resumen: list, asignaciones: list,
                   centroides: list, silhouette: float) -> dict:
    """
    Construye la respuesta. Las etiquetas se asignan por posicion en la lista
    de resumen (ya ordenada por imc_promedio asc), de modo que funcionan para
    cualquier valor de k entre 2 y 8.
    """
    resumen_con_etiqueta = [
        {**row, "etiqueta": _LABEL_POOL[i] if i < len(_LABEL_POOL) else f"Grupo {i}"}
        for i, row in enumerate(resumen)
    ]
    return {
        "algoritmo":        "K-Means",
        "descripcion":      f"Clustering de miembros en {k} grupos por composicion corporal",
        "parametros":       {"k": k, "max_iter": max_iter},
        "silhouette":       silhouette,
        "centroides":       centroides,
        "resumen_clusters": resumen_con_etiqueta,
        "asignaciones":     asignaciones,
        "ejecutado_en":     datetime.now().isoformat(),
    }


# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------

@spark_kmeans_bp.route("/api/analytics/kmeans", methods=["GET"])
@jwt_required()
def kmeans_analytics():
    """
    Devuelve el resultado desde cache (TTL = ANALYTICS_CACHE_TTL_HOURS).
    Si expiro o no existe, entrena el modelo y lo guarda.

    Query params:
      k        (int, 2-8, default=3)
      max_iter (int, default=20)  -- solo se usa al entrenar
    """
    try:
        k        = request.args.get("k",        3,  type=int)
        max_iter = request.args.get("max_iter", 20, type=int)
        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id, k)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark = get_spark()
        resumen, asignaciones, centroides, silhouette = _ejecutar_kmeans(
            spark, k=k, max_iter=max_iter, gym_id=gym_id
        )
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, silhouette)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_kmeans_bp.route("/api/analytics/kmeans/train", methods=["POST"])
@jwt_required()
def kmeans_train():
    """
    Fuerza re-entrenamiento y actualiza cache para este gimnasio y k.

    Body JSON (opcional): { "k": 3, "max_iter": 20 }
    """
    try:
        body     = request.get_json(silent=True) or {}
        k        = int(body.get("k",        request.args.get("k",        3,  type=int)))
        max_iter = int(body.get("max_iter", request.args.get("max_iter", 20, type=int)))
        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id, k)

        spark = get_spark()
        resumen, asignaciones, centroides, silhouette = _ejecutar_kmeans(
            spark, k=k, max_iter=max_iter, gym_id=gym_id
        )
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, silhouette)
        payload["desde_cache"] = False
        cache_set(key, payload)

        return jsonify({**payload,
                        "mensaje": f"K-Means k={k} reentrenado para gimnasio {gym_id}."}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
