import os
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from flask_jwt_extended import jwt_required
from datetime import datetime
from bson.objectid import ObjectId
import re

from app.mongo import get_db
from app.models.user import User
from app.models.miembro import Miembro
from app.utils.tenant import get_tenant_filter

miembros_bp = Blueprint("miembros", __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==============================================================================
# 1. LISTAR MIEMBROS (CON BUSCADOR + PAGINACIÓN)
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["GET"])
@jwt_required()
def listar_miembros():
    import math
    db = get_db()
    page             = request.args.get('page', 1, type=int)
    per_page         = 6
    mostrar_inactivos = request.args.get('inactivos', 'false') == 'true'
    search           = request.args.get('search', '', type=str)
    tenant_filter    = get_tenant_filter()

    estado_filtro = "Inactivo" if mostrar_inactivos else "Activo"

    # Filtro base con aislamiento de tenant.
    # Sprint 2: el seed desnormaliza nombre, email e id_gimnasio_pg directamente
    # en el documento de miembro, por lo que la búsqueda no requiere join con usuarios.
    filtro_miembros = {"estado": estado_filtro}
    if tenant_filter:
        filtro_miembros["id_gimnasio_pg"] = tenant_filter["id_gimnasio"]

    if search:
        regex = re.compile(re.escape(search), re.IGNORECASE)
        filtro_miembros["$or"] = [
            {"nombre": regex},
            {"email":  regex},
        ]

    skip           = (page - 1) * per_page
    total_miembros = db.miembros.count_documents(filtro_miembros)
    miembros_cursor = db.miembros.find(filtro_miembros).sort("fecha_registro", -1).skip(skip).limit(per_page)
    pages          = math.ceil(total_miembros / per_page) if total_miembros > 0 else 0

    miembros_lista = [Miembro(**m).to_dict_full(include_stats=False) for m in miembros_cursor]

    return jsonify({
        "miembros":    miembros_lista,
        "total":       total_miembros,
        "pages":       pages,
        "current_page": page,
    }), 200

# ==============================================================================
# 2. CREAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["POST"])
@jwt_required()
def crear_miembro():
    db = get_db()
    data = request.form
    file = request.files.get('foto')

    nombre = data.get('nombre')
    email = data.get('email')
    password = data.get('password')

    if not nombre or not email:
        return jsonify({"error": "Nombre y Email son obligatorios"}), 400

    if User.find_by_email(email):
        return jsonify({"error": "El email ya está registrado"}), 400

    try:
        # Buscar el ID del rol Miembro dinámicamente
        rol_doc = db.roles.find_one({"nombre": "Miembro"})
        id_rol = rol_doc["_id"] if rol_doc else ObjectId()

        # 1. Crear Usuario
        nuevo_usuario = User(
            nombre=nombre,
            email=email,
            id_role=id_rol,
            activo=True
        )
        nuevo_usuario.set_password(password if password else "gym123")
        user_id = nuevo_usuario.save()

        # 2. Procesar imagen
        filename_bd = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            filename_bd = unique_filename

        # 3. Crear Miembro
        nuevo_miembro = Miembro(
            id_usuario=user_id,
            telefono=data.get("telefono"),
            sexo=data.get("sexo"),
            peso_inicial=data.get("peso_inicial"),
            estatura=data.get("estatura"),
            fecha_registro=datetime.now(),
            estado="Activo",
            foto_perfil=filename_bd
        )
        
        miembro_id = nuevo_miembro.save()
        nuevo_miembro._id = miembro_id

        return jsonify(nuevo_miembro.to_dict_full()), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 3. ACTUALIZAR MIEMBRO (PUT)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["PUT"])
@jwt_required()
def actualizar_miembro(id):
    db = get_db()
    miembro = Miembro.find_by_id(id)
    if not miembro: return jsonify({"error": "Miembro no encontrado"}), 404
    
    usuario = User.find_by_id(miembro.id_usuario)
    if not usuario: return jsonify({"error": "Usuario base no encontrado"}), 404
    
    data = request.form
    file = request.files.get('foto')

    try:
        if data.get('nombre'): usuario.nombre = data.get('nombre')
        
        if data.get('email'):
            existente = User.find_by_email(data.get('email'))
            if existente and existente._id != usuario._id:
                return jsonify({"error": "El email ya está en uso por otro usuario"}), 400
            usuario.email = data.get('email')
        
        # Guardamos cambios del usuario
        usuario.save()
        
        if data.get('telefono'): miembro.telefono = data.get('telefono')
        if data.get('sexo'): miembro.sexo = data.get('sexo')
        if data.get('peso_inicial'): miembro.peso_inicial = data.get('peso_inicial')
        if data.get('estatura'): miembro.estatura = data.get('estatura')

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            miembro.foto_perfil = unique_filename

        miembro.save()
        return jsonify(miembro.to_dict_full()), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 4. ELIMINAR MIEMBRO (Lógico)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["DELETE"])
@jwt_required()
def eliminar_miembro(id):
    try:
        miembro = Miembro.find_by_id(id)
        if not miembro: return jsonify({"error": "No encontrado"}), 404
        
        usuario = User.find_by_id(miembro.id_usuario)

        miembro.estado = "Inactivo"
        miembro.save()
        
        if usuario:
            usuario.activo = False
            usuario.save()

        return jsonify({"message": "Miembro desactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 5. REACTIVAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>/reactivar", methods=["PUT"])
@jwt_required()
def reactivar_miembro(id):
    try:
        miembro = Miembro.find_by_id(id)
        if not miembro: return jsonify({"error": "No encontrado"}), 404
        
        usuario = User.find_by_id(miembro.id_usuario)

        miembro.estado = "Activo"
        miembro.save()
        
        if usuario:
            usuario.activo = True
            usuario.save()

        return jsonify({"message": "Miembro reactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500