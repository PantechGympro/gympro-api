import os
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
from faker import Faker
import random
from datetime import datetime, timedelta

load_dotenv()

# ──────────────────────────────────────────────
# CONEXIÓN
# ──────────────────────────────────────────────
def get_db():
    user     = os.getenv("MONGO_USER")
    password = os.getenv("MONGO_PASSWORD")
    cluster  = os.getenv("MONGO_CLUSTER")
    DB_NAME  = os.getenv("MONGO_DB")

    missing = [k for k, v in {
        "MONGO_USER": user, "MONGO_PASSWORD": password,
        "MONGO_CLUSTER": cluster, "MONGO_DB": DB_NAME
    }.items() if not v]

    if missing:
        raise EnvironmentError(
            f"❌ Variables de entorno faltantes: {', '.join(missing)}"
        )

    uri = f"mongodb+srv://{user}:{password}@{cluster}/{DB_NAME}?retryWrites=true&w=majority"
    client = MongoClient(uri)
    client.admin.command("ping")
    print(f"   🔗 Conectado → {cluster} / {DB_NAME}")
    return client[DB_NAME]

fake = Faker("es_MX")

MESES_ACTIVIDAD = 6

zazaza = (
    "scrypt:32768:8:1$U1umhxfH8LDLojFb$"
    "7c6b302a3cdc30296572691480bfcad86209846fe"
    "6896a4f69c0f22caf58e23a85d8396fdce945a45f"
    "ad84d8b27682c9e9f6919a4d7481bd912aa2d001470309"
)

# ══════════════════════════════════════════════
# LIMPIAR DATOS DE PRUEBA (no toca catálogos)
# ══════════════════════════════════════════════
COLECCIONES_DATOS = [
    "usuarios", "miembros", "asistencias", "pagos", "progreso_fisico",
    "rutinas", "rutina_dias", "rutina_ejercicios",
    "miembro_membresia", "sesiones", "ventas", "detalle_venta",
    "correos_enviados", "correo_miembro",
    "planes_alimenticios", "plan_recetas",
    "perfil_entrenador", "certificaciones_entrenador",
    "logros_entrenador", "evaluaciones_entrenador", "miembro_rutina",
]

def limpiar_datos(db):
    print("🧹 Limpiando datos de prueba anteriores...")
    for col in COLECCIONES_DATOS:
        db[col].delete_many({})
    print("   ✅ Limpieza completada")

def obtener_catalogo(db):
    roles       = {r["nombre"]: r["_id"] for r in db.roles.find()}
    membresias  = list(db.membresias.find())
    productos   = list(db.productos.find())
    tipos_dieta = {t["nombre"]: t["_id"] for t in db.tipos_dieta.find()}
    recetas     = list(db.recetas.find())

    if not roles or not membresias or not tipos_dieta or not recetas:
        raise RuntimeError(
            "❌ Catálogos vacíos. Ejecuta primero tu setup de catálogos."
        )

    return roles, membresias, productos, tipos_dieta, recetas

# ══════════════════════════════════════════════
# 1. USUARIOS Y ROLES (Entrenadores Múltiples)
# ══════════════════════════════════════════════
def crear_admin(db, id_role):
    print("👨‍💼 Creando administrador...")
    doc = {
        "id_role":        id_role,
        "nombre":         "Carlos Admin",
        "email":          "admin@gym.com",
        "password":       zazaza,
        "activo":         True,
        "fecha_creacion": datetime.now() - timedelta(days=365),
    }
    db.usuarios.insert_one(doc)
    print("   ✅ admin@gym.com")
    return doc["_id"]

def crear_recepcionista(db, id_role):
    print("👩‍💼 Creando recepcionista...")
    doc = {
        "id_role":        id_role,
        "nombre":         "Ana Recepcionista",
        "email":          "recepcion@gym.com",
        "password":       zazaza,
        "activo":         True,
        "fecha_creacion": datetime.now() - timedelta(days=400),
    }
    db.usuarios.insert_one(doc)
    print("   ✅ recepcion@gym.com")
    return doc["_id"]

