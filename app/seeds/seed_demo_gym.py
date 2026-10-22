# -*- coding: utf-8 -*-
"""
seed_demo_gym.py
-----------------
Agrega un gimnasio de PRUEBA completo (varias sucursales, staff y hasta 500
usuarios en total) con ~3 meses de historial simulado, usando EXACTAMENTE
el mismo esquema que ya usa `app/seeds/seed_pg.py` -- el seed real de este
proyecto -- confirmado leyendo ese archivo línea por línea en el repo.

Diferencia clave con seed_pg.py: este script es ADITIVO. seed_pg.py hace
`reset_all()` (TRUNCATE de Postgres + drop() de las colecciones de Mongo)
antes de generar datos; ESTE script no toca nada de lo que ya existe --
reutiliza los roles y los planes de suscripción YA sembrados (los crea
seed_pg_base(), que solo corre una vez) y solo agrega sucursales, staff y
miembros nuevos. Así no se pierden los miembros/staff reales de prueba que
ya tengas, ni el gimnasio piloto.

Esquema confirmado contra api/app/seeds/seed_pg.py, api/app/utils/alcance.py
y api/app/routes/owner_gym/sucursales.py (SCRUM-216/222/223) tras releer
esos archivos línea por línea -- no es el esquema de la versión anterior
de este script:
  - Migración 017/019: todo Gimnasio ahora pertenece a una Organizacion
    (gimnasios.id_organizacion, NOT NULL) -- hay que crearla ANTES de
    insertar el Gimnasio, con el helper real app.utils.alcance.
    crear_organizacion_para_gimnasio(), exactamente como hace seed_gimnasio()
    en seed_pg.py (y con el mismo cuidado de orden: ese helper hace su
    propio flush() interno, así que si el Gimnasio ya estaba en la sesión
    revienta igual que sin Organizacion -- ver el comentario en alcance.py).
  - UN SOLO Propietario administrando VARIAS sucursales de la MISMA
    Organizacion (releyendo sucursales.py se confirmó que esto ya está
    soportado por completo, no es una pendiente de HU-42 como asumía una
    versión anterior de este comentario): solo la SEDE PRINCIPAL (la
    primera sucursal, "Centro") crea su propia Organizacion vía
    crear_organizacion_para_gimnasio() y recibe Suscripcion/FacturaSuscripcion
    y el único Usuario con rol Propietario. Las sucursales siguientes
    (Norte, Sur) se cuelgan de esa MISMA Organizacion -- id_organizacion =
    la de la sede principal, es_sucursal_principal=False -- igual que hace
    POST /sucursales en sucursales.py: una sucursal nueva NUNCA tiene su
    propia Suscripcion/FacturaSuscripcion ni su propio Propietario, hereda
    el plan (y aquí, por simplicidad, no copiamos configuración de
    pasarelas -- ese "heredar_cobro" es opcional en el endpoint real y no
    aplica a datos de prueba). Por eso PLAN_A_USAR se cambió a
    "enterprise" (limites.max_sucursales=5): el plan "pro" solo permite 2
    sucursales por organización, menos de las 3 que crea este script.
  - Migración 019: el rol "owner_gym" se renombró a "Propietario" en la
    tabla roles (ver app/constants/roles.py) -- "owner_gym" sigue vivo solo
    como alias informal para JWTs viejos, no como fila real.
  - Migración 020 (HU-45): nuevo rol "Administrador", de alcance de
    sucursal (no se usa en este script -- el único usuario "dueño" que
    crea es el Propietario de la organización completa).
  - Roles reales: "superadmin", "Propietario", "Administrador",
    "Entrenador", "Recepcionista", "Miembro".
  - PlanSuscripcion ahora también tiene precio_anual_mxn (nullable) y
    limites con más llaves (rutinas_ia, max_sucursales) -- no son NOT NULL,
    así que no rompen la inserción si faltan, pero este script los incluye
    para que el catálogo que crea (si tu base está vacía) sea el mismo que
    el real, no una versión vieja.
  - mdb.rutinas guarda los días DENTRO del mismo documento (campo "dias",
    lista de {dia, ejercicios:[...]}), no en colecciones separadas.
  - mdb.miembro_membresia, mdb.pagos, mdb.asistencias, mdb.progreso_fisico,
    mdb.sesiones, mdb.dietas: mismos nombres de campo que usa seed_pg.py,
    confirmados leyendo esa función directamente (ver seed_mongo_gym()).

Si de todos modos algo no coincide (por un cambio futuro al esquema), el
script reporta el error exacto de esa sección y sigue con el resto, en vez
de tronar todo en silencio.

Cómo correrlo (desde la raíz del repo):
    docker compose exec api python -m app.seeds.seed_demo_gym
"""

import os
import random
from datetime import datetime, timedelta, date, timezone

from app import create_app
from app.extensions import db
from app.mongo import get_db
from app.models.pg.rol import Rol
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.suscripcion import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.models.pg.tipo_membresia import TipoMembresia
from app.models.pg.ejercicio import Ejercicio
from app.models.pg.tipo_clase import TipoClase
from app.utils.alcance import crear_organizacion_para_gimnasio

# ──────────────────────────────────────────────────────────────────────────
# 0) CONFIGURACIÓN
# ──────────────────────────────────────────────────────────────────────────

