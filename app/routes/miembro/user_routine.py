from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime
from bson.objectid import ObjectId
from app.mongo import get_db
from app.utils.tenant import require_tenant

user_routines_bp = Blueprint('user_routines', __name__)

def _build_routine_dict(db, rutina_doc):
    """Helper para armar el diccionario de la rutina con sus días y ejercicios"""
    dias = list(db.rutina_dias.find({"id_rutina": rutina_doc["_id"]}).sort("orden", 1))
    
    dias_formateados = []
    for dia in dias:
        ejercicios = list(db.rutina_ejercicios.find({"id_rutina_dia": dia["_id"]}).sort("orden", 1))
        
        ej_formateados = []
        for ej in ejercicios:
            ej_formateados.append({
                "id": str(ej["_id"]),
                "nombre": ej.get("nombre_ejercicio", ""),
                "series": ej.get("series", "3"),
                "reps": ej.get("repeticiones", "12"),
                "peso": ej.get("peso", ""),
                "grupo": ej.get("grupo_muscular", ""),
                "unidad": ej.get("unidad", "kg"),
                "notas": ej.get("notas", ""),
                "orden": ej.get("orden", 0)
            })
            
        dias_formateados.append({
            "id": str(dia["_id"]),
            "dia": dia.get("dia_semana", ""),
            "grupo": dia.get("grupo_muscular", ""),
            "orden": dia.get("orden", 0),
            "ejercicios": ej_formateados
        })
        
    return _routine_meta(rutina_doc, dias_formateados)


def _routine_meta(rutina_doc, dias_formateados):
    return {
        "id": str(rutina_doc["_id"]),
        "nombre": rutina_doc.get("nombre", ""),
        "categoria": rutina_doc.get("categoria", "General"),
        "dificultad": rutina_doc.get("dificultad", "Intermedio"),
        "duracion_minutos": rutina_doc.get("duracion_minutos", 60),
        "descripcion": rutina_doc.get("descripcion", ""),
        "objetivo": rutina_doc.get("objetivo", ""),
        "activa": rutina_doc.get("activa", True),
        "dias": dias_formateados
    }


def _build_ej_doc(dia_id, ej_idx, ed):
    """Documento de ejercicio con grupo muscular, peso y unidad (kg/lb)."""
    return {
        "id_rutina_dia":    dia_id,
        "nombre_ejercicio": ed['nombre'],
        "series":           str(ed.get('series', '3')),
        "repeticiones":     str(ed.get('reps', '12')),
        "peso":             str(ed.get('peso', '') or ''),
        "grupo_muscular":   ed.get('grupo', '') or '',
        "unidad":           (ed.get('unidad') or 'kg'),
        "orden":            ej_idx,
    }


