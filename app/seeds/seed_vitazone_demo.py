"""
seeds/seed_vitazone_demo.py — Ingesta ADITIVA de datos de demostración para
UN SOLO gimnasio ya existente (Vitazone, id=4 por default) — pensado para
tener historial real con qué presentar el mismo día, sin tocar nada de lo
que ya hay en la base de datos.

Diferencia clave con seed_pg.py / seed_server.py: aquellos son destructivos
(TRUNCATE + drop() de todas las colecciones antes de generar datos nuevos).
Este script NO resetea nada — ni una tabla de Postgres ni una colección de
Mongo. Solo agrega filas/documentos nuevos, siempre acotados al gimnasio
indicado (id_gimnasio / id_gimnasio_pg), y respeta a los miembros que ya
estaban registrados: no los borra ni les reescribe el perfil, solo les
agrega historial nuevo dentro de la ventana de fechas.

Por qué NO se reutilizó tal cual el esquema de seed_pg.py/seed_server.py
para el historial (aunque sí se reutilizó para roles/planes/productos):
al revisar el código que de verdad LEE esas colecciones hoy
(api/app/routes/miembro/user_dashboard.py) se encontraron dos desalineaciones
entre el seed de desarrollo y el backend actual:

  1. asistencias: seed_pg.py guarda "fecha" como STRING ("2026-06-01").
     _get_weekly_progress()/_get_workout_stats()/_calcular_racha() comparan
     "fecha" con $gte/$lt contra un datetime real. Mongo no compara bien un
     string contra un datetime en ese tipo de filtro, así que ese historial
     antiguo no mueve esas métricas. Aquí "fecha" se guarda como datetime.
  2. El entrenamiento registrado de verdad vive en
     mdb.entrenamientos_realizados (no en mdb.sesiones, que es el nombre que
     usa el seed de desarrollo para otra cosa) — es la colección que lee
     tanto el dashboard del miembro como /api/user/workouts/muscle-groups
     (lo que alimenta TrabajoPorGrupo.jsx). Se usa el mismo esquema que
     construye POST /api/user/workouts.
  3. La rutina activa del miembro se busca con mdb.rutinas (id_miembro,
     activa=True) + mdb.rutina_dias (dia_semana, grupo_muscular) +
     mdb.rutina_ejercicios — y _get_today_workout() compara dia_semana
     contra nombres de día CON acento ("Miércoles", "Sábado"). El seed de
     desarrollo genera esos nombres SIN acento, así que para esos dos días
     nunca hace match. Aquí se usan los nombres con acento.

Lo que SÍ se mantiene igual que seed_server.py porque se confirmó que sigue
vigente: el esquema de mdb.productos (igual al que crea
POST /api/owner_gym/productos) y el de mdb.pagos (igual al que crea
POST /api/user/membership/renew).

Genera:
  - 10 miembros nuevos + 2 entrenadores nuevos (Usuario en PostgreSQL +
    perfil en mdb.miembros)
  - Catálogo de ejercicios individual por entrenador (Ejercicio en
    PostgreSQL, con id_entrenador propio — lo que espera
    /api/trainer/exercises)
  - 15 productos de punto de venta (mdb.productos)
  - Para TODOS los miembros del gimnasio (los 10 nuevos y los que ya
    estaban registrados antes de correr este script), dentro de la ventana
    [FECHA_INICIO, FECHA_FIN]:
      · pagos de membresía (mdb.pagos), uno cada ~30 días
      · asistencias + entrenamientos registrados (mdb.asistencias +
        mdb.entrenamientos_realizados), 2 a 4 veces por semana, con series/
        peso/volumen/calorías reales — esto es lo que alimenta "Semana
        actual", la racha y "Trabajo por grupo muscular" en el dashboard
      · una rutina propia activa, con los 7 días de la semana y 3-4
        ejercicios por día de entrenamiento (mdb.rutinas + rutina_dias +
        rutina_ejercicios)

No genera (fuera del alcance pedido): progreso_fisico (composición
corporal), dietas, ni ventas de punto de venta (solo el catálogo).

Uso:
  docker compose exec api python -m app.seeds.seed_vitazone_demo
  docker compose exec api python -m app.seeds.seed_vitazone_demo --gym-id 4

Nota de re-ejecución: los pasos de catálogo (productos, ejercicios por
entrenador, membresías) son idempotentes — si ya existen, no se duplican.
El historial (pagos/asistencias/entrenamientos/rutina) NO lo es: está
pensado para correrse una sola vez. Si se corre dos veces, cada miembro
queda con historial duplicado para la misma ventana de fechas.
"""
import os
import sys
import argparse
import random
from datetime import datetime, timedelta, date, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.extensions import db
from app.models.pg.usuario import Usuario
from app.models.pg.rol import Rol
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.tipo_membresia import TipoMembresia
from app.models.pg.ejercicio import Ejercicio
from app.mongo import get_db

