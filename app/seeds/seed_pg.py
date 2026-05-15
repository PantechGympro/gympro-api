"""
seeds/seed_pg.py - Datos iniciales para PostgreSQL + MongoDB.

Genera:
  PostgreSQL:
    - 4 roles del sistema
    - 3 gimnasios (basico / pro / enterprise)
    - 3 planes de suscripcion + 1 suscripcion trialing por gimnasio
    - 120 usuarios: 3 admins + 9 entrenadores + 6 recepcionistas + 102 miembros

  MongoDB:
    - 5 tipos de membresia
    - miembros con perfil + membresia + asistencias + progreso + sesiones + rutinas + pagos

Uso:
  docker compose exec api python -m app.seeds.seed_pg
"""
import os, sys, random
from datetime import datetime, timedelta, timezone, date
from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models.pg.rol                 import Rol
from app.models.pg.gimnasio            import Gimnasio
from app.models.pg.usuario             import Usuario
from app.models.pg.plan_suscripcion    import PlanSuscripcion
from app.models.pg.suscripcion         import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.mongo import get_db

# helpers

RNG = random.Random(42)

NOMBRES_M = ["Carlos", "Luis", "Miguel", "Jorge", "Andres", "Ricardo", "Fernando",
             "Sergio", "Pablo", "Diego", "Alejandro", "Roberto", "Hector", "Ivan",
             "Oscar", "Raul", "Eduardo", "Marco", "Javier", "Victor"]
NOMBRES_F = ["Maria", "Ana", "Laura", "Sofia", "Daniela", "Valentina", "Gabriela",
             "Fernanda", "Paola", "Claudia", "Diana", "Karen", "Lorena", "Patricia",
             "Sandra", "Monica", "Adriana", "Natalia", "Isabella", "Camila"]
APELLIDOS  = ["Garcia", "Martinez", "Lopez", "Gonzalez", "Rodriguez", "Hernandez",
              "Perez", "Sanchez", "Ramirez", "Torres", "Flores", "Diaz", "Morales",
              "Jimenez", "Ruiz", "Gutierrez", "Cruz", "Ortiz", "Castillo", "Reyes"]

OBJETIVOS   = ["Perdida de peso", "Ganancia muscular", "Definicion", "Resistencia",
               "Rehabilitacion", "Acondicionamiento general", "Fuerza maxima"]
GRUPOS_MUS  = ["Pecho", "Espalda", "Piernas", "Hombros", "Biceps", "Triceps",
               "Abdomen", "Gluteos", "Pantorrillas", "Trapecio"]
EJERCICIOS  = {
    "Pecho":       ["Press banca plano", "Press inclinado", "Aperturas", "Fondos"],
    "Espalda":     ["Jalon al pecho", "Remo con barra", "Dominadas", "Remo en maquina"],
    "Piernas":     ["Sentadilla", "Prensa", "Extension", "Curl femoral", "Peso muerto"],
    "Hombros":     ["Press militar", "Elevaciones laterales", "Face pull"],
    "Biceps":      ["Curl con barra", "Curl martillo", "Curl concentrado"],
    "Triceps":     ["Extension triceps", "Press cerrado", "Patada de triceps"],
    "Abdomen":     ["Crunch", "Plancha", "Elevacion de piernas", "Russian twist"],
    "Gluteos":     ["Hip thrust", "Peso muerto rumano", "Abductor"],
    "Pantorrillas":["Elevacion de talones de pie", "Elevacion sentado"],
    "Trapecio":    ["Encogimiento", "Remo al menton"],
}
TIPOS_MEMBRESIA = [
    {"nombre": "Basica",                "precio": 299.00,  "duracion_dias": 30,  "descripcion": "Acceso general"},
    {"nombre": "Premium",               "precio": 499.00,  "duracion_dias": 30,  "descripcion": "Acceso + clases grupales"},
    {"nombre": "Anual",                 "precio": 2999.00, "duracion_dias": 365, "descripcion": "Acceso total 12 meses"},
    {"nombre": "Estudiante",            "precio": 199.00,  "duracion_dias": 30,  "descripcion": "Descuento 33% con credencial"},
    {"nombre": "Entrenamiento Personal","precio": 899.00,  "duracion_dias": 30,  "descripcion": "Acceso + 8 sesiones con entrenador"},
]
METODOS_PAGO = ["Efectivo", "Tarjeta debito", "Tarjeta credito", "Transferencia", "QR"]


