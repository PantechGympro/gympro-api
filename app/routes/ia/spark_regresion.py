"""
spark_regresion.py — Regresión Lineal (Ridge) para predicción de peso corporal.

Motor: scikit-learn (en proceso, sin JVM, sin internet).
Datos: pymongo directo sobre colección progreso_fisico.

Diseño de caché en dos capas:
  1. Entrenamiento global (por gym_id): coeficientes + métricas con TTL configurable.
  2. Predicción individual: usa coeficientes cacheados + pymongo (sin re-entrenar).
     → sub-100ms por request.

Variables de entorno:
    ANALYTICS_CACHE_TTL_HOURS=24
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import datetime, timedelta
import re as _re

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db, resolve_gym_id

spark_regresion_bp = Blueprint("spark_regresion", __name__)


def _trainer_scope():
    """
    Si el usuario autenticado es Entrenador, devuelve su id para acotar el
    modelo SOLO a los miembros que él entrena. Para owner_gym / superadmin
    devuelve None (modelo a nivel de gimnasio).
    """
    if get_jwt().get("role") == "Entrenador":
        try:
            return int(get_jwt_identity())
        except (TypeError, ValueError):
            return None
    return None


def _cache_key(gym_id, trainer_id=None) -> str:
    scope = f"_t{trainer_id}" if trainer_id is not None else ""
    return f"regresion_gym{gym_id}{scope}"


# ── Helpers de fecha ──────────────────────────────────────────────────────────

def _to_naive_datetime(val) -> "datetime | None":
    """
    Convierte cualquier representación de fecha a datetime naive.
    El seed guarda fecha_registro como string 'YYYY-MM-DD'.
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
        # Intentar el string completo con cada formato
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        # Fallback: tomar solo los primeros 10 chars como fecha ISO
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d")
        except ValueError:
            pass
    return None


# ── Entrenamiento global ──────────────────────────────────────────────────────

