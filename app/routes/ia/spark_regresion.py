"""
spark_regresion.py -- Regresion Lineal (Ridge) para prediccion de peso corporal.

Diseno de cache en dos capas:
  1. Entrenamiento global (por gym_id): coeficientes + metricas con TTL configurable
  2. Prediccion individual: usa coeficientes cacheados + pymongo (sin Spark)
     -> sub-100ms por request, sin re-entrenar el modelo

Variables de entorno relevantes:
    SPARK_ENABLED=true
    ANALYTICS_CACHE_TTL_HOURS=24
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime, timedelta

from app.routes.ia.spark_config import get_spark, cache_get, cache_set

spark_regresion_bp = Blueprint("spark_regresion", __name__)


def _cache_key(gym_id) -> str:
    return f"regresion_gym{gym_id}"


# ------------------------------------------------------------------------------
# RESOLVER ID DE MIEMBRO -- pymongo (sin Spark, sin costo de JVM)
# ------------------------------------------------------------------------------

def _resolver_id_miembro_mongo(id_entrada: str):
    """
    Acepta el hex-id de un miembro (progreso_fisico.id_miembro),
    el _id de un documento en miembros, o el id_usuario de un miembro.
    Devuelve el hex-id correcto para consultar progreso_fisico, o None.
    """
    from bson import ObjectId
    from app.routes.ia.spark_config import get_mongo_db

    db = get_mongo_db()
    try:
        oid = ObjectId(id_entrada)
    except Exception:
        return None

    # id_entrada apunta directamente a registros de progreso?
    if db.progreso_fisico.count_documents({"id_miembro": oid}, limit=1):
        return id_entrada

    # Es el _id de un miembro con progreso registrado?
    miembro = db.miembros.find_one({"_id": oid}, {"_id": 1})
    if miembro and db.progreso_fisico.count_documents({"id_miembro": oid}, limit=1):
        return id_entrada

    # Es el id_usuario de un miembro?
    miembro = db.miembros.find_one({"id_usuario": oid}, {"_id": 1})
    if miembro:
        m_oid = miembro["_id"]
        if db.progreso_fisico.count_documents({"id_miembro": m_oid}, limit=1):
            return str(m_oid)

    return None


# ------------------------------------------------------------------------------
# ENTRENAMIENTO GLOBAL CON SPARK
# ------------------------------------------------------------------------------

def _regresion_global(spark, gym_id=None):
    """
    Entrena el modelo de regresion Ridge con datos de progreso_fisico.
    Aplica aislamiento de tenant via pymongo broadcast + isin (evita join complejo).
    Retorna: (model, metricas, coeficientes, tendencia, media_cintura, media_grasa)
    """
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window
    from pyspark.ml.regression import LinearRegression
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.evaluation import RegressionEvaluator
    from pyspark.sql.types import StringType
    from app.routes.ia.spark_config import leer_coleccion, get_mongo_db
    import re as _re

    def oid_hex(val):
        if val is None:
            return None
        m = _re.search(r"[0-9a-fA-F]{24}", str(val))
        return m.group(0) if m else str(val)

    oid_udf = F.udf(oid_hex, StringType())

    # 1. CARGA DE PROGRESO con id_miembro normalizado a hex
    # Nota: el seed guarda fecha_registro como string 'YYYY-MM-DD'.
    # cast("date") parsea tanto strings ISO como timestamps de Mongo.
    df = (
        leer_coleccion(spark, "progreso_fisico")
        .withColumn("id_miembro_hex", oid_udf(F.col("id_miembro")))
        .select(
            F.col("id_miembro_hex").alias("id_miembro"),
            F.col("peso").cast("double"),
            F.col("imc").cast("double").alias("bmi"),           # campo en seed es "imc"
            F.col("cintura").cast("double"),                    # None si no existe → imputacion
            F.col("grasa_corporal").cast("double"),
            F.col("fecha_registro").cast("date").alias("fecha_registro"),  # normalizar string → date
        )
        .filter(
            F.col("peso").isNotNull() &
            F.col("fecha_registro").isNotNull() &
            (F.col("peso") > 0)
        )
    )

    # 2. AISLAMIENTO DE TENANT -- obtener IDs de miembros del gimnasio via pymongo
    if gym_id is not None:
        db_m = get_mongo_db()
        member_ids_hex = [
            _re.search(r"[0-9a-fA-F]{24}", str(m["_id"])).group(0)
            for m in db_m.miembros.find({"id_gimnasio_pg": int(gym_id)}, {"_id": 1})
            if _re.search(r"[0-9a-fA-F]{24}", str(m["_id"]))
        ]
        if not member_ids_hex:
            raise ValueError(f"No hay miembros registrados para el gimnasio {gym_id}.")
        df = df.filter(F.col("id_miembro").isin(member_ids_hex))

    if df.count() < 10:
        raise ValueError("Se necesitan al menos 10 registros de progreso para entrenar el modelo.")

    # 3. FEATURE ENGINEERING -- dias desde primer registro por miembro
    w_min = Window.partitionBy("id_miembro")
    df = df.withColumn(
        "fecha_inicio", F.min("fecha_registro").over(w_min)
    ).withColumn(
        "dias", F.datediff(F.col("fecha_registro"), F.col("fecha_inicio")).cast("double")
    )

    # 4. IMPUTACION DE VALORES FALTANTES
    media_cintura = df.agg(F.avg("cintura")).collect()[0][0] or 80.0
    media_grasa   = df.agg(F.avg("grasa_corporal")).collect()[0][0] or 22.0
    df = df.fillna({"cintura": media_cintura, "grasa_corporal": media_grasa, "bmi": 25.0})

    # 5. VECTORIZACION
    assembler = VectorAssembler(
        inputCols=["dias", "cintura", "grasa_corporal", "bmi"], outputCol="features"
    )
    df_ml = assembler.transform(df).select(
        "features", F.col("peso").alias("label"), "dias", "id_miembro"
    )

    train, test = df_ml.randomSplit([0.8, 0.2], seed=42)

    # 6. REGRESION RIDGE (elasticNetParam=0 -> L2 puro)
    model = LinearRegression(
        featuresCol="features", labelCol="label",
        maxIter=50, regParam=0.1, elasticNetParam=0.0
    ).fit(train)

    # 7. METRICAS
    predicciones = model.transform(test)

    def _eval(metric):
        return round(
            RegressionEvaluator(labelCol="label", predictionCol="prediction", metricName=metric)
            .evaluate(predicciones), 4
        )

    metricas = {"rmse": _eval("rmse"), "r2": _eval("r2"), "mae": _eval("mae")}

    coeficientes = {
        "dias":           round(float(model.coefficients[0]), 6),
        "cintura":        round(float(model.coefficients[1]), 6),
        "grasa_corporal": round(float(model.coefficients[2]), 6),
        "bmi":            round(float(model.coefficients[3]), 6),
        "intercepto":     round(float(model.intercept),       4),
    }

    # 8. TENDENCIA HISTORICA GLOBAL
    tendencia = (
        df.withColumn("mes", F.date_format("fecha_registro", "yyyy-MM"))
        .groupBy("mes")
        .agg(
            F.round(F.avg("peso"), 2).alias("peso_promedio"),
            F.count("*").alias("registros"),
        )
        .orderBy("mes")
    )

    return (
        model,
        metricas,
        coeficientes,
        [row.asDict() for row in tendencia.collect()],
        media_cintura,
        media_grasa,
    )


def _build_global_payload(metricas: dict, coeficientes: dict, tendencia: list) -> dict:
    r2 = metricas["r2"]
    interpretacion = (
        "Excelente -- el modelo explica mas del 80% de la varianza del peso" if r2 > 0.8 else
        "Bueno -- explica mas del 60% de la varianza"                        if r2 > 0.6 else
        "Moderado -- hay factores no capturados (edad, dieta, etc.)"         if r2 > 0.4 else
        "Bajo -- se recomienda mas historial de datos o features adicionales"
    )
    return {
        "algoritmo":             "Regresion Lineal (Ridge)",
        "descripcion":           "Prediccion de peso corporal basada en dias de entrenamiento, grasa y BMI",
        "features_usadas":       ["dias_desde_inicio", "cintura_cm", "grasa_corporal_%", "bmi"],
        "variable_objetivo":     "peso_kg",
        "metricas":              metricas,
        "interpretacion_r2":     interpretacion,
        "coeficientes":          coeficientes,
        "tendencia_peso_global": tendencia,
        "ejecutado_en":          datetime.now().isoformat(),
    }


# ------------------------------------------------------------------------------
# PREDICCION INDIVIDUAL -- pymongo + algebra lineal pura (sin Spark)
# ------------------------------------------------------------------------------

def _to_naive_datetime(val) -> "datetime | None":
    """
    Convierte cualquier representación de fecha a datetime naive (sin timezone).
    Acepta: datetime (con o sin tz), date, str ISO 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS'.
    El seed guarda fecha_registro como string -- esta funcion normaliza ambos casos.
    """
    from datetime import date as _date
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, _date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, str):
        val = val.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val[:len(fmt)], fmt)
            except ValueError:
                continue
    return None


def _predecir_con_coeficientes(id_miembro: str, dias_futuro: int,
                                coeficientes: dict, medias: dict):
    """
    Proyecta el peso futuro de un miembro usando los coeficientes del modelo
    cacheado. Lee el historial directamente de MongoDB via pymongo.

    Formula: peso = intercepto + sum(coef_i * x_i)
    Sin Spark, sin JVM -- O(n_registros_miembro).

    Nota: el seed almacena fecha_registro como string ISO ('YYYY-MM-DD').
    _to_naive_datetime() normaliza strings y datetimes con tz indistintamente.
    """
    from bson import ObjectId
    from app.routes.ia.spark_config import get_mongo_db

    db = get_mongo_db()
    try:
        oid = ObjectId(id_miembro)
    except Exception:
        return None, []

    # Ordenar por fecha string funciona correctamente con formato ISO 'YYYY-MM-DD'
    registros = list(
        db.progreso_fisico.find(
            {"id_miembro": oid},
            {"peso": 1, "imc": 1, "grasa_corporal": 1, "cintura": 1,
             "fecha_registro": 1, "_id": 0},
        ).sort("fecha_registro", 1)
    )

    if not registros:
        return None, []

    # Construir historial -- normalizar fecha independientemente del tipo almacenado
    historial = []
    for r in registros:
        if r.get("peso") is None:
            continue
        try:
            peso = round(float(r["peso"]), 1)
        except (TypeError, ValueError):
            continue
        dt = _to_naive_datetime(r.get("fecha_registro"))
        historial.append({
            "fecha": dt.strftime("%Y-%m-%d") if dt else str(r.get("fecha_registro", "")),
            "peso":  peso,
        })

    if not historial:
        return None, []

    # Dias transcurridos desde el primer registro hasta hoy
    primer_dt = _to_naive_datetime(registros[0].get("fecha_registro"))
    if primer_dt is None:
        dias_actuales = 0
    else:
        dias_actuales = max(0, (datetime.now() - primer_dt).days)

    # Valores del ultimo registro para la prediccion (ceteris paribus)
    ultimo  = registros[-1]
    cintura = float(ultimo.get("cintura") or medias.get("cintura", 80.0))
    grasa   = float(ultimo.get("grasa_corporal") or medias.get("grasa", 22.0))
    bmi     = float(ultimo.get("imc") or 25.0)   # seed guarda campo 'imc'

    coef = coeficientes
    predicciones_futuras = []
    for d in [30, 60, 90, 120, 150, 180]:
        if d <= dias_futuro:
            dias_total = dias_actuales + d
            peso_pred = (
                coef["intercepto"]
                + coef["dias"]           * dias_total
                + coef["cintura"]        * cintura
                + coef["grasa_corporal"] * grasa
                + coef["bmi"]            * bmi
            )
            predicciones_futuras.append({
                "dias_desde_hoy":   d,
                "fecha_estimada":   (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d"),
                "peso_predicho_kg": round(float(peso_pred), 2),
            })

    return historial, predicciones_futuras


# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------

@spark_regresion_bp.route("/api/analytics/regresion", methods=["GET"])
@jwt_required()
def regresion_analytics():
    """
    Devuelve metricas globales del modelo desde cache (TTL = ANALYTICS_CACHE_TTL_HOURS).
    Si expiro o no existe, entrena el modelo y actualiza.
    """
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark = get_spark()
        model, metricas, coeficientes, tendencia, media_cintura, media_grasa = \
            _regresion_global(spark, gym_id)

        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        payload["_medias"] = {"cintura": media_cintura, "grasa": media_grasa}
        cache_set(key, payload)
        return jsonify(payload), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/train", methods=["POST"])
@jwt_required()
def regresion_train():
    """
    Fuerza re-entrenamiento del modelo y actualiza cache para este gimnasio.
    No requiere body.
    """
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        spark = get_spark()
        model, metricas, coeficientes, tendencia, media_cintura, media_grasa = \
            _regresion_global(spark, gym_id)

        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        payload["_medias"] = {"cintura": media_cintura, "grasa": media_grasa}
        cache_set(key, payload)

        return jsonify({
            **payload,
            "mensaje": f"Modelo reentrenado para gimnasio {gym_id}.",
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/predecir/<id_entrada>", methods=["GET"])
@jwt_required()
def predecir_peso_miembro(id_entrada: str):
    """
    Predice el peso futuro de un miembro usando coeficientes cacheados (sin re-entrenar).
    Acepta id de miembro, _id de documento miembros, o id_usuario.

    Si no hay cache para este gimnasio, entrena primero automaticamente.

    Query params:
        dias (int, 30-365, default=180) -- horizonte de prediccion
    """
    try:
        id_entrada  = id_entrada.strip("{}")
        dias_futuro = request.args.get("dias", 180, type=int)
        if not (30 <= dias_futuro <= 365):
            return jsonify({"error": "dias debe estar entre 30 y 365"}), 400

        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key(gym_id)

        # Resolver id sin usar Spark (pymongo, O(1))
        id_miembro_real = _resolver_id_miembro_mongo(id_entrada)
        if id_miembro_real is None:
            return jsonify({
                "error": "No se encontraron registros de progreso para este id.",
                "sugerencia": "Verifica que el miembro tiene registros en progreso_fisico.",
            }), 404

        # Obtener coeficientes desde cache -- si no existen, entrenar primero
        cached = cache_get(key)
        if not cached or "coeficientes" not in cached:
            spark = get_spark()
            _, metricas, coeficientes, tendencia, media_cintura, media_grasa = \
                _regresion_global(spark, gym_id)
            payload = _build_global_payload(metricas, coeficientes, tendencia)
            payload["_medias"] = {"cintura": media_cintura, "grasa": media_grasa}
            payload["desde_cache"] = False
            cache_set(key, payload)
            cached = payload

        coeficientes = cached["coeficientes"]
        medias       = cached.get("_medias", {"cintura": 80.0, "grasa": 22.0})

        # Prediccion pura: pymongo + algebra lineal -- sin Spark
        historial, predicciones = _predecir_con_coeficientes(
            id_miembro_real, dias_futuro, coeficientes, medias
        )

        if historial is None:
            return jsonify({"error": "El miembro no tiene registros de progreso"}), 404

        # Detectar tendencia comparando ultimo peso real vs ultima prediccion
        tendencia_str = "estable"
        if predicciones and historial:
            diff = predicciones[-1]["peso_predicho_kg"] - historial[-1]["peso"]
            if diff < -1.5:   tendencia_str = "bajando"
            elif diff > 1.5:  tendencia_str = "subiendo"

        return jsonify({
            "id_entrada":           id_entrada,
            "id_miembro_resuelto":  id_miembro_real,
            "algoritmo":            "Regresion Lineal (coeficientes cacheados)",
            "horizonte_dias":       dias_futuro,
            "peso_actual_kg":       historial[-1]["peso"] if historial else None,
            "tendencia":            tendencia_str,
            "historial_peso":       historial,
            "predicciones_futuras": predicciones,
            "advertencia":          "Prediccion basada en tendencia historica. Factores como dieta y rutina pueden alterar el resultado.",
            "ejecutado_en":         datetime.now().isoformat(),
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
