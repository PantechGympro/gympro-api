"""
seeds/seed_pg.py — Seed multi-gimnasio para GymPro SaaS.

5 gimnasios con personalidad distinta:
  1. CrossFit Titan      — CrossFit / entrenamiento funcional
  2. Zen Body Studio     — Yoga / pilates / wellness
  3. Iron Temple Gym     — Powerlifting / culturismo
  4. Cardio & Dance      — Cardio / baile / spinning
  5. Elite Performance   — Centro premium full-service

Uso:
  docker compose exec api python -m app.seeds.seed_pg
"""
import os, sys, random
from datetime import datetime, timedelta, timezone, date
from bson import ObjectId
from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from flask_migrate import upgrade as db_upgrade
from app.models.pg.rol                 import Rol
from app.models.pg.gimnasio            import Gimnasio
from app.models.pg.usuario             import Usuario
from app.models.pg.plan_suscripcion    import PlanSuscripcion
from app.models.pg.suscripcion         import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.models.pg.tipo_membresia      import TipoMembresia
from app.models.pg.ejercicio           import Ejercicio
from app.models.pg.tipo_clase          import TipoClase
from app.mongo import get_db

RNG = random.Random(42)

# Guard global: evita emails duplicados dentro de la misma ejecución
_USED_EMAILS: set = set()

NOMBRES_M = ["Carlos","Luis","Miguel","Jorge","Andres","Ricardo","Fernando",
             "Sergio","Pablo","Diego","Alejandro","Roberto","Hector","Ivan",
             "Oscar","Raul","Eduardo","Marco","Javier","Victor","Daniel",
             "Cristian","Abraham","Emmanuel","Kevin","Adrian","Jonathan"]
NOMBRES_F = ["Maria","Ana","Laura","Sofia","Daniela","Valentina","Gabriela",
             "Fernanda","Paola","Claudia","Diana","Karen","Lorena","Patricia",
             "Sandra","Monica","Adriana","Natalia","Isabella","Camila",
             "Jimena","Alejandra","Yesenia","Mariana","Brenda","Citlali"]
APELLIDOS  = ["Garcia","Martinez","Lopez","Gonzalez","Rodriguez","Hernandez",
              "Perez","Sanchez","Ramirez","Torres","Flores","Diaz","Morales",
              "Jimenez","Ruiz","Gutierrez","Cruz","Ortiz","Castillo","Reyes",
              "Mendoza","Vargas","Romero","Herrera","Medina","Aguilar","Soto"]
METODOS_PAGO = ["Efectivo","Tarjeta debito","Tarjeta credito","Transferencia","QR"]

