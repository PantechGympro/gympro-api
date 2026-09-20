import os
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid, OperationFailure

load_dotenv()

# ──────────────────────────────────────────────
# CONEXIÓN
# ──────────────────────────────────────────────
def get_db():
    user     = os.getenv("MONGO_USER")
    password = os.getenv("MONGO_PASSWORD")
    cluster  = os.getenv("MONGO_CLUSTER")
    DB_NAME  = os.getenv("MONGO_DB")

    if not cluster or not DB_NAME:
        raise EnvironmentError(
            "❌ Variables faltantes: MONGO_CLUSTER y MONGO_DB"
        )

    # ── CONEXIÓN LOCAL ─────────────────────────
    if not user or not password:
        uri = f"mongodb://{cluster}/{DB_NAME}"
        print(f"   🔗 Conectando a MongoDB LOCAL → {cluster}")

    # ── CONEXIÓN ATLAS ─────────────────────────
    else:
        uri = f"mongodb+srv://{user}:{password}@{cluster}/{DB_NAME}?retryWrites=true&w=majority"
        print(f"   🔗 Conectando a MongoDB Atlas → {cluster}")

    client = MongoClient(uri)

    # verificar conexión
    client.admin.command("ping")

    return client[DB_NAME], DB_NAME


# ──────────────────────────────────────────────
# HELPER: drop + create con validación opcional
# ──────────────────────────────────────────────
def drop_and_create(db, name: str, validator: dict = None):
    if name in db.list_collection_names():
        db[name].drop()

    opts = {}
    if validator:
        opts["validator"]       = validator
        opts["validationAction"] = "warn"   # warn = advierte pero no bloquea

    db.create_collection(name, **opts)
    print(f"   ✅ {name}")


# ══════════════════════════════════════════════
# VALIDADORES jsonSchema (equivalente a constraints SQL)
# ══════════════════════════════════════════════

V_ROLES = {"$jsonSchema": {"bsonType": "object",
    "required": ["nombre"],
    "properties": {"nombre": {"bsonType": "string", "maxLength": 50}}
}}

V_USUARIOS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_role", "activo", "fecha_creacion"],
    "properties": {
        "id_role":        {"bsonType": "objectId"},
        "nombre":         {"bsonType": ["string", "null"]},
        "email":          {"bsonType": ["string", "null"]},
        "password":       {"bsonType": ["string", "null"]},
        "activo":         {"bsonType": "bool"},
        "fecha_creacion": {"bsonType": "date"},
    }
}}

V_MIEMBROS = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_usuario":    {"bsonType": ["objectId", "null"]},
        "id_entrenador": {"bsonType": ["objectId", "null"]},
        "sexo":          {"bsonType": ["string", "null"], "enum": ["M", "F", "Otro", None]},
        "estado":        {"bsonType": ["string", "null"], "enum": ["Activo", "Inactivo", None]},
    }
}}

V_MEMBRESIAS = {"$jsonSchema": {"bsonType": "object",
    "required": ["nombre"],
    "properties": {
        "nombre":          {"bsonType": "string"},
        "duracion_meses":  {"bsonType": ["int", "null"]},
        "precio":          {"bsonType": ["double", "null"]},
    }
}}

V_PRODUCTOS = {"$jsonSchema": {"bsonType": "object",
    "required": ["nombre"],
    "properties": {
        "nombre":  {"bsonType": "string"},
        "precio":  {"bsonType": ["double", "null"]},
        "stock":   {"bsonType": ["int", "null"]},
    }
}}

V_PAGOS = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_miembro":   {"bsonType": ["objectId", "null"]},
        "id_entrenador":{"bsonType": ["objectId", "null"]},
        "monto":        {"bsonType": ["double", "null"]},
        "metodo_pago":  {"bsonType": ["string", "null"],
                         "enum": ["Efectivo", "Tarjeta", "Transferencia", "Simulado", None]},
    }
}}

V_SESIONES = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_entrenador", "fecha", "hora_inicio"],
    "properties": {
        "id_entrenador": {"bsonType": "objectId"},
        "id_miembro":    {"bsonType": ["objectId", "null"]},
        "id_rutina":     {"bsonType": ["objectId", "null"]},
        "tipo":    {"bsonType": ["string", "null"],
                    "enum": ["Personal", "Grupal", "Consulta", None]},
        "estado":  {"bsonType": ["string", "null"],
                    "enum": ["scheduled", "in-progress", "completed", "cancelled", None]},
    }
}}

