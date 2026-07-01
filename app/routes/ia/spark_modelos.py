"""
spark_modelos.py — Laboratorio de Modelos.

Entrena y compara varios algoritmos de Machine Learning sobre los datos que el
gimnasio ya tiene, y los devuelve lado a lado con sus metricas de evaluacion.
Objetivo academico/demostrativo: mostrar en una sola vista distintas familias
de modelos supervisados y como se miden.

Cubre:
  Regresion (predecir peso corporal desde progreso_fisico):
    - Lineal simple (1 variable: dias)
    - Multiple (dias, cintura, grasa, IMC)
    - Polinomica (grado 2 sobre dias)
    Metricas: R2, RMSE (error cuadratico medio) y MAE (error absoluto medio).

  Clasificacion binaria (predecir riesgo de abandono):
    - Arbol de Decision
    - Random Forest
    - Regresion Logistica
    - SVM lineal
    - SVM no lineal (kernel RBF)
    Metricas: accuracy, precision, recall, F1 y matriz de confusion.

  Clasificacion multiple (predecir el objetivo del miembro):
    - Random Forest multiclase, con matriz de confusion NxN.

Motor: scikit-learn en proceso (sin JVM, sin internet). Caché por gimnasio.
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_modelos_bp = Blueprint("spark_modelos", __name__)


def _cache_key(gym_id) -> str:
    return f"modelos_gym{gym_id}"


def _parse_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=None)
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
        try:
            return datetime.strptime(val[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _gym_members(db, gym_id):
    query = {}
    if gym_id is not None:
        query["id_gimnasio_pg"] = int(gym_id)
    return list(db.miembros.find(query, {
        "_id": 1, "nombre": 1, "objetivo": 1, "estatura": 1,
        "peso_inicial": 1, "sexo": 1,
    }))


# ── Regresion ────────────────────────────────────────────────────────────────

def _build_regresion(db, member_oids):
    import numpy as np

    registros = list(db.progreso_fisico.find(
        {"id_miembro": {"$in": member_oids}},
        {"id_miembro": 1, "peso": 1, "imc": 1, "grasa_corporal": 1,
         "cintura": 1, "fecha_registro": 1},
    ))
    primer = {}
    for r in registros:
        mid = str(r["id_miembro"]); dt = _parse_dt(r.get("fecha_registro"))
        if dt and (mid not in primer or dt < primer[mid]):
            primer[mid] = dt

    filas = []
    for r in registros:
        try:
            peso = float(r["peso"])
        except (TypeError, ValueError):
            continue
        dt = _parse_dt(r.get("fecha_registro"))
        if peso <= 0 or dt is None:
            continue
        mid = str(r["id_miembro"])
        dias = max(0, (dt - primer[mid]).days)
        filas.append({
            "peso": peso, "dias": dias,
            "cintura": float(r.get("cintura") or 0) or None,
            "grasa":   float(r.get("grasa_corporal") or 0) or None,
            "bmi":     float(r.get("imc") or 0) or None,
        })
    if len(filas) < 8:
        return None

    # Imputacion simple de nulos
    def _med(k, dflt):
        vals = [f[k] for f in filas if f[k]]
        return (sum(vals) / len(vals)) if vals else dflt
    mc, mg, mb = _med("cintura", 80.0), _med("grasa", 22.0), _med("bmi", 25.0)
    for f in filas:
        f["cintura"] = f["cintura"] or mc
        f["grasa"]   = f["grasa"]   or mg
        f["bmi"]     = f["bmi"]     or mb

    y = np.array([f["peso"] for f in filas])
    return filas, y, (mc, mg, mb)


def _eval_reg(y_true, y_pred):
    from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error
    return {
        "r2":   round(float(r2_score(y_true, y_pred)), 4),
        "rmse": round(float(mean_squared_error(y_true, y_pred) ** 0.5), 3),
        "mae":  round(float(mean_absolute_error(y_true, y_pred)), 3),
    }


def _regresion_lab(db, member_oids):
    import numpy as np
    from sklearn.linear_model import LinearRegression, Ridge
    from sklearn.preprocessing import PolynomialFeatures

    built = _build_regresion(db, member_oids)
    if built is None:
        return {"error": "Se necesitan al menos 8 registros de progreso fisico para los modelos de regresion."}
    filas, y, _ = built
    n = len(filas)

    dias = np.array([[f["dias"]] for f in filas], dtype=float)
    Xmul = np.array([[f["dias"], f["cintura"], f["grasa"], f["bmi"]] for f in filas], dtype=float)

    modelos = []

    # 1. Lineal simple (solo dias)
    lin = LinearRegression().fit(dias, y)
    modelos.append({"nombre": "Regresion Lineal (1 variable)",
                    "descripcion": "Usa solo el tiempo transcurrido",
                    "metricas": _eval_reg(y, lin.predict(dias))})

    # 2. Multiple
    mul = LinearRegression().fit(Xmul, y)
    modelos.append({"nombre": "Regresion Multiple (4 variables)",
                    "descripcion": "Tiempo, cintura, grasa e IMC",
                    "metricas": _eval_reg(y, mul.predict(Xmul))})

    # 3. Polinomica (grado 2 sobre dias)
    poly = PolynomialFeatures(degree=2, include_bias=False)
    dpoly = poly.fit_transform(dias)
    pol = Ridge(alpha=0.5).fit(dpoly, y)
    modelos.append({"nombre": "Regresion Polinomica (grado 2)",
                    "descripcion": "Captura curvas y mesetas del peso",
                    "metricas": _eval_reg(y, pol.predict(dpoly))})

    # Curva para graficar: lineal vs polinomica sobre el rango de dias
    dmax = float(dias.max()) if n else 180.0
    grid = np.linspace(0, max(30.0, dmax), 24).reshape(-1, 1)
    curva = [{
        "dias":        int(round(float(g[0]))),
        "lineal":      round(float(lin.predict([[g[0]]])[0]), 2),
        "polinomica":  round(float(pol.predict(poly.transform([[g[0]]]))[0]), 2),
    } for g in grid]

    return {"n": n, "target": "Peso corporal (kg)", "modelos": modelos, "curva": curva}


# ── Clasificacion (churn binario) ────────────────────────────────────────────

def _build_churn(db, miembros, member_oids):
    from datetime import timezone, timedelta
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    hace60 = ahora - timedelta(days=60)

    a_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "ultima": {"$max": "$fecha"},
                    "ult60": {"$sum": {"$cond": [{"$gte": ["$fecha", hace60]}, 1, 0]}}}},
    ]
    a_map = {str(r["_id"]): r for r in db.asistencias.aggregate(a_pipe)}
    p_map = {str(r["_id"]): r["t"] for r in db.pagos.aggregate([
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "t": {"$sum": 1}}}])}
    m_map = {str(r["_id"]): r["a"] for r in db.miembro_membresia.aggregate([
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro",
                    "a": {"$max": {"$cond": [{"$eq": ["$estado", "Activa"]}, 1, 0]}}}}])}

    X, y = [], []
    for m in miembros:
        mid = str(m["_id"])
        a = a_map.get(mid, {})
        ultima = _parse_dt(a.get("ultima"))
        dias_sin = (ahora - ultima).days if ultima else 60
        activa = float(m_map.get(mid, 0))
        reg = _parse_dt(m.get("fecha_registro")) if m.get("fecha_registro") else None
        meses = max(1.0, (ahora - reg).days / 30.0) if reg else 1.0
        X.append([dias_sin, float(a.get("ult60", 0)), float(p_map.get(mid, 0)), meses, activa])
        y.append(1 if (dias_sin > 21 or activa == 0) else 0)
    return X, y


def _eval_clf(y_true, y_pred, labels=None):
    from sklearn.metrics import (accuracy_score, precision_score, recall_score,
                                 f1_score, confusion_matrix)
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    return {
        "accuracy":  round(float(accuracy_score(y_true, y_pred)), 3),
        "precision": round(float(precision_score(y_true, y_pred, average="weighted", zero_division=0)), 3),
        "recall":    round(float(recall_score(y_true, y_pred, average="weighted", zero_division=0)), 3),
        "f1":        round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 3),
        "confusion": cm.astype(int).tolist(),
    }


def _clasificacion_lab(db, miembros, member_oids):
    import numpy as np
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import SVC
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split

    X, y = _build_churn(db, miembros, member_oids)
    X = np.array(X, dtype=float); y = np.array(y)
    n = len(y)
    if n < 6 or len(set(y.tolist())) < 2:
        return {"error": "Se necesitan al menos 6 miembros con historial y ambas clases (en riesgo / estable) para comparar clasificadores."}

    holdout = n >= 12
    if holdout:
        try:
            X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)
        except ValueError:
            X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=42)
    else:
        X_tr, X_te, y_tr, y_te = X, X, y, y   # muestra pequena: evaluacion en los mismos datos

    sc = StandardScaler().fit(X_tr)
    X_tr_s, X_te_s = sc.transform(X_tr), sc.transform(X_te)

    defs = [
        ("Arbol de Decision", DecisionTreeClassifier(max_depth=4, random_state=42), False),
        ("Random Forest", RandomForestClassifier(n_estimators=60, max_depth=5, random_state=42, class_weight="balanced"), False),
        ("Regresion Logistica", LogisticRegression(max_iter=1000, class_weight="balanced"), True),
        ("SVM Lineal", SVC(kernel="linear", class_weight="balanced"), True),
        ("SVM No Lineal (RBF)", SVC(kernel="rbf", class_weight="balanced"), True),
    ]
    modelos = []
    for nombre, clf, escala in defs:
        try:
            xt, xe = (X_tr_s, X_te_s) if escala else (X_tr, X_te)
            clf.fit(xt, y_tr)
            met = _eval_clf(y_te, clf.predict(xe), labels=[0, 1])
            modelos.append({"nombre": nombre, "metricas": met})
        except Exception as e:
            modelos.append({"nombre": nombre, "error": str(e)})

    # Reglas del arbol (interpretables) — hasta cierto detalle
    reglas = []
    try:
        from sklearn.tree import export_text
        arbol = DecisionTreeClassifier(max_depth=3, random_state=42).fit(X, y)
        nombres = ["dias sin venir", "visitas 60d", "pagos", "meses activo", "membresia activa"]
        txt = export_text(arbol, feature_names=nombres, max_depth=3)
        reglas = [ln for ln in txt.split("\n") if ln.strip()][:14]
    except Exception:
        reglas = []

    return {
        "n": n,
        "objetivo": "Riesgo de abandono",
        "clases": ["Estable", "En riesgo"],
        "holdout": holdout,
        "modelos": modelos,
        "reglas_arbol": reglas,
    }


# ── Clasificacion multiple (objetivo del miembro) ─────────────────────────────

def _norm_objetivo(raw):
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "perder" in s or "baj" in s or "perdida" in s or "quemar" in s:
        return "Perder peso"
    if "gan" in s or "muscul" in s or "aument" in s or "hipertrof" in s:
        return "Ganar musculo"
    if "manten" in s or "toni" in s or "resist" in s or "flex" in s:
        return "Mantener"
    return "Mantener"


def _multiclase_lab(db, miembros, member_oids):
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split

    # Ultimo progreso por miembro para features de composicion
    prog = {str(r["_id"]): r for r in db.progreso_fisico.aggregate([
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$sort": {"fecha_registro": -1}},
        {"$group": {"_id": "$id_miembro",
                    "imc": {"$first": "$imc"}, "grasa": {"$first": "$grasa_corporal"},
                    "musculo": {"$first": "$masa_muscular"}, "peso": {"$first": "$peso"}}}])}

    X, y = [], []
    for m in miembros:
        obj = _norm_objetivo(m.get("objetivo"))
        if obj is None:
            continue
        mid = str(m["_id"]); p = prog.get(mid, {})
        try:
            imc = float(p.get("imc") or 0) or float(m.get("peso_inicial") or 70) / (float(m.get("estatura") or 1.7) ** 2)
        except (TypeError, ValueError, ZeroDivisionError):
            imc = 25.0
        grasa = float(p.get("grasa") or 20.0)
        musc  = float(p.get("musculo") or 30.0)
        peso  = float(p.get("peso") or m.get("peso_inicial") or 70)
        X.append([imc, grasa, musc, peso]); y.append(obj)

    clases = sorted(set(y))
    if len(X) < 6 or len(clases) < 2:
        return {"error": "Se necesitan al menos 6 miembros con objetivo definido y 2 o mas objetivos distintos."}

    import numpy as np
    Xa = np.array(X, dtype=float)
    holdout = len(X) >= 12
    if holdout:
        try:
            X_tr, X_te, y_tr, y_te = train_test_split(Xa, y, test_size=0.25, random_state=42, stratify=y)
        except ValueError:
            X_tr, X_te, y_tr, y_te = train_test_split(Xa, y, test_size=0.25, random_state=42)
    else:
        X_tr, X_te, y_tr, y_te = Xa, Xa, y, y

    clf = RandomForestClassifier(n_estimators=60, max_depth=5, random_state=42, class_weight="balanced").fit(X_tr, y_tr)
    met = _eval_clf(y_te, clf.predict(X_te), labels=clases)
    return {
        "n": len(X),
        "objetivo": "Objetivo del miembro (segun su composicion corporal)",
        "clases": clases,
        "holdout": holdout,
        "modelos": [{"nombre": "Random Forest (multiclase)", "metricas": met}],
    }


# ── Orquestacion ──────────────────────────────────────────────────────────────

def _ejecutar_modelos(gym_id):
    db = get_mongo_db()
    miembros = _gym_members(db, gym_id)
    if not miembros:
        return {"error": "Aun no hay miembros registrados en este gimnasio."}
    member_oids = [m["_id"] for m in miembros]

    return {
        "regresion":     _regresion_lab(db, member_oids),
        "clasificacion": _clasificacion_lab(db, miembros, member_oids),
        "multiclase":    _multiclase_lab(db, miembros, member_oids),
        "ejecutado_en":  datetime.now().isoformat(),
    }


@spark_modelos_bp.route("/api/analytics/modelos", methods=["GET"])
@jwt_required()
def modelos_analytics():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        key = _cache_key(gym_id)
        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200
        payload = _ejecutar_modelos(gym_id)
        payload["desde_cache"] = False
        if "error" not in payload:
            cache_set(key, payload)
        return jsonify(payload), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_modelos_bp.route("/api/analytics/modelos/train", methods=["POST"])
@jwt_required()
def modelos_train():
    try:
        gym_id = get_jwt().get("id_gimnasio")
        payload = _ejecutar_modelos(gym_id)
        payload["desde_cache"] = False
        if "error" not in payload:
            cache_set(_cache_key(gym_id), payload)
        return jsonify({**payload, "reentrenado": True}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
