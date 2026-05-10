from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from app.mongo import get_db

user_payments_bp = Blueprint('user_payments', __name__)

@user_payments_bp.route('/api/user/payments', methods=['GET'])
@jwt_required()
def get_user_payments():
    """
    Obtiene el historial completo de pagos del usuario autenticado
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        
        miembro = db.miembros.find_one({"id_usuario": user_id})
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404

        pagos = list(db.pagos.find({"id_miembro": miembro["_id"]}).sort("fecha_pago", -1))
        
        # Calcular total pagado mediante agregación (sum)
        pipeline = [
            {"$match": {"id_miembro": miembro["_id"]}},
            {"$group": {"_id": None, "total": {"$sum": "$monto"}}}
        ]
        resultado_suma = list(db.pagos.aggregate(pipeline))
        total_pagado = resultado_suma[0]["total"] if resultado_suma else 0
        
        ultimo_pago = pagos[0] if pagos else None
        
        pagos_formateados = []
        for pago in pagos:
            fecha_p = pago.get("fecha_pago")
            if isinstance(fecha_p, str):
                fecha_p = datetime.strptime(fecha_p[:19], "%Y-%m-%dT%H:%M:%S")
                
            pagos_formateados.append({
                "id": f"PAY-{str(pago['_id'])[-5:].upper()}", # Usamos últimos 5 chars del ObjectId para simular ID
                "date": fecha_p.strftime('%d %b %Y') if isinstance(fecha_p, datetime) else str(fecha_p),
                "concept": pago.get("concepto") or "Pago de membresía",
                "amount": float(pago.get("monto", 0)),
                "method": _format_payment_method(pago.get("metodo_pago")),
                "status": "Completado",
                "rawDate": fecha_p.isoformat() if isinstance(fecha_p, datetime) else str(fecha_p)
            })
        
        # Calcular próximo pago estimado (si tiene membresía activa)
        membresia_activa = db.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado": 'Activa'
        })
        
        proximo_pago = None
        if membresia_activa and membresia_activa.get("fecha_fin"):
            fecha_f = membresia_activa["fecha_fin"]
            if isinstance(fecha_f, str):
                fecha_f = datetime.strptime(fecha_f[:10], "%Y-%m-%d")
            fecha_estimada = fecha_f - timedelta(days=3)
            proximo_pago = fecha_estimada.strftime('%d %b %Y')
        
        up_date = ultimo_pago.get("fecha_pago") if ultimo_pago else None
        if isinstance(up_date, str):
             up_date = datetime.strptime(up_date[:10], "%Y-%m-%d")
             
        return jsonify({
            "stats": {
                "totalPaid": float(total_pagado),
                "lastPayment": up_date.strftime('%d %b %Y') if up_date else "N/A",
                "nextPayment": proximo_pago or "No programado",
                "status": "Al día" if membresia_activa else "Sin membresía"
            },
            "payments": pagos_formateados
        }), 200
        
    except Exception as e:
        print(f"Error en get_user_payments: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _format_payment_method(metodo):
    """Formatea el método de pago para mostrar en el frontend"""
    metodos = {
        "Efectivo": "Efectivo",
        "Tarjeta": "Tarjeta de crédito/débito",
        "Transferencia": "Transferencia bancaria"
    }
    return metodos.get(metodo, metodo)


@user_payments_bp.route('/api/user/payments/stats', methods=['GET'])
@jwt_required()
def get_payment_stats():
    """
    Obtiene estadísticas detalladas de pagos por mes/año
    """
    try:
        db = get_db()
        user_id = ObjectId(get_jwt_identity())
        miembro = db.miembros.find_one({"id_usuario": user_id})
        
        if not miembro:
            return jsonify({"error": "Miembro no encontrado"}), 404
        
        current_year = datetime.now().year
        start_of_year = datetime(current_year, 1, 1)
        end_of_year = datetime(current_year + 1, 1, 1)
        
        # Pipeline de agregación para agrupar por mes y sumar montos
        pipeline = [
            {"$match": {
                "id_miembro": miembro["_id"],
                "fecha_pago": {"$gte": start_of_year, "$lt": end_of_year}
            }},
            {"$group": {
                "_id": {"$month": "$fecha_pago"},
                "total": {"$sum": "$monto"},
                "cantidad": {"$sum": 1}
            }}
        ]
        
        monthly_stats = list(db.pagos.aggregate(pipeline))
        
        # Convertir a diccionario para fácil acceso {mes_numero: stats}
        stats_dict = {doc["_id"]: doc for doc in monthly_stats}
        
        meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        stats_por_mes = []
        
        for i in range(1, 13):
            stat = stats_dict.get(i)
            stats_por_mes.append({
                "mes": meses[i-1],
                "total": float(stat["total"]) if stat else 0,
                "cantidad": int(stat["cantidad"]) if stat else 0
            })
        
        return jsonify({
            "year": current_year,
            "monthly": stats_por_mes
        }), 200
        
    except Exception as e:
        print(f"Error en get_payment_stats: {e}")
        return jsonify({"error": str(e)}), 500