V_ASISTENCIAS = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_miembro": {"bsonType": ["objectId", "null"]},
        "fecha":      {"bsonType": ["date", "null"]},
    }
}}

V_RUTINAS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_miembro", "nombre"],
    "properties": {
        "id_miembro":    {"bsonType": "objectId"},
        "id_entrenador": {"bsonType": ["objectId", "null"]},
        "activa":        {"bsonType": ["bool", "null"]},
        "dificultad":    {"bsonType": ["string", "null"],
                          "enum": ["Principiante", "Intermedio", "Avanzado", None]},
    }
}}

V_RUTINA_DIAS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_rutina"],
    "properties": {
        "id_rutina":  {"bsonType": "objectId"},
        "dia_semana": {"bsonType": ["string", "null"],
                       "enum": ["Lunes","Martes","Miércoles","Jueves",
                                "Viernes","Sábado","Domingo", None]},
    }
}}

V_RUTINA_EJERCICIOS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_rutina_dia", "nombre_ejercicio"],
    "properties": {
        "id_rutina_dia":   {"bsonType": "objectId"},
        "nombre_ejercicio":{"bsonType": "string"},
    }
}}

V_MIEMBRO_MEMBRESIA = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_miembro":   {"bsonType": ["objectId", "null"]},
        "id_membresia": {"bsonType": ["objectId", "null"]},
        "estado":       {"bsonType": ["string", "null"],
                         "enum": ["Activa", "Vencida", "Cancelada", None]},
    }
}}

V_CORREOS = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "tipo": {"bsonType": ["string", "null"],
                 "enum": ["Individual", "Masivo", None]},
    }
}}

V_CORREO_MIEMBRO = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_correo":  {"bsonType": ["objectId", "null"]},
        "id_miembro": {"bsonType": ["objectId", "null"]},
    }
}}

V_DETALLE_VENTA = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_venta":    {"bsonType": ["objectId", "null"]},
        "id_producto": {"bsonType": ["objectId", "null"]},
        "cantidad":    {"bsonType": ["int", "null"]},
        "subtotal":    {"bsonType": ["double", "null"]},
    }
}}

V_PROGRESO = {"$jsonSchema": {"bsonType": "object",
    "properties": {
        "id_miembro": {"bsonType": ["objectId", "null"]},
        "peso":       {"bsonType": ["double", "null"]},
        "bmi":        {"bsonType": ["double", "null"]},
    }
}}

V_TIPOS_DIETA = {"$jsonSchema": {"bsonType": "object",
    "required": ["nombre"],
    "properties": {"nombre": {"bsonType": "string", "maxLength": 50}}
}}

V_RECETAS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_tipo_dieta", "nombre", "tipo_comida", "ingredientes", "preparacion"],
    "properties": {
        "id_tipo_dieta": {"bsonType": "objectId"},
        "tipo_comida":   {"bsonType": "string",
                          "enum": ["Desayuno","Media Mañana","Almuerzo",
                                   "Merienda","Cena","Post-Entreno"]},
    }
}}

V_PLANES = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_miembro", "id_tipo_dieta", "nombre_plan"],
    "properties": {
        "id_miembro":    {"bsonType": "objectId"},
        "id_tipo_dieta": {"bsonType": "objectId"},
        "activo":        {"bsonType": ["bool", "null"]},
    }
}}

V_PLAN_RECETAS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_plan", "id_receta", "dia_semana"],
    "properties": {
        "id_plan":    {"bsonType": "objectId"},
        "id_receta":  {"bsonType": "objectId"},
        "dia_semana": {"bsonType": "string",
                       "enum": ["Lunes","Martes","Miércoles","Jueves",
                                "Viernes","Sábado","Domingo"]},
    }
}}

V_PERFIL_ENTRENADOR = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_entrenador"],
    "properties": {"id_entrenador": {"bsonType": "objectId"}}
}}

V_CERT = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_entrenador", "nombre"],
    "properties": {
        "id_entrenador": {"bsonType": "objectId"},
        "nombre":        {"bsonType": "string"},
    }
}}