def crear_entrenadores(db, id_role, cantidad=4):
    print(f"👨‍🏫 Creando {cantidad} entrenadores...")
    ids_entrenadores = []
    
    for i in range(1, cantidad + 1):
        email = f"entrenador{i}@gym.com"
        doc = {
            "id_role":        id_role,
            "nombre":         fake.name(),
            "email":          email,
            "password":       zazaza,
            "activo":         True,
            "fecha_creacion": datetime.now() - timedelta(days=400),
        }
        db.usuarios.insert_one(doc)
        id_entrenador = doc["_id"]
        
        db.perfil_entrenador.insert_one({
            "id_entrenador":      id_entrenador,
            "telefono":           fake.phone_number()[:20],
            "direccion":          None,
            "especializacion":    random.choice(["Hipertrofia", "Fuerza", "Pérdida de Grasa", "Funcional"]),
            "biografia":          "Entrenador certificado con amplia experiencia.",
            "redes_sociales":     f'{{"instagram":"@{doc["nombre"].split()[0].lower()}_trainer"}}',
            "fecha_creacion":     datetime.now() - timedelta(days=400),
            "fecha_actualizacion":datetime.now(),
        })
        ids_entrenadores.append(id_entrenador)
        print(f"   ✅ {email}")
        
    return ids_entrenadores

# ══════════════════════════════════════════════
# RUTINAS COMPARTIDAS (Pool por Entrenador Máx 6)
# ══════════════════════════════════════════════
OBJETIVOS_RUTINA = {
    "Hipercalórica":   "Aumento de masa muscular",
    "Déficit Calórico":"Pérdida de grasa y tonificación",
    "Vegana":          "Mantenimiento y fuerza",
    "Mediterránea":    "Salud general y resistencia",
    "Keto":            "Definición muscular",
    "Paleo":           "Fuerza funcional",
    "Flexible":        "Mejora general fitness",
}

DIAS_POR_TIPO = {
    "Hipercalórica": [
        ("Lunes",    "Pecho y Tríceps",    [("Press Banca","4","8-10","80kg"),("Press Inclinado","4","10-12","60kg")]),
        ("Martes",   "Espalda y Bíceps",   [("Dominadas","4","8-10","Peso corporal"),("Remo con Barra","4","8-10","70kg")]),
        ("Miércoles","Pierna",             [("Sentadilla","4","8-10","100kg"),("Peso Muerto","4","6-8","120kg")]),
    ],
    "Déficit Calórico": [
        ("Lunes",   "Tren Superior 1",     [("Press Banca","3","12-15","50kg"),("Remo con Barra","3","12-15","50kg")]),
        ("Martes",  "Tren Inferior",       [("Sentadilla","4","15-20","60kg"),("Zancadas","3","15-20","20kg")]),
        ("Jueves",  "Cardio Funcional",    [("Burpees","3","15","Peso corporal"),("Kettlebell Swings","3","20","16kg")]),
    ],
    "_default": [
        ("Lunes",    "Pecho y Tríceps",    [("Press Banca","4","10-12","60kg"),("Fondos","3","10-12","Peso corporal")]),
        ("Miércoles","Espalda y Bíceps",   [("Dominadas","4","8-10","Peso corporal"),("Remo con Barra","4","10-12","60kg")]),
        ("Viernes",  "Pierna",             [("Sentadilla","4","10-12","80kg"),("Peso Muerto","4","8-10","100kg")]),
    ],
}

