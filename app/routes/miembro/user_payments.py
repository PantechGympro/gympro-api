from flask import Blueprint, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta

from app.mongo import get_db
from app.utils.tenant import require_tenant

user_payments_bp = Blueprint('user_payments', __name__)


@user_payments_bp.route('/api/user/payments', methods=['GET'])
@jwt_required()
@require_tenant
def get_user_payments():
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

        pagos = list(mdb.pagos.find({"id_miembro": miembro["_id"]}).sort("fecha_pago", -1))

        pipeline = [
            {"$match": {"id_miembro": miembro["_id"]}},
            {"$group": {"_id": None, "total": {"$sum": "$monto"}}}
        ]
        resultado_suma = list(mdb.pagos.aggregate(pipeline))
        total_pagado   = resultado_suma[0]["total"] if resultado_suma else 0

        ultimo_pago = pagos[0] if pagos else None

        pagos_formateados = []
        for pago in pagos:
            fecha_p = pago.get("fecha_pago")
            if isinstance(fecha_p, str):
                try:    fecha_p = datetime.strptime(fecha_p[:19], "%Y-%m-%dT%H:%M:%S")
                except: fecha_p = datetime.strptime(fecha_p[:10], "%Y-%m-%d")
            pagos_formateados.append({
                "id":      f"PAY-{str(pago['_id'])[-5:].upper()}",
                "date":    fecha_p.strftime('%d %b %Y') if isinstance(fecha_p, datetime) else str(fecha_p),
                "concept": pago.get("concepto") or "Pago de membresía",
                "amount":  float(pago.get("monto", 0)),
                "method":  _format_payment_method(pago.get("metodo_pago")),
                "status":  "Completado",
                "rawDate": fecha_p.isoformat() if isinstance(fecha_p, datetime) else str(fecha_p)
            })

        membresia_activa = mdb.miembro_membresia.find_one({
            "id_miembro": miembro["_id"],
            "estado":     "Activa"
        })
        proximo_pago = None
        if membresia_activa and membresia_activa.get("fecha_fin"):
            fecha_f = membresia_activa["fecha_fin"]
            if isinstance(fecha_f, str):
                fecha_f = datetime.strptime(fecha_f[:10], "%Y-%m-%d")
            proximo_pago = (fecha_f - timedelta(days=3)).strftime('%d %b %Y')

        up_date = ultimo_pago.get("fecha_pago") if ultimo_pago else None
        if isinstance(up_date, str):
            try:    up_date = datetime.strptime(up_date[:10], "%Y-%m-%d")
            except: up_date = None

        return jsonify({
            "stats": {
                "totalPaid":   float(total_pagado),
                "lastPayment": up_date.strftime('%d %b %Y') if up_date else "N/A",
                "nextPayment": proximo_pago or "No programado",
                "status":      "Al día" if membresia_activa else "Sin membresía"
            },
            "payments": pagos_formateados
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@user_payments_bp.route('/api/user/payments/stats', methods=['GET'])
@jwt_required()
@require_tenant
def get_payment_stats():
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

        current_year  = datetime.now().year
        start_of_year = datetime(current_year, 1, 1)
        end_of_year   = datetime(current_year + 1, 1, 1)

        pipeline = [
            {"$match": {
                "id_miembro": miembro["_id"],
                "fecha_pago": {"$gte": start_of_year, "$lt": end_of_year}
            }},
            {"$group": {
                "_id":      {"$month": "$fecha_pago"},
                "total":    {"$sum": "$monto"},
                "cantidad": {"$sum": 1}
            }}
        ]
        monthly_stats = list(mdb.pagos.aggregate(pipeline))
        stats_dict    = {doc["_id"]: doc for doc in monthly_stats}

        meses = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
        return jsonify({
            "year": current_year,
            "monthly": [
                {
                    "mes":      meses[i - 1],
                    "total":    float(stats_dict[i]["total"])    if i in stats_dict else 0,
                    "cantidad": int(stats_dict[i]["cantidad"]) if i in stats_dict else 0
                }
                for i in range(1, 13)
            ]
        }), 200

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _format_payment_method(metodo):
    return {
        "Efectivo":      "Efectivo",
        "Tarjeta":       "Tarjeta de crédito/débito",
        "Transferencia": "Transferencia bancaria"
    }.get(metodo, metodo or "")