# ── Config de los 5 gimnasios ─────────────────────────────────────────────────
GIMNASIOS_CFG = [
    {
        "nombre": "CrossFit Titan",
        "plan": "enterprise",
        "email_contacto": "ops@crossfittitan.mx",
        "telefono": "+52-664-200-0001",
        "admin_email": "admin@crossfittitan.mx",
        "admin_nombre": "Titan Admin",
        "gen_ratio": 0.60,
        "n_trainers": 3, "n_staff": 2, "n_miembros": 38,
        "objetivos": ["Fuerza maxima","Rendimiento deportivo","Perdida de peso",
                      "Acondicionamiento general","Competencia CrossFit"],
        "membresias": [
            {"nombre":"Fundamentos",     "precio":450.0,  "duracion_meses":3, "descripcion":"Programa introductorio 3 meses"},
            {"nombre":"CrossFit Rx",     "precio":900.0,  "duracion_meses":1, "descripcion":"Acceso ilimitado WOD"},
            {"nombre":"Atleta",          "precio":1300.0, "duracion_meses":1, "descripcion":"WOD + clases especializadas + nutricion"},
            {"nombre":"Competidor",      "precio":1800.0, "duracion_meses":1, "descripcion":"Programa elite para competencias"},
            {"nombre":"Drop-in Semanal", "precio":400.0,  "duracion_meses":1, "descripcion":"Acceso una semana"},
        ],
        "ejercicios": [
            ("Power Clean","Cuerpo completo","olimpico"),("Snatch","Cuerpo completo","olimpico"),
            ("Thruster","Cuerpo completo","funcional"),("Box Jump","Piernas","pliometrico"),
            ("Muscle-up","Espalda","gimnasia"),("Handstand Push-up","Hombros","gimnasia"),
            ("Kettlebell Swing","Cuerpo completo","funcional"),("Pull-up","Espalda","gimnasia"),
            ("Double Under","Cardio","cardio"),("Burpee","Cuerpo completo","funcional"),
            ("Wall Ball","Cuerpo completo","funcional"),("Rowing 500m","Cardio","cardio"),
            ("Air Squat","Piernas","funcional"),("Push Press","Hombros","funcional"),
            ("Deadlift","Piernas","fuerza"),("Back Squat","Piernas","fuerza"),
            ("Toes to Bar","Abdomen","gimnasia"),("Rope Climb","Espalda","gimnasia"),
            ("Dumbbell Snatch","Cuerpo completo","funcional"),("GHD Sit-up","Abdomen","funcional"),
        ],
        "clases": [
            ("CrossFit WOD","Workout Of the Day diario",60,16),
            ("Olympic Lifting","Tecnica en levantamiento olimpico",75,10),
            ("Gymnastics","Habilidades de gimnasia aplicada",60,12),
            ("Endurance","Cardio de larga duracion",75,18),
            ("Open Gym","Entrenamiento libre con coaching",120,20),
            ("Foundations","Curso introductorio para nuevos atletas",60,8),
        ],
        "rutina_categorias": ["CrossFit","Funcional","Fuerza","HIIT"],
        "dieta_tipo": "alta_proteina_carbos",
    },
    {
        "nombre": "Zen Body Studio",
        "plan": "pro",
        "email_contacto": "hola@zenbody.mx",
        "telefono": "+52-664-200-0002",
        "admin_email": "admin@zenbody.mx",
        "admin_nombre": "Zen Admin",
        "gen_ratio": 0.30,
        "n_trainers": 3, "n_staff": 1, "n_miembros": 32,
        "objetivos": ["Flexibilidad y movilidad","Reduccion de estres","Bienestar integral",
                      "Tonificacion suave","Rehabilitacion y recovery"],
        "membresias": [
            {"nombre":"Armonia",           "precio":350.0, "duracion_meses":1, "descripcion":"1 clase dia de yoga o pilates"},
            {"nombre":"Bienestar Completo","precio":550.0, "duracion_meses":1, "descripcion":"Clases ilimitadas + meditacion"},
            {"nombre":"Pilates Intensivo", "precio":700.0, "duracion_meses":1, "descripcion":"Reformer + mat ilimitado"},
            {"nombre":"Retiro Mensual",    "precio":400.0, "duracion_meses":1, "descripcion":"4 sesiones de retiro + yoga"},
        ],
        "ejercicios": [
            ("Surya Namaskar","Cuerpo completo","yoga"),("Warrior I y II","Piernas","yoga"),
            ("Downward Dog","Espalda","yoga"),("Chair Pose","Piernas","yoga"),
            ("Triangle Pose","Core","yoga"),("Cat-Cow","Espalda","yoga"),
            ("Pilates Roll-up","Abdomen","pilates"),("The Hundreds","Abdomen","pilates"),
            ("Leg Circles","Piernas","pilates"),("Swan Dive","Espalda","pilates"),
            ("Teaser","Abdomen","pilates"),("Side Kick Series","Gluteos","pilates"),
            ("Butterfly Stretch","Caderas","flexibilidad"),("Seated Forward Fold","Isquiotibiales","flexibilidad"),
            ("Pigeon Pose","Caderas","yoga"),("Tree Pose","Equilibrio","yoga"),
            ("Bridge Pose","Gluteos","yoga"),("Plank Yoga","Core","pilates"),
            ("Child's Pose","Espalda","yoga"),("Savasana","Cuerpo completo","meditacion"),
        ],
        "clases": [
            ("Hatha Yoga","Posturas estaticas y respiracion",60,15),
            ("Vinyasa Flow","Secuencias fluidas con respiracion",55,14),
            ("Hot Yoga","Yoga en ambiente caliente 38C",60,12),
            ("Yin Yoga","Posturas pasivas para tejido conectivo",75,15),
            ("Pilates Mat","Pilates en colchoneta",60,12),
            ("Pilates Reformer","Aparato reformer precision",55,8),
            ("Meditacion Guiada","Tecnicas de mindfulness",45,20),
            ("Aerial Yoga","Yoga con hamacas suspendidas",60,8),
        ],
        "rutina_categorias": ["Yoga","Pilates","Flexibilidad","Mindfulness"],
        "dieta_tipo": "plant_based",
    },
    {
        "nombre": "Iron Temple Gym",
        "plan": "pro",
        "email_contacto": "contacto@irontemple.mx",
        "telefono": "+52-664-200-0003",
        "admin_email": "admin@irontemple.mx",
        "admin_nombre": "Iron Admin",
        "gen_ratio": 0.72,
        "n_trainers": 3, "n_staff": 2, "n_miembros": 40,
        "objetivos": ["Ganancia muscular","Fuerza maxima","Powerlifting competitivo",
                      "Culturismo","Definicion muscular"],
        "membresias": [
            {"nombre":"Basica Hierro",    "precio":350.0,  "duracion_meses":1, "descripcion":"Acceso libre + peso libre"},
            {"nombre":"Culturismo",       "precio":550.0,  "duracion_meses":1, "descripcion":"Acceso + clase semanal nutricion"},
            {"nombre":"Powerlifting Pro", "precio":750.0,  "duracion_meses":1, "descripcion":"Coaching tecnico + programa individual"},
            {"nombre":"VIP Coaching",     "precio":1200.0, "duracion_meses":1, "descripcion":"PT 3x semana + nutricion + seguimiento"},
            {"nombre":"Pareja Hierro",    "precio":900.0,  "duracion_meses":1, "descripcion":"Dos personas Basica Hierro"},
        ],
        "ejercicios": [
            ("Sentadilla Libre","Piernas","fuerza"),("Press Banca Plano","Pecho","fuerza"),
            ("Peso Muerto Clasico","Espalda","fuerza"),("Press Militar","Hombros","fuerza"),
            ("Remo con Barra","Espalda","fuerza"),("Sentadilla Frontal","Piernas","fuerza"),
            ("Rack Pull","Espalda","fuerza"),("Floor Press","Pecho","fuerza"),
            ("Good Morning","Espalda","fuerza"),("Sentadilla Pausa","Piernas","fuerza"),
            ("Press Inclinado","Pecho","fuerza"),("Press Declinado","Pecho","fuerza"),
            ("Curl con Barra","Biceps","hipertrofia"),("Extension Triceps","Triceps","hipertrofia"),
            ("Elevaciones Laterales","Hombros","hipertrofia"),("Prensa Piernas","Piernas","hipertrofia"),
            ("Hip Thrust","Gluteos","hipertrofia"),("Dominadas Cargadas","Espalda","fuerza"),
            ("Fondos Cargados","Triceps","fuerza"),("Jalon al Pecho","Espalda","hipertrofia"),
        ],
        "clases": [
            ("Powerlifting Tecnico","Tecnica en los 3 grandes movimientos",90,8),
            ("Culturismo Competitivo","Volumen y definicion competitiva",75,10),
            ("Strongman Intro","Eventos de strongman para todos",90,10),
            ("Movilidad y Prevencion","Movilidad articular y lesiones",60,15),
            ("Nutricion Deportiva","Taller mensual de alimentacion para fuerza",90,20),
            ("Perdida de Grasa Intensiva","Circuito metabolico + pesas",60,12),
        ],
        "rutina_categorias": ["Powerlifting","Hipertrofia","Fuerza","Definicion"],
        "dieta_tipo": "alta_proteina_calorias",
    },
    {
        "nombre": "Cardio and Dance Studio",
        "plan": "basico",
        "email_contacto": "info@cardiodance.mx",
        "telefono": "+52-664-200-0004",
        "admin_email": "admin@cardiodance.mx",
        "admin_nombre": "Cardio Admin",
        "gen_ratio": 0.35,
        "n_trainers": 2, "n_staff": 1, "n_miembros": 28,
        "objetivos": ["Perdida de peso","Tonificacion","Mejora cardiovascular",
                      "Diversion y socializacion","Resistencia aerobica"],
        "membresias": [
            {"nombre":"Cardio Basica",   "precio":250.0, "duracion_meses":1, "descripcion":"3 clases por semana"},
            {"nombre":"Studio Ilimitado","precio":400.0, "duracion_meses":1, "descripcion":"Clases ilimitadas"},
            {"nombre":"VIP Spinning",    "precio":350.0, "duracion_meses":1, "descripcion":"Spinning ilimitado + 2 clases semana"},
            {"nombre":"Pareja Cardio",   "precio":600.0, "duracion_meses":1, "descripcion":"Dos personas Ilimitado"},
        ],
        "ejercicios": [
            ("Jumping Jacks","Cuerpo completo","cardio"),("High Knees","Cuerpo completo","cardio"),
            ("Mountain Climbers","Core","cardio"),("Burpees Cardio","Cuerpo completo","cardio"),
            ("Jump Rope","Cuerpo completo","cardio"),("Step Aerobics","Piernas","cardio"),
            ("Zumba Combination","Cuerpo completo","baile"),("Bicycle Crunch","Abdomen","cardio"),
            ("Side Shuffles","Piernas","cardio"),("Speed Skaters","Piernas","cardio"),
            ("Tuck Jumps","Cuerpo completo","pliometrico"),("Squat Jump","Piernas","pliometrico"),
            ("Dance Cardio Combo","Cuerpo completo","baile"),("Lateral Lunge","Piernas","funcional"),
            ("Spinning Intervals","Piernas","cardio"),("Boxing Shadow","Cuerpo completo","cardio"),
            ("Grapevine Step","Piernas","baile"),("Hip Hop Combo","Cuerpo completo","baile"),
            ("Plie Jump","Piernas","cardio"),("Core Cardio Circuit","Core","cardio"),
        ],
        "clases": [
            ("Zumba Fitness","Cardio con ritmos latinos y urbanos",50,25),
            ("Spinning","Ciclismo indoor con intervalos",45,18),
            ("Step Aerobics","Coreografias con step y cardio",50,20),
            ("Body Combat","Cardio inspirado en artes marciales",55,22),
            ("Dance Fit","Cardio con coreografias pop",50,20),
            ("Power Pump","Tonificacion muscular con barras",50,18),
            ("Aqua Aerobics","Aerobicos en agua bajo impacto",45,15),
            ("CXWORX","Core y gluteos intensivo 30 min",30,20),
        ],
        "rutina_categorias": ["Cardio","Baile","HIIT","Tonificacion"],
        "dieta_tipo": "deficit_calorico",
    },
    {
        "nombre": "Elite Performance Center",
        "plan": "enterprise",
        "email_contacto": "concierge@eliteperformance.mx",
        "telefono": "+52-664-200-0005",
        "admin_email": "admin@eliteperformance.mx",
        "admin_nombre": "Elite Admin",
        "gen_ratio": 0.50,
        "n_trainers": 4, "n_staff": 3, "n_miembros": 45,
        "objetivos": ["Rendimiento deportivo","Salud preventiva","Composicion corporal",
                      "Bienestar ejecutivo","Rehabilitacion premium"],
        "membresias": [
            {"nombre":"Silver",      "precio":500.0,  "duracion_meses":1, "descripcion":"Acceso full instalaciones"},
            {"nombre":"Gold",        "precio":800.0,  "duracion_meses":1, "descripcion":"Full + 2 PT mes + nutricion"},
            {"nombre":"Platinum",    "precio":1500.0, "duracion_meses":1, "descripcion":"Full + PT ilimitado + spa"},
            {"nombre":"Diamond VIP", "precio":2500.0, "duracion_meses":1, "descripcion":"Servicio concierge todo incluido"},
            {"nombre":"Corporate",   "precio":1200.0, "duracion_meses":1, "descripcion":"Plan corporativo por persona"},
        ],
        "ejercicios": [
            ("Press Banca","Pecho","fuerza"),("Sentadilla","Piernas","fuerza"),
            ("Peso Muerto","Espalda","fuerza"),("TRX Row","Espalda","funcional"),
            ("TRX Push-up","Pecho","funcional"),("Kettlebell Turkish Get-up","Core","funcional"),
            ("Battle Ropes","Cuerpo completo","cardio"),("Sled Push","Piernas","funcional"),
            ("Box Jump","Piernas","pliometrico"),("Ski Erg","Cuerpo completo","cardio"),
            ("Assault Bike","Cuerpo completo","cardio"),("Pallof Press","Core","funcional"),
            ("Farmer Carry","Cuerpo completo","funcional"),("Romanian Deadlift","Piernas","fuerza"),
            ("Pull-up","Espalda","fuerza"),("Dips","Triceps","fuerza"),
            ("Cable Face Pull","Hombros","funcional"),("Goblet Squat","Piernas","funcional"),
            ("Landmine Press","Hombros","funcional"),("Nordic Curl","Isquiotibiales","fuerza"),
        ],
        "clases": [
            ("Personal Training","Sesion individual con entrenador certificado",60,1),
            ("TRX Suspension","Entrenamiento funcional con suspension trainer",50,12),
            ("Functional Movement","Movimientos multiarticulares y movilidad",55,15),
            ("Yoga Ejecutivo","Yoga orientado a estres ejecutivo",50,12),
            ("Spinning Premium","Spinning con metricas y coaching",45,16),
            ("Boxing Fit","Boxeo tecnico y cardio no competitivo",55,12),
            ("Recovery Lab","Foam rolling stretching y terapia frio calor",45,10),
            ("Assessment Mensual","Evaluacion fisica completa mensual",60,5),
        ],
        "rutina_categorias": ["Funcional","Fuerza","Performance","Recovery"],
        "dieta_tipo": "personalizada",
    },
]