@user_routines_bp.route('/routines', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_routines():
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        rutinas_docs = list(db.rutinas.find({"id_miembro": miembro["_id"]}))
        rutinas_completas = [_build_routine_dict(db, r) for r in rutinas_docs]
        
        return jsonify({
            "rutinas": rutinas_completas
        }), 200
        
    except Exception as e:
        print(f"Error en get_user_routines: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_routines_bp.route('/routines/<id>', methods=['GET'])
@jwt_required()
@require_tenant
def get_routine(id):
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        rutina_doc = db.rutinas.find_one({
            "_id": ObjectId(id),
            "id_miembro": miembro["_id"]
        })
        
        if not rutina_doc:
            return jsonify({"error": "Rutina no encontrada"}), 404
        
        return jsonify(_build_routine_dict(db, rutina_doc)), 200
        
    except Exception as e:
        print(f"Error en get_routine: {e}")
        return jsonify({"error": str(e)}), 500


@user_routines_bp.route('/routines', methods=['POST'])
@jwt_required()
@require_tenant
def create_routine():
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        data = request.json
        
        if not data.get('nombre'):
            return jsonify({"error": "El nombre es requerido"}), 400
        
        if not data.get('dias') or len(data['dias']) == 0:
            return jsonify({"error": "Debes agregar al menos un día"}), 400
        
        # Crear rutina — persistir todos los campos que expone _build_routine_dict
        nueva_rutina = {
            "id_miembro":          miembro["_id"],
            "nombre":              data['nombre'],
            "categoria":           data.get('categoria', 'General'),
            "dificultad":          data.get('dificultad', 'Intermedio'),
            "duracion_minutos":    int(data['duracion_minutos']) if data.get('duracion_minutos') else 60,
            "descripcion":         data.get('descripcion', ''),
            "objetivo":            data.get('objetivo', ''),
            "activa":              True,
            "fecha_creacion":      datetime.now(),
            "fecha_actualizacion": datetime.now(),
        }
        rutina_id = db.rutinas.insert_one(nueva_rutina).inserted_id
        nueva_rutina["_id"] = rutina_id
        
        # Crear días y ejercicios
        for idx, dia_data in enumerate(data['dias']):
            nuevo_dia = {
                "id_rutina": rutina_id,
                "dia_semana": dia_data.get('dia', ''),
                "grupo_muscular": dia_data.get('grupo', ''),
                "orden": idx
            }
            dia_id = db.rutina_dias.insert_one(nuevo_dia).inserted_id
            
            ejercicios_a_insertar = []
            for ej_idx, ejercicio_data in enumerate(dia_data.get('ejercicios', [])):
                if ejercicio_data.get('nombre', '').strip():
                    ejercicios_a_insertar.append(_build_ej_doc(dia_id, ej_idx, ejercicio_data))

            if ejercicios_a_insertar:
                db.rutina_ejercicios.insert_many(ejercicios_a_insertar)
        
        return jsonify({
            "message": "Rutina creada exitosamente",
            "rutina": _build_routine_dict(db, nueva_rutina)
        }), 201
        
    except Exception as e:
        print(f"Error en create_routine: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_routines_bp.route('/routines/<id>', methods=['PUT'])
@jwt_required()
@require_tenant
def update_routine(id):
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        rutina_id = ObjectId(id)
        rutina_doc = db.rutinas.find_one({
            "_id": rutina_id,
            "id_miembro": miembro["_id"]
        })
        
        if not rutina_doc:
            return jsonify({"error": "Rutina no encontrada"}), 404
        
        data = request.json
        
        # Actualizar campos de metadata de la rutina
        update_fields = {"fecha_actualizacion": datetime.now()}
        for campo in ('nombre', 'categoria', 'dificultad', 'descripcion', 'objetivo'):
            if data.get(campo) is not None:
                update_fields[campo] = data[campo]
                rutina_doc[campo]    = data[campo]   # refrescar doc local para _build_routine_dict
        if data.get('duracion_minutos') is not None:
            update_fields['duracion_minutos'] = int(data['duracion_minutos'])
            rutina_doc['duracion_minutos']    = update_fields['duracion_minutos']
        db.rutinas.update_one({"_id": rutina_id}, {"$set": update_fields})
        
        # Eliminar días y ejercicios existentes para reemplazarlos
        dias_existentes = list(db.rutina_dias.find({"id_rutina": rutina_id}))
        dias_ids = [d["_id"] for d in dias_existentes]
        
        if dias_ids:
            db.rutina_ejercicios.delete_many({"id_rutina_dia": {"$in": dias_ids}})
            db.rutina_dias.delete_many({"id_rutina": rutina_id})
        
        # Crear nuevos días y ejercicios
        for idx, dia_data in enumerate(data.get('dias', [])):
            nuevo_dia = {
                "id_rutina": rutina_id,
                "dia_semana": dia_data.get('dia', ''),
                "grupo_muscular": dia_data.get('grupo', ''),
                "orden": idx
            }
            dia_id = db.rutina_dias.insert_one(nuevo_dia).inserted_id
            
            ejercicios_a_insertar = []
            for ej_idx, ejercicio_data in enumerate(dia_data.get('ejercicios', [])):
                if ejercicio_data.get('nombre', '').strip():
                    ejercicios_a_insertar.append(_build_ej_doc(dia_id, ej_idx, ejercicio_data))

            if ejercicios_a_insertar:
                db.rutina_ejercicios.insert_many(ejercicios_a_insertar)
        
        return jsonify({
            "message": "Rutina actualizada exitosamente",
            "rutina": _build_routine_dict(db, rutina_doc)
        }), 200
        
    except Exception as e:
        print(f"Error en update_routine: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_routines_bp.route('/routines/<id>', methods=['DELETE'])
@jwt_required()
@require_tenant
def delete_routine(id):
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        rutina_id = ObjectId(id)
        rutina_doc = db.rutinas.find_one({
            "_id": rutina_id,
            "id_miembro": miembro["_id"]
        })
        
        if not rutina_doc:
            return jsonify({"error": "Rutina no encontrada"}), 404
        
        # Eliminar cascada
        dias_existentes = list(db.rutina_dias.find({"id_rutina": rutina_id}))
        dias_ids = [d["_id"] for d in dias_existentes]
        
        if dias_ids:
            db.rutina_ejercicios.delete_many({"id_rutina_dia": {"$in": dias_ids}})
            db.rutina_dias.delete_many({"id_rutina": rutina_id})
            
        db.rutinas.delete_one({"_id": rutina_id})
        
        return jsonify({"message": "Rutina eliminada exitosamente"}), 200
        
    except Exception as e:
        print(f"Error en delete_routine: {e}")
        return jsonify({"error": str(e)}), 500


@user_routines_bp.route('/routines/<id>/duplicate', methods=['POST'])
@jwt_required()
@require_tenant
def duplicate_routine(id):
    try:
        db         = get_db()
        user_pg_id = int(get_jwt_identity())
        gym_id     = g.tenant_id
        miembro    = db.miembros.find_one({"id_usuario_pg": user_pg_id, "id_gimnasio_pg": gym_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
            
        rutina_id = ObjectId(id)
        rutina_original = db.rutinas.find_one({
            "_id": rutina_id,
            "id_miembro": miembro["_id"]
        })
        
        if not rutina_original:
            return jsonify({"error": "Rutina no encontrada"}), 404
        
        # Crear copia de la rutina
        nueva_rutina = rutina_original.copy()
        del nueva_rutina["_id"] # Eliminar el ID original
        nueva_rutina["nombre"] = f"Copia de {rutina_original.get('nombre', '')}"
        nueva_rutina["activa"] = False
        nueva_rutina["fecha_creacion"] = datetime.now()
        nueva_rutina["fecha_actualizacion"] = datetime.now()
        
        nuevo_id_rutina = db.rutinas.insert_one(nueva_rutina).inserted_id
        nueva_rutina["_id"] = nuevo_id_rutina
        
        # Copiar días y ejercicios
        dias_originales = list(db.rutina_dias.find({"id_rutina": rutina_id}))
        
        for dia in dias_originales:
            ejercicios_originales = list(db.rutina_ejercicios.find({"id_rutina_dia": dia["_id"]}))
            
            nuevo_dia = dia.copy()
            del nuevo_dia["_id"]
            nuevo_dia["id_rutina"] = nuevo_id_rutina
            
            nuevo_id_dia = db.rutina_dias.insert_one(nuevo_dia).inserted_id

            ejercicios_a_insertar = []
            for ej in ejercicios_originales:
                nuevo_ej = ej.copy()
                del nuevo_ej["_id"]
                nuevo_ej["id_rutina_dia"] = nuevo_id_dia
                ejercicios_a_insertar.append(nuevo_ej)
                
            if ejercicios_a_insertar:
                db.rutina_ejercicios.insert_many(ejercicios_a_insertar)
        
        return jsonify({
            "message": "Rutina duplicada exitosamente",
            "rutina": _build_routine_dict(db, nueva_rutina)
        }), 201
        
    except Exception as e:
        print(f"Error en duplicate_routine: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
