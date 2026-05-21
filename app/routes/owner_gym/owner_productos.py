"""
owner_productos.py — CRUD de productos del POS por gimnasio.

Endpoints bajo /api/owner_gym/ (registrado con url_prefix en __init__.py):
  GET    /productos           — listar catálogo del gimnasio
  POST   /productos           — crear producto (con hasta 3 imágenes base64)
  PUT    /productos/<id>      — editar producto
  PATCH  /productos/<id>/toggle — activar / desactivar
  DELETE /productos/<id>      — eliminar producto

Las imágenes se almacenan como strings base64 en el array "imagenes" del
documento MongoDB (máx. 3).  El cliente las envía ya codificadas en base64.
"""
from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required
from bson import ObjectId
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

from app.mongo import get_db
from app.utils.tenant import require_tenant

LOW_STOCK_THRESHOLD = 5  # umbral global de "stock bajo"

owner_productos_bp = Blueprint("owner_productos", __name__)


def _serialize(p):
    return {
        "id":          str(p["_id"]),
        "nombre":      p.get("nombre", ""),
        "precio":      p.get("precio", 0),
        "stock":       p.get("stock", 0),
        "categoria":   p.get("categoria", "General"),
        "descripcion": p.get("descripcion", ""),
        "imagenes":    p.get("imagenes", []),
        "activo":      p.get("activo", True),
    }


# ─── GET /alertas ────────────────────────────────────────────────────────────

@owner_productos_bp.route("/alertas", methods=["GET"])
@jwt_required()
@require_tenant
def get_alertas():
    """
    Devuelve alertas operativas del gimnasio:
      · Productos sin stock (nivel: error)
      · Productos con stock bajo ≤ LOW_STOCK_THRESHOLD (nivel: warning)
      · Membresías por vencer próximos 7 días (nivel: warning)
      · Membresías ya vencidas con estado activo (nivel: error)
    """
    db      = get_db()
    gym_id  = g.tenant_id
    alertas = []

    # ── Productos sin stock ───────────────────────────────────────────────────
    sin_stock = list(db.productos.find(
        {"id_gimnasio": gym_id, "activo": True, "stock": 0}
    ))
    for p in sin_stock:
        alertas.append({
            "nivel":   "error",
            "tipo":    "sin_stock",
            "titulo":  f"{p['nombre']} — Sin stock",
            "detalle": "El producto está agotado. Reabastecer urgente.",
            "icono":   "📦",
        })

    # ── Productos con stock bajo ──────────────────────────────────────────────
    bajo_stock = list(db.productos.find({
        "id_gimnasio": gym_id,
        "activo": True,
        "stock": {"$gt": 0, "$lte": LOW_STOCK_THRESHOLD},
    }))
    for p in bajo_stock:
        alertas.append({
            "nivel":   "warning",
            "tipo":    "stock_bajo",
            "titulo":  f"{p['nombre']} — Stock bajo",
            "detalle": f"Quedan {p['stock']} unidad(es). Considera reabastecer.",
            "icono":   "⚠️",
        })

    # ── Membresías por vencer (próximos 7 días) ───────────────────────────────
    now    = datetime.now(timezone.utc)
    hoy    = now.date().isoformat()
    en_7   = (now + relativedelta(days=7)).date().isoformat()
    por_vencer = db.miembro_membresias.count_documents({
        "id_gimnasio": gym_id,
        "estado": "activa",
        "fecha_fin": {"$gte": hoy, "$lte": en_7},
    })
    if por_vencer > 0:
        alertas.append({
            "nivel":   "warning",
            "tipo":    "membresias_por_vencer",
            "titulo":  f"{por_vencer} membresía(s) vencen esta semana",
            "detalle": "Próximos 7 días. Notifica a los miembros para renovar.",
            "icono":   "🗓️",
        })

    # ── Membresías vencidas con estado aún "activa" ───────────────────────────
    vencidas = db.miembro_membresias.count_documents({
        "id_gimnasio": gym_id,
        "estado": "activa",
        "fecha_fin": {"$lt": hoy},
    })
    if vencidas > 0:
        alertas.append({
            "nivel":   "error",
            "tipo":    "membresias_vencidas",
            "titulo":  f"{vencidas} membresía(s) vencida(s) sin actualizar",
            "detalle": "Revisar y marcar como vencidas o renovar.",
            "icono":   "❌",
        })

    return jsonify({"alertas": alertas, "total": len(alertas)}), 200