DIETAS = {
    "alta_proteina_carbos": {
        "nombre":"CrossFit Performance","calorias":2800,"proteina":200,"carbos":280,"grasas":80,
        "notas":"Carbos pre-WOD, proteina post-WOD. Evitar ayuno prolongado.",
        "alimentos":["Arroz blanco","Pollo","Claras de huevo","Avena","Batata","Atun","Platano"],
    },
    "plant_based": {
        "nombre":"Nutricion Consciente","calorias":1900,"proteina":90,"carbos":240,"grasas":65,
        "notas":"Alimentacion basada en plantas. Enfasis en legumbres y cereales integrales.",
        "alimentos":["Tofu","Lentejas","Quinoa","Almendras","Garbanzos","Espinacas","Aguacate"],
    },
    "alta_proteina_calorias": {
        "nombre":"Protocolo Volumen Definicion","calorias":3200,"proteina":240,"carbos":320,"grasas":90,
        "notas":"Volumen 4 meses / definicion 2 meses. Alta proteina constante.",
        "alimentos":["Carne roja","Pollo","Huevos","Arroz","Papa","Proteina whey","Leche entera"],
    },
    "deficit_calorico": {
        "nombre":"Plan Perdida de Peso","calorias":1600,"proteina":130,"carbos":160,"grasas":50,
        "notas":"Deficit calorico moderado. No saltarse desayuno. Hidratacion constante.",
        "alimentos":["Pechuga de pollo","Verduras","Fruta","Yogur griego","Avena","Claras","Esparragos"],
    },
    "personalizada": {
        "nombre":"Plan Ejecutivo Personalizado","calorias":2300,"proteina":160,"carbos":230,"grasas":75,
        "notas":"Plan adaptado al objetivo individual. Revision quincenal con nutriologo.",
        "alimentos":["Salmon","Pavo","Nueces","Frutas del bosque","Aceite de oliva","Brocoli","Huevo"],
    },
}