V_LOGROS = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_entrenador", "titulo"],
    "properties": {
        "id_entrenador": {"bsonType": "objectId"},
        "titulo":        {"bsonType": "string"},
    }
}}

V_EVAL = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_entrenador", "id_miembro", "calificacion"],
    "properties": {
        "id_entrenador": {"bsonType": "objectId"},
        "id_miembro":    {"bsonType": "objectId"},
        "calificacion":  {"bsonType": "int"},
    }
}}

V_MIEMBRO_RUTINA = {"$jsonSchema": {"bsonType": "object",
    "required": ["id_miembro", "id_rutina"],
    "properties": {
        "id_miembro": {"bsonType": "objectId"},
        "id_rutina":  {"bsonType": "objectId"},
        "activa":     {"bsonType": ["bool", "null"]},
    }
}}


# ══════════════════════════════════════════════
# CREACIÓN DE LAS 22 COLECCIONES
# ══════════════════════════════════════════════
COLECCIONES = [
    # (nombre,                  validador)
    ("roles",                   V_ROLES),
    ("usuarios",                V_USUARIOS),
    ("miembros",                V_MIEMBROS),
    ("membresias",              V_MEMBRESIAS),
    ("productos",               V_PRODUCTOS),
    ("ventas",                  None),
    ("correos_enviados",        V_CORREOS),
    ("asistencias",             V_ASISTENCIAS),
    ("pagos",                   V_PAGOS),
    ("progreso_fisico",         V_PROGRESO),
    ("rutinas",                 V_RUTINAS),
    ("rutina_dias",             V_RUTINA_DIAS),
    ("rutina_ejercicios",       V_RUTINA_EJERCICIOS),
    ("miembro_membresia",       V_MIEMBRO_MEMBRESIA),
    ("correo_miembro",          V_CORREO_MIEMBRO),
    ("detalle_venta",           V_DETALLE_VENTA),
    ("sesiones",                V_SESIONES),
    ("tipos_dieta",             V_TIPOS_DIETA),
    ("recetas",                 V_RECETAS),
    ("planes_alimenticios",     V_PLANES),
    ("plan_recetas",            V_PLAN_RECETAS),
    # Tablas del dashboard entrenador
    ("perfil_entrenador",       V_PERFIL_ENTRENADOR),
    ("certificaciones_entrenador", V_CERT),
    ("logros_entrenador",       V_LOGROS),
    ("evaluaciones_entrenador", V_EVAL),
    ("miembro_rutina",          V_MIEMBRO_RUTINA),
]


def crear_colecciones(db):
    print(f"\n📦 Creando {len(COLECCIONES)} colecciones...")
    for nombre, validador in COLECCIONES:
        drop_and_create(db, nombre, validador)
    print(f"\n   Total: {len(db.list_collection_names())} colecciones creadas")