def pregenerar_rutinas_entrenadores(db, entrenadores_ids):
    print("\n📋 Generando máximo 6 rutinas base por entrenador...")
    pool_rutinas = {}
    tipos_base = list(OBJETIVOS_RUTINA.keys())[:6] # Seleccionamos máximo 6 objetivos
    
    for id_ent in entrenadores_ids:
        pool_rutinas[id_ent] = []
        for tipo in tipos_base:
            rutina_doc = {
                "id_miembro":         None, # Rutina plantilla
                "id_entrenador":      id_ent,
                "nombre":             f"Rutina {tipo.split()[0]} - {random.choice(['Intensa', 'Básica', 'Pro'])}",
                "objetivo":           OBJETIVOS_RUTINA[tipo],
                "activa":             True,
                "fecha_creacion":     datetime.now() - timedelta(days=180),
                "fecha_actualizacion":datetime.now(),
                "dificultad":         random.choice(["Principiante", "Intermedio", "Avanzado"]),
                "duracion_minutos":   random.choice([45, 60, 90]),
            }
            db.rutinas.insert_one(rutina_doc)
            id_rutina = rutina_doc["_id"]
            
            dias = DIAS_POR_TIPO.get(tipo, DIAS_POR_TIPO["_default"])
            for orden_dia, (dia, grupo, ejercicios) in enumerate(dias, 1):
                dia_doc = {
                    "id_rutina":      id_rutina,
                    "dia_semana":     dia,
                    "grupo_muscular": grupo,
                    "orden":          orden_dia,
                }
                db.rutina_dias.insert_one(dia_doc)
                id_rutina_dia = dia_doc["_id"]

                ej_docs = [
                    {
                        "id_rutina_dia":    id_rutina_dia,
                        "nombre_ejercicio": ej[0],
                        "series":           ej[1],
                        "repeticiones":     ej[2],
                        "peso":             ej[3],
                        "orden":            ej_orden,
                    }
                    for ej_orden, ej in enumerate(ejercicios, 1)
                ]
                db.rutina_ejercicios.insert_many(ej_docs)
            
            pool_rutinas[id_ent].append(id_rutina)
    
    print("   ✅ Rutinas generadas exitosamente")
    return pool_rutinas

# ══════════════════════════════════════════════
# 2. MIEMBROS (Asignación balanceada MÁX 15)
# ══════════════════════════════════════════════
def crear_miembros(db, id_role_miembro, entrenadores_ids, pool_rutinas, membresias, tipos_dieta, recetas, num_usuarios=50):
    print(f"\n👥 Creando {num_usuarios} miembros con asignación máxima de 15 por entrenador...")
    now        = datetime.now()
    fecha_base = now - timedelta(days=MESES_ACTIVIDAD * 30)
    memb_premium = next(m for m in membresias if m["nombre"] == "Premium Mensual")

    ids_miembros = []
    tipos_dieta_keys = list(OBJETIVOS_RUTINA.keys())
    
    # Diccionario para controlar la carga de los entrenadores
    carga_entrenadores = {eid: 0 for eid in entrenadores_ids}

    for i in range(1, num_usuarios + 1):
        # Seleccionar entrenador que tenga menos de 15 asignados
        entrenadores_disponibles = [eid for eid, count in carga_entrenadores.items() if count < 15]
        if not entrenadores_disponibles:
            raise Exception("❌ No hay capacidad suficiente en los entrenadores (Máx 15 por entrenador). Crea más entrenadores.")
        
        id_entrenador = random.choice(entrenadores_disponibles)
        carga_entrenadores[id_entrenador] += 1

        sexo = random.choice(["M", "F"])
        nombre = fake.name_male() if sexo == "M" else fake.name_female()
        email = f"miembro{i}@gym.com" if i <= 5 else fake.unique.email()

        estatura = round(random.uniform(1.65, 1.95) if sexo == "M" else random.uniform(1.50, 1.80), 2)
        peso = round(random.uniform(65.0, 115.0) if sexo == "M" else random.uniform(50.0, 90.0), 1)

        tipo_dieta_nombre = random.choice(tipos_dieta_keys)
        objetivo = OBJETIVOS_RUTINA.get(tipo_dieta_nombre, "Mejora general")
        fecha_registro = fecha_base + timedelta(days=random.randint(0, 15))

        # ── usuarios ───────────────────────────────────────────────────────
        u_doc = {
            "id_role":        id_role_miembro,
            "nombre":         nombre,
            "email":          email,
            "password":       zazaza,
            "activo":         True,
            "fecha_creacion": fecha_registro,
        }
        db.usuarios.insert_one(u_doc)
        id_usuario = u_doc["_id"]

        # ── miembros ───────────────────────────────────────────────────────
        m_doc = {
            "id_usuario":              id_usuario,
            "id_entrenador":           id_entrenador,
            "telefono":                fake.phone_number()[:20],
            "fecha_nacimiento":        datetime(1995, 5, 20),
            "sexo":                    sexo,
            "peso_inicial":            peso,
            "estatura":                estatura,
            "fecha_registro":          fecha_registro,
            "estado":                  "Activo",
            "objetivo":                objetivo,
            "fecha_asignacion":        fecha_registro,
        }
        db.miembros.insert_one(m_doc)
        id_miembro = m_doc["_id"]

        # ── membresía activa ──────────────────────────────────────────────
        db.miembro_membresia.insert_one({
            "id_miembro":   id_miembro,
            "id_membresia": memb_premium["_id"],
            "fecha_inicio": fecha_registro,
            "fecha_fin":    now + timedelta(days=90),
            "estado":       "Activa",
        })

        # ── Asignación de Rutina (del Pool del entrenador) ────────────────
        id_rutina_asignada = random.choice(pool_rutinas[id_entrenador])
        db.miembro_rutina.insert_one({
            "id_miembro":       id_miembro,
            "id_rutina":        id_rutina_asignada,
            "fecha_asignacion": fecha_registro,
            "activa":           True,
            "fecha_fin":        None,
        })

        ids_miembros.append({"id": id_miembro, "id_entrenador": id_entrenador})
        
        if i % 25 == 0:
            print(f"   ⏳ Creados {i}/{num_usuarios} miembros...")

    # Imprimir distribución final
    print("   📊 Distribución de miembros por entrenador:")
    for eid, num in carga_entrenadores.items():
        print(f"      - Entrenador {eid}: {num} miembros")

    return ids_miembros

