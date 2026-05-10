from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
from datetime import datetime
import os
from bson.objectid import ObjectId
from app.mongo import get_db

user_profile_bp = Blueprint('user_profile', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@user_profile_bp.route('/api/user/profile', methods=['GET'])
@jwt_required()
def get_user_profile():
    """
    Obtiene el perfil completo del usuario autenticado
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        
        usuario = db.usuarios.find_one({"_id": user_id})
        if not usuario:
            return jsonify({"error": "Usuario no encontrado"}), 404
        
        miembro = db.miembros.find_one({"id_usuario": user_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        # Calcular estadísticas de actividad
        total_entrenamientos = db.asistencias.count_documents({
            "id_miembro": miembro["_id"]
        })
        
        # Calcular meses activo
        fecha_registro = miembro.get("fecha_registro")
        if isinstance(fecha_registro, str):
            try:
                fecha_registro = datetime.strptime(fecha_registro[:10], "%Y-%m-%d").date()
            except:
                fecha_registro = None
        elif isinstance(fecha_registro, datetime):
            fecha_registro = fecha_registro.date()

        meses_activo = 0
        if fecha_registro:
            meses_activo = ((datetime.now().date() - fecha_registro).days // 30)
        
        # Formatear fecha de nacimiento
        fn = miembro.get("fecha_nacimiento")
        if isinstance(fn, datetime):
            fn_str = fn.strftime('%d/%m/%Y')
        elif isinstance(fn, str):
            # Asumimos que viene en YYYY-MM-DD
            try:
                fn_str = datetime.strptime(fn[:10], "%Y-%m-%d").strftime('%d/%m/%Y')
            except:
                fn_str = fn
        else:
            fn_str = ""

        # Formatear género
        sexo = miembro.get("sexo")
        genero_str = "Masculino" if sexo == "M" else "Femenino" if sexo == "F" else "Otro" if sexo else ""
        
        peso = miembro.get("peso_inicial")
        estatura = miembro.get("estatura")

        profile_data = {
            "nombre": usuario.get("nombre", ""),
            "email": usuario.get("email", ""),
            "telefono": miembro.get("telefono", ""),
            "fechaNacimiento": fn_str,
            "direccion": "",  
            "genero": genero_str,
            "peso": f"{peso} kg" if peso else "No registrado",
            "altura": f"{estatura} m" if estatura else "No registrado",
            "objetivo": "Tonificación muscular",  
            "nivelExperiencia": "Intermedio",  
            "fotoPerfil": miembro.get("foto_perfil"),
            "mesesActivo": meses_activo,
            "totalEntrenamientos": total_entrenamientos
        }
        
        return jsonify(profile_data), 200
        
    except Exception as e:
        print(f"Error en get_user_profile: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile', methods=['PUT'])
@jwt_required()
def update_user_profile():
    """
    Actualiza el perfil del usuario autenticado
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        
        usuario = db.usuarios.find_one({"_id": user_id})
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not usuario or not miembro:
            return jsonify({"error": "Usuario/Miembro no encontrado"}), 404
        
        # Manejar JSON o FormData
        if request.is_json:
            data = request.json
            file = None
        else:
            data = request.form
            file = request.files.get('foto')
        
        update_usuario = {}
        if data.get('nombre'):
            update_usuario['nombre'] = data.get('nombre')
        
        if data.get('email'):
            existing = db.usuarios.find_one({"email": data.get('email')})
            if existing and existing['_id'] != user_id:
                return jsonify({"error": "El email ya está en uso"}), 400
            update_usuario['email'] = data.get('email')
            
        if update_usuario:
            db.usuarios.update_one({"_id": user_id}, {"$set": update_usuario})
        
        update_miembro = {}
        if data.get('telefono'):
            update_miembro['telefono'] = data.get('telefono')
        
        if data.get('fechaNacimiento'):
            try:
                fecha_str = data.get('fechaNacimiento')
                # Asumimos que viene del front como DD/MM/YYYY
                update_miembro['fecha_nacimiento'] = datetime.strptime(fecha_str, '%d/%m/%Y')
            except:
                pass
        
        if data.get('genero'):
            genero_map = {"Masculino": "M", "Femenino": "F", "Otro": "Otro"}
            if data.get('genero') in genero_map:
                update_miembro['sexo'] = genero_map[data.get('genero')]
        
        if data.get('peso'):
            try:
                peso_str = data.get('peso').replace('kg', '').strip()
                update_miembro['peso_inicial'] = float(peso_str)
            except:
                pass
        
        if data.get('altura'):
            try:
                altura_str = data.get('altura').replace('m', '').strip()
                update_miembro['estatura'] = float(altura_str)
            except:
                pass
        
        # Procesar foto de perfil
        foto_final = miembro.get("foto_perfil")
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            upload_folder = os.path.join(current_app.root_path, 'static/uploads')
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, unique_filename))
            update_miembro['foto_perfil'] = unique_filename
            foto_final = unique_filename
        
        if update_miembro:
            db.miembros.update_one({"_id": miembro["_id"]}, {"$set": update_miembro})
        
        return jsonify({
            "message": "Perfil actualizado correctamente",
            "profile": {
                "nombre": update_usuario.get("nombre", usuario.get("nombre")),
                "email": update_usuario.get("email", usuario.get("email")),
                "fotoPerfil": foto_final
            }
        }), 200
        
    except Exception as e:
        print(f"Error en update_user_profile: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_profile_bp.route('/api/user/profile/photo', methods=['POST'])
@jwt_required()
def upload_profile_photo():
    """
    Endpoint específico para subir foto de perfil
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        file = request.files.get('foto')
        
        if not file or not allowed_file(file.filename):
            return jsonify({"error": "Archivo inválido"}), 400
        
        filename = secure_filename(file.filename)
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        upload_folder = os.path.join(current_app.root_path, 'static/uploads')
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, unique_filename))
        
        db.miembros.update_one(
            {"_id": miembro["_id"]},
            {"$set": {"foto_perfil": unique_filename}}
        )
        
        return jsonify({
            "message": "Foto actualizada correctamente",
            "fotoPerfil": unique_filename
        }), 200
        
    except Exception as e:
        print(f"Error en upload_profile_photo: {e}")
        return jsonify({"error": str(e)}), 500