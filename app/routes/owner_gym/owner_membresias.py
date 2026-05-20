"""
owner_gym/owner_membresias.py — CRUD de Tipos de Membresía del gimnasio.

Consolida la gestión de planes de membresía bajo el prefijo /api/owner_gym.

Endpoints:
    GET    /api/owner_gym/membresias            Listar todos (activos + inactivos)
    POST   /api/owner_gym/membresias            Crear nuevo tipo
    PUT    /api/owner_gym/membresias/<id>       Editar
    PATCH  /api/owner_gym/membresias/<id>/toggle Activar / desactivar
    DELETE /api/owner_gym/membresias/<id>       Eliminar (solo si sin usos)
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required

from app.extensions import db
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant
from app.utils.security import require_role

owner_membresias_bp = Blueprint("owner_membresias", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/owner_gym/membresias
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias", methods=["GET"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def listar_membresias():
    gym_id       = g.tenant_id
    solo_activos = request.args.get("activos", "false").lower() == "true"

    q = TipoMembresia.query.filter_by(id_gimnasio=gym_id)
    if solo_activos:
        q = q.filter_by(activo=True)

    items = q.order_by(TipoMembresia.precio).all()
    return jsonify([m.to_dict() for m in items]), 200


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/owner_gym/membresias
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias", methods=["POST"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def crear_membresia():
    """
    Body JSON:
        {
            "nombre":         "Mensual Plus",
            "duracion_meses": 1,
            "precio":         499.00,
            "descripcion":    "Acceso completo + clases grupales"
        }
    """
    gym_id = g.tenant_id
    data   = request.get_json() or {}

    nombre         = (data.get("nombre") or "").strip()
    duracion_meses = data.get("duracion_meses")
    precio         = data.get("precio")

    if not nombre:
        return jsonify({"msg": "El campo 'nombre' es requerido"}), 400
    try:
        duracion_meses = int(duracion_meses)
        precio         = float(precio)
        if duracion_meses < 1 or precio < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"msg": "duracion_meses (entero ≥1) y precio (decimal ≥0) son requeridos"}), 400

    if TipoMembresia.query.filter_by(id_gimnasio=gym_id, nombre=nombre).first():
        return jsonify({"msg": f"Ya existe una membresía con el nombre '{nombre}'"}), 409

    nueva = TipoMembresia(
        id_gimnasio    = gym_id,
        nombre         = nombre,
        duracion_meses = duracion_meses,
        precio         = precio,
        descripcion    = (data.get("descripcion") or "").strip() or None,
        activo         = True,
    )
    db.session.add(nueva)
    db.session.commit()
    return jsonify(nueva.to_dict()), 201


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/owner_gym/membresias/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def editar_membresia(mem_id: int):
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    data = request.get_json() or {}

    if "nombre" in data:
        nombre = data["nombre"].strip()
        if not nombre:
            return jsonify({"msg": "El nombre no puede estar vacío"}), 400
        existing = TipoMembresia.query.filter_by(id_gimnasio=gym_id, nombre=nombre).first()
        if existing and existing.id != mem_id:
            return jsonify({"msg": f"Ya existe otra membresía con el nombre '{nombre}'"}), 409
        mem.nombre = nombre

    if "duracion_meses" in data:
        try:
            mem.duracion_meses = int(data["duracion_meses"])
        except (ValueError, TypeError):
            return jsonify({"msg": "duracion_meses debe ser un entero"}), 400

    if "precio" in data:
        try:
            mem.precio = float(data["precio"])
        except (ValueError, TypeError):
            return jsonify({"msg": "precio debe ser un número"}), 400

    if "descripcion" in data:
        mem.descripcion = (data["descripcion"] or "").strip() or None

    db.session.commit()
    return jsonify({"msg": "Membresía actualizada", **mem.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/owner_gym/membresias/<id>/toggle
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>/toggle", methods=["PATCH"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def toggle_membresia(mem_id: int):
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    mem.activo = not mem.activo
    db.session.commit()
    estado = "activada" if mem.activo else "desactivada"
    return jsonify({"msg": f"Membresía {estado}", **mem.to_dict()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/owner_gym/membresias/<id>
# ─────────────────────────────────────────────────────────────────────────────
@owner_membresias_bp.route("/membresias/<int:mem_id>", methods=["DELETE"])
@jwt_required()
@require_role("owner_gym")
@require_tenant
def eliminar_membresia(mem_id: int):
    """Elimina solo si no tiene miembro_membresias asociadas."""
    from app.mongo import get_db
    gym_id = g.tenant_id
    mem    = TipoMembresia.query.filter_by(id=mem_id, id_gimnasio=gym_id).first()
    if not mem:
        return jsonify({"msg": "Membresía no encontrada"}), 404

    mdb  = get_db()
    usos = mdb.miembro_membresias.count_documents({"id_membresia": mem_id})
    if usos > 0:
        return jsonify({
            "msg":  f"No se puede eliminar: hay {usos} membresía(s) de miembro activas con este tipo.",
            "tip":  "Desactívala en lugar de eliminarla.",
        }), 409

    db.session.delete(mem)
    db.session.commit()
    return jsonify({"msg": "Membresía eliminada"}), 200
