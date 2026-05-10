from flask import Blueprint, jsonify
from datetime import datetime, timedelta
from app.mongo import get_db

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/dashboard/kpis', methods=['GET'])
def get_dashboard_kpis():
    try:
        db = get_db()
        now = datetime.now()
        
        # 1. MIEMBROS ACTIVOS
        active_members = db.miembros.count_documents({'estado': 'Activo'})
        
        # 2. INGRESOS DEL MES ACTUAL
        # Construimos las fechas de inicio y fin del mes actual
        start_of_month = datetime(now.year, now.month, 1)
        if now.month == 12:
            start_of_next_month = datetime(now.year + 1, 1, 1)
        else:
            start_of_next_month = datetime(now.year, now.month + 1, 1)

        # Usamos el pipeline de agregación para sumar el monto
        current_month_pipeline = [
            {"$match": {"fecha_pago": {"$gte": start_of_month, "$lt": start_of_next_month}}},
            {"$group": {"_id": None, "total": {"$sum": "$monto"}}}
        ]
        
        current_month_result = list(db.pagos.aggregate(current_month_pipeline))
        current_month_income = current_month_result[0]['total'] if current_month_result else 0

        # 3. INGRESOS ÚLTIMOS 6 MESES
        six_months_ago = now - timedelta(days=180)
        
        six_months_pipeline = [
            {"$match": {"fecha_pago": {"$gte": six_months_ago}}},
            {"$group": {
                "_id": {"$month": "$fecha_pago"}, # Agrupamos por el mes extraído de la fecha
                "total": {"$sum": "$monto"}
            }}
        ]
        
        income_query = list(db.pagos.aggregate(six_months_pipeline))

        # Formatear para el gráfico
        revenue_map = {row['_id']: float(row['total']) for row in income_query}
        kpi_revenue = []
        
        # Generar los últimos 6 meses en orden
        for i in range(5, -1, -1):
            date_cursor = now - timedelta(days=i*30) 
            mes_num = date_cursor.month
            kpi_revenue.append(revenue_map.get(mes_num, 0.0))

        return jsonify({
            'active_members': active_members,
            'monthly_revenue': float(current_month_income),
            'revenue_6_months': kpi_revenue
        }), 200

    except Exception as e:
        print(f"Error en dashboard: {e}")
        return jsonify({'error': str(e)}), 500