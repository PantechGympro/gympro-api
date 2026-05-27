"""
user_nutrition.py
──────────────────────────────────────────────────────
Rutas de Plan Alimenticio y Recetas para miembros.

Colecciones MongoDB usadas:
  • dietas         – planes (asignados x entrenador | creados x miembro)
  • recetas        – recetario compartido del gimnasio
  • consumo_recetas – registro de lo que el miembro ha consumido
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from bson.objectid import ObjectId

from app.mongo import get_db
from app.utils.tenant import require_tenant

user_nutrition_bp = Blueprint("user_nutrition", __name__)


# ─── helpers ────────────────────────────────────────────────────────
def _oid(s):
    try:
        return ObjectId(s)
    except Exception:
        return None

def _ser(doc):
    """Serializa ObjectId → str y datetime → ISO string."""
    if doc is None:
        return None
    doc = dict(doc)
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            doc[k] = str(v)
        elif isinstance(v, datetime):
            doc[k] = v.isoformat()
        elif isinstance(v, list):
            doc[k] = [_ser(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i) for i in v]
        elif isinstance(v, dict):
            doc[k] = _ser(v)
    return doc


# ══════════════════════════════════════════════════════════════════
# DIETAS
# ══════════════════════════════════════════════════════════════════

@user_nutrition_bp.route("/api/user/nutrition/dietas", methods=["GET"])
@jwt_required()
@require_tenant
def list_dietas():
    """Devuelve todas las dietas del miembro: asignadas + propias."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        dietas = list(mdb.dietas.find({
            "id_miembro": miembro["_id"],
            "$or": [{"eliminada": {"$exists": False}}, {"eliminada": False}]
        }).sort("fecha_creacion", -1))

        return jsonify({"dietas": [_ser(d) for d in dietas]}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/dietas", methods=["POST"])
@jwt_required()
@require_tenant
def create_dieta():
    """Crea una dieta propia del miembro."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.json or {}

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        nueva = {
            "id_miembro":     miembro["_id"],
            "id_gimnasio_pg": gym_id,
            "nombre":         data.get("nombre", "Mi Dieta"),
            "descripcion":    data.get("descripcion", ""),
            "tipo":           "propia",
            "creado_por":     "miembro",
            "id_creador_pg":  user_pg_id,
            "comidas":        data.get("comidas", []),
            "calorias_meta":  data.get("calorias_meta"),
            "activa":         True,
            "fecha_creacion": datetime.utcnow(),
        }
        r = mdb.dietas.insert_one(nueva)
        nueva["_id"] = r.inserted_id
        return jsonify({"dieta": _ser(nueva)}), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/dietas/<dieta_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def update_dieta(dieta_id):
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.json or {}
        oid        = _oid(dieta_id)
        if not oid:
            return jsonify({"error": "ID inválido"}), 400

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        dieta   = mdb.dietas.find_one({"_id": oid, "id_miembro": miembro["_id"]})
        if not dieta:
            return jsonify({"error": "Dieta no encontrada"}), 404
        if dieta.get("creado_por") != "miembro":
            return jsonify({"error": "No puedes editar una dieta asignada por el entrenador"}), 403

        update = {}
        for f in ["nombre", "descripcion", "comidas", "calorias_meta"]:
            if f in data:
                update[f] = data[f]
        update["fecha_modificacion"] = datetime.utcnow()
        mdb.dietas.update_one({"_id": oid}, {"$set": update})
        return jsonify({"ok": True}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/dietas/<dieta_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def delete_dieta(dieta_id):
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        oid        = _oid(dieta_id)
        if not oid:
            return jsonify({"error": "ID inválido"}), 400

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        dieta   = mdb.dietas.find_one({"_id": oid, "id_miembro": miembro["_id"]})
        if not dieta or dieta.get("creado_por") != "miembro":
            return jsonify({"error": "No permitido"}), 403

        mdb.dietas.update_one({"_id": oid}, {"$set": {"eliminada": True}})
        return jsonify({"ok": True}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════
# RECETAS
# ══════════════════════════════════════════════════════════════════

@user_nutrition_bp.route("/api/user/nutrition/recetas", methods=["GET"])
@jwt_required()
@require_tenant
def list_recetas():
    """
    Devuelve recetas del gimnasio (públicas o propias del miembro/entrenador).
    Incluye: consumo del miembro en el día de hoy.
    """
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        recetas = list(mdb.recetas.find({
            "id_gimnasio_pg": gym_id,
            "$or": [{"eliminada": {"$exists": False}}, {"eliminada": False}]
        }).sort("fecha_creacion", -1))

        # Consumos de hoy
        hoy = datetime.utcnow().date()
        consumos_hoy = set(
            str(c["id_receta"])
            for c in mdb.consumo_recetas.find({
                "id_miembro": miembro["_id"],
                "fecha": {"$gte": datetime(hoy.year, hoy.month, hoy.day)}
            })
        )

        resultado = []
        for r in recetas:
            sr = _ser(r)
            sr["consumida_hoy"] = sr["_id"] in consumos_hoy
            resultado.append(sr)

        return jsonify({"recetas": resultado}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/recetas", methods=["POST"])
@jwt_required()
@require_tenant
def create_receta():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        data       = request.json or {}

        nueva = {
            "id_gimnasio_pg":  gym_id,
            "id_creador_pg":   user_pg_id,
            "creado_por_rol":  "miembro",
            "titulo":          data.get("titulo", "Mi Receta"),
            "descripcion":     data.get("descripcion", ""),
            "categoria":       data.get("categoria", "General"),
            "tiempo_prep":     int(data.get("tiempo_prep") or 0),
            "porciones":       int(data.get("porciones") or 1),
            "calorias":        int(data.get("calorias") or 0),
            "proteina":        float(data.get("proteina") or 0),
            "carbos":          float(data.get("carbos") or 0),
            "grasa":           float(data.get("grasa") or 0),
            "ingredientes":    data.get("ingredientes", []),
            "pasos":           data.get("pasos", []),
            "fecha_creacion":  datetime.utcnow(),
        }
        r = mdb.recetas.insert_one(nueva)
        nueva["_id"] = r.inserted_id
        return jsonify({"receta": _ser(nueva)}), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/recetas/<receta_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def update_receta(receta_id):
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        oid        = _oid(receta_id)
        if not oid:
            return jsonify({"error": "ID inválido"}), 400

        receta = mdb.recetas.find_one({"_id": oid})
        if not receta:
            return jsonify({"error": "Receta no encontrada"}), 404
        if receta.get("id_creador_pg") != user_pg_id:
            return jsonify({"error": "No autorizado"}), 403

        data = request.json or {}
        update = {}
        for f in ["titulo", "descripcion", "categoria", "tiempo_prep", "porciones",
                  "calorias", "proteina", "carbos", "grasa", "ingredientes", "pasos"]:
            if f in data:
                update[f] = data[f]
        update["fecha_modificacion"] = datetime.utcnow()
        mdb.recetas.update_one({"_id": oid}, {"$set": update})
        return jsonify({"ok": True}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/recetas/<receta_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def delete_receta(receta_id):
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        oid        = _oid(receta_id)
        receta     = mdb.recetas.find_one({"_id": oid}) if oid else None
        if not receta or receta.get("id_creador_pg") != user_pg_id:
            return jsonify({"error": "No autorizado"}), 403
        mdb.recetas.update_one({"_id": oid}, {"$set": {"eliminada": True}})
        return jsonify({"ok": True}), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_nutrition_bp.route("/api/user/nutrition/recetas/<receta_id>/consumir", methods=["POST"])
@jwt_required()
@require_tenant
def consumir_receta(receta_id):
    """Registra que el miembro consumió esta receta hoy."""
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        oid        = _oid(receta_id)
        data       = request.json or {}

        miembro = mdb.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        if not miembro or not oid:
            return jsonify({"error": "No encontrado"}), 404

        mdb.consumo_recetas.insert_one({
            "id_miembro":  miembro["_id"],
            "id_receta":   oid,
            "fecha":       datetime.utcnow(),
            "comida":      data.get("comida", "Comida"),
        })
        return jsonify({"ok": True}), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