def _regresion_global(gym_id=None, trainer_id=None):
    """
    Entrena Ridge Regression con datos de progreso_fisico del gimnasio
    (o solo de los miembros del entrenador, si trainer_id viene dado).
    Retorna: (metricas, coeficientes, tendencia, media_cintura, media_grasa)
    """
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
    from bson import ObjectId

    db = get_mongo_db()

    # 1. Obtener IDs de miembros del gimnasio (o solo los del entrenador)
    #
    # NO se filtra por estado "Activo". El modelo relaciona peso con tiempo,
    # cintura, grasa e IMC, y esa relacion no cambia porque una membresia haya
    # vencido: las mediciones pasadas siguen siendo validas para entrenar.
    # Filtrarlas dejaba el modelo sin datos en gimnasios donde la mayoria de
    # miembros figura como inactiva, y la pantalla respondia "aun no hay
    # suficientes datos" con decenas de registros en la base.
    query_m = {}
    if gym_id is not None:
        query_m["id_gimnasio_pg"] = int(gym_id)
    if trainer_id is not None:
        query_m["id_entrenador_pg"] = int(trainer_id)

    member_oids = [m["_id"] for m in db.miembros.find(query_m, {"_id": 1})]
    if not member_oids:
        raise ValueError(f"No hay miembros registrados para el gimnasio {gym_id}.")

    # 2. Leer progreso_fisico de esos miembros
    registros = list(db.progreso_fisico.find(
        {"id_miembro": {"$in": member_oids}},
        {"id_miembro": 1, "peso": 1, "imc": 1, "grasa_corporal": 1,
         "cintura": 1, "fecha_registro": 1},
    ))

    if len(registros) < 10:
        raise ValueError("Se necesitan al menos 10 registros de progreso para entrenar el modelo.")

    # 3. Construir arrays numpy — agrupar primer registro por miembro para "días desde inicio"
    primer_fecha: dict[str, datetime] = {}
    for r in registros:
        mid = str(r["id_miembro"])
        dt  = _to_naive_datetime(r.get("fecha_registro"))
        if dt and (mid not in primer_fecha or dt < primer_fecha[mid]):
            primer_fecha[mid] = dt

    rows = []
    for r in registros:
        try:
            peso = float(r["peso"])
        except (TypeError, ValueError):
            continue
        if peso <= 0:
            continue
        dt = _to_naive_datetime(r.get("fecha_registro"))
        if dt is None:
            continue
        mid  = str(r["id_miembro"])
        dias = max(0, (dt - primer_fecha[mid]).days)
        cintura = r.get("cintura")
        grasa   = r.get("grasa_corporal")
        bmi     = r.get("imc")
        rows.append({
            "peso": peso, "dias": dias,
            "cintura": float(cintura) if cintura else None,
            "grasa":   float(grasa)   if grasa   else None,
            "bmi":     float(bmi)     if bmi     else None,
            "mes": dt.strftime("%Y-%m"),
        })

    if not rows:
        raise ValueError("Datos de progreso insuficientes para el entrenamiento.")

    # 4. Imputación de nulos
    media_cintura = sum(r["cintura"] for r in rows if r["cintura"]) / max(1, sum(1 for r in rows if r["cintura"])) or 80.0
    media_grasa   = sum(r["grasa"]   for r in rows if r["grasa"])   / max(1, sum(1 for r in rows if r["grasa"]))   or 22.0
    for r in rows:
        if r["cintura"] is None: r["cintura"] = media_cintura
        if r["grasa"]   is None: r["grasa"]   = media_grasa
        if r["bmi"]     is None: r["bmi"]      = 25.0

    X = np.array([[r["dias"], r["cintura"], r["grasa"], r["bmi"]] for r in rows])
    y = np.array([r["peso"] for r in rows])

    # 5. Train/test split 80/20 reproducible
    rng   = np.random.default_rng(42)
    idx   = rng.permutation(len(X))
    split = int(len(X) * 0.8)
    X_tr, X_te = X[idx[:split]], X[idx[split:]]
    y_tr, y_te = y[idx[:split]], y[idx[split:]]

    # 6. Ridge Regression (L2, equivalente a elasticNetParam=0 en Spark)
    model = Ridge(alpha=0.1).fit(X_tr, y_tr)
    y_pred = model.predict(X_te) if len(X_te) > 0 else model.predict(X_tr)
    y_ref  = y_te if len(X_te) > 0 else y_tr

    metricas = {
        "rmse": round(float(mean_squared_error(y_ref, y_pred) ** 0.5), 4),
        "r2":   round(float(r2_score(y_ref, y_pred)), 4),
        "mae":  round(float(mean_absolute_error(y_ref, y_pred)), 4),
    }
    coeficientes = {
        "dias":           round(float(model.coef_[0]), 6),
        "cintura":        round(float(model.coef_[1]), 6),
        "grasa_corporal": round(float(model.coef_[2]), 6),
        "bmi":            round(float(model.coef_[3]), 6),
        "intercepto":     round(float(model.intercept_), 4),
    }

    # 7. Tendencia histórica global (promedio de peso por mes)
    from collections import defaultdict
    mes_pesos: dict[str, list] = defaultdict(list)
    for r in rows:
        mes_pesos[r["mes"]].append(r["peso"])

    tendencia = sorted([
        {"mes": mes, "peso_promedio": round(sum(ps)/len(ps), 2), "registros": len(ps)}
        for mes, ps in mes_pesos.items()
    ], key=lambda x: x["mes"])

    return metricas, coeficientes, tendencia, media_cintura, media_grasa


def _build_global_payload(metricas: dict, coeficientes: dict, tendencia: list) -> dict:
    r2 = metricas["r2"]
    interpretacion = (
        "Excelente — el modelo explica más del 80% de la varianza del peso" if r2 > 0.8 else
        "Bueno — explica más del 60% de la varianza"                        if r2 > 0.6 else
        "Moderado — hay factores no capturados (edad, dieta, etc.)"         if r2 > 0.4 else
        "Bajo — se recomienda más historial de datos o features adicionales"
    )
    return {
        "algoritmo":             "Regresión Lineal (Ridge)",
        "descripcion":           "Predicción de peso corporal basada en días de entrenamiento, grasa y BMI",
        "features_usadas":       ["dias_desde_inicio", "cintura_cm", "grasa_corporal_%", "bmi"],
        "variable_objetivo":     "peso_kg",
        "metricas":              metricas,
        "interpretacion_r2":     interpretacion,
        "coeficientes":          coeficientes,
        "tendencia_peso_global": tendencia,
        "ejecutado_en":          datetime.now().isoformat(),
    }