# ══════════════════════════════════════════════
# ÍNDICES (equivalente a KEY / UNIQUE KEY en SQL)
# ══════════════════════════════════════════════
def crear_indices(db):
    print("\n🔍 Creando índices...")

    # roles
    db.roles.create_index("nombre", unique=True, name="idx_roles_nombre")

    # usuarios
    db.usuarios.create_index("email",    unique=True,  name="idx_usuarios_email")
    db.usuarios.create_index("id_role",                name="idx_usuarios_role")
    db.usuarios.create_index("activo",                 name="idx_usuarios_activo")

    # miembros
    db.miembros.create_index("id_usuario",    name="idx_miembros_usuario")
    db.miembros.create_index("id_entrenador", name="idx_miembros_entrenador")
    db.miembros.create_index("estado",        name="idx_miembros_estado")

    # asistencias
    db.asistencias.create_index("id_miembro", name="idx_asistencias_miembro")
    db.asistencias.create_index("fecha",      name="idx_asistencias_fecha")
    db.asistencias.create_index(
        [("id_miembro", ASCENDING), ("fecha", DESCENDING)],
        name="idx_asistencias_miembro_fecha"
    )

    # pagos
    db.pagos.create_index("id_miembro",    name="idx_pagos_miembro")
    db.pagos.create_index("id_entrenador", name="idx_pagos_entrenador")
    db.pagos.create_index("fecha_pago",    name="idx_pagos_fecha")

    # progreso_fisico
    db.progreso_fisico.create_index("id_miembro",     name="idx_progreso_miembro")
    db.progreso_fisico.create_index("fecha_registro", name="idx_progreso_fecha")
    db.progreso_fisico.create_index(
        [("id_miembro", ASCENDING), ("fecha_registro", DESCENDING)],
        name="idx_progreso_miembro_fecha"
    )

    # rutinas
    db.rutinas.create_index("id_miembro",    name="idx_rutinas_miembro")
    db.rutinas.create_index("id_entrenador", name="idx_rutinas_entrenador")
    db.rutinas.create_index("activa",        name="idx_rutinas_activa")

    # rutina_dias
    db.rutina_dias.create_index("id_rutina", name="idx_rutina_dias_rutina")

    # rutina_ejercicios
    db.rutina_ejercicios.create_index("id_rutina_dia", name="idx_rutina_ej_dia")

    # miembro_membresia
    db.miembro_membresia.create_index("id_miembro",   name="idx_mm_miembro")
    db.miembro_membresia.create_index("id_membresia", name="idx_mm_membresia")
    db.miembro_membresia.create_index("estado",       name="idx_mm_estado")

    # correo_miembro
    db.correo_miembro.create_index("id_correo",  name="idx_cm_correo")
    db.correo_miembro.create_index("id_miembro", name="idx_cm_miembro")

    # detalle_venta
    db.detalle_venta.create_index("id_venta",    name="idx_dv_venta")
    db.detalle_venta.create_index("id_producto", name="idx_dv_producto")

    # ventas
    db.ventas.create_index("fecha", name="idx_ventas_fecha")

    # sesiones
    db.sesiones.create_index("id_entrenador", name="idx_ses_entrenador")
    db.sesiones.create_index("id_miembro",    name="idx_ses_miembro")
    db.sesiones.create_index("fecha",         name="idx_ses_fecha")
    db.sesiones.create_index("estado",        name="idx_ses_estado")
    db.sesiones.create_index("id_rutina",     name="idx_ses_rutina")
    db.sesiones.create_index(
        [("id_entrenador", ASCENDING), ("fecha", DESCENDING)],
        name="idx_ses_entrenador_fecha"
    )

    # tipos_dieta
    db.tipos_dieta.create_index("nombre", unique=True, name="idx_tipos_dieta_nombre")

    # recetas
    db.recetas.create_index("id_tipo_dieta", name="idx_recetas_tipo_dieta")
    db.recetas.create_index("tipo_comida",   name="idx_recetas_tipo_comida")

    # planes_alimenticios
    db.planes_alimenticios.create_index("id_miembro",    name="idx_planes_miembro")
    db.planes_alimenticios.create_index("id_tipo_dieta", name="idx_planes_tipo_dieta")
    db.planes_alimenticios.create_index("activo",        name="idx_planes_activo")

    # plan_recetas
    db.plan_recetas.create_index("id_plan",   name="idx_pr_plan")
    db.plan_recetas.create_index("id_receta", name="idx_pr_receta")

    # perfil_entrenador
    db.perfil_entrenador.create_index(
        "id_entrenador", unique=True, name="idx_perfil_entrenador"
    )

    # certificaciones_entrenador
    db.certificaciones_entrenador.create_index(
        "id_entrenador", name="idx_cert_entrenador"
    )

    # logros_entrenador
    db.logros_entrenador.create_index("id_entrenador", name="idx_logros_entrenador")

    # evaluaciones_entrenador
    db.evaluaciones_entrenador.create_index("id_entrenador", name="idx_eval_entrenador")
    db.evaluaciones_entrenador.create_index("id_miembro",    name="idx_eval_miembro")

    # miembro_rutina  (unique compuesto equivale al UNIQUE KEY SQL)
    db.miembro_rutina.create_index(
        [("id_miembro", ASCENDING), ("id_rutina", ASCENDING)],
        unique=True, name="uq_miembro_rutina"
    )
    db.miembro_rutina.create_index("id_rutina", name="idx_mr_rutina")

    print("   ✅ Todos los índices creados")


