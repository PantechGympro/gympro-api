from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from datetime import datetime, timedelta

spark_regresion_bp = Blueprint("spark_regresion", __name__)

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
# CACHÉ — solo se guarda el resultado global (métricas + coeficientes + tendencia)
# Las predicciones individuales NO se cachean (son dinámicas por miembro)
# ──────────────────────────────────────────────────────────────────────────────

CACHE_COLLECTION = "analytics_cache"
CACHE_KEY        = "regresion_global"


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
        print(f"[regresion cache] Error leyendo caché: {e}")
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
        print(f"[regresion cache] Error guardando caché: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: normalizar id
# ──────────────────────────────────────────────────────────────────────────────

def _resolver_id_miembro(spark, id_entrada: str) -> str:
    import sys, os, re
    directorio_actual = os.path.dirname(__file__)
    if directorio_actual not in sys.path:
        sys.path.insert(0, directorio_actual)

    from spark_config import leer_coleccion
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    def oid_hex(val):
        if val is None:
            return None
        m = re.search(r"[0-9a-fA-F]{24}", str(val))
        return m.group(0) if m else str(val)

    oid_udf = F.udf(oid_hex, StringType())

    hits = (
        leer_coleccion(spark, "progreso_fisico")
        .withColumn("id_hex", oid_udf(F.col("id_miembro")))
        .filter(F.col("id_hex") == id_entrada)
        .count()
    )
    if hits > 0:
        return id_entrada

    df_miembro = (
        leer_coleccion(spark, "miembros")
        .withColumn("id_usuario_hex", oid_udf(F.col("id_usuario")))
        .filter(F.col("id_usuario_hex") == id_entrada)
        .select(oid_udf(F.col("_id")).alias("id_miembro_hex"))
        .limit(1)
    )
    filas = df_miembro.collect()
    if filas:
        id_miembro_real = filas[0]["id_miembro_hex"]
        hits2 = (
            leer_coleccion(spark, "progreso_fisico")
            .withColumn("id_hex", oid_udf(F.col("id_miembro")))
            .filter(F.col("id_hex") == id_miembro_real)
            .count()
        )
        if hits2 > 0:
            return id_miembro_real

    return None


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA REGRESIÓN LINEAL (PREDICCIÓN DE PESO)
# ──────────────────────────────────────────────────────────────────────────────

def _regresion_global(spark):
    import sys, os
    directorio_actual = os.path.dirname(__file__)
    if directorio_actual not in sys.path:
        sys.path.insert(0, directorio_actual)

    from spark_config import leer_coleccion
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window
    from pyspark.ml.regression import LinearRegression
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.evaluation import RegressionEvaluator

    # 1. EXTRACCIÓN Y LIMPIEZA
    # Seleccionamos las variables físicas que influyen en el peso corporal.
    df = (
        leer_coleccion(spark, "progreso_fisico")
        .select(
            F.col("id_miembro").cast("string").alias("id_miembro"),
            F.col("peso").cast("double"),
            F.col("bmi").cast("double"),
            F.col("cintura").cast("double"),
            F.col("grasa_corporal").cast("double"),
            F.col("fecha_registro")
        )
        .filter(
            F.col("peso").isNotNull() &
            F.col("fecha_registro").isNotNull() &
            (F.col("peso") > 0)
        )
    )

    # Validación de volumen mínimo para asegurar que el modelo pueda generalizar.
    if df.count() < 10:
        raise ValueError("Se necesitan al menos 10 registros de progreso para entrenar el modelo.")

    # 2. INGENIERÍA DE CARACTERÍSTICAS (FEATURE ENGINEERING)
    # Calculamos la "antigüedad" en días para cada registro respecto al primer registro del miembro.
    # Esto convierte una fecha absoluta en una variable numérica lineal para la regresión.
    w_min = Window.partitionBy("id_miembro")
    df = df.withColumn(
        "fecha_inicio", F.min("fecha_registro").over(w_min)
    ).withColumn(
        "dias", F.datediff(F.col("fecha_registro"), F.col("fecha_inicio")).cast("double")
    )

    # 3. IMPUTACIÓN DE VALORES FALTANTES
    # Se calculan medias globales para llenar huecos y no descartar registros valiosos.
    media_cintura = df.agg(F.avg("cintura")).collect()[0][0] or 80.0
    media_grasa   = df.agg(F.avg("grasa_corporal")).collect()[0][0] or 22.0

    df = df.fillna({"cintura": media_cintura, "grasa_corporal": media_grasa, "bmi": 25.0})

    # 4. PREPARACIÓN DEL VECTOR DE ENTRADA
    # El modelo de Spark requiere que todas las variables independientes estén en una sola columna tipo Vector.
    assembler = VectorAssembler(
        inputCols=["dias", "cintura", "grasa_corporal", "bmi"], outputCol="features"
    )
    df_ml = assembler.transform(df).select(
        "features", F.col("peso").alias("label"), "dias", "id_miembro"
    )

    # División de datos: 80% para aprender patrones y 20% para validar la precisión.
    train, test = df_ml.randomSplit([0.8, 0.2], seed=42)

    # 5. ENTRENAMIENTO (REGRESIÓN RIDGE)
    # Se utiliza regParam=0.1 (Regularización L2) para evitar que los coeficientes crezcan demasiado
    # y el modelo se sobreajuste (overfitting) a ruidos en los datos.
    lr = LinearRegression(
        featuresCol="features", labelCol="label",
        maxIter=50, regParam=0.1, elasticNetParam=0.0
    )
    model = lr.fit(train)

    # 6. EVALUACIÓN DE MÉTRICAS
    predicciones = model.transform(test)
    eval_rmse = RegressionEvaluator(labelCol="label", predictionCol="prediction", metricName="rmse")
    eval_r2   = RegressionEvaluator(labelCol="label", predictionCol="prediction", metricName="r2")
    eval_mae  = RegressionEvaluator(labelCol="label", predictionCol="prediction", metricName="mae")

    # Extracción de coeficientes para entender el impacto de cada variable:
    # Peso = Intercepto + (B1 * dias) + (B2 * cintura) + ...
    coeficientes = {
        "dias":           round(float(model.coefficients[0]), 6),
        "cintura":        round(float(model.coefficients[1]), 6),
        "grasa_corporal": round(float(model.coefficients[2]), 6),
        "bmi":            round(float(model.coefficients[3]), 6),
        "intercepto":     round(float(model.intercept),       4)
    }

    # Agregación para reporte visual de la tendencia real histórica.
    tendencia = (
        df.withColumn("mes", F.date_format("fecha_registro", "yyyy-MM"))
        .groupBy("mes")
        .agg(
            F.round(F.avg("peso"), 2).alias("peso_promedio"),
            F.count("*").alias("registros")
        )
        .orderBy("mes")
    )

    return (
        model,
        {"rmse": round(eval_rmse.evaluate(predicciones), 4), # Error cuadrático medio
         "r2":   round(eval_r2.evaluate(predicciones),   4), # Coeficiente de determinación
         "mae":  round(eval_mae.evaluate(predicciones),  4)},# Error absoluto medio
        coeficientes,
        [row.asDict() for row in tendencia.collect()],
        media_cintura,
        media_grasa
    )


def _build_global_payload(metricas, coeficientes, tendencia):
    """Construye el resumen interpretativo para el usuario final."""
    r2 = metricas["r2"]
    # Lógica de interpretación del R-cuadrado (capacidad predictiva del modelo).
    interpretacion = (
        "Excelente — el modelo explica mas del 80% de la varianza del peso" if r2 > 0.8 else
        "Bueno — explica mas del 60% de la varianza"                         if r2 > 0.6 else
        "Moderado — hay factores no capturados (edad, dieta, etc.)"          if r2 > 0.4 else
        "Bajo — se recomienda mas historial de datos o features adicionales"
    )
    from datetime import datetime
    return {
        "algoritmo":             "Regresion Lineal (Ridge)",
        "descripcion":           "Prediccion de peso corporal basada en dias de entrenamiento, cintura, grasa y BMI",
        "features_usadas":       ["dias_desde_inicio", "cintura_cm", "grasa_corporal_%", "bmi"],
        "variable_objetivo":     "peso_kg",
        "metricas":              metricas,
        "interpretacion_r2":     interpretacion,
        "coeficientes":          coeficientes,
        "tendencia_peso_global": tendencia,
        "ejecutado_en":          datetime.now().isoformat()
    }


def _predecir_miembro(spark, model, id_miembro: str, dias_futuro: int,
                     media_cintura: float, media_grasa: float):
    """
    Realiza una proyección futura para un miembro específico.
    Utiliza el modelo global pero con los datos base (cintura, grasa) del usuario.
    """
    import sys, os, re
    from datetime import datetime, timedelta
    
    # UDF para limpiar y estandarizar el formato de los ID provenientes de MongoDB.
    def oid_hex(val):
        if val is None:
            return None
        m = re.search(r"[0-9a-fA-F]{24}", str(val))
        return m.group(0) if m else str(val)

    from pyspark.sql.types import StringType
    oid_udf = F.udf(oid_hex, StringType())

    # Carga de la historia de progreso del miembro seleccionado.
    df = (
        leer_coleccion(spark, "progreso_fisico")
        .withColumn("id_miembro_hex", oid_udf(F.col("id_miembro")))
        .filter(F.col("id_miembro_hex") == id_miembro)
        .select(
            F.col("peso").cast("double"),
            F.col("bmi").cast("double"),
            F.col("cintura").cast("double"),
            F.col("grasa_corporal").cast("double"),
            F.col("fecha_registro")
        )
        .filter(F.col("peso").isNotNull())
        .orderBy("fecha_registro")
    )

    if df.count() == 0:
        return None, []

    # Obtenemos los últimos valores físicos registrados para proyectarlos.
    ultimo          = df.orderBy(F.col("fecha_registro").desc()).limit(1).collect()[0]
    primer_registro = df.agg(F.min("fecha_registro")).collect()[0][0]
    dias_actuales   = (datetime.now() - primer_registro).days if primer_registro else 0

    historial = [
        {
            "fecha": row["fecha_registro"].strftime("%Y-%m-%d")
                     if hasattr(row["fecha_registro"], "strftime")
                     else str(row["fecha_registro"]),
            "peso": round(float(row["peso"]), 1)
        }
        for row in df.collect()
    ]

    # Datos de entrada estables para la predicción (Ceteris Paribus).
    cintura = float(ultimo["cintura"]         or media_cintura)
    grasa   = float(ultimo["grasa_corporal"] or media_grasa)
    bmi     = float(ultimo["bmi"]            or 25.0)

    from pyspark.ml.feature import VectorAssembler
    assembler = VectorAssembler(
        inputCols=["dias", "cintura", "grasa_corporal", "bmi"], outputCol="features"
    )

    # 7. GENERACIÓN DE PREDICCIONES POR INTERVALOS
    # Generamos estimaciones cada 30 días hasta el límite definido.
    predicciones_futuras = []
    for d in [30, 60, 90, 120, 150, 180]:
        if d <= dias_futuro:
            # Creamos un DataFrame efímero para alimentar el modelo.
            pred_data = spark.createDataFrame(
                [(float(dias_actuales + d), cintura, grasa, bmi)],
                ["dias", "cintura", "grasa_corporal", "bmi"]
            )
            # El modelo aplica la fórmula lineal aprendida:
            # Peso_pred = Intercepto + (coef_dias * dias_futuros) + ...
            peso_pred = model.transform(
                assembler.transform(pred_data)
            ).collect()[0]["prediction"]
            
            predicciones_futuras.append({
                "dias_desde_hoy":   d,
                "fecha_estimada":   (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d"),
                "peso_predicho_kg": round(float(peso_pred), 2)
            })

    return historial, predicciones_futuras

# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS DIAGNÓSTICO (sin cambios)
# ──────────────────────────────────────────────────────────────────────────────

@spark_regresion_bp.route("/api/analytics/regresion/debug2/<id_entrada>", methods=["GET"])
@jwt_required()
def debug_cadena_ids(id_entrada: str):
    try:
        import sys, os, re
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)

        from spark_config import leer_coleccion
        from pyspark.sql import functions as F
        from pyspark.sql.types import StringType

        id_entrada = id_entrada.strip("{}")
        spark = _get_spark()

        def oid_hex(val):
            if val is None:
                return None
            m = re.search(r"[0-9a-fA-F]{24}", str(val))
            return m.group(0) if m else str(val)

        oid_udf = F.udf(oid_hex, StringType())
        resultado = {"id_entrada": id_entrada, "pasos": {}}

        usuarios = [
            r.asDict() for r in (
                leer_coleccion(spark, "usuarios")
                .withColumn("_id_hex", oid_udf(F.col("_id")))
                .filter(F.col("_id_hex") == id_entrada)
                .select(oid_udf(F.col("_id")).alias("id_usuario"), F.col("nombre"), F.col("email"))
            ).collect()
        ]
        resultado["pasos"]["1_en_usuarios"] = {"encontrado": len(usuarios) > 0, "datos": usuarios}

        miembro_por_id = [
            r.asDict() for r in (
                leer_coleccion(spark, "miembros")
                .withColumn("_id_hex", oid_udf(F.col("_id")))
                .filter(F.col("_id_hex") == id_entrada)
                .select(oid_udf(F.col("_id")).alias("id_miembro"),
                        oid_udf(F.col("id_usuario")).alias("id_usuario"), F.col("estado"))
            ).collect()
        ]
        resultado["pasos"]["2_en_miembros_como_id"] = {"encontrado": len(miembro_por_id) > 0, "datos": miembro_por_id}

        miembro_por_usuario = [
            r.asDict() for r in (
                leer_coleccion(spark, "miembros")
                .withColumn("id_usuario_hex", oid_udf(F.col("id_usuario")))
                .filter(F.col("id_usuario_hex") == id_entrada)
                .select(oid_udf(F.col("_id")).alias("id_miembro"),
                        oid_udf(F.col("id_usuario")).alias("id_usuario"), F.col("estado"))
            ).collect()
        ]
        resultado["pasos"]["3_en_miembros_como_id_usuario"] = {"encontrado": len(miembro_por_usuario) > 0, "datos": miembro_por_usuario}

        registros_progreso = (
            leer_coleccion(spark, "progreso_fisico")
            .withColumn("id_hex", oid_udf(F.col("id_miembro")))
            .filter(F.col("id_hex") == id_entrada)
            .count()
        )
        resultado["pasos"]["4_en_progreso_fisico"] = {
            "encontrado": registros_progreso > 0,
            "num_registros": registros_progreso
        }

        id_correcto = None
        if registros_progreso > 0:
            id_correcto = id_entrada
            diagnostico = "OK: Este id ya es correcto para el endpoint predecir."
        elif miembro_por_usuario:
            id_correcto = miembro_por_usuario[0]["id_miembro"]
            diagnostico = f"CORREGIR: Estas pasando el id de USUARIO. El id correcto de MIEMBRO es: {id_correcto}"
        elif miembro_por_id:
            id_correcto = id_entrada
            diagnostico = "PROBLEMA: Es un id de miembro valido pero no tiene registros en progreso_fisico."
        elif usuarios:
            diagnostico = "PROBLEMA: Es un id de usuario sin miembro asociado."
        else:
            diagnostico = "NOT_FOUND: El id no existe en ninguna coleccion."

        resultado["id_correcto_para_predecir"] = id_correcto
        resultado["diagnostico"]               = diagnostico
        return jsonify(resultado), 200

    except RuntimeError as e:
        return jsonify({"error": str(e), "spark_enabled": False}), 503
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/debug/<id_miembro>", methods=["GET"])
@jwt_required()
def debug_miembro(id_miembro: str):
    id_miembro = id_miembro.strip("{}")
    try:
        import sys, os, re
        directorio_actual = os.path.dirname(__file__)
        if directorio_actual not in sys.path:
            sys.path.insert(0, directorio_actual)

        from spark_config import leer_coleccion
        from pyspark.sql import functions as F
        from pyspark.sql.types import StringType, StructType

        spark  = _get_spark()
        df_raw = leer_coleccion(spark, "progreso_fisico")
        tipo_campo = str(df_raw.schema["id_miembro"].dataType)
        es_struct  = isinstance(df_raw.schema["id_miembro"].dataType, StructType)
        subcampos  = ([f.name for f in df_raw.schema["id_miembro"].dataType.fields] if es_struct else [])

        muestras_raw  = [str(r["id_miembro"]) for r in df_raw.select("id_miembro").limit(5).collect()]
        muestras_cast = [str(r["c"]) for r in df_raw.select(F.col("id_miembro").cast("string").alias("c")).limit(5).collect()]

        def oid_hex(val):
            if val is None: return None
            m = re.search(r"[0-9a-fA-F]{24}", str(val))
            return m.group(0) if m else str(val)

        oid_udf = F.udf(oid_hex, StringType())
        muestras_udf = [str(r["h"]) for r in df_raw.select(oid_udf(F.col("id_miembro")).alias("h")).limit(5).collect()]

        total_db   = df_raw.count()
        hits_cast  = df_raw.filter(F.col("id_miembro").cast("string") == id_miembro).count()
        hits_udf   = (df_raw.withColumn("h", oid_udf(F.col("id_miembro"))).filter(F.col("h") == id_miembro).count())

        diagnostico = (
            "OK_UDF"  if hits_udf  > 0 else
            "OK_CAST" if hits_cast > 0 else
            "FALLO: Este id no existe en progreso_fisico. Usa /debug2/{id} para trazar la cadena completa."
        )

        return jsonify({
            "id_recibido": id_miembro, "tipo_campo_spark": tipo_campo,
            "muestras_raw": muestras_raw, "muestras_cast_string": muestras_cast,
            "muestras_udf_hex": muestras_udf, "total_registros_db": total_db,
            "hits_con_cast": hits_cast, "hits_con_udf": hits_udf,
            "diagnostico": diagnostico
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────────────────
# ENDPOINTS PRINCIPALES
# ──────────────────────────────────────────────────────────────────────────────

@spark_regresion_bp.route("/api/analytics/regresion", methods=["GET"])
@jwt_required()
def regresion_analytics():
    """
    Devuelve métricas globales del modelo desde caché.
    Si no hay caché, entrena el modelo por primera vez.
    """
    try:
        cached = _get_cached_result()
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        spark = _get_spark()
        model, metricas, coeficientes, tendencia, media_cintura, media_grasa = _regresion_global(spark)
        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        # Guardar medias para predicciones futuras también
        payload["_medias"] = {"cintura": media_cintura, "grasa": media_grasa}
        _save_cached_result(payload)
        return jsonify(payload), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/train", methods=["POST"])
@jwt_required()
def regresion_train():
    """
    Fuerza el re-entrenamiento del modelo de regresión y actualiza la caché.
    No requiere body.
    """
    try:
        spark = _get_spark()
        model, metricas, coeficientes, tendencia, media_cintura, media_grasa = _regresion_global(spark)
        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        payload["_medias"] = {"cintura": media_cintura, "grasa": media_grasa}
        _save_cached_result(payload)

        return jsonify({
            **payload,
            "mensaje": "Modelo de regresión reentrenado y caché actualizada."
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
    Acepta tanto el id de MIEMBRO como el id de USUARIO.
    Siempre re-entrena el modelo en vivo (las predicciones individuales no se cachean).
    """
    try:
        id_entrada  = id_entrada.strip("{}")
        dias_futuro = request.args.get("dias", 180, type=int)
        if not (30 <= dias_futuro <= 365):
            return jsonify({"error": "dias debe estar entre 30 y 365"}), 400

        spark = _get_spark()
        id_miembro_real = _resolver_id_miembro(spark, id_entrada)

        if id_miembro_real is None:
            return jsonify({
                "error": "No se encontraron registros de progreso para este id.",
                "sugerencia": f"Llama a /api/analytics/regresion/debug2/{id_entrada} para diagnosticar la cadena de ids."
            }), 404

        # Intentar usar medias guardadas en caché para evitar re-entrenar
        media_cintura, media_grasa = 80.0, 22.0
        cached = _get_cached_result()
        if cached and "_medias" in cached:
            media_cintura = cached["_medias"].get("cintura", 80.0)
            media_grasa   = cached["_medias"].get("grasa",   22.0)

        model, _, _, _, media_cintura, media_grasa = _regresion_global(spark)

        historial, predicciones = _predecir_miembro(
            spark, model, id_miembro_real, dias_futuro, media_cintura, media_grasa
        )

        if historial is None:
            return jsonify({"error": "El miembro no tiene registros de progreso"}), 404

        tendencia = "estable"
        if len(predicciones) >= 2 and len(historial) >= 1:
            diff = predicciones[-1]["peso_predicho_kg"] - historial[-1]["peso"]
            if diff < -1.5:  tendencia = "bajando"
            elif diff > 1.5: tendencia = "subiendo"

        return jsonify({
            "id_entrada":           id_entrada,
            "id_miembro_resuelto":  id_miembro_real,
            "algoritmo":            "Regresion Lineal",
            "horizonte_dias":       dias_futuro,
            "peso_actual_kg":       historial[-1]["peso"] if historial else None,
            "tendencia":            tendencia,
            "historial_peso":       historial,
            "predicciones_futuras": predicciones,
            "advertencia":          "Prediccion basada en tendencia historica. Factores como dieta y rutina pueden alterar el resultado.",
            "ejecutado_en":         datetime.now().isoformat()
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500