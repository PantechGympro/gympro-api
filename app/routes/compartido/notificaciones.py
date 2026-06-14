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

# Endpoint del servicio de push de Expo (no requiere credenciales para envíos básicos).
_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _resolver_usuario(claims) -> int | None:
    """Obtiene el id de usuario PG desde los claims o el subject del JWT."""
    id_usuario = claims.get("id")
    if id_usuario:
        return int(id_usuario)
    try:
        return int(get_jwt_identity())
    except (TypeError, ValueError):
        return None


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
# POST /api/notificaciones/push-token   — registrar Expo push token
# ─────────────────────────────────────────────────────────────────────────────

@notificaciones_bp.route("/push-token", methods=["POST"])
@jwt_required()
def registrar_push_token():
    """
    Registra (upsert) el Expo push token del dispositivo del usuario.
    Body: { token: "ExponentPushToken[...]", platform: "ios"|"android" }
    Colección: push_tokens  (deduplicada por token).
    """
    id_usuario = _resolver_usuario(get_jwt())
    if id_usuario is None:
        return jsonify({"error": "No se pudo identificar al usuario"}), 401

    data  = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "token requerido"}), 400

    id_gimnasio = get_jwt().get("id_gimnasio")
    db = get_db()
    db.push_tokens.update_one(
        {"token": token},
        {"$set": {
            "id_usuario_pg":  id_usuario,
            "id_gimnasio_pg": int(id_gimnasio) if id_gimnasio else None,
            "token":          token,
            "platform":       data.get("platform"),
            "actualizado_en": datetime.now(timezone.utc),
        }},
        upsert=True,
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

    # Disparar push (best-effort, no bloquea la creación de la notificación in-app).
    _enviar_push(db, id_usuario_pg, titulo, mensaje, data={
        "tipo":          tipo,
        "referencia_id": referencia_id,
    })

    return str(result.inserted_id)


def _enviar_push(db, id_usuario_pg: int, titulo: str, mensaje: str, data: dict | None = None) -> None:
    """
    Envía una notificación push a todos los dispositivos del usuario vía Expo.
    Silencioso ante cualquier fallo: el push es complementario a la notif. in-app.
    """
    try:
        tokens = [
            t["token"]
            for t in db.push_tokens.find({"id_usuario_pg": int(id_usuario_pg)}, {"token": 1})
            if t.get("token")
        ]
        if not tokens:
            return

        mensajes = [
            {
                "to":    tok,
                "title": titulo,
                "body":  mensaje,
                "sound": "default",
                "data":  data or {},
            }
            for tok in tokens
        ]

        try:
            import requests  # noqa: PLC0415
            requests.post(_EXPO_PUSH_URL, json=mensajes, timeout=5)
        except ImportError:
            # Fallback sin la librería requests
            import json as _json
            import urllib.request as _urlreq  # noqa: PLC0415
            req = _urlreq.Request(
                _EXPO_PUSH_URL,
                data=_json.dumps(mensajes).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            _urlreq.urlopen(req, timeout=5)
    except Exception as ex:
        print(f"[push] No-bloqueante: {ex}")
