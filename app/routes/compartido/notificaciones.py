"""
routes/compartido/notificaciones.py

Blueprint compartido para notificaciones in-app.
Colección MongoDB: notificaciones

Schema de cada documento:
  {
    _id:              ObjectId,
    id_usuario_pg:    int,          # destinatario
    id_gimnasio_pg:   int,
    tipo:             str,          # "cita_nueva" | "cita_cancelada" | "membresia" | ...
    titulo:           str,
    mensaje:          str,
    leida:            bool,
    creado_en:        datetime,
    referencia_tipo:  str,          # "cita" | "pago" | ...
    referencia_id:    str,          # ObjectId como string
  }

Endpoints (todos requieren JWT):
  GET  /api/notificaciones                — lista no leídas del usuario actual (limit 30)
  PATCH /api/notificaciones/leer-todas   — marca todas como leídas
  PATCH /api/notificaciones/<id>/leer    — marca una como leída
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from datetime import datetime, timezone
from bson import ObjectId

from app.mongo import get_db

notificaciones_bp = Blueprint("notificaciones", __name__, url_prefix="/api/notificaciones")


def _serialize(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# GET  /api/notificaciones
# ─────────────────────────────────────────────────────────────────────────────

@notificaciones_bp.route("", methods=["GET"])
@jwt_required()
def get_notificaciones():
    """
    Devuelve las notificaciones del usuario autenticado.
    Query params:
      solo_no_leidas=true|false  (default: false — devuelve todas)
      limit=N                    (default: 30, max: 100)
    """
    claims       = get_jwt()
    id_usuario   = claims.get("id")            # id numérico del usuario PG
    id_gimnasio  = claims.get("id_gimnasio")   # tenant

    if not id_usuario:
        # Fallback: intentar parsear sub como int
        try:
            id_usuario = int(get_jwt_identity())
        except (TypeError, ValueError):
            return jsonify({"error": "No se pudo identificar al usuario"}), 401

    solo_no_leidas = request.args.get("solo_no_leidas", "false").lower() == "true"
    limit          = min(int(request.args.get("limit", 30)), 100)

    db    = get_db()
    query = {"id_usuario_pg": int(id_usuario)}
    if id_gimnasio:
        query["id_gimnasio_pg"] = int(id_gimnasio)
    if solo_no_leidas:
        query["leida"] = False

    docs = list(
        db.notificaciones
        .find(query)
        .sort("creado_en", -1)
        .limit(limit)
    )

    no_leidas = db.notificaciones.count_documents({
        "id_usuario_pg":  int(id_usuario),
        "leida":          False,
        **({"id_gimnasio_pg": int(id_gimnasio)} if id_gimnasio else {}),
    })

    return jsonify({
        "notificaciones": [_serialize(d) for d in docs],
        "no_leidas":      no_leidas,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/notificaciones/leer-todas
# ─────────────────────────────────────────────────────────────────────────────

@notificaciones_bp.route("/leer-todas", methods=["PATCH"])
@jwt_required()
def leer_todas():
    claims      = get_jwt()
    id_usuario  = claims.get("id")
    id_gimnasio = claims.get("id_gimnasio")

    if not id_usuario:
        try:
            id_usuario = int(get_jwt_identity())
        except (TypeError, ValueError):
            return jsonify({"error": "No se pudo identificar al usuario"}), 401

    db    = get_db()
    query = {"id_usuario_pg": int(id_usuario), "leida": False}
    if id_gimnasio:
        query["id_gimnasio_pg"] = int(id_gimnasio)

    result = db.notificaciones.update_many(query, {"$set": {"leida": True}})
    return jsonify({"marcadas": result.modified_count}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/notificaciones/<id>/leer
# ─────────────────────────────────────────────────────────────────────────────

@notificaciones_bp.route("/<notif_id>/leer", methods=["PATCH"])
@jwt_required()
def leer_una(notif_id: str):
    claims     = get_jwt()
    id_usuario = claims.get("id")

    if not id_usuario:
        try:
            id_usuario = int(get_jwt_identity())
        except (TypeError, ValueError):
            return jsonify({"error": "No se pudo identificar al usuario"}), 401

    try:
        oid = ObjectId(notif_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    db = get_db()
    db.notificaciones.update_one(
        {"_id": oid, "id_usuario_pg": int(id_usuario)},
        {"$set": {"leida": True}},
    )
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Helper: crear notificación (para uso interno desde otros módulos)
# ─────────────────────────────────────────────────────────────────────────────

def crear_notificacion(
    db,
    id_usuario_pg: int,
    id_gimnasio_pg: int,
    tipo: str,
    titulo: str,
    mensaje: str,
    referencia_tipo: str = None,
    referencia_id: str = None,
) -> str:
    """
    Inserta una notificación en la colección y retorna el ID insertado como string.
    Pensado para llamarse desde otros blueprints (ej: create_cita).
    """
    doc = {
        "id_usuario_pg":   id_usuario_pg,
        "id_gimnasio_pg":  id_gimnasio_pg,
        "tipo":            tipo,
        "titulo":          titulo,
        "mensaje":         mensaje,
        "leida":           False,
        "creado_en":       datetime.now(timezone.utc),
        "referencia_tipo": referencia_tipo,
        "referencia_id":   referencia_id,
    }
    result = db.notificaciones.insert_one(doc)
    return str(result.inserted_id)
