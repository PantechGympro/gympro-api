import os
from flask import Blueprint, request, jsonify, current_app, g
from werkzeug.utils import secure_filename
from flask_jwt_extended import jwt_required
from datetime import datetime
from bson.objectid import ObjectId
import re

from app.mongo import get_db
from app.models.user import User
from app.models.miembro import Miembro
from app.utils.tenant import get_tenant_filter, require_tenant
# PG — fuente de verdad desde Sprint 2
from app.extensions import db as pg_db
from app.models.pg.usuario import Usuario as UsuarioPG
from app.models.pg.rol import Rol as RolPG

miembros_bp = Blueprint("miembros", __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==============================================================================
# 1. LISTAR MIEMBROS (CON BUSCADOR + PAGINACIÓN)
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["GET"])
@jwt_required()
@require_tenant
def listar_miembros():
    import math
    db        = get_db()
    gym_id    = g.tenant_id
    page      = request.args.get('page', 1, type=int)
    per_page  = 6
    mostrar_inactivos = request.args.get('inactivos', 'false') == 'true'
    search    = request.args.get('search', '', type=str)

    estado_filtro = "Inactivo" if mostrar_inactivos else "Activo"

    # Aislamiento estricto: sólo miembros del gimnasio del token
    filtro_miembros = {
        "estado":         estado_filtro,
        "id_gimnasio_pg": gym_id,
    }

    if search:
        regex = re.compile(re.escape(search), re.IGNORECASE)
        filtro_miembros["$or"] = [
            {"nombre": regex},
            {"email":  regex},
        ]

    skip            = (page - 1) * per_page
    total_miembros  = db.miembros.count_documents(filtro_miembros)
    miembros_cursor = db.miembros.find(filtro_miembros).sort("fecha_registro", -1).skip(skip).limit(per_page)
    pages           = math.ceil(total_miembros / per_page) if total_miembros > 0 else 0

    miembros_lista = [Miembro(**m).to_dict_full(include_stats=False) for m in miembros_cursor]

    return jsonify({
        "miembros":     miembros_lista,
        "total":        total_miembros,
        "pages":        pages,
        "current_page": page,
    }), 200