# ══════════════════════════════════════════════
# SESIONES (Máximo 10 por día por entrenador)
# ══════════════════════════════════════════════
def generar_sesiones(db, entrenadores_ids, miembros_data):
    print("\n📅 Generando sesiones de entrenamiento (Máximo 10 por día/entrenador)...")
    now    = datetime.now()
    inicio = now - timedelta(days=MESES_ACTIVIDAD * 30)
    ubi    = ["Sala Principal", "Box", "Exterior"]
    
    sesiones = []
    dias_pasados = (now - inicio).days

    for dias_atras in range(dias_pasados, -1, -1):
        fecha = now - timedelta(days=dias_atras)
        es_hoy = (dias_atras == 0)

        for id_ent in entrenadores_ids:
            # Obtener miembros de este entrenador
            miembros_del_entrenador = [m["id"] for m in miembros_data if m["id_entrenador"] == id_ent]
            if not miembros_del_entrenador:
                continue
            
            # Máximo 10 sesiones por día para no sobrecargar
            num_sesiones = random.randint(2, 10) 
            
            for _ in range(num_sesiones):
                if es_hoy:
                    estado = random.choice(["completed","in-progress","scheduled"])
                else:
                    estado = random.choices(["completed","cancelled"], weights=[0.9, 0.1])[0]

                sesiones.append({
                    "id_entrenador":      id_ent,
                    "id_miembro":         random.choice(miembros_del_entrenador),
                    "id_rutina":          None,
                    "fecha":              fecha,
                    "hora_inicio":        f"{random.randint(6,20):02d}:{random.choice([0,15,30,45]):02d}:00",
                    "duracion_minutos":   random.choice([45, 60]),
                    "tipo":               "Personal",
                    "ubicacion":          random.choice(ubi),
                    "estado":             estado,
                    "asistencia":         estado in ("completed", "in-progress"),
                    "fecha_creacion":     fecha,
                })

    for i in range(0, len(sesiones), 2000):
        db.sesiones.insert_many(sesiones[i:i+2000])

    print(f"   ✅ {len(sesiones):,} sesiones insertadas respetando límites")

