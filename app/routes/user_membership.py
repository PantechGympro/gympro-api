from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from app.mongo import get_db

user_membership_bp = Blueprint('user_membership', __name__)


@user_membership_bp.route('/api/user/membership', methods=['GET'])
@jwt_required()
def get_user_membership():
    """
    Obtiene información de la membresía actual del usuario
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        # Buscar membresía activa
        membresia_activa = db.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado": 'Activa'
        })
        
        if not membresia_activa:
            return jsonify({
                "tieneMembresia": False,
                "mensaje": "No tienes una membresía activa"
            }), 200
        
        # Obtener datos de la membresía
        membresia = db.membresias.find_one({"_id": membresia_activa["id_membresia"]})
        
        # Calcular días restantes
        fecha_fin = membresia_activa.get("fecha_fin")
        if isinstance(fecha_fin, str):
            fecha_fin = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
        elif isinstance(fecha_fin, datetime):
            fecha_fin = fecha_fin.date()
            
        dias_restantes = (fecha_fin - datetime.now().date()).days
        
        return jsonify({
            "tieneMembresia": True,
            "membresia": {
                "id": str(membresia_activa["_id"]),
                "nombre": membresia.get("nombre", "N/A") if membresia else "N/A",
                "fechaInicio": membresia_activa.get("fecha_inicio").strftime('%Y-%m-%d') if isinstance(membresia_activa.get("fecha_inicio"), datetime) else str(membresia_activa.get("fecha_inicio")),
                "fechaFin": fecha_fin.strftime('%Y-%m-%d'),
                "diasRestantes": dias_restantes,
                "estado": "activa" if dias_restantes > 0 else "por_vencer",
                "precio": float(membresia.get("precio", 0)) if membresia else 0
            }
        }), 200
        
    except Exception as e:
        print(f"Error en get_user_membership: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/plans', methods=['GET'])
@jwt_required()
def get_available_plans():
    """
    Obtiene los planes de membresía disponibles para renovación
    """
    try:
        db = get_db()
        membresias = list(db.membresias.find({}))
        
        planes = []
        # Buscar el precio de 1 mes base para calcular ahorros
        precio_mensual = 950
        for m in membresias:
            if m.get("duracion_meses") == 1:
                precio_mensual = float(m.get("precio", 950))
                break
                
        for mem in membresias:
            duracion = int(mem.get("duracion_meses", 1))
            precio = float(mem.get("precio", 0))
            
            ahorro = (precio_mensual * duracion) - precio if duracion > 1 else 0
            
            plan_id = "monthly" if duracion == 1 else "quarterly" if duracion == 3 else "annual"
            
            planes.append({
                "id": plan_id,
                "id_membresia": str(mem["_id"]),
                "nombre": mem.get("nombre"),
                "duracion_meses": duracion,
                "precio": precio,
                "ahorro": max(0, float(ahorro))
            })
        
        return jsonify({"planes": planes}), 200
        
    except Exception as e:
        print(f"Error en get_available_plans: {e}")
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/renew', methods=['POST'])
@jwt_required()
def renew_membership():
    """
    Procesa la renovación de membresía
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        data = request.json
        id_membresia_str = data.get('id_membresia')
        metodo_pago = data.get('metodo_pago', 'Tarjeta') 
        
        if not id_membresia_str:
            return jsonify({"error": "ID de membresía requerido"}), 400
            
        metodos_validos = ['Efectivo', 'Tarjeta', 'Transferencia']
        if metodo_pago not in metodos_validos:
            return jsonify({"error": f"Método de pago inválido. Usar: {', '.join(metodos_validos)}"}), 400
        
        membresia = db.membresias.find_one({"_id": ObjectId(id_membresia_str)})
        if not membresia:
            return jsonify({"error": "Membresía no encontrada"}), 404
        
        membresia_activa = db.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado": 'Activa'
        })
        
        fecha_inicio = datetime.now()
        
        if membresia_activa:
            f_fin_activa = membresia_activa.get("fecha_fin")
            if isinstance(f_fin_activa, str):
                f_fin_activa = datetime.strptime(f_fin_activa[:10], "%Y-%m-%d")
                
            if f_fin_activa > fecha_inicio:
                fecha_inicio = f_fin_activa + timedelta(days=1)
            
            # Cambiar estado de la anterior a Vencida
            db.miembro_membresia.update_one(
                {"_id": membresia_activa["_id"]},
                {"$set": {"estado": "Vencida"}}
            )
        
        duracion = int(membresia.get("duracion_meses", 1))
        fecha_fin = fecha_inicio + timedelta(days=duracion * 30)
        
        # Crear nueva membresía
        db.miembro_membresia.insert_one({
            "id_miembro": miembro["_id"],
            "id_membresia": membresia["_id"],
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "estado": 'Activa'
        })
        
        # Registrar pago
        db.pagos.insert_one({
            "id_miembro": miembro["_id"],
            "monto": float(membresia.get("precio", 0)),
            "metodo_pago": metodo_pago,
            "concepto": f"Renovación {membresia.get('nombre')}",
            "fecha_pago": datetime.now()
        })
        
        return jsonify({
            "message": "Membresía renovada exitosamente",
            "membresia": {
                "nombre": membresia.get("nombre"),
                "fechaInicio": fecha_inicio.strftime('%Y-%m-%d'),
                "fechaFin": fecha_fin.strftime('%Y-%m-%d'),
                "monto": float(membresia.get("precio", 0))
            }
        }), 201
        
    except Exception as e:
        print(f"Error en renew_membership: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_membership_bp.route('/api/user/membership/payment-methods', methods=['GET'])
@jwt_required()
def get_payment_methods():
    """
    Obtiene los métodos de pago guardados del usuario
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        pagos_recientes = list(db.pagos.find({
            "id_miembro": miembro["_id"]
        }).sort("fecha_pago", -1).limit(5))
        
        metodos_vistos = set()
        metodos = []
        
        for idx, pago in enumerate(pagos_recientes):
            metodo = pago.get("metodo_pago")
            if metodo and metodo not in metodos_vistos:
                metodos_vistos.add(metodo)
                
                if metodo == 'Tarjeta':
                    numero_display = "**** **** **** 4242"
                elif metodo == 'Transferencia':
                    numero_display = "Cuenta bancaria"
                else:
                    numero_display = metodo
                
                metodos.append({
                    "id": idx + 1,
                    "tipo": metodo,
                    "numero": numero_display,
                    "principal": len(metodos) == 0 # El primero es el principal
                })
        
        return jsonify({"metodos": metodos}), 200
        
    except Exception as e:
        print(f"Error en get_payment_methods: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500