RNG = random.Random(2026)

GYM_ID_DEFAULT = 4
GYM_NOMBRE_DEFAULT = "Vitazone"

FECHA_INICIO = date(2026, 6, 1)
FECHA_FIN = date(2026, 10, 8)

N_MIEMBROS_NUEVOS = 10
N_TRAINERS_NUEVOS = 2
N_PRODUCTOS = 15

NOMBRES_M = ["Carlos", "Luis", "Miguel", "Jorge", "Andres", "Ricardo", "Fernando",
             "Sergio", "Pablo", "Diego", "Alejandro", "Roberto", "Hector", "Ivan",
             "Oscar", "Raul", "Eduardo", "Marco", "Javier", "Victor"]
NOMBRES_F = ["Maria", "Ana", "Laura", "Sofia", "Daniela", "Valentina", "Gabriela",
             "Fernanda", "Paola", "Claudia", "Diana", "Karen", "Lorena", "Patricia",
             "Sandra", "Monica", "Adriana", "Natalia", "Isabella", "Camila"]
APELLIDOS = ["Garcia", "Martinez", "Lopez", "Gonzalez", "Rodriguez", "Hernandez",
             "Perez", "Sanchez", "Ramirez", "Torres", "Flores", "Diaz", "Morales",
             "Jimenez", "Ruiz", "Gutierrez", "Cruz", "Ortiz", "Castillo", "Reyes"]
METODOS_PAGO = ["Efectivo", "Tarjeta debito", "Tarjeta credito", "Transferencia", "QR"]
OBJETIVOS = ["Perdida de peso", "Ganancia muscular", "Acondicionamiento general",
             "Tonificacion", "Rendimiento deportivo"]

# Nombres de día CON acento — exactamente los que compara
# _get_today_workout() en user_dashboard.py (DIAS_ES). Si se usan sin
# acento (como hace seed_pg.py) la rutina del día nunca hace match en
# miércoles ni sábado.
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Grupo muscular por día de entrenamiento (domingo = descanso). Los nombres
# vienen directo de _GRUPOS_CANONICOS en user_dashboard.py para que
# "Trabajo por grupo muscular" los reconozca sin caer en "General".
PLAN_SEMANA = {
    "Lunes": "pecho", "Martes": "espalda", "Miércoles": "piernas",
    "Jueves": "hombros", "Viernes": "biceps", "Sábado": "cardio",
    "Domingo": "descanso",
}

