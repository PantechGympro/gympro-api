from flask import Blueprint, request, jsonify, current_app, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
from datetime import datetime
import os

from app.mongo import get_db
from app.models.pg.usuario import Usuario
from app.extensions import db as pg_db
from app.utils.tenant import require_tenant

user_profile_bp = Blueprint('user_profile', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@user_profile_bp.route('/api/user/profile', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_profile():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        # Datos base desde PG
        usuario = Usuario.query.get(user_pg_id)
        if not usuario:
            return jsonify({"error": "Usuario no encontrado"}), 404

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        total_entrenamientos = mdb.asistencias.count_documents({"id_miembro": miembro["_id"]})

        fecha_registro = miembro.get("fecha_registro")
        if isinstance(fecha_registro, str):
            try:   fecha_registro = datetime.strptime(fecha_registro[:10], "%Y-%m-%d").date()
            except: fecha_registro = None
        elif isinstance(fecha_registro, datetime):
            fecha_registro = fecha_registro.date()

        meses_activo = ((datetime.now().date() - fecha_registro).days // 30) if fecha_registro else 0

        fn = miembro.get("fecha_nacimiento")
        if isinstance(fn, datetime):     fn_str = fn.strftime('%d/%m/%Y')
        elif isinstance(fn, str) and fn:
            try:    fn_str = datetime.strptime(fn[:10], "%Y-%m-%d").strftime('%d/%m/%Y')
            except: fn_str = fn
        else:   fn_str = ""

        sexo = miembro.get("sexo", "")
        genero_str = {"M": "Masculino", "F": "Femenino", "Masculino": "Masculino",
                      "Femenino": "Femenino"}.get(sexo, sexo or "")

        peso    = miembro.get("peso_inicial")
        estatura= miembro.get("estatura")

        return jsonify({
            "nombre":              usuario.nombre,
            "email":               usuario.email,
            "telefono":            miembro.get("telefono", ""),
            "fechaNacimiento":     fn_str,
            "direccion":           "",
            "genero":              genero_str,
            "peso":                f"{peso} kg" if peso else "No registrado",
            "altura":              f"{estatura} m" if estatura else "No registrado",
            "objetivo":            miembro.get("objetivo", "Tonificación muscular"),
            "nivelExperiencia":    "Intermedio",
            "fotoPerfil":          miembro.get("foto_perfil"),
            "mesesActivo":         meses_activo,
            "totalEntrenamientos": total_entrenamientos
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile', methods=['PUT'])
@jwt_required()
@require_tenant
def update_user_profile():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        usuario = Usuario.query.get(user_pg_id)
        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not usuario or not miembro:
            return jsonify({"error": "Usuario/Miembro no encontrado"}), 404

        if request.is_json:
            data = request.json or {}
            file = None
        else:
            data = request.form
            file = request.files.get('foto')

        # Actualizar PG (nombre / email)
        if data.get('nombre'): usuario.nombre = data['nombre']
        if data.get('email'):
            existing = Usuario.query.filter_by(email=data['email']).first()
            if existing and existing.id != user_pg_id:
                return jsonify({"error": "El email ya está en uso"}), 400
            usuario.email = data['email']
        pg_db.session.commit()

        # Actualizar miembro en Mongo
        update_miembro = {}
        if data.get('telefono'):       update_miembro['telefono'] = data['telefono']
        if data.get('fechaNacimiento'):
            try:    update_miembro['fecha_nacimiento'] = datetime.strptime(data['fechaNacimiento'], '%d/%m/%Y')
            except: pass
        if data.get('genero'):
            gmap = {"Masculino": "Masculino", "Femenino": "Femenino", "Otro": "Otro"}
            if data['genero'] in gmap: update_miembro['sexo'] = gmap[data['genero']]
        if data.get('peso'):
            try:    update_miembro['peso_inicial'] = float(str(data['peso']).replace('kg', '').strip())
            except: pass
        if data.get('altura'):
            try:    update_miembro['estatura'] = float(str(data['altura']).replace('m', '').strip())
            except: pass
        # Contacto de emergencia (from CompleteProfile)
        if data.get('contacto_emergencia_nombre'):
            update_miembro['contacto_emergencia_nombre'] = data['contacto_emergencia_nombre']
        if data.get('contacto_emergencia_telefono'):
            update_miembro['contacto_emergencia_telefono'] = data['contacto_emergencia_telefono']
        # Desnormalizar nombre en Mongo también
        if data.get('nombre'):
            update_miembro['nombre'] = data['nombre']
            update_miembro['email']  = usuario.email

        # Procesar foto
        foto_final = miembro.get("foto_perfil")
        if file and allowed_file(file.filename):
            filename        = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder   = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            update_miembro['foto_perfil'] = unique_filename
            foto_final = unique_filename

        if update_miembro:
            mdb.miembros.update_one({"_id": miembro["_id"]}, {"$set": update_miembro})

        return jsonify({
            "message": "Perfil actualizado correctamente",
            "profile": {
                "nombre":    usuario.nombre,
                "email":     usuario.email,
                "fotoPerfil":foto_final
            }
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile/photo', methods=['POST'])
@jwt_required()
@require_tenant
def upload_profile_photo():
    try:
        mdb        = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id

        miembro = mdb.miembros.find_one({
            "id_usuario_pg":  user_pg_id,
            "id_gimnasio_pg": gym_id
        })
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        file = request.files.get('foto')
        if not file or not allowed_file(file.filename):
            return jsonify({"error": "Archivo inválido"}), 400

        filename        = secure_filename(file.filename)
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        upload_folder   = os.path.join(current_app.root_path, 'static/uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, unique_filename))

        mdb.miembros.update_one({"_id": miembro["_id"]},
                                {"$set": {"foto_perfil": unique_filename}})
        return jsonify({
            "message":   "Foto actualizada correctamente",
            "fotoPerfil":unique_filename
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500
