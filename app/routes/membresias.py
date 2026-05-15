"""
routes/membresias.py -- CRUD de TipoMembresia por gimnasio (PostgreSQL).

Migrado de MongoDB en Sprint 3 / US14.
Todos los endpoints son tenant-scoped: leen g.tenant_id via @require_tenant.
La respuesta mantiene el alias 'id_membresia' para compatibilidad con el frontend.
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required
from app.extensions import db
from app.models.pg.tipo_membresia import TipoMembresia
from app.utils.tenant import require_tenant

membresias_bp = Blueprint("membresias", __name__)


@membresias_bp.route("/api/membresias", methods=["GET"])
@jwt_required()
@require_tenant
def listar_membresias():
    """Lista las membresias activas del gimnasio autenticado."""
    id_gimnasio  = g.tenant_id
    solo_activas = request.args.get("activas", "true").lower() != "false"

    query = TipoMembresia.query.filter_by(id_gimnasio=id_gimnasio)
    if solo_activas:
        query = query.filter_by(activo=True)
    membresias = query.order_by(TipoMembresia.nombre).all()

    resultados = []
    for m in membresias:
        d = m.to_dict()
        d["id_membresia"] = d["id"]   # alias de compatibilidad con el frontend
        resultados.append(d)

    return jsonify(resultados), 200


@membresias_bp.route("/api/membresias", methods=["POST"])
@jwt_required()
@require_tenant
def crear_membresia():
    """Crea un nuevo tipo de membresia para el gimnasio."""
    id_gimnasio = g.tenant_id
    data = request.get_json(silent=True) or {}

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "El campo 'nombre' es obligatorio"}), 400

    precio = data.get("precio")
    if precio is None:
        return jsonify({"error": "El campo 'precio' es obligatorio"}), 400
    try:
        precio = float(precio)
        if precio < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "El campo 'precio' debe ser un numero positivo"}), 400

    duracion_meses = data.get("duracion_meses", 1)
    try:
        duracion_meses = int(duracion_meses)
        if duracion_meses < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "El campo 'duracion_meses' debe ser un entero >= 1"}), 400

    existente = TipoMembresia.query.filter_by(id_gimnasio=id_gimnasio, nombre=nombre).first()
    if existente:
        return jsonify({"error": f"Ya existe una membresia con nombre '{nombre}'"}), 409

    nueva = TipoMembresia(
        id_gimnasio=id_gimnasio,
        nombre=nombre,
        duracion_meses=duracion_meses,
        precio=precio,
        descripcion=data.get("descripcion"),
        activo=bool(data.get("activo", True)),
    )
    db.session.add(nueva)
    db.session.commit()

    d = nueva.to_dict()
    d["id_membresia"] = d["id"]
    return jsonify(d), 201


@membresias_bp.route("/api/membresias/<int:membresia_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_membresia(membresia_id):
    """Actualiza un tipo de membresia existente del gimnasio."""
    id_gimnasio = g.tenant_id
    m = TipoMembresia.query.filter_by(id=membresia_id, id_gimnasio=id_gimnasio).first_or_404()
    data = request.get_json(silent=True) or {}

    if "nombre" in data:
        nombre = data["nombre"].strip()
        dup = TipoMembresia.query.filter(
            TipoMembresia.id_gimnasio == id_gimnasio,
            TipoMembresia.nombre == nombre,
            TipoMembresia.id != membresia_id,
        ).first()
        if dup:
            return jsonify({"error": f"Ya existe otra membresia con nombre '{nombre}'"}), 409
        m.nombre = nombre

    if "precio" in data:
        try:
            m.precio = float(data["precio"])
        except (TypeError, ValueError):
            return jsonify({"error": "precio invalido"}), 400

    if "duracion_meses" in data:
        try:
            m.duracion_meses = int(data["duracion_meses"])
        except (TypeError, ValueError):
            return jsonify({"error": "duracion_meses invalido"}), 400

    if "descripcion" in data:
        m.descripcion = data["descripcion"]
    if "activo" in data:
        m.activo = bool(data["activo"])

    db.session.commit()
    d = m.to_dict()
    d["id_membresia"] = d["id"]
    return jsonify(d), 200


@membresias_bp.route("/api/membresias/<int:membresia_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_membresia(membresia_id):
    """Soft-delete: marca la membresia como inactiva."""
    id_gimnasio = g.tenant_id
    m = TipoMembresia.query.filter_by(id=membresia_id, id_gimnasio=id_gimnasio).first_or_404()
    m.activo = False
    db.session.commit()
    return jsonify({"mensaje": "Membresia desactivada"}), 200