# (nombre, grupo_muscular, tipo) — grupo_muscular en minúsculas, tal como
# espera _canon_grupo() para no caer en "General".
EJERCICIOS_CATALOGO = [
    ("Press de banca", "pecho", "fuerza"),
    ("Press inclinado con mancuerna", "pecho", "hipertrofia"),
    ("Aperturas con mancuerna", "pecho", "hipertrofia"),
    ("Press militar", "hombros", "fuerza"),
    ("Elevaciones laterales", "hombros", "hipertrofia"),
    ("Face pull", "hombros", "funcional"),
    ("Dominadas", "espalda", "fuerza"),
    ("Remo con barra", "espalda", "fuerza"),
    ("Jalon al pecho", "espalda", "hipertrofia"),
    ("Curl con barra", "biceps", "hipertrofia"),
    ("Curl martillo", "biceps", "hipertrofia"),
    ("Extension de triceps en polea", "triceps", "hipertrofia"),
    ("Fondos en paralelas", "triceps", "fuerza"),
    ("Sentadilla libre", "piernas", "fuerza"),
    ("Prensa de piernas", "piernas", "hipertrofia"),
    ("Peso muerto rumano", "piernas", "fuerza"),
    ("Zancadas con mancuerna", "piernas", "funcional"),
    ("Hip thrust", "gluteos", "hipertrofia"),
    ("Plancha abdominal", "abdomen", "funcional"),
    ("Crunch en polea", "abdomen", "hipertrofia"),
    ("Caminadora", "cardio", "cardio"),
    ("Bicicleta estatica", "cardio", "cardio"),
    ("Eliptica", "cardio", "cardio"),
]

MEMBRESIAS_DEFAULT = [
    {"nombre": "Básica", "precio": 399.0, "duracion_meses": 1, "tipo": "estandar",
     "descripcion": "Acceso a piso de pesas y cardio",
     "beneficios": ["Acceso ilimitado a piso de pesas y cardio", "Casillero incluido"]},
    {"nombre": "Plus", "precio": 599.0, "duracion_meses": 1, "tipo": "estandar",
     "descripcion": "Acceso completo + clases grupales",
     "beneficios": ["Todo lo de Básica", "Clases grupales ilimitadas", "1 evaluación física al mes"]},
    {"nombre": "Premium", "precio": 999.0, "duracion_meses": 1, "tipo": "estandar",
     "descripcion": "Acceso completo + entrenador personal",
     "beneficios": ["Todo lo de Plus", "4 sesiones de personal training al mes", "Plan de nutrición inicial"]},
]

# (nombre, categoria, precio_base, stock_base, descripcion) — categorías
# tal como las define el selector del frontend (POSProductoModal.jsx).
PRODUCTOS_POS = [
    ("Proteina Whey 1kg — Vainilla", "Suplementos", 850.0, 24, "Whey concentrada 24g de proteina por porcion, sabor vainilla."),
    ("Proteina Whey 1kg — Chocolate", "Suplementos", 850.0, 18, "Whey concentrada 24g de proteina por porcion, sabor chocolate."),
    ("Creatina Monohidratada 300g", "Suplementos", 480.0, 30, "Creatina micronizada sin sabor, 5g por porcion."),
    ("Pre-entreno Explosivo 300g", "Suplementos", 620.0, 15, "Pre-entreno con cafeina, beta-alanina y citrulina."),
    ("BCAA 2:1:1 200 caps", "Suplementos", 390.0, 20, "Aminoacidos de cadena ramificada para recuperacion muscular."),
    ("Barra de proteina (unidad)", "Snacks", 45.0, 80, "20g de proteina, bajo en azucar, sabor chocolate y cacahuate."),
    ("Barra energetica (unidad)", "Snacks", 35.0, 60, "Avena, miel y frutos secos, ideal antes de entrenar."),
    ("Agua embotellada 600ml", "Bebidas", 20.0, 150, "Agua purificada, presentacion individual."),
    ("Bebida electrolitos 500ml", "Bebidas", 35.0, 90, "Repone electrolitos perdidos durante el entrenamiento."),
    ("Playera Vitazone (unisex)", "Ropa", 280.0, 35, "Playera deportiva transpirable con logo bordado."),
    ("Toalla deportiva Vitazone", "Ropa", 190.0, 45, "Toalla de microfibra de secado rapido, tamano mediano."),
    ("Guantes de entrenamiento", "Accesorios", 220.0, 28, "Guantes acolchados con soporte de muneca."),
    ("Cinturon de levantamiento", "Accesorios", 650.0, 12, "Cinturon de cuero para sentadilla y peso muerto."),
    ("Shaker Vitazone 700ml", "Accesorios", 150.0, 50, "Shaker con malla mezcladora, libre de BPA."),
    ("Candado de casillero", "General", 95.0, 60, "Candado de combinacion para casilleros del gimnasio."),
]

