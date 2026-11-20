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

from app.mongo import get_db
from app.utils.membresia import resumen_vencimientos
from app.utils.tenant import require_tenant
from app.utils.timezone import local_now_naive

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
        # Combos: un producto que agrupa varios artículos en un solo precio.
        # Al venderse descuenta el stock de cada componente (ver ventas.py).
        "es_combo":    bool(p.get("es_combo", False)),
        "items_combo": p.get("items_combo", []),
    }


def _limpiar_items_combo(valor):
    """
    Normaliza los componentes de un combo.
    Cada item: {"id_producto": "<ObjectId>", "nombre": str, "cantidad": int}
    """
    if not isinstance(valor, list):
        return []
    salida = []
    for it in valor[:20]:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id_producto") or "").strip()
        if not pid:
            continue
        try:
            cantidad = max(1, int(it.get("cantidad") or 1))
        except (TypeError, ValueError):
            cantidad = 1
        salida.append({
            "id_producto": pid,
            "nombre":      str(it.get("nombre") or "").strip()[:120],
            "cantidad":    cantidad,
        })
    return salida


def _stock_disponible_combo(db, gym_id, items):
    """
    Unidades del combo que se pueden armar con el inventario actual: el mínimo
    entre (stock de cada componente / cantidad requerida).
    """
    from bson.objectid import ObjectId
    if not items:
        return 0
    disponibles = []
    for it in items:
        try:
            comp = db.productos.find_one(
                {"_id": ObjectId(it["id_producto"]), "id_gimnasio": gym_id})
        except Exception:
            return 0
        if not comp:
            return 0
        disponibles.append(int(comp.get("stock", 0)) // max(1, int(it.get("cantidad", 1))))
    return max(0, min(disponibles)) if disponibles else 0


# ─── GET /alertas ────────────────────────────────────────────────────────────

@owner_productos_bp.route("/alertas", methods=["GET"])
@jwt_required()
@require_tenant
def get_alertas():
    """
    Devuelve alertas operativas del gimnasio:
      · Productos sin stock (nivel: error)
      · Productos con stock bajo ≤ LOW_STOCK_THRESHOLD (nivel: warning)
      · Membresías por vencer en los próximos 7 días (nivel: warning)
      · Membresías vencidas en los últimos 7 días (nivel: error)
      · Entregas pendientes del punto de venta (warning; error si ya pasó la hora)
    """
    db      = get_db()
    gym_id  = g.tenant_id
    # Las alertas NO llevan icono en el payload: el icono es decision de
    # presentacion y cada cliente (web y movil) elige el suyo segun `nivel` y
    # `tipo`. Antes viajaban emojis, que se veian distintos en cada sistema.
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
        })

    # ── Membresías por vencer / vencidas recientes ────────────────────────────
    # Regla única compartida con el listado "Por vencer" (utils/membresia.py).
    # Antes se consultaba `miembro_membresias` (plural, estado "activa", con
    # id_gimnasio): esa colección/campos no existen, así que nunca contaba nada.
    venc = resumen_vencimientos(db, gym_id)
    if venc["por_vencer"] > 0:
        n = venc["por_vencer"]
        alertas.append({
            "nivel":   "warning",
            "tipo":    "membresias_por_vencer",
            "titulo":  f"{n} membresía{'s' if n != 1 else ''} por vencer",
            "detalle": f"Vencen en los próximos {venc['dias']} días. Avisa a los miembros para que renueven.",
            "ruta":    "/owner/por-vencer",
        })
    if venc["vencidas_recientes"] > 0:
        n = venc["vencidas_recientes"]
        alertas.append({
            "nivel":   "error",
            "tipo":    "membresias_vencidas",
            "titulo":  f"{n} membresía{'s' if n != 1 else ''} vencida{'s' if n != 1 else ''} recientemente",
            "detalle": f"Vencieron en los últimos {venc['dias']} días. Contáctalos para recuperarlos.",
            "ruta":    "/owner/por-vencer",
        })

    # ── Entregas pendientes (compras del punto de venta por entregar) ─────────
    ahora_local = local_now_naive()
    corte = (ahora_local.strftime("%Y-%m-%d"), ahora_local.strftime("%H:%M"))
    pendientes = list(db.ventas.find(
        {"id_gimnasio": gym_id, "estado_entrega": "pendiente"},
        {"fecha_recogida": 1, "hora_recogida": 1},
    ))
    if pendientes:
        # Igual que el panel de entregas: pasada la hora acordada se resalta,
        # nunca se cancela (el producto ya está pagado).
        atrasadas = sum(
            1 for v in pendientes
            if (v.get("fecha_recogida") or "", v.get("hora_recogida") or "") < corte
        )
        n = len(pendientes)
        alertas.append({
            "nivel":   "error" if atrasadas else "warning",
            "tipo":    "entregas_pendientes",
            "titulo":  f"{n} entrega{'s' if n != 1 else ''} pendiente{'s' if n != 1 else ''}",
            "detalle": (f"{atrasadas} ya pasó la hora de recogida. " if atrasadas else "")
                       + "Entrega los productos a los miembros que los esperan.",
            "ruta":    "/owner/deliveries",
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

    # Para los combos, el stock mostrado es cuántos se pueden armar hoy con el
    # inventario de sus componentes.
    for p in productos:
        if p["es_combo"]:
            p["stock"] = _stock_disponible_combo(db, g.tenant_id, p["items_combo"])

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

    es_combo    = bool(data.get("es_combo"))
    items_combo = _limpiar_items_combo(data.get("items_combo")) if es_combo else []
    if es_combo and not items_combo:
        return jsonify({"error": "Un combo debe incluir al menos un producto"}), 400

    doc = {
        "id_gimnasio": g.tenant_id,
        "nombre":      nombre,
        "precio":      precio,
        # El stock de un combo no se lleva por separado: se deriva del stock de
        # sus componentes al momento de venderlo.
        "stock":       0 if es_combo else max(0, int(data.get("stock", 0))),
        "categoria":   (data.get("categoria") or ("Combos" if es_combo else "General")).strip(),
        "descripcion": (data.get("descripcion") or "").strip(),
        "imagenes":    imagenes,
        "activo":      True,
        "es_combo":    es_combo,
        "items_combo": items_combo,
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

    if "es_combo" in data:
        update["es_combo"] = bool(data["es_combo"])

    if "items_combo" in data:
        update["items_combo"] = _limpiar_items_combo(data["items_combo"])

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