# ── Predicción individual (sin re-entrenar) ───────────────────────────────────

def _resolver_id_miembro_mongo(id_entrada: str):
    """
    Resuelve id_entrada → hex ObjectId de progreso_fisico.id_miembro.

    Acepta tres formas de id_entrada:
      1. ObjectId hex (24 chars) del propio miembro — path legacy Mongo
      2. ObjectId hex del usuario Mongo — se resuelve por id_usuario
      3. Entero PG como string (ej. "42") — se resuelve por id_usuario_pg (Sprint 2+)
    """
    from bson import ObjectId
    db = get_mongo_db()

    # ── Path 1 y 2: ObjectId válido ──────────────────────────────────────────
    try:
        oid = ObjectId(id_entrada)

        # ¿El id es directamente el _id del miembro y tiene progreso?
        if db.progreso_fisico.count_documents({"id_miembro": oid}, limit=1):
            return id_entrada

        # ¿El id apunta al _id de un miembro?
        miembro = db.miembros.find_one({"_id": oid}, {"_id": 1})
        if miembro:
            m_oid = miembro["_id"]
            if db.progreso_fisico.count_documents({"id_miembro": m_oid}, limit=1):
                return str(m_oid)

        # ¿El id es el id_usuario (Mongo) de un miembro?
        miembro = db.miembros.find_one({"id_usuario": oid}, {"_id": 1})
        if miembro:
            m_oid = miembro["_id"]
            if db.progreso_fisico.count_documents({"id_miembro": m_oid}, limit=1):
                return str(m_oid)

    except Exception:
        pass  # No es un ObjectId — continuar al path PG

    # ── Path 3: entero PG (id_usuario_pg) ────────────────────────────────────
    try:
        pg_id   = int(id_entrada)
        miembro = db.miembros.find_one({"id_usuario_pg": pg_id}, {"_id": 1})
        if miembro:
            m_oid = miembro["_id"]
            if db.progreso_fisico.count_documents({"id_miembro": m_oid}, limit=1):
                return str(m_oid)
            # El miembro existe pero aún no tiene registros de progreso
            return str(m_oid)   # devolver el id para que el endpoint retorne el error apropiado
    except (ValueError, TypeError):
        pass

    return None


MIN_REGISTROS_PERSONALES = 3