def nombre_aleatorio(femenino=False):
    if femenino:
        return f"{RNG.choice(NOMBRES_F)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"
    return f"{RNG.choice(NOMBRES_M)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"


def email_slug(nombre, idx, dominio="gymprodev.com"):
    """Genera email único; si colisiona (mismo nombre + idx por truncado) agrega sufijo."""
    clean = nombre.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),(" ",".")]:
        clean = clean.replace(a, b)
    base = f"{clean[:20]}{idx}"
    email = f"{base}@{dominio}"
    # Garantía de unicidad dentro de la ejecución
    counter = 0
    while email in _USED_EMAILS:
        counter += 1
        email = f"{base}x{counter}@{dominio}"
    _USED_EMAILS.add(email)
    return email


def fecha_aleatoria(inicio_days=-730, fin_days=-30):
    base = datetime.now(timezone.utc)
    return base + timedelta(days=RNG.randint(inicio_days, fin_days))


def reset_all(mdb):
    """
    Limpieza atómica.
    - PG: TRUNCATE en una sola transacción usando engine.begin() (bypass ORM session).
          Si falla, lanza excepción y el seed se aborta limpiamente — sin estados inconsistentes.
    - MongoDB: drop() de cada colección y recreación de índices únicos antes de insertar.
    """
    print("Reset BD ===")

    # ── PostgreSQL ─────────────────────────────────────────────────────────────
    # Garantizar que el schema está al día antes de truncar.
    # db_upgrade() aplica todas las migraciones Alembic pendientes (idempotente).
    # En primera ejecución crea las tablas; en re-ejecuciones es no-op.
    print("  Aplicando migraciones Alembic...")
    db_upgrade()

    # Truncar todas las tablas en una sola transacción atómica.
    # Tras db_upgrade() las tablas siempre existen.
    # IMPORTANTE: usar nombres reales de tabla (plural, según __tablename__).
    with db.engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "facturas_suscripcion, suscripciones, tipos_membresia, ejercicios, "
            "tipos_clase, usuarios, gimnasios, planes_suscripcion, roles "
            "RESTART IDENTITY CASCADE"
        ))
    print("  PG truncado (atómico)")

    # ── MongoDB ────────────────────────────────────────────────────────────────
    for col in ["miembros", "miembro_membresia", "pagos", "asistencias",
                "progreso_fisico", "sesiones", "rutinas", "dietas",
                "membresias", "usuarios", "roles", "ventas"]:
        mdb[col].drop()

    # Índices únicos — previenen duplicados en re-ejecuciones parciales
    mdb.miembros.create_index("id_usuario_pg", unique=True, sparse=True)
    # No unique — un miembro puede tener múltiples membresías históricas (renovaciones)
    mdb.miembro_membresia.create_index("id_miembro", sparse=True)

    # Limpiar guard de emails para la nueva ejecución
    _USED_EMAILS.clear()

    print("  MongoDB limpiado + índices únicos creados\n")


