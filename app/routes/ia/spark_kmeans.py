"""
spark_kmeans.py — Clustering K-Means de miembros por composición corporal.

Motor: scikit-learn (en proceso, sin JVM, sin internet).
Datos: pymongo directo sobre colecciones miembros y progreso_fisico.

Caché por (gym_id, k) con TTL configurable (ANALYTICS_CACHE_TTL_HOURS).
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import datetime

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_kmeans_bp = Blueprint("spark_kmeans", __name__)


def _trainer_scope():
    """
    Si el usuario autenticado es Entrenador, devuelve su id para acotar el
    análisis SOLO a los miembros que él entrena (id_entrenador_pg). Para
    owner_gym / superadmin devuelve None (análisis a nivel de gimnasio).
    """
    if get_jwt().get("role") == "Entrenador":
        try:
            return int(get_jwt_identity())
        except (TypeError, ValueError):
            return None
    return None

_LABEL_POOL = [
    "Principiante / Alta Prioridad",
    "Intermedio / Mantenimiento",
    "Avanzado / Optimización",
    "Elite / Rendimiento",
    "Senior / Bajo Impacto",
    "Recuperación Activa",
    "Alto Volumen / Hipertrofia",
    "Definición / Corte",
]


def _cache_key(gym_id, k: int, trainer_id=None) -> str:
    scope = f"_t{trainer_id}" if trainer_id is not None else ""
    return f"kmeans_gym{gym_id}{scope}_k{k}"


# ── Lógica K-Means ────────────────────────────────────────────────────────────

def _ejecutar_kmeans(k: int = 3, max_iter: int = 300, seed: int = 42, gym_id=None, trainer_id=None):
    """
    K-Means con StandardScaler sobre features de composición corporal.
    Retorna: (resumen_clusters, asignaciones, centroides, silhouette)
    """
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import silhouette_score

    db = get_mongo_db()

    # 1. Cargar miembros del gimnasio (o solo los del entrenador, si aplica)
    query_m = {}
    if gym_id is not None:
        query_m["id_gimnasio_pg"] = int(gym_id)
    if trainer_id is not None:
        query_m["id_entrenador_pg"] = int(trainer_id)
    query_m["peso_inicial"] = {"$ne": None}
    query_m["estatura"]     = {"$ne": None}

    miembros = list(db.miembros.find(query_m, {
        "_id": 1, "nombre": 1, "id_usuario_pg": 1,
        "peso_inicial": 1, "estatura": 1, "sexo": 1,
    }))
    if not miembros:
        raise ValueError("No hay miembros con datos suficientes para clustering.")

    # 2. Cargar último registro de progreso por miembro (para peso/imc/grasa/musculo actuales)
    member_oids = [m["_id"] for m in miembros]
    pipeline = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$sort": {"fecha_registro": -1}},
        {"$group": {
            "_id":          "$id_miembro",
            "peso":         {"$first": "$peso"},
            "bmi":          {"$first": "$imc"},
            "grasa":        {"$first": "$grasa_corporal"},
            "musculo":      {"$first": "$masa_muscular"},
        }},
    ]
    progreso_map = {str(r["_id"]): r for r in db.progreso_fisico.aggregate(pipeline)}

    # 3. Combinar features
    records = []
    for m in miembros:
        mid  = str(m["_id"])
        prog = progreso_map.get(mid, {})

        try:
            peso_i   = float(m.get("peso_inicial") or 0)
            estatura = float(m.get("estatura") or 0)
        except (TypeError, ValueError):
            continue
        if estatura <= 0:
            continue

        imc_calc = peso_i / (estatura ** 2)

        peso    = float(prog.get("peso")    or peso_i)
        imc     = float(prog.get("bmi")     or imc_calc)
        grasa   = float(prog.get("grasa")   or 20.0)
        musculo = float(prog.get("musculo") or 30.0)

        records.append({
            "id_miembro":   mid,
            "nombre":       m.get("nombre", ""),
            "id_usuario_pg": m.get("id_usuario_pg"),
            "sexo":         m.get("sexo", ""),
            "peso":         peso,
            "imc":          imc,
            "grasa":        grasa,
            "musculo":      musculo,
        })

    n = len(records)
    if n < k:
        raise ValueError(f"Datos insuficientes: {n} miembros con datos, se necesitan al menos {k}.")

    X = np.array([[r["peso"], r["imc"], r["grasa"], r["musculo"]] for r in records])

    # 4. Escalado + K-Means
    scaler  = StandardScaler()
    X_sc    = scaler.fit_transform(X)
    model   = KMeans(n_clusters=k, max_iter=max_iter, random_state=seed, n_init=10).fit(X_sc)
    labels  = model.labels_

    sil = round(float(silhouette_score(X_sc, labels)), 4) if n > k else 0.0

    # 5. Centroides en espacio escalado (para inspección técnica)
    centroides = [
        {"cluster": i,
         "peso_norm":    round(float(c[0]), 4),
         "imc_norm":     round(float(c[1]), 4),
         "grasa_norm":   round(float(c[2]), 4),
         "musculo_norm": round(float(c[3]), 4)}
        for i, c in enumerate(model.cluster_centers_)
    ]

    # 6. Resumen por cluster (valores reales, ordenado por imc promedio)
    from collections import defaultdict
    cluster_rows: dict[int, list] = defaultdict(list)
    for i, r in enumerate(records):
        cluster_rows[int(labels[i])].append(r)

    resumen_raw = []
    for cl, rows in cluster_rows.items():
        resumen_raw.append({
            "cluster":         cl,
            "num_miembros":    len(rows),
            "peso_promedio":   round(sum(r["peso"]   for r in rows) / len(rows), 2),
            "imc_promedio":    round(sum(r["imc"]    for r in rows) / len(rows), 2),
            "grasa_promedio":  round(sum(r["grasa"]  for r in rows) / len(rows), 2),
            "musculo_promedio":round(sum(r["musculo"]for r in rows) / len(rows), 2),
        })
    resumen_raw.sort(key=lambda x: x["imc_promedio"])

    asignaciones = [
        {
            "id_miembro": r["id_miembro"],
            "nombre":     r["nombre"],
            "id_usuario_pg": r["id_usuario_pg"],
            "cluster":    int(labels[i]),
            "sexo":       r["sexo"],
            "peso":       round(r["peso"],    1),
            "imc":        round(r["imc"],     2),
            "grasa":      round(r["grasa"],   1),
            "musculo":    round(r["musculo"], 1),
        }
        for i, r in enumerate(records)
    ]
    asignaciones.sort(key=lambda x: x["cluster"])

    return resumen_raw, asignaciones, centroides, sil


def _build_payload(k, max_iter, resumen, asignaciones, centroides, silhouette) -> dict:
    resumen_con_etiqueta = [
        {**row, "etiqueta": _LABEL_POOL[i] if i < len(_LABEL_POOL) else f"Grupo {i}"}
        for i, row in enumerate(resumen)
    ]
    return {
        "algoritmo":        "K-Means",
        "descripcion":      f"Clustering de miembros en {k} grupos por composición corporal",
        "parametros":       {"k": k, "max_iter": max_iter},
        "silhouette":       silhouette,
        "centroides":       centroides,
        "resumen_clusters": resumen_con_etiqueta,
        "asignaciones":     asignaciones,
        "ejecutado_en":     datetime.now().isoformat(),
    }


# ── Enriquecimiento de nombres desde PostgreSQL ───────────────────────────────

def _enrich_nombres(asignaciones: list) -> list:
    from app.models.pg.usuario import Usuario
    pg_ids = set()
    for row in asignaciones:
        uid = row.get("id_usuario_pg")
        if uid is not None:
            try: pg_ids.add(int(uid))
            except (TypeError, ValueError): pass

    if not pg_ids:
        return asignaciones

    user_map = {u.id: u.nombre for u in Usuario.query.filter(Usuario.id.in_(pg_ids)).all()}
    enriched = []
    for row in asignaciones:
        new_row = dict(row)
        uid = row.get("id_usuario_pg")
        if uid is not None:
            try:
                real = user_map.get(int(uid))
                if real: new_row["nombre"] = real
            except (TypeError, ValueError): pass
        enriched.append(new_row)
    return enriched


# ── Endpoints ─────────────────────────────────────────────────────────────────

@spark_kmeans_bp.route("/api/analytics/kmeans", methods=["GET"])
@jwt_required()
def kmeans_analytics():
    """Devuelve resultado desde caché. Si expiró, entrena y guarda."""
    try:
        k        = request.args.get("k",        3,  type=int)
        max_iter = request.args.get("max_iter", 300, type=int)
        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        gym_id     = get_jwt().get("id_gimnasio")
        trainer_id = _trainer_scope()
        key        = _cache_key(gym_id, k, trainer_id)

        cached = cache_get(key)
        if cached:
            cached["desde_cache"] = True
            return jsonify(cached), 200

        resumen, asignaciones, centroides, sil = _ejecutar_kmeans(
            k=k, max_iter=max_iter, gym_id=gym_id, trainer_id=trainer_id
        )
        asignaciones = _enrich_nombres(asignaciones)
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, sil)
        payload["desde_cache"] = False
        cache_set(key, payload)
        return jsonify(payload), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_kmeans_bp.route("/api/analytics/kmeans/train", methods=["POST"])
@jwt_required()
def kmeans_train():
    """Fuerza re-entrenamiento y actualiza caché."""
    try:
        body     = request.get_json(silent=True) or {}
        k        = int(body.get("k",        request.args.get("k",        3,  type=int)))
        max_iter = int(body.get("max_iter", request.args.get("max_iter", 300, type=int)))
        if not (2 <= k <= 8):
            return jsonify({"error": "k debe estar entre 2 y 8"}), 400

        gym_id     = get_jwt().get("id_gimnasio")
        trainer_id = _trainer_scope()
        key        = _cache_key(gym_id, k, trainer_id)

        resumen, asignaciones, centroides, sil = _ejecutar_kmeans(
            k=k, max_iter=max_iter, gym_id=gym_id, trainer_id=trainer_id
        )
        asignaciones = _enrich_nombres(asignaciones)
        payload = _build_payload(k, max_iter, resumen, asignaciones, centroides, sil)
        payload["desde_cache"] = False
        cache_set(key, payload)

        ambito = f"entrenador {trainer_id}" if trainer_id else f"gimnasio {gym_id}"
        return jsonify({**payload,
                        "mensaje": f"K-Means k={k} reentrenado para {ambito}."}), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