_EMAIL_DOMINIO = "vitazonedemo.com"


def nombre_aleatorio(femenino):
    if femenino:
        return f"{RNG.choice(NOMBRES_F)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"
    return f"{RNG.choice(NOMBRES_M)} {RNG.choice(APELLIDOS)} {RNG.choice(APELLIDOS)}"


def email_unico(nombre, idx):
    """Genera un email y garantiza que no choque con uno ya existente en la
    base de datos (a diferencia de seed_pg.py, aquí sí puede haber usuarios
    de ejecuciones/registros anteriores, así que se valida contra la BD)."""
    clean = nombre.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), (" ", ".")]:
        clean = clean.replace(a, b)
    base = f"{clean[:20]}{idx}"
    email = f"{base}@{_EMAIL_DOMINIO}"
    counter = 0
    while Usuario.query.filter_by(email=email).first() is not None:
        counter += 1
        email = f"{base}x{counter}@{_EMAIL_DOMINIO}"
    return email


def _parse_fecha(valor):
    """Interpreta fecha_registro venga como date, datetime o string ISO."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def get_or_create_gimnasio(gym_id):
    gym = Gimnasio.query.get(gym_id)
    if gym:
        print(f"  Usando gimnasio existente #{gym.id}: {gym.nombre}")
        return gym
    gym = Gimnasio.query.filter_by(nombre=GYM_NOMBRE_DEFAULT).first()
    if gym:
        print(f"  No existe el gimnasio #{gym_id}, pero sí '{GYM_NOMBRE_DEFAULT}' con id #{gym.id}. Se usa ese.")
        return gym
    print(f"  No existe el gimnasio #{gym_id} ni uno llamado '{GYM_NOMBRE_DEFAULT}'. Se crea nuevo.")
    gym = Gimnasio(nombre=GYM_NOMBRE_DEFAULT, plan="pro",
                   email_contacto="contacto@vitazone.mx", telefono="+52-728-200-0004",
                   activo=True)
    db.session.add(gym)
    db.session.commit()
    if gym.id != gym_id:
        print(f"  AVISO: el gimnasio nuevo quedo con id #{gym.id}, no #{gym_id} "
              f"(ese era el siguiente id disponible en la secuencia).")
    return gym


def crear_membresias_default(gym):
    tm_list = []
    for tm_data in MEMBRESIAS_DEFAULT:
        existe = TipoMembresia.query.filter_by(id_gimnasio=gym.id, nombre=tm_data["nombre"]).first()
        if existe:
            tm_list.append(existe)
            continue
        tm = TipoMembresia(id_gimnasio=gym.id, activo=True, **tm_data)
        db.session.add(tm)
        db.session.flush()
        tm_list.append(tm)
    db.session.commit()
    print(f"  Tipos de membresia disponibles para pagos: {len(tm_list)} (creados los que no existian)")
    return tm_list


def crear_miembros_nuevos(gym, roles):
    nuevos = []
    for i in range(N_MIEMBROS_NUEVOS):
        femenino = RNG.random() > 0.5
        nombre = nombre_aleatorio(femenino)
        email = email_unico(nombre, i + 1)
        u = Usuario(nombre=nombre, email=email, id_rol=roles["Miembro"].id,
                    id_gimnasio=gym.id, activo=True)
        u.set_password(f"Vita{i + 1}!")
        db.session.add(u)
        db.session.flush()
        nuevos.append(u)
    db.session.commit()
    print(f"  Miembros nuevos creados: {len(nuevos)}")
    return nuevos


def crear_entrenadores_nuevos(gym, roles):
    trainers = []
    for i in range(N_TRAINERS_NUEVOS):
        femenino = RNG.random() > 0.5
        nombre = nombre_aleatorio(femenino)
        email = email_unico(nombre, 100 + i)
        u = Usuario(nombre=nombre, email=email, id_rol=roles["Entrenador"].id,
                    id_gimnasio=gym.id, activo=True)
        u.set_password(f"Train{i + 1}!")
        db.session.add(u)
        db.session.flush()
        trainers.append(u)
    db.session.commit()
    print(f"  Entrenadores nuevos creados: {len(trainers)}")
    return trainers


def crear_catalogo_ejercicios(gym, trainers):
    """Biblioteca individual por entrenador (Ejercicio.id_entrenador) — lo
    que lee GET /api/trainer/exercises. Idempotente: si ya existe
    (gimnasio, entrenador, nombre) no se duplica."""
    total = 0
    for trainer in trainers:
        for nombre_ej, grupo, tipo_ej in EJERCICIOS_CATALOGO:
            ya_existe = Ejercicio.query.filter_by(
                id_gimnasio=gym.id, id_entrenador=trainer.id, nombre=nombre_ej
            ).first()
            if ya_existe:
                continue
            es_cardio = tipo_ej == "cardio"
            db.session.add(Ejercicio(
                id_gimnasio=gym.id, id_entrenador=trainer.id, nombre=nombre_ej,
                descripcion=f"Ejercicio de {tipo_ej} enfocado en {grupo}. "
                            f"Realiza cada repeticion con control y buena tecnica postural.",
                grupo_muscular=grupo, tipo=tipo_ej,
                series=RNG.choice([3, 4, 5]),
                repeticiones=RNG.choice(["8", "10", "10-12", "12-15"]),
                duracion_min=RNG.choice([10, 15, 20]) if es_cardio else None,
            ))
            total += 1
    db.session.commit()
    print(f"  Ejercicios de catalogo creados: {total} ({len(EJERCICIOS_CATALOGO)} x {len(trainers)} entrenadores, menos los que ya existian)")


def crear_productos_pos(mdb, gym):
    """Catálogo de productos del punto de venta — mismo esquema que crea
    POST /api/owner_gym/productos. Idempotente por (id_gimnasio, nombre)."""
    ahora = datetime.now(timezone.utc)
    creados = 0
    for nombre, categoria, precio_base, stock_base, descripcion in PRODUCTOS_POS[:N_PRODUCTOS]:
        if mdb.productos.find_one({"id_gimnasio": gym.id, "nombre": nombre}):
            continue
        variacion = RNG.uniform(0.95, 1.05)
        mdb.productos.insert_one({
            "id_gimnasio": gym.id,
            "nombre": nombre,
            "precio": round(precio_base * variacion, 2),
            "stock": max(0, stock_base + RNG.randint(-5, 10)),
            "categoria": categoria,
            "descripcion": descripcion,
            "imagenes": [],
            "activo": True,
            "es_combo": False,
            "items_combo": [],
            "created_at": ahora,
        })
        creados += 1
    print(f"  Productos POS creados: {creados} (de {min(N_PRODUCTOS, len(PRODUCTOS_POS))} solicitados; el resto ya existia)")


def obtener_o_crear_perfil_miembro(mdb, usuario, gym, fecha_reg_nueva=None):
    """Perfil en mdb.miembros. Si el miembro ya estaba registrado, se usa su
    perfil tal cual (no se toca). Si no tiene uno (caso excepcional), se crea
    uno nuevo con datos demograficos razonables."""
    doc = mdb.miembros.find_one({"id_usuario_pg": usuario.id})
    if doc:
        return doc, False

    femenino = RNG.random() > 0.5
    peso_inicial = round(RNG.uniform(55, 100), 1)
    estatura = round(RNG.uniform(1.55, 1.90), 2)
    fecha_reg = fecha_reg_nueva or FECHA_INICIO
    fecha_nac = date.today() - timedelta(days=RNG.randint(18 * 365, 55 * 365))
    doc = {
        "id_usuario_pg": usuario.id,
        "nombre": usuario.nombre,
        "email": usuario.email,
        "id_gimnasio_pg": gym.id,
        "telefono": f"+52-728-{RNG.randint(100, 999)}-{RNG.randint(1000, 9999)}",
        "fecha_nacimiento": str(fecha_nac),
        "sexo": "Femenino" if femenino else "Masculino",
        "peso_inicial": peso_inicial,
        "estatura": estatura,
        "estado": "Activo",
        "objetivo": RNG.choice(OBJETIVOS),
        "peso_objetivo": round(peso_inicial * RNG.uniform(0.85, 1.1), 1),
        "fecha_registro": str(fecha_reg),
        "created_at": datetime.combine(fecha_reg, datetime.min.time()),
    }
    res = mdb.miembros.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc, True


def generar_historial_miembro(mdb, gym, usuario, miembro_doc, tm_list, fecha_inicio_hist, fecha_fin_hist):
    """Pagos + rutina propia + asistencias/entrenamientos dentro de la
    ventana [fecha_inicio_hist, fecha_fin_hist] para UN miembro."""
    uid = miembro_doc["_id"]
    gym_id = gym.id
    ahora = datetime.now(timezone.utc)

    # ── Pagos de membresia: uno cada ~30 dias ──────────────────────────────
    pagos_bulk = []
    f = fecha_inicio_hist
    while f <= fecha_fin_hist:
        tm = RNG.choice(tm_list)
        f_dt = datetime(f.year, f.month, f.day, RNG.randint(8, 20), RNG.choice([0, 15, 30, 45]))
        pagos_bulk.append({
            "id_miembro": uid, "id_gimnasio": gym_id,
            "monto": float(tm.precio),
            "metodo_pago": RNG.choice(METODOS_PAGO),
            "concepto": f"Membresia {tm.nombre}",
            "fecha_pago": f_dt,
            "estado": "Pagado",
            "referencia": f"REF{RNG.randint(100000, 999999)}",
        })
        f += timedelta(days=30 + RNG.randint(-3, 3))
    if pagos_bulk:
        mdb.pagos.insert_many(pagos_bulk)

    # ── Rutina propia activa: 7 dias, 3-4 ejercicios en cada dia que no es
    # descanso (mdb.rutinas + rutina_dias + rutina_ejercicios — lo que lee
    # _get_today_workout()) ────────────────────────────────────────────────
    rutina_id = mdb.rutinas.insert_one({
        "id_miembro": uid, "id_gimnasio_pg": gym_id, "id_entrenador_pg": None,
        "nombre": f"Plan personal de {usuario.nombre.split()[0]}",
        "categoria": RNG.choice(["Fuerza", "Hipertrofia", "Funcional"]),
        "dificultad": RNG.choice(["Principiante", "Intermedio", "Avanzado"]),
        "duracion_minutos": RNG.choice([45, 60, 75]),
        "descripcion": "Rutina generada como parte de los datos de demostracion.",
        "activa": True, "fecha_creacion": ahora, "fecha_actualizacion": ahora,
    }).inserted_id

    for orden, dia in enumerate(DIAS_SEMANA):
        grupo = PLAN_SEMANA[dia]
        dia_id = mdb.rutina_dias.insert_one({
            "id_rutina": rutina_id, "dia_semana": dia,
            "grupo_muscular": grupo, "orden": orden,
        }).inserted_id
        if grupo == "descanso":
            continue
        pool = [e for e in EJERCICIOS_CATALOGO if e[1] == grupo] or EJERCICIOS_CATALOGO
        elegidos = RNG.sample(pool, min(4, len(pool)))
        mdb.rutina_ejercicios.insert_many([
            {"id_rutina_dia": dia_id, "nombre_ejercicio": ej[0], "series": str(RNG.choice([3, 4, 5])),
             "repeticiones": str(RNG.choice([8, 10, 12, 15])), "peso": "", "grupo_muscular": ej[1],
             "unidad": "kg", "notas": "", "orden": o}
            for o, ej in enumerate(elegidos)
        ])

    # ── Asistencias + entrenamientos registrados: 2-4 veces por semana ─────
    peso_actual = float(miembro_doc.get("peso_inicial") or RNG.uniform(60, 90))
    dias_totales = (fecha_fin_hist - fecha_inicio_hist).days
    semanas = max(1, dias_totales // 7 + 1)

    asistencias_bulk = []
    n_entrenamientos = 0
    for semana in range(semanas):
        n_sesiones = RNG.randint(2, 4)
        offsets = RNG.sample(range(7), min(n_sesiones, 7))
        for offset in offsets:
            fecha_dia = fecha_inicio_hist + timedelta(days=semana * 7 + offset)
            if fecha_dia < fecha_inicio_hist or fecha_dia > fecha_fin_hist:
                continue
            fecha_dt = datetime(fecha_dia.year, fecha_dia.month, fecha_dia.day,
                                 RNG.randint(6, 20), RNG.choice([0, 15, 30, 45]))

            asistencias_bulk.append({
                "id_miembro": uid, "id_gimnasio": gym_id, "fecha": fecha_dt,
                "hora_entrada": fecha_dt.strftime("%H:%M:%S"), "origen": "manual",
            })

            dia_nombre = DIAS_SEMANA[fecha_dia.weekday()]
            grupo_dia = PLAN_SEMANA.get(dia_nombre, "cardio")
            if grupo_dia == "descanso":
                grupo_dia = RNG.choice(["pecho", "espalda", "piernas", "cardio"])
            pool = [e for e in EJERCICIOS_CATALOGO if e[1] == grupo_dia] or EJERCICIOS_CATALOGO
            elegidos = RNG.sample(pool, min(4, len(pool)))

            ejercicios_doc, total_series, volumen = [], 0, 0.0
            for ej_nombre, ej_grupo, ej_tipo in elegidos:
                series = []
                for _s in range(RNG.randint(3, 4)):
                    reps = RNG.choice([8, 10, 12, 15])
                    peso_serie = 0 if ej_tipo == "cardio" else round(RNG.uniform(15, 80), 1)
                    series.append({"repeticiones": reps, "peso": peso_serie, "unidad": "kg"})
                    total_series += 1
                    volumen += reps * peso_serie
                ejercicios_doc.append({"nombre": ej_nombre, "grupo": ej_grupo,
                                        "series": series, "completado": True})

            duracion = RNG.choice([45, 50, 60, 70, 75])
            met = 6.0 if grupo_dia in ("cardio", "piernas") else 5.0
            calorias = round(met * peso_actual * (duracion / 60.0))

            peso_corporal_hoy = None
            if RNG.random() < 0.12:
                peso_actual = round(max(45, peso_actual + RNG.uniform(-0.4, 0.3)), 1)
                peso_corporal_hoy = peso_actual

            mdb.entrenamientos_realizados.insert_one({
                "id_miembro": uid, "id_gimnasio_pg": gym_id,
                "id_rutina": rutina_id if RNG.random() < 0.7 else None,
                "nombre_rutina": f"Dia de {grupo_dia}" if RNG.random() < 0.7 else "Entrenamiento libre",
                "grupo_muscular": grupo_dia,
                "fecha": fecha_dt,
                "duracion_min": duracion,
                "duracion_estimada_min": duracion,
                "ejercicios": ejercicios_doc,
                "total_ejercicios": len(ejercicios_doc),
                "total_series": total_series,
                "volumen_total": round(volumen, 1),
                "calorias_estimadas": calorias,
                "peso_corporal": peso_corporal_hoy,
                "notas": None,
            })
            n_entrenamientos += 1

    if asistencias_bulk:
        mdb.asistencias.insert_many(asistencias_bulk)

    return {"pagos": len(pagos_bulk), "asistencias": len(asistencias_bulk), "entrenamientos": n_entrenamientos}


def seed_vitazone(gym_id):
    app = create_app()
    with app.app_context():
        mdb = get_db()
        gym = get_or_create_gimnasio(gym_id)

        roles = {r.nombre: r for r in Rol.query.all()}
        faltantes = [r for r in ("Entrenador", "Miembro") if r not in roles]
        if faltantes:
            raise RuntimeError(
                f"Faltan roles en la base de datos: {faltantes}. "
                f"Este script no los crea (a diferencia de seed_pg.py) porque asume "
                f"una plataforma que ya tiene al menos un gimnasio dado de alta."
            )

        tm_list = TipoMembresia.query.filter_by(id_gimnasio=gym.id, activo=True).all()
        if not tm_list:
            tm_list = crear_membresias_default(gym)
        else:
            print(f"  Tipos de membresia ya existentes para este gimnasio: {len(tm_list)}")

        existentes = Usuario.query.filter_by(id_gimnasio=gym.id, id_rol=roles["Miembro"].id).all()
        print(f"  Miembros que ya estaban registrados en el gimnasio: {len(existentes)}")

        nuevos_miembros = crear_miembros_nuevos(gym, roles)
        trainers = crear_entrenadores_nuevos(gym, roles)
        crear_catalogo_ejercicios(gym, trainers)
        crear_productos_pos(mdb, gym)

        todos_los_miembros = existentes + nuevos_miembros
        nuevos_ids = {u.id for u in nuevos_miembros}

        total_pagos = total_asist = total_entre = 0
        print(f"\n  Generando historial ({FECHA_INICIO} a {FECHA_FIN}) para {len(todos_los_miembros)} miembros...")
        for usuario in todos_los_miembros:
            es_nuevo = usuario.id in nuevos_ids
            fecha_reg_nueva = (FECHA_INICIO + timedelta(days=RNG.randint(0, 45))) if es_nuevo else None
            miembro_doc, _creado = obtener_o_crear_perfil_miembro(mdb, usuario, gym, fecha_reg_nueva)

            fecha_reg_parsed = _parse_fecha(miembro_doc.get("fecha_registro")) or FECHA_INICIO
            f_inicio_hist = max(FECHA_INICIO, fecha_reg_parsed)
            if f_inicio_hist > FECHA_FIN:
                f_inicio_hist = FECHA_INICIO

            resumen = generar_historial_miembro(mdb, gym, usuario, miembro_doc, tm_list, f_inicio_hist, FECHA_FIN)
            total_pagos += resumen["pagos"]
            total_asist += resumen["asistencias"]
            total_entre += resumen["entrenamientos"]

        print(f"\n{'=' * 56}")
        print("INGESTA DE VITAZONE COMPLETADA")
        print(f"{'=' * 56}")
        print(f"  Gimnasio: #{gym.id} {gym.nombre}")
        print(f"  Miembros nuevos: {len(nuevos_miembros)}  |  Ya registrados: {len(existentes)}  |  Total con historial nuevo: {len(todos_los_miembros)}")
        print(f"  Entrenadores nuevos: {len(trainers)}")
        print(f"  Pagos generados: {total_pagos}")
        print(f"  Asistencias generadas: {total_asist}")
        print(f"  Entrenamientos registrados: {total_entre}")
        print()
        print(f"  {'Rol':<12} {'Email':<30} Password")
        print(f"  {'-' * 12} {'-' * 30} {'-' * 12}")
        for i, u in enumerate(nuevos_miembros):
            print(f"  {'Miembro':<12} {u.email:<30} Vita{i + 1}!")
        for i, u in enumerate(trainers):
            print(f"  {'Entrenador':<12} {u.email:<30} Train{i + 1}!")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingesta aditiva de datos de demostracion para un gimnasio.")
    parser.add_argument("--gym-id", type=int, default=GYM_ID_DEFAULT,
                         help=f"ID del gimnasio a usar (default: {GYM_ID_DEFAULT})")
    args = parser.parse_args()
    seed_vitazone(args.gym_id)