# ══════════════════════════════════════════════
# 5. PROGRESO, ASISTENCIAS Y PAGOS (Vital para ML y MapReduce)
# ══════════════════════════════════════════════
def generar_progreso_asistencias_pagos(db):
    print("\n📈 Generando historial de progreso, asistencias y pagos...")
    miembros = list(db.miembros.find())
    now = datetime.now()
    
    progresos, asistencias, pagos = [], [], []
    metodos_pago = ["Efectivo", "Tarjeta", "Transferencia"]
    
    for m in miembros:
        id_m = m["_id"]
        fecha_reg = m["fecha_registro"]
        peso_actual = m["peso_inicial"]
        estatura = m["estatura"]
        id_entrenador = m["id_entrenador"]
        
        meses_activos = (now.year - fecha_reg.year) * 12 + now.month - fecha_reg.month
        if meses_activos < 1: meses_activos = 1
        
        # Generar un registro por cada mes activo
        for mes in range(meses_activos + 1):
            fecha_hito = fecha_reg + timedelta(days=30 * mes)
            if fecha_hito > now: break
            
            # Variación de peso y métricas (simulando progreso)
            peso_actual += random.uniform(-1.5, 0.5) 
            bmi = round(peso_actual / (estatura ** 2), 2)
            grasa = round(random.uniform(15.0, 30.0) - (mes * 0.2), 1)
            musculo = round(random.uniform(25.0, 45.0) + (mes * 0.1), 1)
            cintura = round(random.uniform(70.0, 100.0) - (mes * 0.5), 1)
            
            progresos.append({
                "id_miembro": id_m,
                "peso": round(peso_actual, 1),
                "bmi": bmi,
                "grasa_corporal": grasa,
                "masa_muscular": musculo,
                "cintura": cintura,
                "fecha_registro": fecha_hito
            })
            
            # Un pago mensual
            pagos.append({
                "id_miembro": id_m,
                "id_entrenador": id_entrenador,
                "monto": random.choice([80.0, 90.0, 100.0]),
                "metodo_pago": random.choice(metodos_pago),
                "fecha_pago": fecha_hito
            })

        # Generar asistencias aleatorias (ej. 3 a 5 veces por semana)
        dias_totales = (now - fecha_reg).days
        for dia in range(dias_totales):
            if random.random() < 0.6: # 60% de probabilidad de ir al gym ese día
                asistencias.append({
                    "id_miembro": id_m,
                    "fecha": fecha_reg + timedelta(days=dia)
                })

    if progresos: db.progreso_fisico.insert_many(progresos)
    if asistencias: db.asistencias.insert_many(asistencias)
    if pagos: db.pagos.insert_many(pagos)
    print(f"  ✅ {len(progresos)} progresos, {len(asistencias)} asistencias y {len(pagos)} pagos creados.")

# ══════════════════════════════════════════════
# 6. PLANES ALIMENTICIOS Y VENTAS
# ══════════════════════════════════════════════
def generar_dietas_ventas_correos(db, tipos_dieta, recetas, productos):
    print("\n🥗 Generando planes alimenticios, ventas de mostrador y correos...")
    miembros = list(db.miembros.find())
    now = datetime.now()
    
    # Planes Alimenticios
    for m in miembros:
        tipo_dieta_id = random.choice(list(tipos_dieta.values()))
        plan_doc = {
            "id_miembro": m["_id"],
            "id_tipo_dieta": tipo_dieta_id,
            "nombre_plan": f"Plan Nutricional - {m['objetivo']}",
            "activo": True
        }
        db.planes_alimenticios.insert_one(plan_doc)
        
        # Asignar 3 recetas al azar a este plan para un par de días
        recetas_compatibles = [r for r in recetas if r["id_tipo_dieta"] == tipo_dieta_id]
        if recetas_compatibles:
            for dia in ["Lunes", "Miércoles", "Viernes"]:
                db.plan_recetas.insert_one({
                    "id_plan": plan_doc["_id"],
                    "id_receta": random.choice(recetas_compatibles)["_id"],
                    "dia_semana": dia
                })

    # Ventas de Mostrador (Productos)
    for _ in range(30): # 30 ventas aleatorias
        venta_doc = {"fecha": now - timedelta(days=random.randint(0, 60))}
        db.ventas.insert_one(venta_doc)
        
        prod = random.choice(productos)
        cant = random.randint(1, 3)
        db.detalle_venta.insert_one({
            "id_venta": venta_doc["_id"],
            "id_producto": prod["_id"],
            "cantidad": cant,
            "subtotal": prod["precio"] * cant
        })

    # Correos
    correo_doc = {"tipo": "Masivo", "asunto": "¡Promoción de Verano!", "fecha": now}
    db.correos_enviados.insert_one(correo_doc)
    db.correo_miembro.insert_many([
        {"id_correo": correo_doc["_id"], "id_miembro": m["_id"]} 
        for m in random.sample(miembros, min(10, len(miembros)))
    ])
    print("  ✅ Dietas, ventas y correos listos.")