def seed_pg_base():
    print("Roles + Planes SaaS ===")
    roles = {}
    # Roles de la plataforma:
    #   superadmin  → operador de la plataforma (sin gimnasio asignado)
    #   owner_gym   → dueño/admin del gimnasio (tenant admin)
    #   Entrenador  → trainer asignado al gimnasio
    #   Recepcionista → staff de front-desk
    #   Miembro     → cliente final
    for nombre in ["superadmin", "owner_gym", "Entrenador", "Recepcionista", "Miembro"]:
        r = Rol(nombre=nombre)
        db.session.add(r)
        db.session.flush()
        roles[nombre] = r
    db.session.commit()

    PLANES_SAAS = [
        {"nombre":"basico",     "precio_mensual_mxn": 49900,  "max_miembros":  50, "descripcion":"Hasta 50 miembros."},
        {"nombre":"pro",        "precio_mensual_mxn":149900,  "max_miembros": 200, "descripcion":"Hasta 200 miembros + Analytics."},
        {"nombre":"enterprise", "precio_mensual_mxn":399900,  "max_miembros":None, "descripcion":"Ilimitado + SLA."},
    ]
    planes_map = {}
    for pd in PLANES_SAAS:
        p = PlanSuscripcion(**pd)
        db.session.add(p)
        db.session.flush()
        planes_map[p.nombre] = p
    db.session.commit()
    # ── Usuario superadmin de plataforma (sin gimnasio) ───────────────────────
    sa_user = Usuario(
        nombre="Platform Admin",
        email="superadmin@gymprodev.com",
        id_rol=roles["superadmin"].id,
        id_gimnasio=None,
        activo=True,
    )
    sa_user.set_password("SuperAdmin1234!")
    db.session.add(sa_user)
    db.session.commit()
    _USED_EMAILS.add("superadmin@gymprodev.com")
    print(f"  Superadmin: superadmin@gymprodev.com")

    print(f"  {len(roles)} roles, {len(planes_map)} planes SaaS")
    return roles, planes_map


