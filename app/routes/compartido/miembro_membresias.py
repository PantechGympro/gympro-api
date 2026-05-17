from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from app.mongo import get_db

miembro_membresias_bp = Blueprint('miembro_membresias', __name__)

@miembro_membresias_bp.route('/miembro-membresias/expiran', methods=['GET'])
# @jwt_required()  # Descomenta si necesitas seguridad
def membresias_por_expirar():
    try:
        db = get_db()
        # Obtener parámetros (por defecto 7 días)
        dias = request.args.get('dias', default=7, type=int)
        
        # En MongoDB es mejor usar datetime completo para comparaciones
        hoy = datetime.now()
        # Reseteamos horas, minutos, segundos a 0 para comparación justa
        inicio_hoy = datetime(hoy.year, hoy.month, hoy.day)
        fecha_limite = inicio_hoy + timedelta(days=dias)
        
        # Consultar DB: Membresías activas que vencen en el rango
        resultados = list(db.miembro_membresia.find({
            "fecha_fin": {"$gte": inicio_hoy, "$lte": fecha_limite},
            "estado": "Activa"
        }))

        data = []
        for mm in resultados:
            # Obtener nombre con seguridad simulando las relaciones
            nombre_miembro = "Desconocido"
            if "id_miembro" in mm:
                miembro_doc = db.miembros.find_one({"_id": mm["id_miembro"]})
                if miembro_doc and "id_usuario" in miembro_doc:
                    usuario_doc = db.usuarios.find_one({"_id": miembro_doc["id_usuario"]})
                    if usuario_doc and "nombre" in usuario_doc:
                        nombre_miembro = usuario_doc["nombre"]
            
            # Obtener nombre de la membresía (PG primero, legacy Mongo como fallback)
            nombre_plan = "N/A"
            if "id_membresia" in mm:
                id_mem = mm["id_membresia"]
                try:
                    from app.models.pg.tipo_membresia import TipoMembresia
                    tm = TipoMembresia.query.get(int(id_mem))
                    if tm:
                        nombre_plan = tm.nombre
                except (TypeError, ValueError, Exception):
                    membresia_doc = db.membresias.find_one({"_id": id_mem})
                    if membresia_doc:
                        nombre_plan = membresia_doc.get("nombre", "N/A")
            
            # Calcular estado de urgencia
            # Asegurarnos de que fecha_fin sea datetime para la resta
            fecha_fin_dt = mm["fecha_fin"]
            if isinstance(fecha_fin_dt, str):
                 fecha_fin_dt = datetime.strptime(fecha_fin_dt, '%Y-%m-%d')
            
            dias_restantes = (fecha_fin_dt - inicio_hoy).days
            status = 'urgent' if dias_restantes <= 3 else 'warning'

            data.append({
                "id": str(mm["_id"]),
                "miembro": nombre_miembro,
                "plan": nombre_plan,
                "fecha_fin": fecha_fin_dt.strftime('%Y-%m-%d') if isinstance(fecha_fin_dt, datetime) else str(mm["fecha_fin"]),
                "status": status
            })

        return jsonify({"data": data}), 200

    except Exception as e:
        print(f"Error en membresias_por_expirar: {e}")
        return jsonify({"error": str(e)}), 500


@miembro_membresias_bp.route("/miembro/membresia-activa", methods=["GET"])
@jwt_required()
def obtener_membresia_activa():
    """
    Endpoint que verifica si el usuario tiene membresía activa y qué tipo es.
    Retorna el nivel de acceso basado en tu tabla de membresías.
    """
    try:
        db = get_db()
        user_id_str = get_jwt_identity()
        user_id = ObjectId(user_id_str)

        # 🔍 Buscar el miembro asociado al usuario
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({
                "tiene_membresia": False,
                "mensaje": "No se encontró perfil de miembro"
            }), 200

        # 🔍 Buscar membresía activa (ordenamos descendente por fecha_fin y tomamos 1)
        membresia_activa_cursor = db.miembro_membresia.find(
            {
                "id_miembro": miembro["_id"],
                "estado": "Activa"
            }
        ).sort("fecha_fin", -1).limit(1)
        
        membresia_activa_lista = list(membresia_activa_cursor)
        membresia_activa = membresia_activa_lista[0] if membresia_activa_lista else None

        if not membresia_activa or "id_membresia" not in membresia_activa:
            return jsonify({"tiene_membresia": False}), 200

        # ✅ Resolver nombre del plan: PG primero (Sprint 3+), legacy Mongo como fallback
        id_membresia = membresia_activa["id_membresia"]
        nombre_plan = "Desconocido"
        plan_id = None

        try:
            from app.models.pg.tipo_membresia import TipoMembresia
            pg_id = int(id_membresia)
            tm = TipoMembresia.query.get(pg_id)
            if tm:
                nombre_plan = tm.nombre
                plan_id = str(pg_id)
        except (TypeError, ValueError, Exception):
            # Legacy Mongo path
            plan_doc = db.membresias.find_one({"_id": id_membresia})
            if plan_doc:
                nombre_plan = plan_doc.get("nombre", "Desconocido")
                plan_id = str(plan_doc["_id"])

        planes_premium = ["Premium", "VIP"]
        tipo_acceso = "premium" if any(p in nombre_plan for p in planes_premium) else "basico"

        # Manejo seguro de fechas para strftime
        fecha_inicio = membresia_activa.get("fecha_inicio")
        fecha_fin = membresia_activa.get("fecha_fin")

        str_inicio = fecha_inicio.strftime("%Y-%m-%d") if isinstance(fecha_inicio, datetime) else str(fecha_inicio)
        str_fin = fecha_fin.strftime("%Y-%m-%d") if isinstance(fecha_fin, datetime) else str(fecha_fin)

        return jsonify({
            "tiene_membresia": True,
            "membresia": {
                "id": plan_id,
                "nombre": nombre_plan,
                "tipo": tipo_acceso,
                "fecha_inicio": str_inicio,
                "fecha_fin": str_fin,
                "estado": membresia_activa.get("estado")
            }
        }), 200

    except Exception as e:
        print(f"❌ Error en obtener_membresia_activa: {e}")
        return jsonify({"error": str(e)}), 500