def nombre_aleatorio():
    if RNG.random() < 0.5:
        return f"{RNG.choice(NOMBRES_M)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"
    return f"{RNG.choice(NOMBRES_F)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"


def fecha_aleatoria(inicio_days=-730, fin_days=-1):
    base = datetime.now(timezone.utc)
    return base + timedelta(days=RNG.randint(inicio_days, fin_days))


def slug(nombre: str, idx: int) -> str:
    partes = nombre.lower().split()
    return f"{partes[0]}{idx}@gymprodev.com"


# PostgreSQL

def seed_pg():
    print("PostgreSQL ===")

    roles_data = ["Administrador", "Entrenador", "Recepcionista", "Miembro"]
    roles = {}
    print("Roles:")
    for nombre in roles_data:
        r = Rol.query.filter_by(nombre=nombre).first()
        if not r:
            r = Rol(nombre=nombre)
            db.session.add(r)
            db.session.flush()
            print(f"  + {nombre}")
        else:
            print(f"  ok {nombre}")
        roles[nombre] = r
    db.session.commit()

    gimnasios_data = [
        {"nombre": "GymPro Centro",  "plan": "pro",        "email_contacto": "centro@gympro.mx",  "telefono": "+52-664-100-0001"},
        {"nombre": "GymPro Norte",   "plan": "basico",     "email_contacto": "norte@gympro.mx",   "telefono": "+52-664-100-0002"},
        {"nombre": "GymPro Premium", "plan": "enterprise", "email_contacto": "premium@gympro.mx", "telefono": "+52-664-100-0003"},
    ]
    gimnasios = []
    print("\nGimnasios:")
    for gd in gimnasios_data:
        g = Gimnasio.query.filter_by(nombre=gd["nombre"]).first()
        if not g:
            g = Gimnasio(**gd, activo=True)
            db.session.add(g)
            db.session.flush()
            print(f"  + {g.nombre} [{g.plan}]")
        else:
            print(f"  ok {g.nombre}")
        gimnasios.append(g)
    db.session.commit()

    print("\nUsuarios PostgreSQL:")
    idx = 0
    for gi, gym in enumerate(gimnasios):
        staff = [
            ("Administrador", 1),
            ("Entrenador",    3),
            ("Recepcionista", 2),
            ("Miembro",      34),
        ]
        if gi == 0:
            email = "admin@gymprosaas.com"
            if not Usuario.query.filter_by(email=email).first():
                u = Usuario(nombre="Administrador GymPro", email=email,
                            id_rol=roles["Administrador"].id, id_gimnasio=gym.id, activo=True)
                u.set_password("Admin1234!")
                db.session.add(u)
                db.session.flush()
            staff[0] = ("Administrador", 0)

        for rol_nombre, cantidad in staff:
            for _ in range(cantidad):
                idx += 1
                nombre = nombre_aleatorio()
                email  = slug(nombre, idx)
                if not Usuario.query.filter_by(email=email).first():
                    u = Usuario(
                        nombre=nombre, email=email,
                        id_rol=roles[rol_nombre].id,
                        id_gimnasio=gym.id, activo=True,
                    )
                    u.set_password(f"Pass{idx}!")
                    db.session.add(u)
                    db.session.flush()

    db.session.commit()
    total = Usuario.query.count()
    print(f"  Total usuarios PG: {total}")

    return gimnasios, roles


# Billing

