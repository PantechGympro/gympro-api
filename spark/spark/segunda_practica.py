from pymongo import MongoClient
from spark_config import DB_NAME, MONGO_URI
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
coleccion_progreso = db["progreso_fisico"]

pipeline_peso_perdido = [
    {"$sort": {"id_miembro": 1, "fecha_registro": 1}},
    {
        "$group": {
            "_id": "$id_miembro",
            "peso_inicial": {"$first": "$peso"}, 
            "peso_actual": {"$last": "$peso"},  
            "total_registros": {"$sum": 1}
        }
    },
    {"$match": {"total_registros": {"$gt": 1}}},
    {
        "$project": {
            "peso_perdido": {
                "$subtract": ["$peso_inicial", "$peso_actual"]
            }
        }
    },
    {
        "$group": {
            "_id": None,
            "total_miembros_evaluados": {"$sum": 1},
            "promedio_peso_perdido": {"$avg": "$peso_perdido"}
        }
    },
    {
        "$project": {
            "_id": 0, 
            "total_miembros_evaluados": 1,
            "promedio_peso_perdido": {"$round": ["$promedio_peso_perdido", 2]}
        }
    }
]

resultados = list(coleccion_progreso.aggregate(pipeline_peso_perdido))

print("\n" + "=" * 55)
print("  REPORTE")
print("=" * 55)

if resultados:
    data = resultados[0] 
    promedio = data['promedio_peso_perdido']
    
    print(f"Total de miembros evaluados: {data['total_miembros_evaluados']}")
    
    if promedio > 0:
        print(f"¡Los miembros han perdido un promedio de {promedio} kg! en los ultimos 6 meses ")
    elif promedio < 0:
        print(f"Nota: En promedio, los miembros han ganado {abs(promedio)} kg. (¿Fase de volumen? )")
    else:
        print("El peso promedio de los miembros se ha mantenido exactamente igual.")
else:
    print("No hay suficientes datos de progreso (mínimo 2 registros por miembro) para calcular el promedio.")

print("-" * 55)