PLAN_A_USAR = "enterprise"  # uno de los ya sembrados: starter / basico / pro / enterprise
# "enterprise" porque es el único con limites.max_sucursales >= 3 -- ver
# verificar_capacidad_sucursal() en utils/capacidad.py: "pro" solo permite 2.
DOMINIO_DEMO = "demo-gym.test"

# Un solo Propietario administra las 3 sucursales de SUCURSALES_CFG (todas
# bajo la MISMA Organizacion) -- ver crear_sucursal() más abajo.
PROPIETARIO_NOMBRE = "Admin Demo Fitness"
PROPIETARIO_EMAIL = "propietario@demo-gym.test"

PASSWORD_ADMIN = "Admin1234!"
PASSWORD_TRAINER = "Train1234!"
PASSWORD_STAFF = "Recep1234!"
PASSWORD_MIEMBRO = "Gym1234!"

DIAS_HISTORIAL = 90  # ~3 meses

RNG = random.Random()  # usa random.Random(42) si quieres resultados reproducibles

OBJETIVOS = ["Perdida de peso", "Ganancia muscular", "Acondicionamiento general",
             "Tonificacion", "Rendimiento deportivo"]
METODOS_PAGO = ["Efectivo", "Tarjeta debito", "Tarjeta credito", "Transferencia", "QR"]
DIAS_NOMBRES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado"]

EJERCICIOS_GENERICOS = [
    ("Press de banca", "Pecho", "fuerza"), ("Press inclinado con mancuerna", "Pecho", "hipertrofia"),
    ("Press militar", "Hombros", "fuerza"), ("Elevaciones laterales", "Hombros", "hipertrofia"),
    ("Dominadas", "Espalda", "fuerza"), ("Remo con barra", "Espalda", "fuerza"),
    ("Jalon al pecho", "Espalda", "hipertrofia"), ("Curl con barra", "Biceps", "hipertrofia"),
    ("Extension de triceps en polea", "Triceps", "hipertrofia"), ("Sentadilla libre", "Piernas", "fuerza"),
    ("Prensa de piernas", "Piernas", "hipertrofia"), ("Peso muerto rumano", "Piernas", "fuerza"),
    ("Zancadas con mancuerna", "Piernas", "funcional"), ("Plancha abdominal", "Abdomen", "funcional"),
    ("Caminadora", "Cardio", "cardio"), ("Bicicleta estatica", "Cardio", "cardio"),
    ("Eliptica", "Cardio", "cardio"), ("Burpees", "Cuerpo completo", "funcional"),
]
CLASES_GENERICAS = [
    ("Spinning", "Clase de ciclismo indoor en grupo", 45, 20),
    ("Funcional", "Entrenamiento funcional en circuito", 50, 16),
    ("Yoga", "Clase de yoga para flexibilidad y movilidad", 60, 15),
    ("Zumba", "Baile cardiovascular en grupo", 45, 25),
]
DIETA_TIPOS = ["alta_proteina_carbos", "deficit_calorico", "personalizada"]

# Config por sucursal. La primera ("Centro") es la SEDE PRINCIPAL -- la que
# crea la Organizacion, la Suscripcion y el único Propietario (ver
# crear_sucursal()). Norte y Sur son sucursales adicionales de esa MISMA
# Organizacion, sin su propio admin ni su propia suscripción.
SUCURSALES_CFG = [
    {"nombre": "Demo Fitness - Centro", "email_contacto": "centro@demo-gym.test",
     "telefono": "+52-722-100-0001",
     "gen_ratio": 0.55, "n_trainers": 2, "n_staff": 2, "n_miembros": 160, "dieta_tipo": DIETA_TIPOS[0]},
    {"nombre": "Demo Fitness - Norte", "email_contacto": "norte@demo-gym.test",
     "telefono": "+52-722-100-0002",
     "gen_ratio": 0.50, "n_trainers": 2, "n_staff": 2, "n_miembros": 160, "dieta_tipo": DIETA_TIPOS[1]},
    {"nombre": "Demo Fitness - Sur", "email_contacto": "sur@demo-gym.test",
     "telefono": "+52-722-100-0003",
     "gen_ratio": 0.45, "n_trainers": 2, "n_staff": 2, "n_miembros": 160, "dieta_tipo": DIETA_TIPOS[2]},
]
# Total: 1 Propietario (de la organización completa) + (2 trainers + 2 staff)
# * 3 sucursales = 12 staff de sucursal, + 480 miembros = 493 (<=500)

MEMBRESIAS_DEMO = [
    {"nombre": "Básica", "precio": 399.0, "duracion_meses": 1, "tipo": "estandar",
     "descripcion": "Acceso a piso de pesas y cardio", "beneficios": ["Acceso ilimitado", "Casillero incluido"]},
    {"nombre": "Plus", "precio": 599.0, "duracion_meses": 1, "tipo": "estandar",
     "descripcion": "Acceso completo + clases grupales", "beneficios": ["Todo lo de Básica", "Clases grupales ilimitadas"]},
    {"nombre": "Premium", "precio": 999.0, "duracion_meses": 3, "tipo": "estandar",
     "descripcion": "Acceso completo + entrenador personal, 3 meses", "beneficios": ["Todo lo de Plus", "4 sesiones de PT al mes"]},
]