# ==============================================================================
# 2. CREAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros", methods=["POST"])
@jwt_required()
@require_tenant
def crear_miembro():
    db     = get_db()
    gym_id = g.tenant_id
    data   = request.form
    file   = request.files.get('foto')

    nombre   = data.get('nombre')
    email    = data.get('email')
    password = data.get('password')

    if not nombre or not email:
        return jsonify({"error": "Nombre y Email son obligatorios"}), 400

    # Verificar duplicado en PG y en Mongo
    if UsuarioPG.query.filter_by(email=email.strip().lower()).first():
        return jsonify({"error": "El email ya está registrado"}), 400
    if User.find_by_email(email):
        return jsonify({"error": "El email ya está registrado"}), 400

    try:
        # 1. Crear Usuario en PostgreSQL (fuente de verdad desde Sprint 2)
        rol_pg = RolPG.query.filter_by(nombre="Miembro").first()
        if not rol_pg:
            return jsonify({"error": "Rol 'Miembro' no encontrado en base de datos"}), 500

        pg_usuario = UsuarioPG(
            nombre      = nombre,
            email       = email.strip().lower(),
            id_rol      = rol_pg.id,
            id_gimnasio = gym_id,
            activo      = True,
        )
        pg_usuario.set_password(password if password else "gym123")
        pg_db.session.add(pg_usuario)
        pg_db.session.flush()          # obtener el id sin commit aún

        # 2. Procesar imagen
        filename_bd = None
        if file and allowed_file(file.filename):
            filename        = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder   = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            filename_bd = unique_filename

        # 3. Crear documento Miembro en MongoDB con referencia al usuario PG
        nuevo_miembro = Miembro(
            id_usuario_pg  = pg_usuario.id,    # FK al usuario PG — permite el lookup en endpoints
            nombre         = nombre,            # desnormalizado para búsquedas/listados
            email          = email.strip().lower(),
            id_gimnasio_pg = gym_id,            # tenant — aislamiento estricto
            telefono       = data.get("telefono"),
            sexo           = data.get("sexo"),
            peso_inicial   = data.get("peso_inicial"),
            estatura       = data.get("estatura"),
            fecha_registro = datetime.now(),
            estado         = "Activo",
            foto_perfil    = filename_bd,
        )
        miembro_id        = nuevo_miembro.save()
        nuevo_miembro._id = miembro_id

        pg_db.session.commit()

        return jsonify(nuevo_miembro.to_dict_full()), 201

    except Exception as e:
        pg_db.session.rollback()
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 3. ACTUALIZAR MIEMBRO (PUT)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["PUT"])
@jwt_required()
@require_tenant
def actualizar_miembro(id):
    db      = get_db()
    gym_id  = g.tenant_id
    miembro = Miembro.find_by_id(id)
    if not miembro:
        return jsonify({"error": "Miembro no encontrado"}), 404
    if miembro.id_gimnasio_pg != gym_id:
        return jsonify({"error": "No autorizado"}), 403

    # Resolver usuario: PG (nuevo) o Mongo legacy (pre-Sprint 2)
    usuario_pg    = UsuarioPG.query.get(miembro.id_usuario_pg) if miembro.id_usuario_pg else None
    usuario_mongo = User.find_by_id(miembro.id_usuario) if miembro.id_usuario else None

    data = request.form
    file = request.files.get('foto')

    try:
        nuevo_nombre = data.get('nombre') or None
        nuevo_email  = (data.get('email') or "").strip().lower() or None

        # Actualizar usuario en PG si existe
        if usuario_pg:
            if nuevo_nombre:
                usuario_pg.nombre = nuevo_nombre
            if nuevo_email:
                conflicto = UsuarioPG.query.filter_by(email=nuevo_email).first()
                if conflicto and conflicto.id != usuario_pg.id:
                    return jsonify({"error": "El email ya está en uso por otro usuario"}), 400
                usuario_pg.email = nuevo_email
            pg_db.session.commit()

        # Actualizar usuario en Mongo si existe (legacy)
        elif usuario_mongo:
            if nuevo_nombre:
                usuario_mongo.nombre = nuevo_nombre
            if nuevo_email:
                existente = User.find_by_email(nuevo_email)
                if existente and existente._id != usuario_mongo._id:
                    return jsonify({"error": "El email ya está en uso por otro usuario"}), 400
                usuario_mongo.email = nuevo_email
            usuario_mongo.save()

        # Actualizar campos desnormalizados en Miembro (búsquedas + to_dict)
        if nuevo_nombre:
            miembro.nombre = nuevo_nombre
        if nuevo_email:
            miembro.email = nuevo_email

        if data.get('telefono'):     miembro.telefono     = data.get('telefono')
        if data.get('sexo'):         miembro.sexo         = data.get('sexo')
        if data.get('peso_inicial'): miembro.peso_inicial = data.get('peso_inicial')
        if data.get('estatura'):     miembro.estatura     = data.get('estatura')

        if file and allowed_file(file.filename):
            filename        = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder   = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            miembro.foto_perfil = unique_filename

        miembro.save()
        return jsonify(miembro.to_dict_full()), 200

    except Exception as e:
        pg_db.session.rollback()
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 4. ELIMINAR MIEMBRO (Lógico)
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_miembro(id):
    try:
        gym_id  = g.tenant_id
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404
        if miembro.id_gimnasio_pg != gym_id:
            return jsonify({"error": "No autorizado"}), 403

        miembro.estado = "Inactivo"
        miembro.save()

        # Desactivar en PG (nuevo) o Mongo (legacy)
        if miembro.id_usuario_pg:
            u_pg = UsuarioPG.query.get(miembro.id_usuario_pg)
            if u_pg:
                u_pg.activo = False
                pg_db.session.commit()
        elif miembro.id_usuario:
            u_mongo = User.find_by_id(miembro.id_usuario)
            if u_mongo:
                u_mongo.activo = False
                u_mongo.save()

        return jsonify({"message": "Miembro desactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================================================================
# 5. REACTIVAR MIEMBRO
# ==============================================================================
@miembros_bp.route("/api/miembros/<id>/reactivar", methods=["PUT"])
@jwt_required()
@require_tenant
def reactivar_miembro(id):
    try:
        gym_id  = g.tenant_id
        miembro = Miembro.find_by_id(id)
        if not miembro:
            return jsonify({"error": "No encontrado"}), 404
        if miembro.id_gimnasio_pg != gym_id:
            return jsonify({"error": "No autorizado"}), 403

        miembro.estado = "Activo"
        miembro.save()

        # Reactivar en PG (nuevo) o Mongo (legacy)
        if miembro.id_usuario_pg:
            u_pg = UsuarioPG.query.get(miembro.id_usuario_pg)
            if u_pg:
                u_pg.activo = True
                pg_db.session.commit()
        elif miembro.id_usuario:
            u_mongo = User.find_by_id(miembro.id_usuario)
            if u_mongo:
                u_mongo.activo = True
                u_mongo.save()

        return jsonify({"message": "Miembro reactivado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