def seed_gimnasio(cfg, roles, planes_map, idx_start):
    print(f"\n{'='*56}")
    print(f"  {cfg['nombre'].upper()}")
    print(f"{'='*56}")

    gym = Gimnasio(nombre=cfg["nombre"], plan=cfg["plan"],
                   email_contacto=cfg["email_contacto"],
                   telefono=cfg["telefono"], activo=True)
    db.session.add(gym)
    db.session.flush()

    ahora = datetime.now(timezone.utc)
    plan  = planes_map[cfg["plan"]]
    sub   = Suscripcion(id_gimnasio=gym.id, id_plan=plan.id, estado="active",
                        fecha_inicio=ahora, fecha_proximo_cobro=ahora+timedelta(days=30))
    db.session.add(sub)
    db.session.flush()
    db.session.add(FacturaSuscripcion(
        id_suscripcion=sub.id, monto=plan.precio_mensual_mxn, moneda="MXN",
        estado="pagada", fecha_emision=ahora, fecha_vencimiento=ahora+timedelta(days=30)))

    admin = Usuario(nombre=cfg["admin_nombre"], email=cfg["admin_email"],
                    id_rol=roles["owner_gym"].id, id_gimnasio=gym.id, activo=True)
    admin.set_password("Admin1234!")
    db.session.add(admin)
    db.session.flush()
    _USED_EMAILS.add(cfg["admin_email"])   # registrar para evitar colisión
    print(f"  Admin: {cfg['admin_email']}")

    idx = idx_start
    miembros_pg = []

    for _ in range(cfg["n_trainers"]):
        idx += 1
        femenino = RNG.random() > 0.5
        nombre = nombre_aleatorio(femenino)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Entrenador"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(f"Train{idx}!")
        db.session.add(u); db.session.flush()

    for _ in range(cfg["n_staff"]):
        idx += 1
        nombre = nombre_aleatorio(RNG.random() > 0.4)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Recepcionista"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(f"Recep{idx}!")
        db.session.add(u); db.session.flush()

    for _ in range(cfg["n_miembros"]):
        idx += 1
        femenino = RNG.random() > cfg["gen_ratio"]
        nombre = nombre_aleatorio(femenino)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Miembro"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(f"Gym{idx}!")
        db.session.add(u); db.session.flush()
        miembros_pg.append(u)

    db.session.commit()

    tm_map = {}
    for tm_data in cfg["membresias"]:
        tm = TipoMembresia(id_gimnasio=gym.id, activo=True, **tm_data)
        db.session.add(tm); db.session.flush()
        tm_map[tm_data["nombre"]] = tm

    for (nombre_ej, grupo, tipo_ej) in cfg["ejercicios"]:
        db.session.add(Ejercicio(id_gimnasio=gym.id, nombre=nombre_ej,
                                 grupo_muscular=grupo, tipo=tipo_ej,
                                 series=RNG.choice([3,4,5]),
                                 repeticiones=RNG.choice(["5","8","10","10-12","12-15","AMRAP"])))
    db.session.flush()

    for (nombre_cl, desc, dur, cap) in cfg["clases"]:
        db.session.add(TipoClase(id_gimnasio=gym.id, nombre=nombre_cl,
                                 descripcion=desc, duracion_minutos=dur, capacidad_max=cap))
    db.session.flush()
    db.session.commit()

    print(f"  Miembros: {cfg['n_miembros']}  Entrenadores: {cfg['n_trainers']}  Staff: {cfg['n_staff']}")
    print(f"  TipoMem: {len(tm_map)}  Ejercicios: {len(cfg['ejercicios'])}  Clases: {len(cfg['clases'])}")
    return gym, miembros_pg, tm_map, idx