DIETAS = {
    "alta_proteina_carbos": {"nombre": "Plan Alto Rendimiento", "calorias": 2800, "notas": "Carbos antes de entrenar, proteina despues.",
                              "alimentos": ["Arroz blanco", "Pollo", "Claras de huevo", "Avena", "Platano"]},
    "deficit_calorico": {"nombre": "Plan Pérdida de Peso", "calorias": 1600, "notas": "Deficit moderado, no saltarse comidas.",
                          "alimentos": ["Pechuga de pollo", "Verduras", "Fruta", "Yogur griego", "Avena"]},
    "personalizada": {"nombre": "Plan Personalizado", "calorias": 2300, "notas": "Ajustado al objetivo individual.",
                       "alimentos": ["Salmon", "Pavo", "Nueces", "Aceite de oliva", "Brocoli"]},
}

NOMBRES_M = ["Carlos", "Luis", "Miguel", "Jorge", "Andres", "Ricardo", "Fernando", "Sergio", "Pablo",
             "Diego", "Alejandro", "Roberto", "Hector", "Ivan", "Oscar", "Raul", "Eduardo", "Javier"]
NOMBRES_F = ["Maria", "Ana", "Laura", "Sofia", "Daniela", "Valentina", "Gabriela", "Fernanda", "Paola",
             "Claudia", "Diana", "Karen", "Lorena", "Patricia", "Sandra", "Monica", "Adriana", "Natalia"]
APELLIDOS = ["Garcia", "Martinez", "Lopez", "Gonzalez", "Rodriguez", "Hernandez", "Perez", "Sanchez",
             "Ramirez", "Torres", "Flores", "Diaz", "Morales", "Jimenez", "Ruiz", "Gutierrez", "Cruz", "Ortiz"]

_USED_EMAILS = set()


def nombre_aleatorio(femenino=False):
    if femenino:
        return f"{RNG.choice(NOMBRES_F)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"
    return f"{RNG.choice(NOMBRES_M)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"


def email_slug(nombre, idx, dominio=DOMINIO_DEMO):
    clean = nombre.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), (" ", ".")]:
        clean = clean.replace(a, b)
    base = f"{clean[:20]}{idx}"
    email = f"{base}@{dominio}"
    counter = 0
    while email in _USED_EMAILS or Usuario.query.filter_by(email=email).first() is not None:
        counter += 1
        email = f"{base}x{counter}@{dominio}"
    _USED_EMAILS.add(email)
    return email


def fecha_registro_aleatoria():
    """Fecha de alta del miembro, entre hace DIAS_HISTORIAL días y hace 5 días."""
    dias_atras = RNG.randint(5, DIAS_HISTORIAL)
    return datetime.now(timezone.utc) - timedelta(days=dias_atras)


# ──────────────────────────────────────────────────────────────────────────
# 1) Arranque
# ──────────────────────────────────────────────────────────────────────────

print("=== Semilla de gimnasio demo (aditiva, no borra nada existente) ===")
# +1: el único Propietario de la organización (no uno por sucursal).
total_usuarios_estimado = 1 + sum(c["n_trainers"] + c["n_staff"] + c["n_miembros"] for c in SUCURSALES_CFG)
if os.environ.get("SEED_AUTO_CONFIRM") != "1":
    resp = input(
        f"Esto va a INSERTAR (no borra nada existente) ~{total_usuarios_estimado} usuarios nuevos y "
        f"varios miles de documentos de historial.\nEscribe SI para continuar: "
    )
    if resp.strip().upper() != "SI":
        print("Cancelado, no se insertó nada.")
        raise SystemExit(0)

app = create_app()
ctx = app.app_context()
ctx.push()
mdb = get_db()

# ── Catálogo base (roles + planes) ──────────────────────────────────────
# Si tu base está completamente vacía (nunca corriste ningún seed), estos dos
# catálogos no existen todavía. En vez de obligarte a correr el seed_pg.py
# ORIGINAL (que hace TRUNCATE de todo), este script crea SOLO lo que falte
# del catálogo -- mismos valores exactos que usa seed_pg_base() en
# api/app/seeds/seed_pg.py (re-leído después de tu merge) -- sin tocar nada
# más. "Administrador" se crea para que el catálogo quede completo, pero
# este script no lo usa (ningún Usuario que crea tiene ese rol).

ROLES_BASE = ["superadmin", "Propietario", "Administrador", "Entrenador", "Recepcionista", "Miembro"]
ROLES_REQUERIDOS = ["Propietario", "Entrenador", "Recepcionista", "Miembro"]

