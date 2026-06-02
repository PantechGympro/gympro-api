"""
admin/miembros.py — CRUD de miembros del gimnasio.

Fotos de perfil: se aceptan como base64 en el body JSON (campo foto_base64).
Se almacenan directamente en MongoDB, eliminando dependencia de filesystem.

Endpoints:
    GET    /api/miembros                  listar con paginación y búsqueda
    POST   /api/miembros                  crear miembro (JSON)
    PUT    /api/miembros/<id>             actualizar miembro (JSON)
    DELETE /api/miembros/<id>             desactivar (lógico)
    PUT    /api/miembros/<id>/reactivar   reactivar
"""
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from datetime import datetime
from bson.objectid import ObjectId

from app.mongo import get_db
from app.models.user import User
from app.models.miembro import Miembro

miembros_bp = Blueprint("miembros", __name__)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _parse_body():
    """Acepta JSON o form-data."""
    if request.is_json:
        return request.get_json() or {}
    return request.form.to_dict()


# ─── GET /api/miembros ────────────────────────────────────────────────────────

@miembros_bp.route("/api/miembros", methods=["GET"])
@jwt_required()
def listar_miembros():
    from app.utils.tenant import get_tenant_filter
    db        = get_db()
    page      = int(request.args.get("page", 1))
    per_page  = int(request.args.get("per_page", 12))
    search    = request.args.get("search", "").strip()
    inactivos = request.args.get("inactivos", "false").lower() == "true"

    base_filter = get_tenant_filter()
    estado      = "Inactivo" if inactivos else "Activo"
    query       = {**base_filter, "estado": estado}

    if search:
        # Búsqueda por nombre o email en campos desnormalizados
        regex = {"$regex": search, "$options": "i"}
        query["$or"] = [{"nombre": regex}, {"email": regex}]

    total  = db.miembros.count_documents(query)
    skip   = (page - 1) * per_page
    docs   = list(db.miembros.find(query).sort("fecha_registro", -1).skip(skip).limit(per_page))

    miembros = [Miembro(**m).to_dict_full(include_stats=False) for m in docs]

    return jsonify({
        "miembros": miembros,
        "total":    total,
        "page":     page,
        "pages":    max(1, -(-total // per_page)),
    }), 200


# ─── POST /api/miembros ───────────────────────────────────────────────────────

@miembros_bp.route("/api/miembros", methods=["POST"])
@jwt_required()
def crear_miembro():
    db   = get_db()
    data = _parse_body()

    nombre   = (data.get("nombre") or "").strip()
    email    = (data.get("email")  or "").strip()
    password = (data.get("password") or "").strip()

    if not nombre or not email:
        return jsonify({"error": "Nombre y email son obligatorios"}), 400

    if User.find_by_email(email):
        return jsonify({"error": "El email ya está registrado"}), 400

    try:
        rol_doc = db.roles.find_one({"nombre": "Miembro"})
        id_rol  = rol_doc["_id"] if rol_doc else ObjectId()

        nuevo_usuario = User(nombre=nombre, email=email, id_role=id_rol, activo=True)
        nuevo_usuario.set_password(password or "gym123")
        user_id = nuevo_usuario.save()

        nuevo_miembro = Miembro(
            id_usuario    = user_id,
            telefono      = data.get("telefono"),
            sexo          = data.get("sexo"),
            peso_inicial  = data.get("peso_inicial"),
            estatura      = data.get("estatura"),
            fecha_registro= datetime.now(),
            estado        = "Activo",
            foto_perfil   = data.get("foto_base64") or None,  # base64 directo
        )
        miembro_id          = nuevo_miembro.save()
        nuevo_miembro._id   = miembro_id
        return jsonify(nuevo_miembro.to_dict_full()), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── PUT /api/miembros/<id> ───────────────────────────────────────────────────

@miembros_bp.route("/api/miembros/<id>", methods=["PUT"])
@jwt_required()
def actualizar_miembro(id):
    miembro = Miembro.find_by_id(id)
    if not miembro:
        return jsonify({"error": "Miembro no encontrado"}), 404

    usuario = User.find_by_id(miembro.id_usuario)
    if not usuario:
        return jsonify({"error": "Usuario base no encontrado"}), 404

    data = _parse_body()

    try:
        if data.get("nombre"): usuario.nombre = data["nombre"].strip()

        if data.get("email"):
            email    = data["email"].strip()
            existente = User.find_by_email(email)
            if existente and str(existente._id) != str(usuario._id):
                return jsonify({"error": "El email ya está en uso"}), 400
            usuario.email = email

        if data.get("password"):
            usuario.set_password(data["password"])

        usuario.save()

        if data.get("telefono"):    miembro.telefono    = data["telefono"]
        if data.get("sexo"):        miembro.sexo        = data["sexo"]
        if data.get("peso_inicial"):miembro.peso_inicial= data["peso_inicial"]
        if data.get("estatura"):    miembro.estatura    = data["estatura"]

        # foto_base64: si viene (no vacío), actualizar; si viene vacío string, ignorar
        foto = data.get("foto_base64")
        import sys
        print(f"[DEBUG PUT miembro] content_type={request.content_type!r} "
              f"is_json={request.is_json} "
              f"keys={list(data.keys())} "
              f"foto_b64={'sí len='+str(len(foto)) if foto else 'no/vacío'}",
              file=sys.stderr, flush=True)
        if foto and foto.startswith("data:image"):
            miembro.foto_perfil = foto

        miembro.save()
        return jsonify(miembro.to_dict_full()), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── DELETE /api/miembros/<id> ────────────────────────────────────────────────

@miembros_bp.route("/api/miembros/<id>", methods=["DELETE"])
@jwt_required()
def eliminar_miembro(id):
    try:
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404

        usuario = User.find_by_id(miembro.id_usuario)
        miembro.estado = "Inactivo"
        miembro.save()
        if usuario:
            usuario.activo = False
            usuario.save()
        return jsonify({"message": "Miembro desactivado"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── PUT /api/miembros/<id>/reactivar ────────────────────────────────────────

@miembros_bp.route("/api/miembros/<id>/reactivar", methods=["PUT"])
@jwt_required()
def reactivar_miembro(id):
    try:
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404

        usuario = User.find_by_id(miembro.id_usuario)
        miembro.estado = "Activo"
        miembro.save()
        if usuario:
            usuario.activo = True
            usuario.save()
        return jsonify({"message": "Miembro reactivado"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
