"""
spark_rutinas.py — Recomendaciones de rutina personalizadas.

Motor: scikit-learn NearestNeighbors (filtrado colaborativo, en proceso, sin JVM).

Algoritmo:
  1. Representar cada miembro como vector: IMC, objetivo_encoded, total_asistencias.
  2. StandardScaler + NearestNeighbors (cosine distance).
  3. Para el miembro consultado, encontrar los N vecinos más similares.
  4. Devolver las rutinas más frecuentes entre esos vecinos.

Endpoints:
  GET /api/analytics/rutinas/recomendaciones?id_miembro=<hex>
  GET /api/analytics/rutinas/populares
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt
from datetime import datetime

from app.routes.ia.spark_config import cache_get, cache_set, get_mongo_db

spark_rutinas_bp = Blueprint("spark_rutinas", __name__)

_OBJETIVO_MAP = {
    "Perdida de peso":           0,
    "Ganancia muscular":         1,
    "Definicion":                2,
    "Resistencia":               3,
    "Rehabilitacion":            4,
    "Acondicionamiento general": 5,
    "Fuerza maxima":             6,
}


def _cache_key_pop(gym_id):
    return f"rutinas_populares_gym{gym_id}"


def _cache_key_rec(id_miembro):
    return f"rutinas_rec_{id_miembro}"


# ── Rutinas populares del gimnasio (sin ML, solo aggregation) ─────────────────

def _rutinas_populares(gym_id=None) -> list:
    db    = get_mongo_db()
    match = {} if gym_id is None else {"id_gimnasio": int(gym_id)}
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id":       "$nombre",
            "veces":     {"$sum": 1},
            "categoria": {"$first": "$categoria"},
            "dificultad":{"$first": "$dificultad"},
            "duracion":  {"$first": "$duracion_minutos"},
        }},
        {"$sort": {"veces": -1}},
        {"$limit": 10},
    ]
    return list(db.rutinas.aggregate(pipeline))


# ── Recomendaciones por similitud de perfil ───────────────────────────────────

def _recomendaciones_para_miembro(id_miembro_hex: str, gym_id=None, top_n: int = 5) -> dict:
    import numpy as np
    from sklearn.preprocessing import StandardScaler
    from sklearn.neighbors import NearestNeighbors
    from bson import ObjectId

    db = get_mongo_db()

    # Validar miembro
    try:
        oid = ObjectId(id_miembro_hex)
    except Exception:
        return {"error": "id_miembro inválido"}

    miembro_doc = db.miembros.find_one(
        {"_id": oid},
        {"nombre": 1, "objetivo": 1, "peso_inicial": 1, "estatura": 1, "id_gimnasio_pg": 1}
    )
    if not miembro_doc:
        return {"error": "Miembro no encontrado"}

    # Cargar todos los miembros del gimnasio para construir el espacio de similitud
    query_m = {}
    if gym_id is not None:
        query_m["id_gimnasio_pg"] = int(gym_id)
    miembros = list(db.miembros.find(query_m, {
        "_id": 1, "objetivo": 1, "peso_inicial": 1, "estatura": 1,
    }))

    # Total asistencias por miembro
    member_oids = [m["_id"] for m in miembros]
    asist_pipe = [
        {"$match": {"id_miembro": {"$in": member_oids}}},
        {"$group": {"_id": "$id_miembro", "total": {"$sum": 1}}},
    ]
    asist_map = {str(r["_id"]): float(r["total"]) for r in db.asistencias.aggregate(asist_pipe)}

    # Construir feature matrix
    records = []
    for m in miembros:
        try:
            peso     = float(m.get("peso_inicial") or 70.0)
            estatura = float(m.get("estatura") or 1.70)
            if estatura <= 0: estatura = 1.70
        except (TypeError, ValueError):
            continue
        imc     = peso / (estatura ** 2)
        obj_enc = float(_OBJETIVO_MAP.get(m.get("objetivo", ""), 0))
        asist   = asist_map.get(str(m["_id"]), 0.0)
        records.append({
            "id": str(m["_id"]),
            "imc": imc, "objetivo_enc": obj_enc, "asistencias": asist,
        })

    if len(records) < 2:
        # Fallback: rutinas populares
        populares = _rutinas_populares(gym_id)
        return {
            "id_miembro": id_miembro_hex,
            "nombre":     miembro_doc.get("nombre", ""),
            "modo":       "popular_fallback",
            "recomendaciones": [
                {"nombre": r["_id"], "categoria": r.get("categoria", ""), "veces": r["veces"]}
                for r in populares[:top_n]
            ],
        }

    X = np.array([[r["imc"], r["objetivo_enc"], r["asistencias"]] for r in records])
    ids = [r["id"] for r in records]

    # Escalar + NearestNeighbors con distancia coseno
    scaler = StandardScaler()
    X_sc   = scaler.fit_transform(X)

    target_idx = next((i for i, r in enumerate(records) if r["id"] == id_miembro_hex), None)
    if target_idx is None:
        # Miembro no tiene datos suficientes — fallback a populares
        populares = _rutinas_populares(gym_id)
        return {
            "id_miembro": id_miembro_hex,
            "nombre":     miembro_doc.get("nombre", ""),
            "modo":       "popular_fallback",
            "recomendaciones": [
                {"nombre": r["_id"], "categoria": r.get("categoria", ""), "veces": r["veces"]}
                for r in populares[:top_n]
            ],
        }

    n_neighbors = min(16, len(records))
    nn = NearestNeighbors(n_neighbors=n_neighbors, metric="cosine", algorithm="brute")
    nn.fit(X_sc)
    _, indices = nn.kneighbors(X_sc[target_idx].reshape(1, -1))

    # Excluir el propio miembro y tomar hasta 15 vecinos
    vecinos_ids_hex = [ids[i] for i in indices[0] if ids[i] != id_miembro_hex][:15]

    # Rutinas más frecuentes entre esos vecinos
    from bson import ObjectId as OID
    try:
        oids_vecinos = [OID(v) for v in vecinos_ids_hex]
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

    # Si no hay rutinas de los vecinos, usar las populares del gym
    if not rutinas_rec:
        rutinas_rec = _rutinas_populares(gym_id)[:top_n]
        modo = "popular_fallback"
    else:
        modo = "collaborative_filtering"

    return {
        "id_miembro":      id_miembro_hex,
        "nombre":          miembro_doc.get("nombre", ""),
        "objetivo":        miembro_doc.get("objetivo", ""),
        "modo":            modo,
        "vecinos_usados":  len(vecinos_ids_hex),
        "recomendaciones": [
            {
                "nombre":     r["_id"],
                "categoria":  r.get("categoria", ""),
                "dificultad": r.get("dificultad", ""),
                "duracion":   r.get("duracion", ""),
                "frecuencia": r["veces"],
            }
            for r in rutinas_rec
        ],
        "ejecutado_en": datetime.now().isoformat(),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

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
            {"nombre": r["_id"], "categoria": r.get("categoria", ""),
             "dificultad": r.get("dificultad", ""), "veces": r["veces"]}
            for r in rutinas
        ]
        cache_set(key, result)
        return jsonify({"desde_cache": False, "rutinas": result}), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@spark_rutinas_bp.route("/api/analytics/rutinas/recomendaciones", methods=["GET"])
@jwt_required()
def rutinas_recomendaciones():
    id_miembro = request.args.get("id_miembro", "").strip()
    if not id_miembro:
        return jsonify({"error": "Parámetro 'id_miembro' requerido"}), 400

    try:
        gym_id = get_jwt().get("id_gimnasio")
        key    = _cache_key_rec(id_miembro)

        cached = cache_get(key)
        if cached:
            return jsonify({**cached, "desde_cache": True}), 200

        result = _recomendaciones_para_miembro(id_miembro, gym_id)
        if "error" not in result:
            cache_set(key, result)
        result["desde_cache"] = False
        return jsonify(result), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