# ══════════════════════════════════════════════
# DATOS ESTÁTICOS
# ══════════════════════════════════════════════
def insertar_datos_estaticos(db):
    print("\n📋 Insertando datos estáticos...")

    # ── Roles ─────────────────────────────────────────────────────────────
    roles = [
        {"nombre": "Administrador"},
        {"nombre": "Entrenador"},
        {"nombre": "Recepcionista"},
        {"nombre": "Miembro"},
    ]
    db.roles.insert_many(roles)
    print(f"   ✅ roles: {len(roles)} documentos")

    # ── Membresías ─────────────────────────────────────────────────────────
    membresias = [
        {"nombre": "Básica Mensual",   "duracion_meses": 1,  "precio": 80.00},
        {"nombre": "Premium Mensual",  "duracion_meses": 1,  "precio": 90.00},
        {"nombre": "Básica Anual",     "duracion_meses": 12, "precio": 300.00},
        {"nombre": "Premium Anual",    "duracion_meses": 12, "precio": 550.00},
        {"nombre": "Estudiante",       "duracion_meses": 1,  "precio": 25.00},
        {"nombre": "Familiar",         "duracion_meses": 1,  "precio": 80.00},
        {"nombre": "VIP",              "duracion_meses": 1,  "precio": 100.00},
    ]
    db.membresias.insert_many(membresias)
    print(f"   ✅ membresias: {len(membresias)} documentos")

    # ── Productos ──────────────────────────────────────────────────────────
    productos = [
        {"nombre": "Proteína Whey 1kg",  "precio": 450.00, "stock": 50},
        {"nombre": "Creatina 300g",      "precio": 280.00, "stock": 30},
        {"nombre": "BCAA 200 caps",      "precio": 320.00, "stock": 25},
        {"nombre": "Pre-Workout",        "precio": 380.00, "stock": 20},
        {"nombre": "Shaker 600ml",       "precio": 80.00,  "stock": 100},
        {"nombre": "Toalla deportiva",   "precio": 120.00, "stock": 60},
        {"nombre": "Guantes gimnasio",   "precio": 150.00, "stock": 40},
        {"nombre": "Botella agua 1L",    "precio": 95.00,  "stock": 80},
        {"nombre": "Barra proteína",     "precio": 35.00,  "stock": 200},
        {"nombre": "Electrolitos",       "precio": 180.00, "stock": 45},
    ]
    db.productos.insert_many(productos)
    print(f"   ✅ productos: {len(productos)} documentos")

    # ── Tipos de dieta ─────────────────────────────────────────────────────
    tipos_dieta = [
        {"nombre": "Hipercalórica",    "descripcion": "Dieta para aumento de masa muscular",              "calorias_objetivo": "2800-3500 kcal"},
        {"nombre": "Déficit Calórico", "descripcion": "Dieta para pérdida de grasa",                      "calorias_objetivo": "1500-2000 kcal"},
        {"nombre": "Vegana",           "descripcion": "Dieta basada en plantas sin productos animales",    "calorias_objetivo": "2000-2500 kcal"},
        {"nombre": "Mediterránea",     "descripcion": "Dieta balanceada estilo mediterráneo",              "calorias_objetivo": "2000-2300 kcal"},
        {"nombre": "Keto",             "descripcion": "Dieta baja en carbohidratos alta en grasas",        "calorias_objetivo": "1800-2200 kcal"},
        {"nombre": "Paleo",            "descripcion": "Dieta basada en alimentos no procesados",           "calorias_objetivo": "2000-2400 kcal"},
        {"nombre": "Flexible",         "descripcion": "Dieta balanceada sin restricciones específicas",    "calorias_objetivo": "2000-2500 kcal"},
    ]
    result = db.tipos_dieta.insert_many(tipos_dieta)
    tipo_ids = {t["nombre"]: _id for t, _id in zip(tipos_dieta, result.inserted_ids)}
    print(f"   ✅ tipos_dieta: {len(tipos_dieta)} documentos")

    # ── Recetas ────────────────────────────────────────────────────────────
    recetas = _construir_recetas(tipo_ids)
    db.recetas.insert_many(recetas)
    print(f"   ✅ recetas: {len(recetas)} documentos")