PLANES_BASE = [
    {
        "nombre": "starter", "precio_mensual_mxn": 0, "precio_anual_mxn": 0, "max_miembros": 30,
        "descripcion": "Plan gratuito para probar GymPro con un grupo pequeño de miembros.",
        "titulo_comercial": "Para arrancar sin costo", "orden": 0, "destacado": False,
        "caracteristicas": [
            "Hasta 30 miembros activos", "Registro de asistencias y pagos",
            "1 usuario administrador", "Soporte por correo electronico",
        ],
        "limites": {"pos": False, "pasarelas_pago": False, "analiticas_ia": False,
                    "rutinas_ia": False, "reportes_pdf": False, "app_movil": False,
                    "max_staff": 1, "max_sucursales": 1},
    },
    {
        "nombre": "basico", "precio_mensual_mxn": 64900, "precio_anual_mxn": 649000, "max_miembros": 80,
        "descripcion": "Gestion completa del dia a dia para gimnasios en operacion.",
        "titulo_comercial": "Ideal para gimnasios pequeños", "orden": 1, "destacado": False,
        "caracteristicas": [
            "Hasta 80 miembros activos", "Punto de venta y control de productos",
            "Cobros en linea con PayPal y Mercado Pago", "Hasta 3 usuarios de staff",
            "Reportes en PDF", "App movil para tus miembros",
            "Amplia tu limite comprando lotes de miembros adicionales",
        ],
        "limites": {"pos": True, "pasarelas_pago": True, "analiticas_ia": False,
                    "rutinas_ia": False, "reportes_pdf": True, "app_movil": True,
                    "max_staff": 3, "max_sucursales": 1},
    },
    {
        "nombre": "pro", "precio_mensual_mxn": 139900, "precio_anual_mxn": 1399000, "max_miembros": 300,
        "descripcion": "Suma inteligencia de negocio y automatizacion con IA.",
        "titulo_comercial": "El mas elegido por gimnasios en crecimiento", "orden": 2, "destacado": True,
        "caracteristicas": [
            "Hasta 300 miembros activos", "Todo lo del plan Basico",
            "Analiticas con IA: riesgo de abandono y clientes por valor",
            "Generacion de rutinas y dietas asistida por IA",
            "Respaldos automaticos de tu informacion", "Hasta 10 usuarios de staff",
            "Amplia tu limite comprando lotes de miembros adicionales",
        ],
        "limites": {"pos": True, "pasarelas_pago": True, "analiticas_ia": True,
                    "rutinas_ia": True, "reportes_pdf": True, "app_movil": True,
                    "backups_automaticos": True, "max_staff": 10, "max_sucursales": 2},
    },
    {
        "nombre": "enterprise", "precio_mensual_mxn": 299900, "precio_anual_mxn": 2999000, "max_miembros": 400,
        "descripcion": "Alta capacidad por sucursal, con soporte dedicado y acuerdos de nivel de servicio.",
        "titulo_comercial": "Para cadenas y multiples sucursales", "orden": 3, "destacado": False,
        "caracteristicas": [
            "Hasta 400 miembros y 20 staff por sucursal, hasta 5 sucursales", "Todo lo del plan Pro",
            "Laboratorio de Machine Learning completo", "Soporte prioritario con SLA",
            "Acompañamiento en la implementacion",
            "Amplia tu limite comprando lotes de miembros adicionales",
        ],
        "limites": {"pos": True, "pasarelas_pago": True, "analiticas_ia": True,
                    "rutinas_ia": True, "reportes_pdf": True, "app_movil": True,
                    "backups_automaticos": True, "ml_lab": True, "soporte_sla": True,
                    "max_staff": 20, "max_sucursales": 5},
    },
]

roles = {r.nombre: r for r in Rol.query.all()}

# Autocorrección de una corrida anterior de ESTE MISMO script (de antes de
# que tu rama trajera la migración 019, que renombró "owner_gym" a
# "Propietario" en la tabla roles real). Si tu base ya tiene un "owner_gym"
# -- porque este script lo creó él mismo en una corrida vieja -- y todavía
# no lo usa ningún Usuario, lo corregimos en vez de dejarlo huérfano o
# crear un "Propietario" aparte.
if "Propietario" not in roles and "owner_gym" in roles:
    rol_viejo = roles["owner_gym"]
    en_uso = Usuario.query.filter_by(id_rol=rol_viejo.id).count() > 0
    if not en_uso:
        print("  Encontré un rol 'owner_gym' de una corrida anterior de este script (antes de la migración "
              "019, que lo renombró a 'Propietario') -- lo corrijo en tu base, no crea uno aparte...")
        rol_viejo.nombre = "Propietario"
        db.session.commit()
        roles["Propietario"] = rol_viejo
        del roles["owner_gym"]

faltantes = [r for r in ROLES_REQUERIDOS if r not in roles]
if faltantes:
    print(f"  No existen los roles {faltantes} en tu base -- los creo ahora (catálogo base, no borra nada)...")
    for nombre_rol in ROLES_BASE:
        if nombre_rol not in roles:
            r = Rol(nombre=nombre_rol)
            db.session.add(r)
            db.session.flush()
            roles[nombre_rol] = r
    db.session.commit()
    print(f"  Roles creados: {[r for r in ROLES_BASE if r in roles]}")

plan = PlanSuscripcion.query.filter_by(nombre=PLAN_A_USAR).first()
if plan is None:
    if PlanSuscripcion.query.first() is None:
        print("  No existe ningún PlanSuscripcion en tu base -- creo el catálogo base "
              "(starter/basico/pro/enterprise), no borra nada...")
        creados = {}
        for pd in PLANES_BASE:
            p = PlanSuscripcion(**pd)
            db.session.add(p)
            db.session.flush()
            creados[p.nombre] = p
        db.session.commit()
        plan = creados.get(PLAN_A_USAR) or next(iter(creados.values()))
        print(f"  Planes creados: {list(creados.keys())}")
    else:
        plan = PlanSuscripcion.query.first()
print(f"  Usando el plan '{plan.nombre}' para las suscripciones de las sucursales nuevas.")


# ──────────────────────────────────────────────────────────────────────────
# 2) Por sucursal: Gimnasio + Suscripción + Factura + staff + catálogo
# ──────────────────────────────────────────────────────────────────────────

