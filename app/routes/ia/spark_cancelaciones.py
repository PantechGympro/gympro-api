"""
spark_cancelaciones.py -- Prediccion de riesgo de cancelacion de membresia.

Sprint 4 / US19: Random Forest Classifier (MLlib) entrenado con datos de
asistencia, pagos y estado de membresia por miembro.

Features:
  - dias_sin_asistir       : dias desde la ultima asistencia registrada
  - num_asistencias_ult60  : asistencias en los ultimos 60 dias
  - total_pagos            : numero de pagos historicos del miembro
  - meses_activo           : meses desde el registro del miembro
  - tiene_membresia_activa : 1 si la membresia esta activa, 0 si vencio

Label (target):
  - 1 = en riesgo (dias_sin_asistir > 21 O membresia vencida)
  - 0 = activo y estable

Endpoints:
  GET  /api/analytics/cancelaciones         -- prediccion desde cache
  POST /api/analytics/cancelaciones/train   -- re-entrena y actualiza cache
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import get_spark, cache_get, cache_set

spark_cancelaciones_bp = Blueprint("spark_cancelaciones", __name__)


def _cache_key(gym_id) -> str:
    return f"cancelaciones_gym{gym_id}"


# ─── Logica del modelo ────────────────────────────────────────────────────────

def _ejecutar_cancelaciones(spark, gym_id=None) -> dict:
    from pyspark.sql import functions as F
    from pyspark.sql.types import IntegerType, DoubleType
    from pyspark.ml.classification import RandomForestClassifier
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.evaluation import BinaryClassificationEvaluator, MulticlassClassificationEvaluator
    from app.routes.ia.spark_config import leer_coleccion
    from datetime import datetime, timezone

    ahora = datetime.now(timezone.utc)

    # ── 1. Cargar colecciones desde MongoDB ───────────────────────────────────
    df_miembros = leer_coleccion(spark, "miembros").select(
        F.col("_id").alias("id_miembro"),
        F.col("id_gimnasio_pg").cast("integer"),
        F.col("nombre"),
        F.col("fecha_registro"),
    )
    if gym_id is not None:
        df_miembros = df_miembros.filter(F.col("id_gimnasio_pg") == int(gym_id))

    df_asistencias = leer_coleccion(spark, "asistencias").select(
        F.col("id_miembro"),
        F.to_timestamp(F.col("fecha")).alias("fecha_asist"),
    )
    if gym_id is not None:
        df_asistencias = df_asistencias.filter(F.col("id_gimnasio") == int(gym_id))

    df_pagos = leer_coleccion(spark, "pagos").select(
        F.col("id_miembro"),
        F.to_timestamp(F.col("fecha_pago")).alias("fecha_pago"),
    )
    if gym_id is not None:
        df_pagos = df_pagos.filter(F.col("id_gimnasio") == int(gym_id))

    df_membresías = leer_coleccion(spark, "miembro_membresia").select(
        F.col("id_miembro"),
        F.col("estado").alias("estado_mm"),
        F.col("fecha_fin"),
    )

    # ── 2. Feature engineering ────────────────────────────────────────────────
    # Ultima asistencia por miembro
    ultima_asist = df_asistencias.groupBy("id_miembro").agg(
        F.max("fecha_asist").alias("ultima_asistencia"),
        F.count(F.when(F.col("fecha_asist") >= F.lit(ahora).cast("timestamp") - F.expr("INTERVAL 60 DAYS"), 1)).alias("num_asistencias_ult60"),
    )

    # Total de pagos por miembro
    total_pagos = df_pagos.groupBy("id_miembro").agg(
        F.count("*").alias("total_pagos"),
    )

    # Membresia activa
    mm_activa = df_membresías.groupBy("id_miembro").agg(
        F.max(F.when(F.col("estado_mm") == "Activa", 1).otherwise(0)).alias("tiene_membresia_activa"),
    )

    # Meses activo desde registro
    df_m = df_miembros.withColumn(
        "meses_activo",
        F.round(
            F.datediff(F.lit(ahora).cast("date"), F.to_date(F.to_timestamp(F.col("fecha_registro")))) / 30.0,
            1
        ).cast(DoubleType()),
    )

    # Join de features
    df_feat = (
        df_m
        .join(ultima_asist, on="id_miembro", how="left")
        .join(total_pagos, on="id_miembro", how="left")
        .join(mm_activa, on="id_miembro", how="left")
        .withColumn(
            "dias_sin_asistir",
            F.datediff(
                F.lit(ahora).cast("date"),
                F.to_date(F.col("ultima_asistencia")),
            ).cast(DoubleType()),
        )
        .fillna({
            "dias_sin_asistir":       60.0,
            "num_asistencias_ult60":  0.0,
            "total_pagos":            0.0,
            "meses_activo":           1.0,
            "tiene_membresia_activa": 0,
        })
        .withColumn("num_asistencias_ult60", F.col("num_asistencias_ult60").cast(DoubleType()))
        .withColumn("total_pagos",           F.col("total_pagos").cast(DoubleType()))
        .withColumn("tiene_membresia_activa",F.col("tiene_membresia_activa").cast(DoubleType()))
    )

    # ── 3. Label: en_riesgo = 1 si inactivo mas de 21 dias o sin membresia ───
    df_feat = df_feat.withColumn(
        "label",
        F.when(
            (F.col("dias_sin_asistir") > 21) | (F.col("tiene_membresia_activa") == 0),
            1.0,
        ).otherwise(0.0),
    )

    feature_cols = [
        "dias_sin_asistir", "num_asistencias_ult60",
        "total_pagos", "meses_activo", "tiene_membresia_activa",
    ]

    assembler = VectorAssembler(inputCols=feature_cols, outputCol="features", handleInvalid="keep")
    df_asm    = assembler.transform(df_feat).select("id_miembro", "nombre", "features", "label",
                                                     "dias_sin_asistir", "tiene_membresia_activa")

    # Necesitamos al menos 10 filas para train/test split
    total_filas = df_asm.count()
    if total_filas < 10:
        return {"error": "Datos insuficientes para entrenar el modelo", "total_miembros": total_filas}

    # ── 4. Entrenar Random Forest ─────────────────────────────────────────────
    train, test = df_asm.randomSplit([0.8, 0.2], seed=42)

    rf = RandomForestClassifier(
        featuresCol="features", labelCol="label",
        numTrees=50, maxDepth=5, seed=42,
        featureSubsetStrategy="auto",
    )
    modelo = rf.fit(train)

    # ── 5. Evaluacion ─────────────────────────────────────────────────────────
    predicciones_test = modelo.transform(test)
    bin_eval   = BinaryClassificationEvaluator(labelCol="label", metricName="areaUnderROC")
    multi_eval = MulticlassClassificationEvaluator(labelCol="label", predictionCol="prediction",
                                                    metricName="accuracy")
    auc      = bin_eval.evaluate(predicciones_test)
    accuracy = multi_eval.evaluate(predicciones_test)

    # ── 6. Prediccion sobre todos los miembros ────────────────────────────────
    predicciones_all = modelo.transform(df_asm)

    # Extraer probabilidad de clase 1
    get_prob = F.udf(lambda v: float(v[1]) if v is not None else 0.0, DoubleType())
    pred_rows = (
        predicciones_all
        .withColumn("probabilidad", get_prob(F.col("probability")))
        .select("id_miembro", "nombre", "dias_sin_asistir",
                "tiene_membresia_activa", "prediction", "probabilidad")
        .orderBy(F.col("probabilidad").desc())
        .limit(200)
        .collect()
    )

    predicciones = []
    for row in pred_rows:
        prob   = row["probabilidad"]
        riesgo = "alto" if prob >= 0.65 else "medio" if prob >= 0.35 else "bajo"
        predicciones.append({
            "id_miembro":         str(row["id_miembro"]),
            "nombre":             row["nombre"] or "",
            "dias_sin_asistir":   int(row["dias_sin_asistir"] or 0),
            "membresia_activa":   bool(row["tiene_membresia_activa"]),
            "probabilidad":       round(prob, 4),
            "riesgo":             riesgo,
        })

    # ── 7. Importancia de features ────────────────────────────────────────────
    importancias = [
        {"feature": col, "importancia": round(float(imp), 4)}
        for col, imp in zip(feature_cols, modelo.featureImportances.toArray())
    ]
    importancias.sort(key=lambda x: x["importancia"], reverse=True)

    # ── 8. Resumen de riesgo ──────────────────────────────────────────────────
    alto   = sum(1 for p in predicciones if p["riesgo"] == "alto")
    medio  = sum(1 for p in predicciones if p["riesgo"] == "medio")
    activos = len(predicciones) - alto - medio

    return {
        "algoritmo":             "Random Forest Classifier",
        "descripcion":           "Prediccion de riesgo de cancelacion de membresia",
        "metricas":              {"accuracy": round(accuracy, 4), "auc_roc": round(auc, 4)},
        "importancia_features":  importancias,
        "predicciones":          predicciones,
        "resumen": {
            "total":       len(predicciones),
            "riesgo_alto": alto,
            "riesgo_medio": medio,
            "activos":     activos,
        },
        "ejecutado_en": datetime.now().isoformat(),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@spark_cancelaciones_bp.route("/api/analytics/cancelaciones", methods=["GET"])
@jwt_required()
def cancelaciones_analytics():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark   = get_spark()
        payload = _ejecutar_cancelaciones(spark, gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@spark_cancelaciones_bp.route("/api/analytics/cancelaciones/train", methods=["POST"])
@jwt_required()
def cancelaciones_train():
    try:
        gym_id  = get_jwt().get("id_gimnasio")
        spark   = get_spark()
        payload = _ejecutar_cancelaciones(spark, gym_id)
        payload["desde_cache"] = False
        cache_set(_cache_key(gym_id), payload)
        return jsonify({**payload, "reentrenado": True}), 200

    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