# ══════════════════════════════════════════════
# 7. EXTRAS DEL ENTRENADOR (Certificaciones, Logros, Evaluaciones)
# ══════════════════════════════════════════════
def generar_extras_entrenadores(db):
    print("\n🏅 Añadiendo certificaciones y evaluaciones a los entrenadores...")
    entrenadores = list(db.usuarios.find({"email": {"$regex": "entrenador"}}))
    miembros = list(db.miembros.find())
    
    for ent in entrenadores:
        db.certificaciones_entrenador.insert_one({
            "id_entrenador": ent["_id"],
            "nombre": random.choice(["CrossFit L1", "Nutrición Deportiva ISO", "Primeros Auxilios"])
        })
        db.logros_entrenador.insert_one({
            "id_entrenador": ent["_id"],
            "titulo": random.choice(["Entrenador del mes", "Top Transformaciones 2024"])
        })
        
        # Asignar evaluaciones aleatorias de miembros a este entrenador
        for _ in range(random.randint(2, 5)):
            db.evaluaciones_entrenador.insert_one({
                "id_entrenador": ent["_id"],
                "id_miembro": random.choice(miembros)["_id"],
                "calificacion": random.randint(4, 5) # Entrenadores buenos ;)
            })
    print("  ✅ Extras de entrenadores listos.")

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main():
    print("\n🚀 GYM MONGO SEED — OPTIMIZADO PARA MÚLTIPLES ENTRENADORES")
    print("=" * 60)

    db = get_db()
    limpiar_datos(db)
    roles, membresias, productos, tipos_dieta, recetas = obtener_catalogo(db)

    crear_admin(db, roles["Administrador"])
    crear_recepcionista(db, roles["Recepcionista"])
    
    # 1. Creamos 4 entrenadores
    entrenadores_ids = crear_entrenadores(db, roles["Entrenador"], cantidad=4)
    
    # 2. Generamos el pool de rutinas (máx 6 por entrenador)
    pool_rutinas = pregenerar_rutinas_entrenadores(db, entrenadores_ids)

    # 3. Creamos miembros asignándolos balanceadamente
    miembros_data = crear_miembros(
        db, roles["Miembro"], entrenadores_ids, pool_rutinas,
        membresias, tipos_dieta, recetas, num_usuarios=50
    )

    # 4. Generamos sesiones limitadas por entrenador
    generar_sesiones(db, entrenadores_ids, miembros_data)

    # ---> NUEVAS LLAMADAS PARA LLENAR EL RESTO DE COLECCIONES <---
    # 5. Progreso Físico, Asistencias y Pagos (Para PySpark)
    generar_progreso_asistencias_pagos(db)

    # 6. Planes Alimenticios, Ventas de Mostrador y Correos
    generar_dietas_ventas_correos(db, tipos_dieta, recetas, productos)

    # 7. Currículum y evaluaciones de los entrenadores
    generar_extras_entrenadores(db)

    print("\n✅ ¡POBLACIÓN TOTAL EXITOSA EN LAS 22 COLECCIONES!\n")

if __name__ == "__main__":
    main()