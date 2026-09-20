from pymongo import MongoClient
from spark_config import DB_NAME, MONGO_URI

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
coleccion = db["pagos"]


pipeline = [
    {"$group": {
        "_id": "$metodo_pago", 
        "total_transacciones": {"$sum": 1},  
        "ingresos_totales": {"$sum": "$monto"},  
        "promedio_pago": {"$avg": "$monto"} 
    }},
    {"$sort": {"ingresos_totales": -1}} 
]

resultados = list(coleccion.aggregate(pipeline))

print("\n" + "=" * 40)
print(" RESUMEN DE PAGOS POR MÉTODO")
print("=" * 40)

for r in resultados:
    print(f"Método de Pago: {r['_id']}")
    print(f"  Total de transacciones: {r['total_transacciones']}")
    print(f"  Ingresos totales: ${r['ingresos_totales']:,.2f}")
    print(f"  Promedio por pago: ${r['promedio_pago']:,.2f}")
    print("-" * 40)