def seed_billing(gimnasios):
    print("\nBilling / Suscripciones ===")

    PLANES = [
        {"nombre": "basico",     "precio_mensual_mxn":  49900, "max_miembros":  50,   "descripcion": "Hasta 50 miembros activos."},
        {"nombre": "pro",        "precio_mensual_mxn": 149900, "max_miembros": 200,   "descripcion": "Hasta 200 miembros + Analytics (Spark)."},
        {"nombre": "enterprise", "precio_mensual_mxn": 399900, "max_miembros": None,  "descripcion": "Miembros ilimitados + SLA + soporte dedicado."},
    ]

    planes_map = {}
    for pd in PLANES:
        p = PlanSuscripcion.query.filter_by(nombre=pd["nombre"]).first()
        if not p:
            p = PlanSuscripcion(**pd)
            db.session.add(p)
            db.session.flush()
            print(f"  + Plan {p.nombre} (${p.precio_mensual_mxn / 100:,.2f} MXN/mes)")
        else:
            print(f"  ok Plan {p.nombre}")
        planes_map[p.nombre] = p
    db.session.commit()

    ahora = datetime.now(timezone.utc)
    for gym in gimnasios:
        existing = Suscripcion.query.filter_by(id_gimnasio=gym.id).first()
        if existing:
            print(f"  ok Suscripcion gym {gym.id} ({existing.estado})")
            continue

        plan_nombre = gym.plan if isinstance(gym.plan, str) else gym.plan.value
        plan = planes_map.get(plan_nombre, planes_map["basico"])
        sub = Suscripcion(
            id_gimnasio         = gym.id,
            id_plan             = plan.id,
            estado              = "trialing",
            fecha_inicio        = ahora,
            fecha_proximo_cobro = ahora + timedelta(days=14),
        )
        db.session.add(sub)
        db.session.flush()

        db.session.add(FacturaSuscripcion(
            id_suscripcion    = sub.id,
            monto             = plan.precio_mensual_mxn,
            moneda            = "MXN",
            estado            = "pendiente",
            fecha_emision     = ahora,
            fecha_vencimiento = ahora + timedelta(days=14),
        ))
        print(f"  + Suscripcion gym {gym.id} -> {plan.nombre} (trialing 14d)")

    db.session.commit()


# MongoDB