def _regresion_personal(historial: list, dias_futuro: int):
    """
    Ajusta una recta al peso del propio miembro y proyecta hacia adelante.

    Existe porque el modelo del gimnasio necesita 10 registros repartidos entre
    varios miembros, y eso dejaba sin prediccion a alguien que ya tenia las
    suyas: la pantalla decia "6 / 3 registros" con la barra llena y aun asi
    respondia que faltaban datos. Con tres mediciones propias hay suficiente
    para trazar su tendencia, que ademas es mas fiel que aplicarle los
    coeficientes promedio del gimnasio.

    Es un ajuste por minimos cuadrados sobre (dias, peso), sin dependencias:
    para una serie de unas pocas decenas de puntos no hace falta mas.

    Devuelve (predicciones, r2) o (None, None) si no alcanza el minimo.
    """
    if len(historial) < MIN_REGISTROS_PERSONALES:
        return None, None

    base = _to_naive_datetime(historial[0].get("_dt"))
    if base is None:
        return None, None

    puntos = []
    for h in historial:
        dt = _to_naive_datetime(h.get("_dt"))
        if dt is None:
            continue
        puntos.append(((dt - base).days, h["peso"]))

    if len(puntos) < MIN_REGISTROS_PERSONALES:
        return None, None

    n   = len(puntos)
    sx  = sum(p[0] for p in puntos)
    sy  = sum(p[1] for p in puntos)
    sxx = sum(p[0] * p[0] for p in puntos)
    sxy = sum(p[0] * p[1] for p in puntos)

    denominador = n * sxx - sx * sx
    if denominador == 0:
        # Todas las mediciones son del mismo dia: no hay eje temporal que
        # ajustar. Se proyecta el peso actual como una linea plana.
        pendiente, interseccion = 0.0, sy / n
    else:
        pendiente    = (n * sxy - sx * sy) / denominador
        interseccion = (sy - pendiente * sx) / n

    # Coeficiente de determinacion: cuanta de la variacion del peso explica el
    # paso del tiempo. Sirve para avisar cuando la tendencia es poco fiable.
    media_y = sy / n
    ss_tot  = sum((p[1] - media_y) ** 2 for p in puntos)
    ss_res  = sum((p[1] - (interseccion + pendiente * p[0])) ** 2 for p in puntos)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0

    dias_hasta_hoy = max(0, (datetime.now() - base).days)
    predicciones = []
    for d in (30, 60, 90, 120, 150, 180):
        if d > dias_futuro:
            continue
        peso = interseccion + pendiente * (dias_hasta_hoy + d)
        predicciones.append({
            "dias_desde_hoy":   d,
            "fecha_estimada":   (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d"),
            # Un modelo lineal extrapolado a 6 meses puede dar cifras absurdas
            # (pesos negativos o de 300 kg). Se acota a un rango humano para no
            # mostrarle al miembro una proyeccion imposible.
            "peso_predicho_kg": round(max(30.0, min(300.0, float(peso))), 2),
        })

    return predicciones, round(float(r2), 4)


def _historial_de_miembro(id_miembro: str):
    """Serie de peso del miembro, ordenada, con la fecha cruda para calcular."""
    from bson import ObjectId
    db = get_mongo_db()
    try:
        oid = ObjectId(id_miembro)
    except Exception:
        return [], []

    registros = list(
        db.progreso_fisico.find(
            {"id_miembro": oid},
            {"peso": 1, "imc": 1, "grasa_corporal": 1, "cintura": 1, "fecha_registro": 1, "_id": 0},
        ).sort("fecha_registro", 1)
    )

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
            "_dt":   r.get("fecha_registro"),
        })

    return registros, historial


def _predecir_con_coeficientes(id_miembro: str, dias_futuro: int,
                                coeficientes: dict, medias: dict):
    """Proyecta el peso futuro usando coeficientes cacheados + pymongo. Sin scikit-learn."""
    from bson import ObjectId
    db = get_mongo_db()
    try:
        oid = ObjectId(id_miembro)
    except Exception:
        return None, []

    registros = list(
        db.progreso_fisico.find(
            {"id_miembro": oid},
            {"peso": 1, "imc": 1, "grasa_corporal": 1, "cintura": 1, "fecha_registro": 1, "_id": 0},
        ).sort("fecha_registro", 1)
    )

    if not registros:
        return None, []

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

    primer_dt = _to_naive_datetime(registros[0].get("fecha_registro"))
    dias_actuales = max(0, (datetime.now() - primer_dt).days) if primer_dt else 0

    ultimo  = registros[-1]
    cintura = float(ultimo.get("cintura") or medias.get("cintura", 80.0))
    grasa   = float(ultimo.get("grasa_corporal") or medias.get("grasa", 22.0))
    bmi     = float(ultimo.get("imc") or 25.0)

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


MIN_REGISTROS_MODELO = 10