def _construir_recetas(tipo_ids):
    """Devuelve lista de documentos receta con id_tipo_dieta como ObjectId."""

    def r(tipo, nombre, comida, ing, prep, cal, prot, carb, gras, tmin):
        return {
            "id_tipo_dieta":      tipo_ids[tipo],
            "nombre":             nombre,
            "tipo_comida":        comida,
            "ingredientes":       ing,
            "preparacion":        prep,
            "calorias":           float(cal),
            "proteinas":          float(prot),
            "carbohidratos":      float(carb),
            "grasas":             float(gras),
            "tiempo_preparacion": tmin,
        }

    return [
        # ── HIPERCALÓRICA ──────────────────────────────────────────────────
        r("Hipercalórica","Desayuno Power","Desayuno",
          "5 claras, 2 huevos, 100g avena, 1 plátano, 30g mantequilla de maní",
          "1. Cocinar huevos revueltos. 2. Cocinar avena. 3. Servir con plátano.",
          850,52,95,28,15),
        r("Hipercalórica","Batido Ganador","Media Mañana",
          "2 scoops proteína, 100g avena, 1 plátano, 30g almendras, 400ml leche entera",
          "1. Licuar. 2. Servir frío.",720,58,82,22,5),
        r("Hipercalórica","Pechuga con Arroz Power","Almuerzo",
          "250g pechuga, 200g arroz integral, 150g brócoli, 2 cdas aceite oliva",
          "1. Cocinar arroz. 2. Saltear pollo. 3. Cocer brócoli al vapor.",780,65,88,18,30),
        r("Hipercalórica","Wrap de Atún","Merienda",
          "2 latas atún, 2 tortillas integrales, 50g queso, vegetales, mayonesa light",
          "1. Mezclar atún con mayonesa. 2. Rellenar tortillas. 3. Calentar.",620,54,48,22,10),
        r("Hipercalórica","Salmón con Quinoa","Cena",
          "200g salmón, 150g quinoa, 100g espárragos, aceite oliva, limón",
          "1. Cocinar quinoa. 2. Hornear salmón. 3. Saltear espárragos.",690,48,55,26,25),
        r("Hipercalórica","Batido Post-Entreno","Post-Entreno",
          "2 scoops proteína, 50g dextrosa, 1 plátano, 5g creatina",
          "1. Mezclar con agua. 2. Tomar inmediatamente.",420,50,68,2,3),

        # ── DÉFICIT CALÓRICO ───────────────────────────────────────────────
        r("Déficit Calórico","Omelette Ligero","Desayuno",
          "4 claras, 1 huevo, espinacas, champiñones, tomate",
          "1. Batir claras. 2. Saltear vegetales. 3. Cocinar omelette.",220,28,8,6,10),
        r("Déficit Calórico","Yogurt con Frutos Rojos","Media Mañana",
          "200g yogurt griego 0%, 100g frutos rojos, 10g almendras",
          "1. Servir yogurt. 2. Agregar frutos rojos.",210,22,18,6,3),
        r("Déficit Calórico","Ensalada de Pollo","Almuerzo",
          "150g pechuga, lechuga, tomate, pepino, zanahoria, vinagre balsámico",
          "1. Cocinar pollo. 2. Cortar vegetales. 3. Aliñar.",280,38,15,5,15),
        r("Déficit Calórico","Manzana con Almendras","Merienda",
          "1 manzana, 15g almendras",
          "1. Cortar manzana. 2. Servir con almendras.",180,4,28,6,2),
        r("Déficit Calórico","Pescado al Vapor","Cena",
          "180g pescado blanco, vegetales mixtos, limón, especias",
          "1. Cocinar al vapor. 2. Servir con vegetales.",240,36,12,4,20),
        r("Déficit Calórico","Batido de Proteína","Post-Entreno",
          "1 scoop proteína, 200ml agua, hielo",
          "1. Mezclar y servir.",120,24,3,1,2),

        # ── VEGANA ─────────────────────────────────────────────────────────
        r("Vegana","Bowl de Avena Vegano","Desayuno",
          "80g avena, 200ml leche almendras, 1 plátano, 20g nueces, canela",
          "1. Cocinar avena. 2. Agregar plátano y nueces.",420,12,65,14,8),
        r("Vegana","Hummus con Vegetales","Media Mañana",
          "100g hummus, zanahoria, pepino, apio",
          "1. Cortar vegetales. 2. Servir con hummus.",180,8,22,6,5),
        r("Vegana","Buddha Bowl","Almuerzo",
          "150g tofu, 100g quinoa, aguacate, garbanzos, espinaca, tahini",
          "1. Cocinar quinoa. 2. Saltear tofu. 3. Armar bowl.",520,26,48,24,25),
        r("Vegana","Batido Verde","Merienda",
          "Espinaca, plátano, manzana, leche de coco, chía",
          "1. Licuar. 2. Servir frío.",240,6,42,8,5),
        r("Vegana","Curry de Lentejas","Cena",
          "150g lentejas, leche de coco, curry, espinaca, tomate, cebolla",
          "1. Cocinar lentejas. 2. Saltear cebolla. 3. Agregar especias.",380,18,52,12,30),
        r("Vegana","Batido Proteína Vegana","Post-Entreno",
          "1 scoop proteína vegana, plátano, leche almendras, mantequilla almendra",
          "1. Licuar todo.",320,28,38,10,3),

        # ── MEDITERRÁNEA ───────────────────────────────────────────────────
        r("Mediterránea","Tostadas Mediterráneas","Desayuno",
          "2 rebanadas pan integral, aguacate, tomate, huevo pochado, aceite oliva",
          "1. Tostar. 2. Untar aguacate. 3. Agregar tomate y huevo.",380,18,42,18,12),
        r("Mediterránea","Ensalada Griega","Almuerzo",
          "Lechuga, tomate, pepino, aceitunas, queso feta, aceite oliva",
          "1. Cortar. 2. Mezclar. 3. Aliñar.",290,12,18,20,10),
        r("Mediterránea","Pollo al Limón","Cena",
          "180g pechuga, limón, ajo, hierbas, aceite oliva, vegetales",
          "1. Marinar. 2. Hornear. 3. Servir.",340,42,12,14,35),

        # ── KETO ───────────────────────────────────────────────────────────
        r("Keto","Huevos con Aguacate","Desayuno",
          "3 huevos, 1 aguacate, tocino, queso cheddar",
          "1. Freír huevos y tocino. 2. Servir con aguacate.",520,32,8,42,10),
        r("Keto","Salmón con Espárragos","Almuerzo",
          "200g salmón, espárragos, mantequilla, limón",
          "1. Hornear salmón. 2. Saltear espárragos en mantequilla.",480,38,6,36,25),
        r("Keto","Ensalada César Keto","Cena",
          "Lechuga romana, 150g pollo, parmesano, aderezo césar keto",
          "1. Cocinar pollo. 2. Armar ensalada.",380,42,8,22,15),

        # ── PALEO ──────────────────────────────────────────────────────────
        r("Paleo","Batido Paleo","Desayuno",
          "Huevos, plátano, nueces, leche de coco, canela",
          "1. Licuar todo.",380,18,38,20,5),
        r("Paleo","Carne con Batata","Almuerzo",
          "200g carne res, 150g batata, brócoli, aceite coco",
          "1. Cocinar carne. 2. Hornear batata. 3. Cocer brócoli.",520,45,42,22,40),
        r("Paleo","Pollo con Vegetales Paleo","Cena",
          "180g pollo, calabacín, pimientos, cebolla, aceite oliva",
          "1. Saltear pollo. 2. Agregar vegetales.",340,40,18,14,25),

        # ── FLEXIBLE ───────────────────────────────────────────────────────
        r("Flexible","Pancakes Proteicos","Desayuno",
          "40g avena, 2 huevos, 1 plátano, 1 scoop proteína",
          "1. Licuar. 2. Cocinar como pancakes.",420,38,48,10,15),
        r("Flexible","Wrap de Pollo","Almuerzo",
          "Tortilla integral, 150g pollo, lechuga, tomate, aguacate, queso",
          "1. Cocinar pollo. 2. Armar wrap.",480,42,38,18,15),
        r("Flexible","Pasta con Atún","Cena",
          "100g pasta integral, 1 lata atún, tomate, aceite oliva, ajo",
          "1. Cocinar pasta. 2. Saltear ajo. 3. Mezclar.",450,35,58,12,20),
    ]


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🏋️  GYM MONGO SETUP - 22 COLECCIONES (1 por tabla SQL)")
    print("=" * 60)

    db, db_name = get_db()
    print(f"   📁 Base de datos: {db_name}\n")

    crear_colecciones(db)
    crear_indices(db)
    insertar_datos_estaticos(db)

    print("\n" + "=" * 60)
    print("✅ SETUP COMPLETADO")
    print("   Siguiente paso: python gym_mongo_seed.py")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()