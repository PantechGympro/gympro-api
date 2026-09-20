from flask import Blueprint, jsonify
from datetime import datetime
from flask_jwt_extended import jwt_required
from app.mongo import get_db
from app.utils.tenant import get_tenant_filter

dashboard_bp = Blueprint('dashboard', __name__)


def _prev_months(now: datetime, n: int):
    """Devuelve lista de (year, month) de los últimos n meses en orden ascendente."""
    months = []
    year, month = now.year, now.month
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(months))


@dashboard_bp.route('/dashboard/kpis', methods=['GET'])
@jwt_required()
def get_dashboard_kpis():
    try:
        db  = get_db()
        now = datetime.now()
        tenant_filter = get_tenant_filter()

        # Filtros por colección — miembros usa id_gimnasio_pg, pagos usa id_gimnasio
        miembros_filter  = {"estado": "Activo"}
        pagos_base_match = {}
        if tenant_filter:
            miembros_filter["id_gimnasio_pg"] = tenant_filter["id_gimnasio"]
            pagos_base_match["id_gimnasio"]   = tenant_filter["id_gimnasio"]

        # 1. MIEMBROS ACTIVOS (filtrados por gimnasio)
        active_members = db.miembros.count_documents(miembros_filter)

        # 2. INGRESOS DEL MES ACTUAL
        # $toDate normaliza tanto strings ISO "YYYY-MM-DD" como objetos ISODate
        # garantizando que el filtro funcione independientemente de cómo se almacenó la fecha
        start_of_month      = datetime(now.year, now.month, 1)
        next_month          = now.month % 12 + 1
        next_month_year     = now.year + (1 if now.month == 12 else 0)
        start_of_next_month = datetime(next_month_year, next_month, 1)

        current_month_pipeline = [
            {"$match": pagos_base_match},
            {"$addFields": {"fecha_dt": {"$toDate": "$fecha_pago"}}},
            {"$match": {"fecha_dt": {"$gte": start_of_month, "$lt": start_of_next_month}}},
            {"$group": {"_id": None, "total": {"$sum": "$monto"}}},
        ]
        result = list(db.pagos.aggregate(current_month_pipeline))
        current_month_income = result[0]["total"] if result else 0

        # 3. INGRESOS ÚLTIMOS 6 MESES
        # Indexado por (año, mes) para evitar colisión cuando los meses cruzan año calendario
        last_6  = _prev_months(now, 6)
        oldest  = datetime(last_6[0][0], last_6[0][1], 1)

        six_months_pipeline = [
            {"$match": pagos_base_match},
            {"$addFields": {"fecha_dt": {"$toDate": "$fecha_pago"}}},
            {"$match": {"fecha_dt": {"$gte": oldest}}},
            {"$group": {
                "_id": {
                    "year":  {"$year":  "$fecha_dt"},
                    "month": {"$month": "$fecha_dt"},
                },
                "total": {"$sum": "$monto"},
            }},
        ]
        income_query = list(db.pagos.aggregate(six_months_pipeline))
        revenue_map  = {
            (row["_id"]["year"], row["_id"]["month"]): float(row["total"])
            for row in income_query
        }
        kpi_revenue = [revenue_map.get(ym, 0.0) for ym in last_6]

        return jsonify({
            "active_members":   active_members,
            "monthly_revenue":  float(current_month_income),
            "revenue_6_months": kpi_revenue,
        }), 200

    except Exception as e:
        print(f"Error en dashboard: {e}")
        return jsonify({"error": str(e)}), 500