# ─── GET /productos ───────────────────────────────────────────────────────────

@owner_productos_bp.route("/productos", methods=["GET"])
@jwt_required()
@require_tenant
def listar_productos():
    db     = get_db()
    solo_activos = request.args.get("activos", "false").lower() == "true"
    filtro = {"id_gimnasio": g.tenant_id}
    if solo_activos:
        filtro["activo"] = True

    cursor    = db.productos.find(filtro).sort("nombre", 1)
    productos = [_serialize(p) for p in cursor]
    return jsonify({"productos": productos, "total": len(productos)}), 200


# ─── POST /productos ──────────────────────────────────────────────────────────

@owner_productos_bp.route("/productos", methods=["POST"])
@jwt_required()
@require_tenant
def crear_producto():
    db   = get_db()
    data = request.get_json() or {}

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "El nombre es obligatorio"}), 400

    try:
        precio = float(data.get("precio", 0))
        if precio < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Precio inválido"}), 400

    imagenes = data.get("imagenes", [])
    if not isinstance(imagenes, list):
        imagenes = []
    imagenes = imagenes[:3]          # máximo 3

    doc = {
        "id_gimnasio": g.tenant_id,
        "nombre":      nombre,
        "precio":      precio,
        "stock":       max(0, int(data.get("stock", 0))),
        "categoria":   (data.get("categoria") or "General").strip(),
        "descripcion": (data.get("descripcion") or "").strip(),
        "imagenes":    imagenes,
        "activo":      True,
        "created_at":  datetime.now(timezone.utc),
    }
    result = db.productos.insert_one(doc)
    doc["_id"] = result.inserted_id
    return jsonify(_serialize(doc)), 201


# ─── PUT /productos/<id> ──────────────────────────────────────────────────────

@owner_productos_bp.route("/productos/<producto_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def editar_producto(producto_id):
    db = get_db()
    try:
        oid = ObjectId(producto_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    if not db.productos.find_one({"_id": oid, "id_gimnasio": g.tenant_id}):
        return jsonify({"error": "Producto no encontrado"}), 404

    data   = request.get_json() or {}
    update = {"updated_at": datetime.now(timezone.utc)}

    if "nombre" in data:
        nombre = data["nombre"].strip()
        if not nombre:
            return jsonify({"error": "El nombre no puede estar vacío"}), 400
        update["nombre"] = nombre

    if "precio" in data:
        try:
            update["precio"] = float(data["precio"])
        except (TypeError, ValueError):
            return jsonify({"error": "Precio inválido"}), 400

    if "stock" in data:
        update["stock"] = max(0, int(data["stock"]))

    if "categoria" in data:
        update["categoria"] = data["categoria"].strip()

    if "descripcion" in data:
        update["descripcion"] = data["descripcion"].strip()

    if "imagenes" in data:
        imagenes = data["imagenes"] if isinstance(data["imagenes"], list) else []
        update["imagenes"] = imagenes[:3]

    if "activo" in data:
        update["activo"] = bool(data["activo"])

    db.productos.update_one({"_id": oid}, {"$set": update})
    updated = db.productos.find_one({"_id": oid})
    return jsonify(_serialize(updated)), 200


# ─── PATCH /productos/<id>/toggle ────────────────────────────────────────────

@owner_productos_bp.route("/productos/<producto_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_tenant
def toggle_producto(producto_id):
    db = get_db()
    try:
        oid = ObjectId(producto_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    doc = db.productos.find_one({"_id": oid, "id_gimnasio": g.tenant_id})
    if not doc:
        return jsonify({"error": "Producto no encontrado"}), 404

    nuevo_estado = not doc.get("activo", True)
    db.productos.update_one({"_id": oid}, {"$set": {"activo": nuevo_estado}})
    return jsonify({"activo": nuevo_estado}), 200


# ─── DELETE /productos/<id> ───────────────────────────────────────────────────

@owner_productos_bp.route("/productos/<producto_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_producto(producto_id):
    db = get_db()
    try:
        oid = ObjectId(producto_id)
    except Exception:
        return jsonify({"error": "ID inválido"}), 400

    result = db.productos.delete_one({"_id": oid, "id_gimnasio": g.tenant_id})
    if result.deleted_count == 0:
        return jsonify({"error": "Producto no encontrado"}), 404

    return jsonify({"msg": "Producto eliminado"}), 200
