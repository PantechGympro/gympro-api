from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from datetime import datetime

spark_kmeans_bp = Blueprint("spark_kmeans", __name__)

CLUSTER_LABELS = {
    0: "Principiante / Alta Prioridad",
    1: "Intermedio / Mantenimiento",
    2: "Avanzado / Optimización"
}

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



CACHE_COLLECTION = "analytics_cache"
CACHE_KEY_PREFIX  = "kmeans"

def _cache_key(k: int) -> str:
    return f"{CACHE_KEY_PREFIX}_k{k}"


def _get_cached_result(k: int):
    """
    Devuelve el documento guardado en analytics_cache para este k,
    o None si no existe. Requiere que spark_config exponga `get_mongo_db()`.
    """
    try:
        import sys, os
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)
        from spark_config import get_mongo_db          # ← añadir este helper en spark_config
        db  = get_mongo_db()
        doc = db[CACHE_COLLECTION].find_one({"_id": _cache_key(k)})
        if doc:
            doc.pop("_id", None)
            return doc
    except Exception as e:
        print(f"[kmeans cache] Error leyendo caché: {e}")
    return None


def _save_cached_result(k: int, payload: dict):
    """
    Guarda (upsert) el resultado completo en analytics_cache.
    """
    try:
        import sys, os
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)
        from spark_config import get_mongo_db
        db = get_mongo_db()
        db[CACHE_COLLECTION].replace_one(
            {"_id": _cache_key(k)},
            {"_id": _cache_key(k), **payload},
            upsert=True
        )
    except Exception as e:
        print(f"[kmeans cache] Error guardando caché: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA K-MEANS
# ──────────────────────────────────────────────────────────────────────────────

def _ejecutar_kmeans(spark, k: int = 3, max_iter: int = 20, seed: int = 42):
    import sys, os
    
    # Configuración de rutas para asegurar que los módulos locales sean importables
    directorio_actual = os.path.dirname(__file__)
    if directorio_actual not in sys.path:
        sys.path.insert(0, directorio_actual)

    from spark_config import leer_coleccion
    from pyspark.sql import functions as F
    from pyspark.ml.clustering import KMeans
    from pyspark.ml.feature import VectorAssembler, StandardScaler
    from pyspark.ml.evaluation import ClusteringEvaluator

    # 1. CARGA Y LIMPIEZA DE DATOS DE MIEMBROS
    # Se extraen las características base y se filtran registros inválidos o nulos
    df_miembros = (
        leer_coleccion(spark, "miembros")
        .select(
            F.col("_id").alias("id_miembro"),
            F.col("peso_inicial").cast("double"),
            F.col("estatura").cast("double"),
            F.col("sexo")
        )
        .filter(
            F.col("peso_inicial").isNotNull() &
            F.col("estatura").isNotNull() &
            (F.col("estatura") > 0)
        )
    )

    # 2. PROCESAMIENTO DE PROGRESO FÍSICO
    # Se utiliza una ventana (Window) para obtener solo el registro de progreso más reciente de cada miembro
    from pyspark.sql.window import Window
    w = Window.partitionBy("id_miembro_prog").orderBy(F.col("fecha_registro").desc())

    df_progreso = (
        leer_coleccion(spark, "progreso_fisico")
        .select(
            F.col("id_miembro").alias("id_miembro_prog"),
            F.col("peso").cast("double"),
            F.col("bmi").cast("double"),
            F.col("grasa_corporal").cast("double"),
            F.col("masa_muscular").cast("double"),
            F.col("fecha_registro")
        )
        .withColumn("rn", F.row_number().over(w))
        .filter(F.col("rn") == 1) # Mantiene solo la última actualización de peso/grasa
        .drop("rn", "fecha_registro")
    )

    # 3. INTEGRACIÓN DE DATOS (JOIN)
    # Se une la información de perfil con el progreso físico más reciente
    df = df_miembros.join(
        df_progreso,
        df_miembros["id_miembro"] == df_progreso["id_miembro_prog"],
        "left"
    )

    # 4. INGENIERÍA DE CARACTERÍSTICAS Y TRATAMIENTO DE NULOS
    # - Se calcula el IMC si no existe.
    # - Se imputan valores por defecto si el miembro no tiene registros de progreso.
    df = df.withColumn(
        "imc_calculado",
        F.when(F.col("bmi").isNotNull(), F.col("bmi")).otherwise(
            F.col("peso_inicial") / (F.col("estatura") * F.col("estatura"))
        )
    ).withColumn(
        "peso_final", F.coalesce(F.col("peso"), F.col("peso_inicial"))
    ).withColumn(
        "grasa_final", F.coalesce(F.col("grasa_corporal"), F.lit(20.0)) # Valor base: 20% grasa
    ).withColumn(
        "musculo_final", F.coalesce(F.col("masa_muscular"), F.lit(30.0)) # Valor base: 30kg músculo
    )

    # Selección final de columnas para el modelo
    df_features = df.select(
        F.col("id_miembro"),
        F.col("peso_final").alias("peso"),
        F.col("imc_calculado").alias("imc"),
        F.col("grasa_final").alias("grasa"),
        F.col("musculo_final").alias("musculo"),
        F.col("sexo")
    ).filter(
        F.col("peso").isNotNull() & F.col("imc").isNotNull()
    )

    # Validación de seguridad para evitar errores en el algoritmo de clustering
    if df_features.count() < k:
        raise ValueError(f"Datos insuficientes: se necesitan al menos {k} miembros con datos.")

    # 5. PREPARACIÓN DE VECTORES PARA MLlib
    # VectorAssembler agrupa las columnas numéricas en un solo vector de características
    assembler = VectorAssembler(
        inputCols=["peso", "imc", "grasa", "musculo"],
        outputCol="features_raw"
    )
    df_assembled = assembler.transform(df_features)

    # Escalado de datos (Estandarización)
    # K-Means es sensible a la escala; StandardScaler asegura que todas las variables tengan peso equitativo
    scaler = StandardScaler(
        inputCol="features_raw", outputCol="features",
        withStd=True, withMean=True
    )
    scaler_model = scaler.fit(df_assembled)
    df_scaled = scaler_model.transform(df_assembled)

    # 6. ENTRENAMIENTO DEL MODELO K-MEANS
    kmeans = KMeans(
        featuresCol="features", predictionCol="cluster",
        k=k, maxIter=max_iter, seed=seed
    )
    model = kmeans.fit(df_scaled)
    df_result = model.transform(df_scaled)

    # 7. EVALUACIÓN DEL MODELO
    # Se calcula el Coeficiente de Silueta (Silhouette) para medir la cohesión y separación de los clusters
    evaluator = ClusteringEvaluator(
        featuresCol="features", predictionCol="cluster", metricName="silhouette"
    )
    silhouette = evaluator.evaluate(df_result)

    # Extracción de centroides (puntos medios de cada cluster)
    centroides_raw = model.clusterCenters()
    centroides = [
        {
            "cluster":      i,
            "peso_norm":    round(float(c[0]), 4),
            "imc_norm":     round(float(c[1]), 4),
            "grasa_norm":   round(float(c[2]), 4),
            "musculo_norm": round(float(c[3]), 4),
        }
        for i, c in enumerate(centroides_raw)
    ]

    # 8. GENERACIÓN DE RESULTADOS Y RESUMEN
    # Se calculan los promedios reales (sin escalar) de cada cluster para interpretación de negocio
    resumen_clusters = (
        df_result
        .groupBy("cluster")
        .agg(
            F.count("*").alias("num_miembros"),
            F.round(F.avg("peso"),    2).alias("peso_promedio"),
            F.round(F.avg("imc"),     2).alias("imc_promedio"),
            F.round(F.avg("grasa"),   2).alias("grasa_promedio"),
            F.round(F.avg("musculo"), 2).alias("musculo_promedio")
        )
        .orderBy("cluster")
    )

    # Función auxiliar para convertir ObjectIDs de MongoDB a texto plano (hexadecimal)
    from pyspark.sql.types import StringType
    import re as _re

    def _oid_hex(val):
        if val is None:
            return None
        m = _re.search(r"[0-9a-fA-F]{24}", str(val))
        return m.group(0) if m else str(val)

    oid_udf = F.udf(_oid_hex, StringType())

    # Formateo de las asignaciones individuales de cada miembro a su cluster
    asignaciones = (
        df_result
        .select(
            oid_udf(F.col("id_miembro")).alias("id_miembro"),
            "cluster", "sexo",
            F.round("peso",    1).alias("peso"),
            F.round("imc",     2).alias("imc"),
            F.round("grasa",   1).alias("grasa"),
            F.round("musculo", 1).alias("musculo")
        )
        .orderBy("cluster")
    )

    # Retorno de datos en formatos nativos de Python (listas de diccionarios)
    return (
        [row.asDict() for row in resumen_clusters.collect()],
        [row.asDict() for row in asignaciones.collect()],
        centroides,
        round(silhouette, 4)
    )


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@spark_kmeans_bp.route("/api/analytics/kmeans", methods=["GET"])
@jwt_required()
def kmeans_analytics():
    """
    Devuelve el resultado guardado en caché (MongoDB).
    Si no existe caché para este k, entrena el modelo por primera vez.

    Query params:
      k        (int, default=3)
      max_iter (int, default=20)  ← solo se usa en el primer entrenamiento
    """
    try:
        k        = request.args.get("k",        3,  type=int)
        max_iter = request.args.get("max_iter", 20, type=int)

        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        # ── Intentar servir desde caché ───────────────────────────────────────
        cached = _get_cached_result(k)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        # ── Sin caché: entrenar por primera vez ───────────────────────────────
        spark = _get_spark()
        resumen, asignaciones, centroides, silhouette = _ejecutar_kmeans(
            spark, k=k, max_iter=max_iter
        )
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, silhouette)
        payload["desde_cache"] = False
        _save_cached_result(k, payload)
        return jsonify(payload), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_kmeans_bp.route("/api/analytics/kmeans/train", methods=["POST"])
@jwt_required()
def kmeans_train():
    """
    Fuerza el re-entrenamiento del modelo K-Means y actualiza la caché.

    Body JSON (opcional):
      { "k": 3, "max_iter": 20 }

    Si no se envía body, usa k=3 y max_iter=20 por defecto.
    También acepta query params como fallback.
    """
    try:
        body     = request.get_json(silent=True) or {}
        k        = body.get("k",        request.args.get("k",        3,  type=int))
        max_iter = body.get("max_iter", request.args.get("max_iter", 20, type=int))
        k        = int(k)
        max_iter = int(max_iter)

        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        spark = _get_spark()
        resumen, asignaciones, centroides, silhouette = _ejecutar_kmeans(
            spark, k=k, max_iter=max_iter
        )
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, silhouette)
        payload["desde_cache"] = False
        _save_cached_result(k, payload)

        return jsonify({
            **payload,
            "mensaje": f"Modelo K-Means k={k} reentrenado y caché actualizada."
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500