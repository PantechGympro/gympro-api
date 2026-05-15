"""
spark_rutinas.py -- Recomendaciones de rutina personalizadas.

Sprint 4 / US20: filtrado colaborativo basado en el perfil del miembro.

Algoritmo:
  1. Representar cada miembro como vector de features: IMC, objetivo (encoded),
     grupos_musculares_preferidos (frecuencia), dias_activo.
  2. Normalizar con StandardScaler y calcular similitud coseno con Spark.
  3. Para el miembro consultado, encontrar los N vecinos mas similares.
  4. Devolver las rutinas mas frecuentes entre esos vecinos como recomendaciones.

Endpoints:
  GET  /api/analytics/rutinas/recomendaciones?id_miembro=<hex>  -- recomendaciones por miembro
  GET  /api/analytics/rutinas/populares                          -- rutinas mas populares del gym
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.spark_config import get_spark, cache_get, cache_set

spark_rutinas_bp = Blueprint("spark_rutinas", __name__)

# Mapeo de objetivos a entero para vectorizacion
_OBJETIVO_MAP = {
    "Perdida de peso":          0,
    "Ganancia muscular":        1,
    "Definicion":               2,
    "Resistencia":              3,
    "Rehabilitacion":           4,
    "Acondicionamiento general":5,
    "Fuerza maxima":            6,
}


def _cache_key_pop(gym_id):
    return f"rutinas_populares_gym{gym_id}"


def _cache_key_rec(id_miembro):
    return f"rutinas_rec_{id_miembro}"


# ─── Rutinas populares del gimnasio ──────────────────────────────────────────

def _rutinas_populares(gym_id=None) -> list:
    from app.routes.spark_config import get_mongo_db
    db    = get_mongo_db()
    match = {} if gym_id is None else {"id_gimnasio": int(gym_id)}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":      "$nombre",
            "veces":    {"$sum": 1},
            "categoria":{"$first": "$categoria"},
            "dificultad":{"$first":"$dificultad"},
            "duracion": {"$first": "$duracion_minutos"},
        }},
        {"$sort": {"veces": -1}},
        {"$limit": 10},
    ]
    return list(db.rutinas.aggregate(pipeline))


# ─── Recomendaciones por similitud de perfil ─────────────────────────────────

def _recomendaciones_para_miembro(spark, id_miembro_hex: str, gym_id=None, top_n: int = 5) -> dict:
    from pyspark.sql import functions as F
    from pyspark.sql.types import DoubleType, IntegerType
    from pyspark.ml.feature import VectorAssembler, StandardScaler
    from pyspark.ml.functions import vector_to_array
    from app.routes.spark_config import leer_coleccion, get_mongo_db
    from bson import ObjectId

    db = get_mongo_db()

    # ── Validar miembro ───────────────────────────────────────────────────────
    try:
        oid = ObjectId(id_miembro_hex)
    except Exception:
        return {"error": "id_miembro invalido"}

    miembro_doc = db.miembros.find_one({"_id": oid}, {"nombre":1,"objetivo":1,"peso_inicial":1,"estatura":1})
    if not miembro_doc:
        return {"error": "Miembro no encontrado"}

    # ── Cargar datos desde Spark ───────────────────────────────────────────────
    df_miembros = leer_coleccion(spark, "miembros").select(
        F.col("_id").alias("id_miembro"),
        F.col("id_gimnasio_pg").cast("integer"),
        F.col("objetivo"),
        F.col("peso_inicial").cast(DoubleType()),
        F.col("estatura").cast(DoubleType()),
    )
    if gym_id is not None:
        df_miembros = df_miembros.filter(F.col("id_gimnasio_pg") == int(gym_id))

    # Numero de asistencias por miembro (proxy de actividad)
    df_asist = leer_coleccion(spark, "asistencias").groupBy("id_miembro").agg(
        F.count("*").cast(DoubleType()).alias("total_asistencias"),
    )

    # Encode objetivo como entero
    mapping_expr = F.create_map(
        *[item for pair in [(F.lit(k), F.lit(v)) for k, v in _OBJETIVO_MAP.items()] for item in pair]
    )
    df_feat = (
        df_miembros
        .join(df_asist, on="id_miembro", how="left")
        .fillna({"total_asistencias": 0.0, "peso_inicial": 70.0, "estatura": 1.70})
        .withColumn(
            "imc",
            F.when(F.col("estatura") > 0,
                   F.col("peso_inicial") / (F.col("estatura") * F.col("estatura"))
            ).otherwise(F.lit(24.0)).cast(DoubleType()),
        )
        .withColumn("objetivo_enc", (mapping_expr[F.col("objetivo")]).cast(DoubleType()))
        .fillna({"objetivo_enc": 0.0})
    )

    feature_cols = ["imc", "objetivo_enc", "total_asistencias"]
    assembler    = VectorAssembler(inputCols=feature_cols, outputCol="features", handleInvalid="keep")
    scaler_model = StandardScaler(inputCol="features", outputCol="features_scaled",
                                   withStd=True, withMean=True)

    df_asm    = assembler.transform(df_feat)
    df_scaled = scaler_model.fit(df_asm).transform(df_asm)

    # ── Similitud coseno con el miembro target ───────────────────────────────
    target_id   = id_miembro_hex
    target_row  = df_scaled.filter(F.col("id_miembro").cast("string") == target_id).first()

    if target_row is None:
        # Miembro no tiene asistencias; usar populares como fallback
        populares = _rutinas_populares(gym_id)
        return {
            "id_miembro":       id_miembro_hex,
            "nombre":           miembro_doc.get("nombre",""),
            "modo":             "popular_fallback",
            "recomendaciones":  [
                {"nombre": r["_id"], "categoria": r.get("categoria",""), "veces": r["veces"]}
                for r in populares[:5]
            ],
        }

    target_vec = target_row["features_scaled"].toArray()
    target_norm = float((target_vec ** 2).sum() ** 0.5) or 1.0

    # Convertir features_scaled a array para calcular similitud
    df_arr = df_scaled.withColumn("vec_arr", vector_to_array("features_scaled"))
    rows   = df_arr.filter(F.col("id_miembro").cast("string") != target_id).collect()

    similitudes = []
    for row in rows:
        vec = row["vec_arr"]
        if not vec:
            continue
        import math
        dot  = sum(a*b for a, b in zip(target_vec, vec))
        norm = math.sqrt(sum(v*v for v in vec)) or 1.0
        sim  = dot / (target_norm * norm)
        similitudes.append((str(row["id_miembro"]), sim))

    similitudes.sort(key=lambda x: x[1], reverse=True)
    vecinos_ids = [s[0] for s in similitudes[:15]]

    # ── Rutinas de los vecinos ────────────────────────────────────────────────
    from bson import ObjectId as OID
    try:
        oids_vecinos = [OID(v) for v in vecinos_ids]
    except Exception:
        oids_vecinos = []

    pipeline = [
        {"$match": {"id_miembro": {"$in": oids_vecinos}}},
        {"$group": {
            "_id":       "$nombre",
            "veces":     {"$sum": 1},
            "categoria": {"$first": "$categoria"},
            "dificultad":{"$first": "$dificultad"},
            "duracion":  {"$first": "$duracion_minutos"},
        }},
        {"$sort": {"veces": -1}},
        {"$limit": top_n},
    ]
    rutinas_rec = list(db.rutinas.aggregate(pipeline))

    return {
        "id_miembro":      id_miembro_hex,
        "nombre":          miembro_doc.get("nombre",""),
        "objetivo":        miembro_doc.get("objetivo",""),
        "modo":            "collaborative_filtering",
        "vecinos_usados":  len(vecinos_ids),
        "recomendaciones": [
            {
                "nombre":     r["_id"],
                "categoria":  r.get("categoria",""),
                "dificultad": r.get("dificultad",""),
                "duracion":   r.get("duracion",""),
                "frecuencia": r["veces"],
            }
            for r in rutinas_rec
        ],
        "ejecutado_en":    datetime.now().isoformat(),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@spark_rutinas_bp.route("/api/analytics/rutinas/populares", methods=["GET"])
@jwt_required()
def rutinas_populares():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key_pop(gym_id)

        cached = cache_get(key)
        if cached:
            return jsonify({"desde_cache": True, "rutinas": cached}), 200

        rutinas = _rutinas_populares(gym_id)
        result  = [
            {"nombre": r["_id"], "categoria": r.get("categoria",""),
             "dificultad": r.get("dificultad",""), "veces": r["veces"]}
            for r in rutinas
        ]
        cache_set(key, result)
        return jsonify({"desde_cache": False, "rutinas": result}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@spark_rutinas_bp.route("/api/analytics/rutinas/recomendaciones", methods=["GET"])
@jwt_required()
def rutinas_recomendaciones():
    id_miembro = request.args.get("id_miembro", "").strip()
    if not id_miembro:
        return jsonify({"error": "Parametro 'id_miembro' requerido"}), 400

    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key_rec(id_miembro)

        cached = cache_get(key)
        if cached:
            return jsonify({**cached, "desde_cache": True}), 200

        spark  = get_spark()
        result = _recomendaciones_para_miembro(spark, id_miembro, gym_id)
        if "error" not in result:
            cache_set(key, result)
        result["desde_cache"] = False
        return jsonify(result), 200

    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