def crear_sucursal(cfg, organizacion_id=None):
    """
    Crea la sucursal, o la reutiliza si ya existe (p. ej. porque una corrida
    anterior se cayó a medias) y solo completa lo que falte -- nunca duplica
    el Gimnasio/admin, y solo agrega los trainers/staff/miembros/catálogo que
    todavía no existan hasta llegar a los totales de cfg. Así el script es
    seguro de re-ejecutar después de cualquier error, sin limpiar nada a mano.

    organizacion_id=None  → esta es la SEDE PRINCIPAL (la primera sucursal):
        crea su propia Organizacion (crear_organizacion_para_gimnasio),
        Suscripcion/FacturaSuscripcion y el ÚNICO Usuario Propietario que
        va a administrar TODAS las sucursales de esta organización.
    organizacion_id=<id>  → sucursal ADICIONAL de una organización que ya
        existe (la de la sede principal): solo crea el Gimnasio colgado de
        ella (id_organizacion=organizacion_id, es_sucursal_principal=False),
        sin Suscripcion/Factura ni Propietario nuevo -- exactamente la
        invariante real de POST /sucursales en sucursales.py ("una sucursal
        nueva no tiene plan ni dueño propios, hereda el de su
        organización").
    """
    es_principal = organizacion_id is None
    print(f"\n{'=' * 56}\n  {cfg['nombre'].upper()}{'  (sede principal)' if es_principal else ''}\n{'=' * 56}")

    gym = Gimnasio.query.filter_by(nombre=cfg["nombre"]).first()
    if gym is not None:
        print(f"  Ya existe '{cfg['nombre']}' (id={gym.id}) de una corrida anterior -- lo reutilizo, "
              f"no creo otra sucursal ni otro admin duplicado.")
        if not es_principal and gym.id_organizacion != organizacion_id:
            # Reliquia de una corrida con el diseño anterior (una Organizacion
            # por sucursal) -- se avisa en vez de corregirlo solo, porque
            # mover un Gimnasio de Organizacion a mano puede dejar huérfanas
            # filas de Suscripcion/FacturaSuscripcion que esta sucursal no
            # debería tener.
            print(f"  AVISO: '{cfg['nombre']}' pertenece a la Organizacion {gym.id_organizacion}, "
                  f"no a la {organizacion_id} de la sede principal (probablemente de una corrida "
                  f"anterior con el diseño viejo de \"una Organizacion por sucursal\"). No se mueve "
                  f"automáticamente -- revísalo a mano si quieres que quede bajo la misma organización.")
    else:
        gym = Gimnasio(nombre=cfg["nombre"], plan=plan.nombre,
                       email_contacto=cfg["email_contacto"], telefono=cfg["telefono"], activo=True)

        if es_principal:
            # HU-41 (SCRUM-212): gimnasios.id_organizacion es NOT NULL -- hay
            # que resolverlo ANTES de add()/flush(), igual que hace
            # seed_gimnasio() en seed_pg.py. crear_organizacion_para_gimnasio()
            # solo lee gym.nombre (ya puesto por el constructor) y escribe
            # gym.id_organizacion/es_sucursal_principal en memoria, pero hace
            # su propio flush() interno -- si "gym" ya estuviera en la
            # sesión, ese flush intentaría insertarlo con id_organizacion
            # todavía en None. plan=plan.nombre para que la organización
            # nueva quede con el mismo plan que se va a usar en las
            # sucursales que cuelguen de ella, no con el default "basico"
            # del modelo (que limitaría miembros/staff/sucursales de más).
            crear_organizacion_para_gimnasio(gym, plan=plan.nombre)
        else:
            # Sucursal adicional de la MISMA organización -- nunca se llama
            # a crear_organizacion_para_gimnasio() aquí, o crearía una
            # Organizacion nueva por sucursal (el diseño que justo se quiere
            # evitar). Mismo valor que pone POST /sucursales en sucursales.py.
            gym.id_organizacion = organizacion_id
            gym.es_sucursal_principal = False

        db.session.add(gym)
        db.session.flush()

        if es_principal:
            ahora = datetime.now(timezone.utc)
            sub = Suscripcion(id_gimnasio=gym.id, id_plan=plan.id, estado="active",
                               fecha_inicio=ahora, fecha_proximo_cobro=ahora + timedelta(days=30))
            db.session.add(sub)
            db.session.flush()
            db.session.add(FacturaSuscripcion(
                id_suscripcion=sub.id, monto=plan.precio_mensual_mxn, moneda="MXN",
                estado="pagada", fecha_emision=ahora, fecha_vencimiento=ahora + timedelta(days=30)))

            admin = Usuario(nombre=PROPIETARIO_NOMBRE, email=PROPIETARIO_EMAIL,
                             id_rol=roles["Propietario"].id, id_gimnasio=gym.id, activo=True)
            admin.set_password(PASSWORD_ADMIN)
            db.session.add(admin)
            db.session.flush()
            print(f"  Propietario (de toda la organización): {PROPIETARIO_EMAIL} / {PASSWORD_ADMIN}")

        db.session.commit()

    trainers = Usuario.query.filter_by(id_gimnasio=gym.id, id_rol=roles["Entrenador"].id).all()
    n_staff_actual = Usuario.query.filter_by(id_gimnasio=gym.id, id_rol=roles["Recepcionista"].id).count()
    miembros_pg = Usuario.query.filter_by(id_gimnasio=gym.id, id_rol=roles["Miembro"].id).all()

    idx = RNG.randint(10000, 99999)  # evita choque de índice con los otros gimnasios ya sembrados

    for _ in range(max(0, cfg["n_trainers"] - len(trainers))):
        idx += 1
        nombre = nombre_aleatorio(RNG.random() > 0.5)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Entrenador"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(PASSWORD_TRAINER)
        db.session.add(u)
        db.session.flush()
        trainers.append(u)

    for _ in range(max(0, cfg["n_staff"] - n_staff_actual)):
        idx += 1
        nombre = nombre_aleatorio(RNG.random() > 0.4)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Recepcionista"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(PASSWORD_STAFF)
        db.session.add(u)
        db.session.flush()

    for _ in range(max(0, cfg["n_miembros"] - len(miembros_pg))):
        idx += 1
        femenino = RNG.random() > cfg["gen_ratio"]
        nombre = nombre_aleatorio(femenino)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Miembro"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(PASSWORD_MIEMBRO)
        db.session.add(u)
        db.session.flush()
        miembros_pg.append(u)

    db.session.commit()

    tm_map = {tm.nombre: tm for tm in TipoMembresia.query.filter_by(id_gimnasio=gym.id).all()}
    if not tm_map:
        for tm_data in MEMBRESIAS_DEMO:
            tm = TipoMembresia(id_gimnasio=gym.id, activo=True, **tm_data)
            db.session.add(tm)
            db.session.flush()
            tm_map[tm_data["nombre"]] = tm

    if Ejercicio.query.filter_by(id_gimnasio=gym.id).first() is None:
        for nombre_ej, grupo, tipo_ej in EJERCICIOS_GENERICOS:
            for trainer in trainers:
                es_cardio = tipo_ej == "cardio"
                db.session.add(Ejercicio(
                    id_gimnasio=gym.id, id_entrenador=trainer.id, nombre=nombre_ej,
                    descripcion=f"Ejercicio de {tipo_ej} enfocado en {grupo.lower()}.",
                    grupo_muscular=grupo, tipo=tipo_ej, series=RNG.choice([3, 4, 5]),
                    repeticiones=RNG.choice(["8", "10", "10-12", "12-15"]),
                    duracion_min=RNG.choice([10, 15, 20]) if es_cardio else None,
                ))

    if TipoClase.query.filter_by(id_gimnasio=gym.id).first() is None:
        for nombre_cl, desc, dur, cap in CLASES_GENERICAS:
            db.session.add(TipoClase(id_gimnasio=gym.id, nombre=nombre_cl, descripcion=desc,
                                       duracion_minutos=dur, capacidad_max=cap))

    db.session.commit()
    print(f"  Miembros: {len(miembros_pg)}  Entrenadores: {len(trainers)}  Staff: {n_staff_actual}  "
          f"TipoMem: {len(tm_map)}  Clases: {len(CLASES_GENERICAS)}")
    return gym, miembros_pg, tm_map