def seed_mongo_gym(gym, miembros_pg, tm_map, cfg):
    mdb    = get_db()
    dieta  = DIETAS[cfg["dieta_tipo"]]
    gym_id = gym.id
    tm_items = list(tm_map.items())
    total  = {k: 0 for k in ["miembros","miembro_membresia","pagos",
                               "asistencias","progreso","sesiones","rutinas","dietas"]}

    for usuario in miembros_pg:
        peso_inicial = round(RNG.uniform(52, 115), 1)
        estatura     = round(RNG.uniform(1.54, 1.92), 2)
        sexo         = "Masculino" if RNG.random() < cfg["gen_ratio"] else "Femenino"
        objetivo     = RNG.choice(cfg["objetivos"])
        fecha_nac    = date.today() - timedelta(days=RNG.randint(18*365, 58*365))
        fecha_reg    = fecha_aleatoria(-600, -60)

        uid = ObjectId()
        miembro_doc = {
            "id_usuario_pg":  usuario.id,
            "nombre":         usuario.nombre,
            "email":          usuario.email,
            "id_gimnasio_pg": gym_id,
            "telefono":       f"+52-664-{RNG.randint(100,999)}-{RNG.randint(1000,9999)}",
            "fecha_nacimiento": str(fecha_nac),
            "sexo":           sexo,
            "peso_inicial":   peso_inicial,
            "estatura":       estatura,
            "estado":         RNG.choices(["Activo","Activo","Activo","Inactivo"],weights=[85,85,85,15])[0],
            "objetivo":       objetivo,
            "peso_objetivo":  round(peso_inicial * RNG.uniform(0.82, 1.12), 1),
            "grasa_objetivo": round(RNG.uniform(9, 22), 1),
            "masa_muscular_objetivo": round(RNG.uniform(28, 52), 1),
            "fecha_registro": fecha_reg,
            "created_at":     fecha_reg,
        }
        # upsert por id_usuario_pg — idempotente en re-ejecuciones
        res = mdb.miembros.find_one_and_update(
            {"id_usuario_pg": usuario.id},
            {"$setOnInsert": {"_id": uid}, "$set": miembro_doc},
            upsert=True,
            return_document=True,
        )
        uid = res["_id"]   # usar el _id real (nuevo o existente)
        total["miembros"] += 1

        # Membresía — id entero PG
        tm_nombre, tm_obj = RNG.choice(tm_items)
        dur_dias = (tm_obj.duracion_meses or 1) * 30
        f_inicio = fecha_reg + timedelta(days=2)
        f_fin    = f_inicio  + timedelta(days=dur_dias)
        activa   = f_fin.replace(tzinfo=None) > datetime.now()

        # upsert por id_miembro — un solo registro de membresía activa por miembro
        mdb.miembro_membresia.update_one(
            {"id_miembro": uid},
            {"$set": {
                "id_membresia": tm_obj.id,
                "fecha_inicio": str(f_inicio.date()) if hasattr(f_inicio,"date") else str(f_inicio),
                "fecha_fin":    str(f_fin.date())    if hasattr(f_fin,"date")    else str(f_fin),
                "estado":       "Activa" if activa else "Vencida",
                "created_at":   f_inicio,
            }},
            upsert=True,
        )
        total["miembro_membresia"] += 1

        # Pagos
        pagos_bulk = []
        for p_idx in range(RNG.randint(3, 10)):
            f_pago = fecha_reg + timedelta(days=p_idx*30 + RNG.randint(0,5))
            _, tm_p = RNG.choice(tm_items)
            f_pago_dt = f_pago if isinstance(f_pago, datetime) else datetime.fromisoformat(str(f_pago))
            pagos_bulk.append({
                "id_miembro":  uid, "id_gimnasio": gym_id,
                "monto":       float(tm_p.precio),
                "metodo_pago": RNG.choice(METODOS_PAGO),
                "concepto":    f"Membresia {tm_p.nombre}",
                "fecha_pago":  f_pago_dt,
                "estado":      "Pagado",
                "referencia":  f"REF{RNG.randint(100000,999999)}",
            })
        mdb.pagos.insert_many(pagos_bulk)
        total["pagos"] += len(pagos_bulk)

        # Asistencias
        dias_asist = RNG.sample(range(5, 550), RNG.randint(20, 45))
        asist_bulk = []
        for d in dias_asist:
            f = fecha_reg + timedelta(days=d)
            f_date = f.date() if hasattr(f,"date") else f
            hora = f"{RNG.randint(6,21):02d}:{RNG.choice(['00','15','30','45'])}"
            asist_bulk.append({
                "id_miembro":   uid, "id_gimnasio": gym_id,
                "fecha":        str(f_date),
                "hora_entrada": hora,
                "hora_salida":  f"{min(23,int(hora[:2])+RNG.randint(1,2)):02d}:{hora[3:]}",
            })
        mdb.asistencias.insert_many(asist_bulk)
        total["asistencias"] += len(asist_bulk)

        # Progreso
        peso_a, grasa_a, musc_a = peso_inicial, round(RNG.uniform(16,38),1), round(RNG.uniform(26,44),1)
        prog_bulk = []
        for mes in range(RNG.randint(8, 22)):
            f = fecha_reg + timedelta(days=mes*28 + RNG.randint(0,7))
            peso_a  = round(max(48, peso_a  + RNG.uniform(-1.0, 0.6)), 1)
            grasa_a = round(max(7,  grasa_a + RNG.uniform(-0.6, 0.3)), 1)
            musc_a  = round(min(62, musc_a  + RNG.uniform(-0.1, 0.5)), 1)
            prog_bulk.append({
                "id_miembro":      uid,
                "fecha_registro":  str(f.date()) if hasattr(f,"date") else str(f),
                "peso":            peso_a, "estatura": estatura,
                "imc":             round(peso_a/(estatura**2),1),
                "grasa_corporal":  grasa_a, "masa_muscular": musc_a,
                "presion_arterial":f"{RNG.randint(108,138)}/{RNG.randint(68,92)}",
                "frecuencia_cardiaca": RNG.randint(56,88),
                "observaciones":   RNG.choice(["Buen progreso","Mantener ritmo",
                                               "Aumentar intensidad","Revisar nutricion",""]),
            })
        mdb.progreso_fisico.insert_many(prog_bulk)
        total["progreso"] += len(prog_bulk)

        # Sesiones
        tipos_ses = [c[0] for c in cfg["clases"]]
        ses_bulk  = []
        for _ in range(RNG.randint(10, 22)):
            f = fecha_reg + timedelta(days=RNG.randint(5,520))
            ses_bulk.append({
                "id_miembro":  uid, "id_gimnasio": gym_id,
                "fecha":       str(f.date()) if hasattr(f,"date") else str(f),
                "hora_inicio": f"{RNG.randint(6,21):02d}:00",
                "duracion_min":RNG.choice([45,55,60,75,90]),
                "tipo":        RNG.choice(tipos_ses),
                "estado":      RNG.choices(["completed","cancelled","pending"],weights=[75,15,10])[0],
                "calorias_est":RNG.randint(220,720),
            })
        mdb.sesiones.insert_many(ses_bulk)
        total["sesiones"] += len(ses_bulk)

        # Rutinas
        ejercicios_list = [e[0] for e in cfg["ejercicios"]]
        dias_nombres    = ["Lunes","Martes","Miercoles","Jueves","Viernes","Sabado"]
        rutinas_bulk    = []
        for _ in range(RNG.randint(1, 3)):
            cat    = RNG.choice(cfg["rutina_categorias"])
            n_dias = RNG.randint(3, 5)
            dias   = []
            for i in range(n_dias):
                ejercs = RNG.sample(ejercicios_list, min(4, len(ejercicios_list)))
                dias.append({
                    "dia": dias_nombres[i % len(dias_nombres)],
                    "ejercicios": [
                        {"nombre": ej, "series": RNG.randint(3,5),
                         "repeticiones": RNG.choice([6,8,10,12,15,"AMRAP","30s"]),
                         "peso_sugerido": round(RNG.uniform(8,100),1), "notas": ""}
                        for ej in ejercs
                    ],
                })
            rutinas_bulk.append({
                "id_miembro":       uid, "id_gimnasio": gym_id,
                "nombre":           f"Rutina {cat} - {objetivo[:18]}",
                "categoria":        cat,
                "dificultad":       RNG.choice(["Principiante","Intermedio","Avanzado"]),
                "duracion_minutos": RNG.choice([45,60,75,90]),
                "descripcion":      f"Programa de {cat.lower()} orientado a {objetivo.lower()}.",
                "dias":             dias, "activa": True, "created_at": fecha_reg,
            })
        mdb.rutinas.insert_many(rutinas_bulk)
        total["rutinas"] += len(rutinas_bulk)

        # Dieta
        v = round(RNG.uniform(0.88, 1.12), 2)
        mdb.dietas.insert_one({
            "id_miembro":     uid, "id_gimnasio": gym_id,
            "nombre":         dieta["nombre"],
            "calorias_dia":   round(dieta["calorias"]*v),
            "proteina_g":     round(dieta["proteina"]*v),
            "carbos_g":       round(dieta["carbos"]*v),
            "grasas_g":       round(dieta["grasas"]*v),
            "notas":          dieta["notas"],
            "alimentos_base": dieta["alimentos"],
            "objetivo":       objetivo,
            "activa":         True, "created_at": fecha_reg,
        })
        total["dietas"] += 1

    return total