def _diagnostico_datos(gym_id, trainer_id):
    """
    Cuenta cuantos miembros y registros hay en el alcance pedido.

    Sirve para que la pantalla explique QUE falta en lugar de un "no hay datos
    suficientes" que no dice si el problema son los miembros, las mediciones o
    el alcance del entrenador.
    """
    db = get_mongo_db()
    query = {}
    if gym_id is not None:
        query["id_gimnasio_pg"] = int(gym_id)
    if trainer_id is not None:
        query["id_entrenador_pg"] = int(trainer_id)

    oids = [m["_id"] for m in db.miembros.find(query, {"_id": 1})]
    registros = db.progreso_fisico.count_documents(
        {"id_miembro": {"$in": oids}, "peso": {"$ne": None}}
    ) if oids else 0
    con_registro = len(db.progreso_fisico.distinct(
        "id_miembro", {"id_miembro": {"$in": oids}, "peso": {"$ne": None}}
    )) if oids else 0

    return {
        "miembros_en_alcance":  len(oids),
        "miembros_con_medidas": con_registro,
        "registros":            registros,
        "minimo_requerido":     MIN_REGISTROS_MODELO,
        "alcance":              "entrenador" if trainer_id is not None else "gimnasio",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@spark_regresion_bp.route("/api/analytics/regresion", methods=["GET"])
@jwt_required()
def regresion_analytics():
    """Devuelve métricas globales desde caché. Si expiró, re-entrena."""
    try:
        gym_id     = resolve_gym_id()
        trainer_id = _trainer_scope()
        key        = _cache_key(gym_id, trainer_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        metricas, coeficientes, tendencia, mc, mg = _regresion_global(gym_id, trainer_id)
        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        payload["_medias"] = {"cintura": mc, "grasa": mg}
        cache_set(key, payload)
        return jsonify(payload), 200

    except ValueError as ve:
        # Se acompana el motivo con las cifras reales del alcance para que la
        # pantalla pueda decir "tienes 4 miembros y 6 mediciones, faltan 4"
        # en lugar de un mensaje generico que no orienta a nadie.
        try:
            detalle = _diagnostico_datos(resolve_gym_id(), _trainer_scope())
        except Exception:
            detalle = {}
        return jsonify({"error": str(ve), **detalle}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/train", methods=["POST"])
@jwt_required()
def regresion_train():
    """Fuerza re-entrenamiento y actualiza caché."""
    try:
        gym_id     = resolve_gym_id()
        trainer_id = _trainer_scope()
        key        = _cache_key(gym_id, trainer_id)

        metricas, coeficientes, tendencia, mc, mg = _regresion_global(gym_id, trainer_id)
        payload = _build_global_payload(metricas, coeficientes, tendencia)
        payload["desde_cache"] = False
        payload["_medias"] = {"cintura": mc, "grasa": mg}
        cache_set(key, payload)

        ambito = f"entrenador {trainer_id}" if trainer_id else f"gimnasio {gym_id}"
        return jsonify({**payload,
                        "mensaje": f"Modelo reentrenado para {ambito}."}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_regresion_bp.route("/api/analytics/regresion/predecir/<id_entrada>", methods=["GET"])
@jwt_required()
def predecir_peso_miembro(id_entrada: str):
    """
    Predice el peso futuro de un miembro.

    Usa dos modelos y prefiere el mejor disponible:

      1. Modelo del gimnasio (cacheado). Pondera dias, cintura, grasa e IMC.
         Necesita 10 registros repartidos entre varios miembros.
      2. Regresion sobre el historial del PROPIO miembro. Basta con 3
         mediciones suyas.

    El segundo no es un parche: es mas fiel para el individuo, porque describe
    su tendencia real en vez de aplicarle los coeficientes promedio del
    gimnasio. Antes solo existia el primero, y por eso un miembro con seis
    mediciones propias veia "6 / 3 registros" con la barra llena y, aun asi,
    el mensaje de que faltaban datos: el gimnasio entero no llegaba a diez.
    """
    try:
        id_entrada  = id_entrada.strip("{}")
        dias_futuro = request.args.get("dias", 180, type=int)
        if not (30 <= dias_futuro <= 365):
            return jsonify({"error": "dias debe estar entre 30 y 365"}), 400

        gym_id     = resolve_gym_id()
        trainer_id = _trainer_scope()

        id_miembro_real = _resolver_id_miembro_mongo(id_entrada)
        if id_miembro_real is None:
            return jsonify({
                "error": "Este miembro todavia no tiene registros de progreso fisico.",
                "sugerencia": "Pidele que registre su peso desde Progreso Fisico.",
                "registros": 0,
                "minimo_requerido": MIN_REGISTROS_PERSONALES,
            }), 404

        registros, historial = _historial_de_miembro(id_miembro_real)
        if not historial:
            return jsonify({
                "error": "Este miembro todavia no tiene registros de peso.",
                "sugerencia": "Pidele que registre su peso desde Progreso Fisico.",
                "registros": 0,
                "minimo_requerido": MIN_REGISTROS_PERSONALES,
            }), 404

        # ── Modelo 1: el del gimnasio, si esta entrenado o puede entrenarse ──
        coeficientes = medias = None
        key = _cache_key(gym_id, trainer_id)
        cached = cache_get(key)
        if not cached or "coeficientes" not in cached:
            # Un entrenador con pocos datos propios cae al modelo del gimnasio.
            for scope in ([trainer_id, None] if trainer_id is not None else [None]):
                try:
                    k = _cache_key(gym_id, scope)
                    c = cache_get(k)
                    if c and "coeficientes" in c:
                        cached, key = c, k
                        break
                    metricas, coefs, tendencia, mc, mg = _regresion_global(gym_id, scope)
                    payload = _build_global_payload(metricas, coefs, tendencia)
                    payload["_medias"] = {"cintura": mc, "grasa": mg}
                    payload["desde_cache"] = False
                    cache_set(k, payload)
                    cached, key = payload, k
                    break
                except ValueError:
                    continue   # sin datos suficientes en este alcance

        if cached and "coeficientes" in cached:
            coeficientes = cached["coeficientes"]
            medias       = cached.get("_medias", {"cintura": 80.0, "grasa": 22.0})

        predicciones = None
        modelo       = None
        calidad      = None

        if coeficientes:
            _, predicciones = _predecir_con_coeficientes(
                id_miembro_real, dias_futuro, coeficientes, medias
            )
            if predicciones:
                modelo = "gimnasio"

        # ── Modelo 2: la propia tendencia del miembro ────────────────────────
        if not predicciones:
            predicciones, calidad = _regresion_personal(historial, dias_futuro)
            if predicciones:
                modelo = "personal"

        if not predicciones:
            # Ni uno ni otro: falta historial propio. Se devuelve el conteo real
            # para que la pantalla muestre cuanto falta en lugar de un mensaje
            # generico que contradiga su propia barra de progreso.
            return jsonify({
                "error": "Todavia no hay mediciones suficientes para proyectar una tendencia.",
                "sugerencia": (
                    f"Se necesitan al menos {MIN_REGISTROS_PERSONALES} registros "
                    "de peso en fechas distintas."
                ),
                "registros": len(historial),
                "minimo_requerido": MIN_REGISTROS_PERSONALES,
            }), 404

        tendencia_str = "estable"
        diferencia = None
        if predicciones and historial:
            diferencia = round(predicciones[-1]["peso_predicho_kg"] - historial[-1]["peso"], 2)
            if diferencia < -1.5:   tendencia_str = "bajando"
            elif diferencia > 1.5:  tendencia_str = "subiendo"

        # El historial se devuelve sin la fecha cruda, que solo servia de apoyo
        # para el calculo y no es serializable a JSON.
        historial_limpio = [{"fecha": h["fecha"], "peso": h["peso"]} for h in historial]

        return jsonify({
            "id_entrada":           id_entrada,
            "id_miembro_resuelto":  id_miembro_real,
            "algoritmo": ("Regresion lineal sobre el historial del miembro"
                          if modelo == "personal"
                          else "Regresion lineal multiple del gimnasio"),
            "modelo":               modelo,
            # r2 solo lo aporta el modelo personal; con el del gimnasio la
            # calidad se mide sobre el conjunto y no sobre este miembro.
            "calidad_ajuste":       calidad,
            "registros":            len(historial_limpio),
            "horizonte_dias":       dias_futuro,
            "peso_actual_kg":       historial_limpio[-1]["peso"] if historial_limpio else None,
            "cambio_estimado_kg":   diferencia,
            "tendencia":            tendencia_str,
            "historial_peso":       historial_limpio,
            "predicciones_futuras": predicciones,
            "advertencia": ("Proyeccion basada en la tendencia registrada. "
                            "La dieta, la rutina y el descanso pueden cambiar el resultado."),
            "ejecutado_en":         datetime.now().isoformat(),
        }), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