# ──────────────────────────────────────────────────────────────────────────
# 3) Por miembro: perfil + membresía + pagos + asistencias + progreso +
#    sesiones + rutina + dieta -- mismo esquema que seed_mongo_gym() de
#    seed_pg.py, acotado a una ventana de ~DIAS_HISTORIAL días en vez de
#    los 2 años que usa el seed base (aquí el gimnasio es "nuevo").
# ──────────────────────────────────────────────────────────────────────────

def seed_mongo_miembro(gym, usuario, tm_map, dieta_tipo):
    gym_id = gym.id
    tm_items = list(tm_map.items())
    dieta = DIETAS[dieta_tipo]

    peso_inicial = round(RNG.uniform(52, 115), 1)
    estatura = round(RNG.uniform(1.54, 1.92), 2)
    sexo = RNG.choice(["Masculino", "Femenino"])
    objetivo = RNG.choice(OBJETIVOS)
    fecha_nac = date.today() - timedelta(days=RNG.randint(18 * 365, 58 * 365))
    fecha_reg = fecha_registro_aleatoria()
    dias_desde_registro = max(1, (datetime.now(timezone.utc) - fecha_reg).days)

    miembro_doc = {
        "id_usuario_pg": usuario.id, "nombre": usuario.nombre, "email": usuario.email,
        "id_gimnasio_pg": gym_id,
        "telefono": f"+52-722-{RNG.randint(100, 999)}-{RNG.randint(1000, 9999)}",
        "fecha_nacimiento": str(fecha_nac), "sexo": sexo,
        "peso_inicial": peso_inicial, "estatura": estatura,
        "estado": RNG.choices(["Activo", "Activo", "Activo", "Inactivo"], weights=[85, 85, 85, 15])[0],
        "objetivo": objetivo, "peso_objetivo": round(peso_inicial * RNG.uniform(0.82, 1.12), 1),
        "grasa_objetivo": round(RNG.uniform(9, 22), 1), "masa_muscular_objetivo": round(RNG.uniform(28, 52), 1),
        "fecha_registro": fecha_reg, "created_at": fecha_reg,
    }
    uid = mdb.miembros.insert_one(miembro_doc).inserted_id

    # Membresía activa
    tm_nombre, tm_obj = RNG.choice(tm_items)
    dur_dias = (tm_obj.duracion_meses or 1) * 30
    f_inicio = fecha_reg + timedelta(days=1)
    f_fin = f_inicio + timedelta(days=dur_dias)
    activa = f_fin.replace(tzinfo=None) > datetime.now()
    mdb.miembro_membresia.update_one(
        {"id_miembro": uid},
        {"$set": {"id_membresia": tm_obj.id, "fecha_inicio": str(f_inicio.date()), "fecha_fin": str(f_fin.date()),
                   "estado": "Activa" if activa else "Vencida", "created_at": f_inicio}},
        upsert=True,
    )

    # Pagos -- uno cada ~30 días, acotado a lo que ya transcurrió desde el alta
    n_pagos = min(RNG.randint(1, 4), dias_desde_registro // 28 + 1)
    pagos_bulk = []
    for p_idx in range(n_pagos):
        f_pago = fecha_reg + timedelta(days=p_idx * 30 + RNG.randint(0, 5))
        _, tm_p = RNG.choice(tm_items)
        pagos_bulk.append({
            "id_miembro": uid, "id_gimnasio": gym_id, "monto": float(tm_p.precio),
            "metodo_pago": RNG.choice(METODOS_PAGO), "concepto": f"Membresia {tm_p.nombre}",
            "fecha_pago": f_pago, "estado": "Pagado", "referencia": f"REF{RNG.randint(100000, 999999)}",
        })
    if pagos_bulk:
        mdb.pagos.insert_many(pagos_bulk)

    # Asistencias -- 2-4 veces por semana dentro de la ventana ya transcurrida.
    # El límite superior se escala con los días transcurridos, pero para un
    # miembro recién registrado (dias_desde_registro chico) ese límite puede
    # caer por debajo del mínimo de 4 -- max(4, ...) evita el
    # "empty range in randrange()" que tronaba aquí con miembros de <9 días.
    limite_asist = max(4, int(dias_desde_registro / 7 * 4) + 1)
    n_asistencias = min(RNG.randint(4, limite_asist), dias_desde_registro)
    dias_asist = RNG.sample(range(0, dias_desde_registro), min(n_asistencias, dias_desde_registro)) if dias_desde_registro > 0 else []
    asist_bulk = []
    for d in dias_asist:
        f_date = (fecha_reg + timedelta(days=d)).date()
        hora = f"{RNG.randint(6, 21):02d}:{RNG.choice(['00', '15', '30', '45'])}"
        asist_bulk.append({
            "id_miembro": uid, "id_gimnasio": gym_id, "fecha": str(f_date), "hora_entrada": hora,
            "hora_salida": f"{min(23, int(hora[:2]) + RNG.randint(1, 2)):02d}:{hora[3:]}",
        })
    if asist_bulk:
        mdb.asistencias.insert_many(asist_bulk)

    # Progreso físico -- una medición cada ~28 días
    peso_a, grasa_a, musc_a = peso_inicial, round(RNG.uniform(16, 38), 1), round(RNG.uniform(26, 44), 1)
    n_meses = max(1, dias_desde_registro // 28)
    prog_bulk = []
    for mes in range(n_meses):
        f = fecha_reg + timedelta(days=mes * 28 + RNG.randint(0, 5))
        peso_a = round(max(48, peso_a + RNG.uniform(-1.0, 0.6)), 1)
        grasa_a = round(max(7, grasa_a + RNG.uniform(-0.6, 0.3)), 1)
        musc_a = round(min(62, musc_a + RNG.uniform(-0.1, 0.5)), 1)
        prog_bulk.append({
            "id_miembro": uid, "fecha_registro": str(f.date()), "peso": peso_a, "estatura": estatura,
            "imc": round(peso_a / (estatura ** 2), 1), "grasa_corporal": grasa_a, "masa_muscular": musc_a,
            "presion_arterial": f"{RNG.randint(108, 138)}/{RNG.randint(68, 92)}",
            "frecuencia_cardiaca": RNG.randint(56, 88),
            "observaciones": RNG.choice(["Buen progreso", "Mantener ritmo", "Aumentar intensidad", "Revisar nutricion", ""]),
        })
    if prog_bulk:
        mdb.progreso_fisico.insert_many(prog_bulk)

    # Sesiones (clases reservadas)
    n_sesiones = min(RNG.randint(2, 8), max(1, dias_desde_registro // 5))
    ses_bulk = []
    for _ in range(n_sesiones):
        f = fecha_reg + timedelta(days=RNG.randint(0, dias_desde_registro))
        ses_bulk.append({
            "id_miembro": uid, "id_gimnasio": gym_id, "fecha": str(f.date()),
            "hora_inicio": f"{RNG.randint(6, 21):02d}:00", "duracion_min": RNG.choice([45, 55, 60, 75, 90]),
            "tipo": RNG.choice([c[0] for c in CLASES_GENERICAS]),
            "estado": RNG.choices(["completed", "cancelled", "pending"], weights=[75, 15, 10])[0],
            "calorias_est": RNG.randint(220, 720),
        })
    if ses_bulk:
        mdb.sesiones.insert_many(ses_bulk)

    # Rutina propia (1-2), con los días embebidos en el mismo documento
    for _ in range(RNG.randint(1, 2)):
        ejercicios_list = [e[0] for e in EJERCICIOS_GENERICOS]
        n_dias = RNG.randint(3, 5)
        dias = []
        for i in range(n_dias):
            ejercs = RNG.sample(ejercicios_list, min(4, len(ejercicios_list)))
            dias.append({
                "dia": DIAS_NOMBRES[i % len(DIAS_NOMBRES)],
                "ejercicios": [
                    {"nombre": ej, "series": RNG.randint(3, 5),
                     "repeticiones": RNG.choice([8, 10, 12, 15, "AMRAP"]),
                     "peso_sugerido": round(RNG.uniform(8, 100), 1), "notas": ""}
                    for ej in ejercs
                ],
            })
        mdb.rutinas.insert_one({
            "id_miembro": uid, "id_gimnasio": gym_id,
            "nombre": f"Rutina {RNG.choice(['Fuerza', 'Funcional', 'Hipertrofia'])} - {objetivo[:18]}",
            "categoria": RNG.choice(["Fuerza", "Funcional", "Hipertrofia"]),
            "dificultad": RNG.choice(["Principiante", "Intermedio", "Avanzado"]),
            "duracion_minutos": RNG.choice([45, 60, 75, 90]), "descripcion": f"Rutina orientada a {objetivo.lower()}.",
            "dias": dias, "activa": True, "created_at": fecha_reg,
        })

    # Dieta propia
    v = round(RNG.uniform(0.88, 1.12), 2)
    cal_dia = round(dieta["calorias"] * v)
    comidas = [
        {"nombre": n, "hora": h, "calorias": round(cal_dia * p), "alimentos": RNG.sample(dieta["alimentos"], min(3, len(dieta["alimentos"])))}
        for n, h, p in zip(["Desayuno", "Comida", "Cena"], ["07:30", "14:00", "20:00"], [0.30, 0.45, 0.25])
    ]
    mdb.dietas.insert_one({
        "id_miembro": uid, "id_gimnasio_pg": gym_id, "nombre": dieta["nombre"], "descripcion": dieta["notas"],
        "tipo": "propia", "creado_por": "miembro", "id_creador_pg": usuario.id, "comidas": comidas,
        "calorias_meta": cal_dia, "objetivo": objetivo, "activa": True, "fecha_creacion": fecha_reg,
    })

    return {"pagos": len(pagos_bulk), "asistencias": len(asist_bulk), "progreso": len(prog_bulk), "sesiones": len(ses_bulk)}


# ──────────────────────────────────────────────────────────────────────────
# 4) Correr todo
# ──────────────────────────────────────────────────────────────────────────

gran_total = {"pagos": 0, "asistencias": 0, "progreso": 0, "sesiones": 0}
organizacion_id = None  # se fija al crear/reutilizar la sede principal (primer cfg)
total_staff = 0
total_miembros = 0

for i, cfg in enumerate(SUCURSALES_CFG):
    gym, miembros_pg, tm_map = crear_sucursal(cfg, organizacion_id=organizacion_id)
    if i == 0:
        # A partir de aquí, el resto de sucursales se crean colgadas de ESTA
        # misma Organizacion -- sea porque "gym" se creó ahora, o porque ya
        # existía de una corrida anterior (en ambos casos gym.id_organizacion
        # ya está resuelto al volver de crear_sucursal()).
        organizacion_id = gym.id_organizacion
    total_staff += cfg["n_trainers"] + cfg["n_staff"]
    total_miembros += len(miembros_pg)

    print(f"  Generando historial de miembros de '{cfg['nombre']}'...")
    n_saltados = 0
    # Variable distinta de la "i" del for externo (índice de sucursal) --
    # reusar el mismo nombre aquí funcionaba por casualidad (el "if i == 0"
    # de arriba ya se evalúa antes de llegar a este bucle), pero invitaba a
    # un bug real en cuanto alguien agregara código después de este bucle
    # que todavía esperara el índice de sucursal.
    for j, usuario in enumerate(miembros_pg):
        # Si una corrida anterior ya le generó su documento en mdb.miembros,
        # no lo volvemos a procesar -- evita historial duplicado al
        # reintentar después de un error a medio camino.
        if mdb.miembros.find_one({"id_usuario_pg": usuario.id}, {"_id": 1}) is not None:
            n_saltados += 1
            continue
        res = seed_mongo_miembro(gym, usuario, tm_map, cfg["dieta_tipo"])
        for k in gran_total:
            gran_total[k] += res[k]
        if (j + 1) % 50 == 0:
            print(f"    ...{j + 1}/{len(miembros_pg)} miembros con historial")
    if n_saltados:
        print(f"  ({n_saltados} miembros ya tenían historial de una corrida anterior -- se dejaron intactos)")

print(f"\n{'=' * 70}")
print("SEMILLA DE GIMNASIO DEMO COMPLETADA")
print(f"{'=' * 70}")
print(f"Organización (id={organizacion_id}) con {len(SUCURSALES_CFG)} sucursales, "
      f"un solo Propietario: {[c['nombre'] for c in SUCURSALES_CFG]}")
print(f"Staff de sucursal (entrenadores + recepcionistas): {total_staff}")
print(f"Miembros totales: {total_miembros}")
print(f"Total de usuarios creados: 1 (Propietario) + {total_staff} (staff) + {total_miembros} (miembros) "
      f"= {1 + total_staff + total_miembros}")
print(f"Documentos de historial en Mongo: {gran_total}")
print()
print(f"  Propietario (administra las {len(SUCURSALES_CFG)} sucursales): {PROPIETARIO_EMAIL} / {PASSWORD_ADMIN}")
print(f"\nContraseñas de staff/miembros nuevos -- Entrenador: {PASSWORD_TRAINER} | Recepcionista: {PASSWORD_STAFF} | Miembro: {PASSWORD_MIEMBRO}")
print("=" * 70)

ctx.pop()
