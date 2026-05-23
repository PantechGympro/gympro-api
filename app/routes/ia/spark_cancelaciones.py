"""
spark_cancelaciones.py — Predicción de riesgo de cancelación de membresía.

Motor: scikit-learn RandomForestClassifier (en proceso, sin JVM, sin internet).
Datos: pymongo directo sobre colecciones miembros, asistencias, pagos, miembro_membresia.

Features:
  - dias_sin_asistir       : días desde la última asistencia registrada
  - num_asistencias_ult60  : asistencias en los últimos 60 días
  - total_pagos            : número de pagos históricos del miembro
  - meses_activo           : meses desde el registro del miembro
  - tiene_membresia_activa : 1.0 si la membresía está activa, 0.0 si venció

Label (target):
  - 1 = en riesgo (dias_sin_asistir > 21 O membresía vencida)
  - 0 = activo y estable
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime, timedelta, timezone

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_cancelaciones_bp = Blueprint("spark_cancelaciones", __name__)


def _cache_key(gym_id) -> str:
    return f"cancelaciones_gym{gym_id}"


# ── Lógica del modelo ─────────────────────────────────────────────────────────

def _ejecutar_cancelaciones(gym_id=None) -> dict:
    """
    Entrena RandomForestClassifier y predice riesgo de cancelación para
    todos los miembros del gimnasio.
    """
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import roc_auc_score, accuracy_score

    db   = get_mongo_db()
    ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    hace60 = ahora - timedelta(days=60)

    # 1. Miembros del gimnasio
    query_m = {}
    if gym_id is not None:
        query_m["id_gimnasio_pg"] = int(gym_id)
    miembros = list(db.miembros.find(query_m, {"_id": 1, "nombre": 1, "fecha_registro": 1}))

    if len(miembros) < 5:
        return {"error": "Datos insuficientes para entrenar el modelo",
                "total_miembros": len(miembros)}

    member_oids = [m["_id"] for m in miembros]
    member_map  = {str(m["_id"]): m for m in miembros}

    # 2. Última asistencia y conteo en últimos 60 días por miembro
    asist_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {
            "_id":              "$id_miembro",
            "ultima":           {"$max": "$fecha"},
            "ult60":            {"$sum": {"$cond": [{"$gte": ["$fecha", hace60]}, 1, 0]}},
        }},
    ]
    asist_map = {str(r["_id"]): r for r in db.asistencias.aggregate(asist_pipe)}

    # 3. Total pagos por miembro
    pagos_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "total": {"$sum": 1}}},
    ]
    pagos_map = {str(r["_id"]): r["total"] for r in db.pagos.aggregate(pagos_pipe)}

    # 4. Membresía activa por miembro
    mm_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {
            "_id":    "$id_miembro",
            "activa": {"$max": {"$cond": [{"$eq": ["$estado", "Activa"]}, 1, 0]}},
        }},
    ]
    mm_map = {str(r["_id"]): r["activa"] for r in db.miembro_membresia.aggregate(mm_pipe)}

    # 5. Construir feature matrix
    def _parse_dt(val):
        if val is None: return None
        if isinstance(val, datetime): return val.replace(tzinfo=None)
        if isinstance(val, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try: return datetime.strptime(val[:len(fmt)], fmt)
                except ValueError: continue
        return None

    rows = []
    for m in miembros:
        mid = str(m["_id"])
        asist   = asist_map.get(mid, {})
        ultima  = _parse_dt(asist.get("ultima"))
        ult60   = float(asist.get("ult60", 0))
        pagos   = float(pagos_map.get(mid, 0))
        activa  = float(mm_map.get(mid, 0))
        reg_dt  = _parse_dt(m.get("fecha_registro"))
        meses   = max(1.0, (ahora - reg_dt).days / 30.0) if reg_dt else 1.0
        dias_sin = (ahora - ultima).days if ultima else 60.0

        label = 1.0 if (dias_sin > 21 or activa == 0) else 0.0
        rows.append({
            "id_miembro":  mid,
            "nombre":      m.get("nombre", ""),
            "dias_sin_asistir": dias_sin,
            "num_asistencias_ult60": ult60,
            "total_pagos": pagos,
            "meses_activo": meses,
            "tiene_membresia_activa": activa,
            "label": label,
        })

    if len(rows) < 10:
        return {"error": "Datos insuficientes para entrenar el modelo",
                "total_miembros": len(rows)}

    feature_cols = [
        "dias_sin_asistir", "num_asistencias_ult60",
        "total_pagos", "meses_activo", "tiene_membresia_activa",
    ]
    X = np.array([[r[c] for c in feature_cols] for r in rows])
    y = np.array([r["label"] for r in rows])

    # 6. Train/test split 80/20
    rng   = np.random.default_rng(42)
    idx   = rng.permutation(len(X))
    split = max(1, int(len(X) * 0.8))
    X_tr, X_te = X[idx[:split]], X[idx[split:]]
    y_tr, y_te = y[idx[:split]], y[idx[split:]]

    # 7. Random Forest
    rf = RandomForestClassifier(
        n_estimators=50, max_depth=5, random_state=42, class_weight="balanced"
    ).fit(X_tr, y_tr)

    # 8. Evaluación
    y_pred = rf.predict(X_te) if len(X_te) > 0 else rf.predict(X_tr)
    y_ref  = y_te if len(X_te) > 0 else y_tr
    y_prob = rf.predict_proba(X_te)[:, 1] if len(X_te) > 0 else rf.predict_proba(X_tr)[:, 1]

    acc = round(float(accuracy_score(y_ref, y_pred)), 4)
    try:
        auc = round(float(roc_auc_score(y_ref, y_prob)), 4)
    except Exception:
        auc = 0.0

    # 9. Predicción sobre todos los miembros
    probs_all = rf.predict_proba(X)[:, 1]
    predicciones = []
    for i, r in enumerate(rows):
        prob   = float(probs_all[i])
        riesgo = "alto" if prob >= 0.65 else "medio" if prob >= 0.35 else "bajo"
        predicciones.append({
            "id_miembro":        r["id_miembro"],
            "nombre":            r["nombre"],
            "dias_sin_asistir":  int(r["dias_sin_asistir"]),
            "membresia_activa":  bool(r["tiene_membresia_activa"]),
            "probabilidad":      round(prob, 4),
            "riesgo":            riesgo,
        })
    predicciones.sort(key=lambda x: x["probabilidad"], reverse=True)
    predicciones = predicciones[:200]

    # 10. Importancia de features
    importancias = sorted([
        {"feature": col, "importancia": round(float(imp), 4)}
        for col, imp in zip(feature_cols, rf.feature_importances_)
    ], key=lambda x: x["importancia"], reverse=True)

    alto   = sum(1 for p in predicciones if p["riesgo"] == "alto")
    medio  = sum(1 for p in predicciones if p["riesgo"] == "medio")
    activos = len(predicciones) - alto - medio

    return {
        "algoritmo":            "Random Forest Classifier",
        "descripcion":          "Predicción de riesgo de cancelación de membresía",
        "metricas":             {"accuracy": acc, "auc_roc": auc},
        "importancia_features": importancias,
        "predicciones":         predicciones,
        "resumen": {
            "total":        len(predicciones),
            "riesgo_alto":  alto,
            "riesgo_medio": medio,
            "activos":      activos,
        },
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

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

        payload = _ejecutar_cancelaciones(gym_id)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_cancelaciones_bp.route("/api/analytics/cancelaciones/train", methods=["POST"])
@jwt_required()
def cancelaciones_train():
    try:
        gym_id  = get_jwt().get("id_gimnasio")
        payload = _ejecutar_cancelaciones(gym_id)
        payload["desde_cache"] = False
        cache_set(_cache_key(gym_id), payload)
        return jsonify({**payload, "reentrenado": True}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
