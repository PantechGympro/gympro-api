"""
routes/catalogos.py -- CRUD de Ejercicios y TiposClase por gimnasio (PostgreSQL).

Sprint 3 / US14. Tenant-scoped via g.tenant_id (@require_tenant).
Registrado en create_app con prefix /api.
"""
from flask import Blueprint, jsonify, request, g
from flask_jwt_extended import jwt_required
from app.extensions import db
from app.models.pg.ejercicio  import Ejercicio
from app.models.pg.tipo_clase import TipoClase
from app.utils.tenant import require_tenant

catalogos_bp = Blueprint("catalogos", __name__)

# ---------------------------------------------------------------------------
# Ejercicios
# ---------------------------------------------------------------------------

@catalogos_bp.route("/catalogos/ejercicios", methods=["GET"])
@jwt_required()
@require_tenant
def listar_ejercicios():
    id_gimnasio  = g.tenant_id
    solo_activos = request.args.get("activos", "true").lower() != "false"
    tipo         = request.args.get("tipo")
    grupo        = request.args.get("grupo_muscular")

    q = Ejercicio.query.filter_by(id_gimnasio=id_gimnasio)
    if solo_activos:
        q = q.filter_by(activo=True)
    if tipo:
        q = q.filter(Ejercicio.tipo.ilike(f"%{tipo}%"))
    if grupo:
        q = q.filter(Ejercicio.grupo_muscular.ilike(f"%{grupo}%"))

    return jsonify([e.to_dict() for e in q.order_by(Ejercicio.nombre).all()]), 200


@catalogos_bp.route("/catalogos/ejercicios", methods=["POST"])
@jwt_required()
@require_tenant
def crear_ejercicio():
    id_gimnasio = g.tenant_id
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "El campo 'nombre' es obligatorio"}), 400

    if Ejercicio.query.filter_by(id_gimnasio=id_gimnasio, nombre=nombre).first():
        return jsonify({"error": f"Ya existe un ejercicio con nombre '{nombre}'"}), 409

    ej = Ejercicio(
        id_gimnasio=id_gimnasio,
        nombre=nombre,
        descripcion=data.get("descripcion"),
        grupo_muscular=data.get("grupo_muscular"),
        tipo=data.get("tipo"),
        series=data.get("series"),
        repeticiones=data.get("repeticiones"),
        duracion_min=data.get("duracion_min"),
        activo=bool(data.get("activo", True)),
    )
    db.session.add(ej)
    db.session.commit()
    return jsonify(ej.to_dict()), 201


@catalogos_bp.route("/catalogos/ejercicios/<int:ejercicio_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_ejercicio(ejercicio_id):
    id_gimnasio = g.tenant_id
    ej = Ejercicio.query.filter_by(id=ejercicio_id, id_gimnasio=id_gimnasio).first_or_404()
    data = request.get_json(silent=True) or {}

    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            return jsonify({"error": "nombre no puede ser vacio"}), 400
        dup = Ejercicio.query.filter(
            Ejercicio.id_gimnasio == id_gimnasio,
            Ejercicio.nombre == nombre,
            Ejercicio.id != ejercicio_id,
        ).first()
        if dup:
            return jsonify({"error": f"Ya existe otro ejercicio con nombre '{nombre}'"}), 409
        ej.nombre = nombre

    for campo in ["descripcion", "grupo_muscular", "tipo", "repeticiones"]:
        if campo in data:
            setattr(ej, campo, data[campo])

    for campo in ["series", "duracion_min"]:
        if campo in data and data[campo] is not None:
            try:
                setattr(ej, campo, int(data[campo]))
            except (TypeError, ValueError):
                return jsonify({"error": f"Campo '{campo}' debe ser entero"}), 400

    if "activo" in data:
        ej.activo = bool(data["activo"])

    db.session.commit()
    return jsonify(ej.to_dict()), 200


@catalogos_bp.route("/catalogos/ejercicios/<int:ejercicio_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_ejercicio(ejercicio_id):
    id_gimnasio = g.tenant_id
    ej = Ejercicio.query.filter_by(id=ejercicio_id, id_gimnasio=id_gimnasio).first_or_404()
    ej.activo = False
    db.session.commit()
    return jsonify({"mensaje": "Ejercicio desactivado"}), 200


# ---------------------------------------------------------------------------
# Tipos de Clase
# ---------------------------------------------------------------------------

@catalogos_bp.route("/catalogos/tipos-clase", methods=["GET"])
@jwt_required()
@require_tenant
def listar_tipos_clase():
    id_gimnasio  = g.tenant_id
    solo_activos = request.args.get("activos", "true").lower() != "false"
    q = TipoClase.query.filter_by(id_gimnasio=id_gimnasio)
    if solo_activos:
        q = q.filter_by(activo=True)
    return jsonify([t.to_dict() for t in q.order_by(TipoClase.nombre).all()]), 200


@catalogos_bp.route("/catalogos/tipos-clase", methods=["POST"])
@jwt_required()
@require_tenant
def crear_tipo_clase():
    id_gimnasio = g.tenant_id
    data = request.get_json(silent=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "El campo 'nombre' es obligatorio"}), 400

    if TipoClase.query.filter_by(id_gimnasio=id_gimnasio, nombre=nombre).first():
        return jsonify({"error": f"Ya existe un tipo de clase con nombre '{nombre}'"}), 409

    tc = TipoClase(
        id_gimnasio=id_gimnasio,
        nombre=nombre,
        descripcion=data.get("descripcion"),
        duracion_minutos=data.get("duracion_minutos", 60),
        capacidad_max=data.get("capacidad_max"),
        activo=bool(data.get("activo", True)),
    )
    db.session.add(tc)
    db.session.commit()
    return jsonify(tc.to_dict()), 201


@catalogos_bp.route("/catalogos/tipos-clase/<int:tipo_id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_tipo_clase(tipo_id):
    id_gimnasio = g.tenant_id
    tc = TipoClase.query.filter_by(id=tipo_id, id_gimnasio=id_gimnasio).first_or_404()
    data = request.get_json(silent=True) or {}

    if "nombre" in data:
        nombre = (data["nombre"] or "").strip()
        if not nombre:
            return jsonify({"error": "nombre no puede ser vacio"}), 400
        dup = TipoClase.query.filter(
            TipoClase.id_gimnasio == id_gimnasio,
            TipoClase.nombre == nombre,
            TipoClase.id != tipo_id,
        ).first()
        if dup:
            return jsonify({"error": f"Ya existe otro tipo de clase con nombre '{nombre}'"}), 409
        tc.nombre = nombre

    if "descripcion" in data:
        tc.descripcion = data["descripcion"]
    if "duracion_minutos" in data and data["duracion_minutos"] is not None:
        try:
            tc.duracion_minutos = int(data["duracion_minutos"])
        except (TypeError, ValueError):
            return jsonify({"error": "duracion_minutos debe ser entero"}), 400
    if "capacidad_max" in data:
        tc.capacidad_max = int(data["capacidad_max"]) if data["capacidad_max"] is not None else None
    if "activo" in data:
        tc.activo = bool(data["activo"])

    db.session.commit()
    return jsonify(tc.to_dict()), 200


@catalogos_bp.route("/catalogos/tipos-clase/<int:tipo_id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_tipo_clase(tipo_id):
    id_gimnasio = g.tenant_id
    tc = TipoClase.query.filter_by(id=tipo_id, id_gimnasio=id_gimnasio).first_or_404()
    tc.activo = False
    db.session.commit()
    return jsonify({"mensaje": "Tipo de clase desactivado"}), 200