def seed():
    app = create_app()
    with app.app_context():
        mdb = get_db()
        reset_all(mdb)
        roles, planes_map = seed_pg_base()

        grand_total = {}
        idx = 0
        credentials = []

        for cfg in GIMNASIOS_CFG:
            gym, miembros_pg, tm_map, idx = seed_gimnasio(cfg, roles, planes_map, idx)
            credentials.append((cfg["nombre"], cfg["admin_email"], "Admin1234!"))
            totales = seed_mongo_gym(gym, miembros_pg, tm_map, cfg)
            print(f"  MongoDB docs: ", end="")
            for col, n in totales.items():
                print(f"{col}={n}", end="  ")
                grand_total[col] = grand_total.get(col, 0) + n
            print()

        print(f"\n{'='*56}")
        print("SEED COMPLETADO")
        print(f"{'='*56}")
        print(f"  PG: {Rol.query.count()} roles | {Gimnasio.query.count()} gimnasios | "
              f"{Usuario.query.count()} usuarios | {TipoMembresia.query.count()} membresias PG")
        print(f"  MongoDB total: {sum(grand_total.values())} documentos")
        print()
        print(f"  {'Gimnasio':<30} {'Email admin':<36} Password")
        print(f"  {'-'*30} {'-'*36} {'-'*12}")
        for nombre, email, pwd in credentials:
            print(f"  {nombre:<30} {email:<36} {pwd}")
        print()


if __name__ == "__main__":
    seed()