def seed_mongo(gimnasios):
    """
    Genera documentos MongoDB para todos los usuarios con rol Miembro en PG.
    Consulta directamente la BD para evitar bugs de zip/indices.
    """
    print("\nMongoDB ===")
    mdb = get_db()

    print("Membresias:")
    tipo_ids = []
    for tm in TIPOS_MEMBRESIA:
        existing = mdb.membresias.find_one({"nombre": tm["nombre"]})
        if not existing:
            r = mdb.membresias.insert_one(tm)
            tipo_ids.append(r.inserted_id)
            print(f"  + {tm['nombre']}")
        else:
            tipo_ids.append(existing["_id"])
            print(f"  ok {tm['nombre']}")

    # Obtener miembros PG directamente de la BD (evita bugs de indices)
    rol_miembro = Rol.query.filter_by(nombre="Miembro").first()
    if not rol_miembro:
        print("  ERROR: rol Miembro no encontrado")
        return 0

    # Construir mapa gym_id -> gym object
    gym_map = {g.id: g for g in gimnasios}

    miembros_pg = Usuario.query.filter_by(id_rol=rol_miembro.id).all()
    total_docs = {"miembros": 0, "asistencias": 0, "progreso": 0,
                  "sesiones": 0, "rutinas": 0, "pagos": 0, "miembro_membresia": 0}

    print(f"\nGenerando datos para {len(miembros_pg)} miembros...")

    for usuario in miembros_pg:
        gym = gym_map.get(usuario.id_gimnasio)
        if not gym:
            continue

        uid          = ObjectId()
        peso_inicial = round(RNG.uniform(55, 110), 1)
        estatura     = round(RNG.uniform(1.55, 1.90), 2)
        sexo         = RNG.choice(["Masculino", "Femenino"])
        objetivo     = RNG.choice(OBJETIVOS)
        fecha_nac    = date.today() - timedelta(days=RNG.randint(18*365, 55*365))
        fecha_reg    = fecha_aleatoria(-540, -30)

        if not mdb.miembros.find_one({"id_usuario_pg": usuario.id}):
            mdb.miembros.insert_one({
                "_id":              uid,
                "id_usuario_pg":    usuario.id,
                "nombre":           usuario.nombre,
                "email":            usuario.email,
                "id_gimnasio_pg":   gym.id,
                "telefono":         f"+52-664-{RNG.randint(100,999)}-{RNG.randint(1000,9999)}",
                "fecha_nacimiento": str(fecha_nac),
                "sexo":             sexo,
                "peso_inicial":     peso_inicial,
                "estatura":         estatura,
                "estado":           "Activo",
                "objetivo":         objetivo,
                "peso_objetivo":    round(peso_inicial * RNG.uniform(0.85, 1.10), 1),
                "grasa_objetivo":   round(RNG.uniform(10, 22), 1),
                "masa_muscular_objetivo": round(RNG.uniform(30, 50), 1),
                "fecha_registro":   fecha_reg,
                "created_at":       fecha_reg,
            })
            total_docs["miembros"] += 1
        else:
            uid = mdb.miembros.find_one({"id_usuario_pg": usuario.id})["_id"]

        tipo_id  = RNG.choice(tipo_ids)
        tipo_doc = mdb.membresias.find_one({"_id": tipo_id})
        f_inicio = fecha_reg + timedelta(days=1)
        f_fin    = f_inicio  + timedelta(days=tipo_doc["duracion_dias"])
        estado_mm = "Activa" if f_fin.replace(tzinfo=None) > datetime.now() else "Vencida"

        if not mdb.miembro_membresia.find_one({"id_miembro": uid}):
            mdb.miembro_membresia.insert_one({
                "id_miembro":   uid,
                "id_membresia": tipo_id,
                "fecha_inicio": str(f_inicio.date()),
                "fecha_fin":    str(f_fin.date()),
                "estado":       estado_mm,
                "created_at":   f_inicio,
            })
            total_docs["miembro_membresia"] += 1

        dias_asistencia = RNG.sample(range(1, 540), RNG.randint(25, 40))
        asistencias_bulk = []
        for d in dias_asistencia:
            f = (fecha_reg + timedelta(days=d)).date()
            hora_entrada = f"{RNG.randint(6,20):02d}:{RNG.choice(['00','15','30','45'])}"
            asistencias_bulk.append({
                "id_miembro":   uid,
                "id_gimnasio":  gym.id,
                "fecha":        str(f),
                "hora_entrada": hora_entrada,
                "hora_salida":  f"{int(hora_entrada[:2]) + RNG.randint(1,2):02d}:{hora_entrada[3:]}",
            })
        if asistencias_bulk:
            mdb.asistencias.insert_many(asistencias_bulk)
            total_docs["asistencias"] += len(asistencias_bulk)

        peso_actual    = peso_inicial
        grasa_actual   = round(RNG.uniform(18, 35), 1)
        musculo_actual = round(RNG.uniform(28, 42), 1)
        progreso_bulk  = []
        for mes in range(RNG.randint(10, 20)):
            f = fecha_reg + timedelta(days=mes * 28 + RNG.randint(0, 7))
            peso_actual    = round(max(50, peso_actual    + RNG.uniform(-0.8, 0.5)), 1)
            grasa_actual   = round(max(8,  grasa_actual   + RNG.uniform(-0.5, 0.3)), 1)
            musculo_actual = round(min(60, musculo_actual + RNG.uniform(-0.1, 0.4)), 1)
            imc = round(peso_actual / (estatura ** 2), 1)
            progreso_bulk.append({
                "id_miembro":          uid,
                "fecha_registro":      str(f.date()) if hasattr(f, 'date') else str(f),
                "peso":                peso_actual,
                "estatura":            estatura,
                "imc":                 imc,
                "grasa_corporal":      grasa_actual,
                "masa_muscular":       musculo_actual,
                "presion_arterial":    f"{RNG.randint(110,135)}/{RNG.randint(70,90)}",
                "frecuencia_cardiaca": RNG.randint(58, 85),
                "observaciones":       RNG.choice(["Buen progreso", "Mantener ritmo", "Aumentar intensidad", ""]),
            })
        if progreso_bulk:
            mdb.progreso_fisico.insert_many(progreso_bulk)
            total_docs["progreso"] += len(progreso_bulk)

        sesiones_bulk = []
        for s in range(RNG.randint(12, 20)):
            f = fecha_reg + timedelta(days=RNG.randint(5, 520))
            estado_ses = RNG.choices(["completed","cancelled","pending"], weights=[75,15,10])[0]
            sesiones_bulk.append({
                "id_miembro":   uid,
                "id_gimnasio":  gym.id,
                "fecha":        str(f.date()) if hasattr(f, 'date') else str(f),
                "hora_inicio":  f"{RNG.randint(6,20):02d}:00",
                "duracion_min": RNG.choice([45, 60, 75, 90]),
                "tipo":         RNG.choice(["Fuerza", "Cardio", "HIIT", "Funcional", "Yoga"]),
                "estado":       estado_ses,
                "calorias_est": RNG.randint(250, 700),
            })
        if sesiones_bulk:
            mdb.sesiones.insert_many(sesiones_bulk)
            total_docs["sesiones"] += len(sesiones_bulk)

        rutinas_bulk = []
        for _ in range(RNG.randint(2, 4)):
            grupos      = RNG.sample(GRUPOS_MUS, RNG.randint(3, 5))
            dias_semana = ["Lunes","Martes","Miercoles","Jueves","Viernes","Sabado"]
            dias = []
            for i, grupo in enumerate(grupos):
                ejercs = RNG.sample(EJERCICIOS[grupo], min(3, len(EJERCICIOS[grupo])))
                dias.append({
                    "dia":            dias_semana[i % len(dias_semana)],
                    "grupo_muscular": grupo,
                    "ejercicios":     [
                        {"nombre": ej, "series": RNG.randint(3,5),
                         "repeticiones": RNG.choice([8,10,12,15]),
                         "peso": round(RNG.uniform(10, 80), 1), "notas": ""}
                        for ej in ejercs
                    ],
                })
            letra = RNG.choice(["A", "B", "C"])
            rutinas_bulk.append({
                "id_miembro":       uid,
                "id_gimnasio":      gym.id,
                "nombre":           f"Rutina {letra} - {objetivo[:15]}",
                "categoria":        RNG.choice(["Fuerza","Hipertrofia","Cardio","Funcional"]),
                "dificultad":       RNG.choice(["Principiante","Intermedio","Avanzado"]),
                "duracion_minutos": RNG.choice([45, 60, 75, 90]),
                "descripcion":      f"Rutina orientada a {objetivo.lower()}",
                "dias":             dias,
                "activa":           True,
                "created_at":       fecha_reg,
            })
        if rutinas_bulk:
            mdb.rutinas.insert_many(rutinas_bulk)
            total_docs["rutinas"] += len(rutinas_bulk)

        pagos_bulk = []
        for p in range(RNG.randint(4, 10)):
            f = fecha_reg + timedelta(days=p * 30 + RNG.randint(0, 5))
            tipo_doc2 = mdb.membresias.find_one({"_id": RNG.choice(tipo_ids)})
            pagos_bulk.append({
                "id_miembro":  uid,
                "id_gimnasio": gym.id,
                "monto":       tipo_doc2["precio"],
                "metodo_pago": RNG.choice(METODOS_PAGO),
                "concepto":    f"Membresia {tipo_doc2['nombre']}",
                "fecha_pago":  f if isinstance(f, datetime) else datetime.fromisoformat(str(f)),
                "estado":      "Pagado",
                "referencia":  f"REF{RNG.randint(100000,999999)}",
            })
        if pagos_bulk:
            mdb.pagos.insert_many(pagos_bulk)
            total_docs["pagos"] += len(pagos_bulk)

    print("\nDocumentos insertados en MongoDB:")
    total = 0
    for col, n in total_docs.items():
        print(f"  {col:<22} {n:>5}")
        total += n
    print(f"  TOTAL                  {total:>5}")
    return total


# Entry point

def seed():
    app = create_app()
    with app.app_context():
        gimnasios, roles = seed_pg()
        seed_billing(gimnasios)
        total_mongo = seed_mongo(gimnasios)

        pg_total = Usuario.query.count()
        print(f"\nSeed completado")
        print(f"  PostgreSQL: {pg_total} usuarios en {len(gimnasios)} gimnasios")
        print(f"  MongoDB:    {total_mongo}+ documentos")
        print(f"\n  Login admin: admin@gymprosaas.com / Admin1234!")


if __name__ == "__main__":
    seed()
