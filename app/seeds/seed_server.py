"""
seeds/seed_server.py — Seed reducido para el despliegue de demostración en el
servidor (SCRUM-163).

seed_pg.py genera 5 gimnasios pensados para desarrollo local. Este seed es
distinto: genera exactamente lo que necesita la imagen que se publica en el
VPS para que cualquier módulo del sistema tenga algo que mostrar desde el
primer login, sin arrastrar el volumen de datos (ni el tiempo de build) del
seed de desarrollo.

Contenido generado:
  - 1 superadmin de plataforma + planes de suscripción SaaS de la plataforma
    (heredados de seed_pg_base(), sin cambios)
  - 3 gimnasios: CrossFit Titan, Iron Temple Gym, Elite Performance Center
    — los tres, de los 5 de seed_pg.py, que ya traen configuradas exactamente
    5 membresías, que es lo que pide esta historia
  - Por gimnasio: 1 owner_gym, 2 entrenadores, 1 recepcionista, 28 miembros
  - Cada miembro con historial completo en MongoDB (pagos, asistencias,
    progreso físico, sesiones, rutina y dieta propias) para que las métricas
    de cada módulo tengan datos reales que mostrar
  - Un subconjunto de miembros de cada gimnasio vinculado a uno de sus dos
    entrenadores (pt_solicitudes en estado "aceptada"), con una rutina del
    catálogo del entrenador asignada (rutinas + rutina_dias + rutina_ejercicios
    + rutinas_asignadas) y un plan alimenticio asignado (mdb.dietas esquema v2,
    con id_entrenador_pg + id_miembro_pg) — así el módulo de "Mis clientes"
    del entrenador y "Mi rutina"/"Mi nutrición" del miembro tienen contenido
    real y no solo historial genérico.

Nota sobre los planes de suscripción SaaS: la historia pide 5; el seed de
desarrollo (seed_pg_base(), reutilizado aquí sin cambios) solo define 3
(básico/pro/enterprise). Se optó por no inventar 2 planes ficticios y dejar
los 3 reales tal como están, ya que es lo que la propia plataforma ofrece
hoy en /superadmin/planes — hay que confirmarlo con el Product Owner si de
verdad hacen falta 5 planes de PLATAFORMA (no de gimnasio).

Uso:
  docker compose exec api python -m app.seeds.seed_server
"""
import os, sys
from datetime import datetime, timedelta, timezone

from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.models.pg.usuario import Usuario
from app.mongo import get_db

from app.seeds.seed_pg import (
    RNG, GIMNASIOS_CFG,
    nombre_aleatorio, email_slug,
    reset_all, seed_pg_base, seed_mongo_gym,
)
from app.models.pg.gimnasio            import Gimnasio
from app.models.pg.suscripcion         import Suscripcion
from app.models.pg.factura_suscripcion import FacturaSuscripcion
from app.models.pg.tipo_membresia      import TipoMembresia
from app.models.pg.ejercicio           import Ejercicio
from app.models.pg.tipo_clase          import TipoClase
from app.extensions import db

# Los únicos 3 gimnasios de seed_pg.py que ya traen exactamente 5 membresías
# configuradas (requisito explícito de esta historia).
NOMBRES_GIMNASIOS_SERVIDOR = {"CrossFit Titan", "Iron Temple Gym", "Elite Performance Center"}

N_TRAINERS_SERVIDOR = 2
N_STAFF_SERVIDOR    = 1
N_MIEMBROS_SERVIDOR = 28
CLIENTES_POR_TRAINER = 5   # cuántos miembros quedan vinculados a cada entrenador con contenido asignado


def _gimnasios_servidor_cfg():
    """Las 3 configuraciones de seed_pg.py, con los tamaños que pide esta historia."""
    seleccion = [dict(cfg) for cfg in GIMNASIOS_CFG if cfg["nombre"] in NOMBRES_GIMNASIOS_SERVIDOR]
    if len(seleccion) != 3:
        encontrados = [c["nombre"] for c in seleccion]
        raise RuntimeError(
            f"Se esperaban 3 gimnasios con 5 membresías en GIMNASIOS_CFG, se encontraron: {encontrados}. "
            "Revisa NOMBRES_GIMNASIOS_SERVIDOR si seed_pg.py cambió."
        )
    for cfg in seleccion:
        cfg["n_trainers"] = N_TRAINERS_SERVIDOR
        cfg["n_staff"]    = N_STAFF_SERVIDOR
        cfg["n_miembros"] = N_MIEMBROS_SERVIDOR
    return seleccion


def seed_gimnasio_servidor(cfg, roles, planes_map, idx_start):
    """
    Igual que seed_gimnasio() de seed_pg.py, pero además devuelve las listas de
    Usuario de entrenadores y staff — seed_gimnasio() las descarta, y aquí se
    necesitan para vincular clientes a cada entrenador más adelante.
    """
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
                        fecha_inicio=ahora, fecha_proximo_cobro=ahora + timedelta(days=30))
    db.session.add(sub)
    db.session.flush()
    db.session.add(FacturaSuscripcion(
        id_suscripcion=sub.id, monto=plan.precio_mensual_mxn, moneda="MXN",
        estado="pagada", fecha_emision=ahora, fecha_vencimiento=ahora + timedelta(days=30)))

    admin = Usuario(nombre=cfg["admin_nombre"], email=cfg["admin_email"],
                    id_rol=roles["owner_gym"].id, id_gimnasio=gym.id, activo=True)
    admin.set_password("Admin1234!")
    db.session.add(admin)
    db.session.flush()
    print(f"  Owner: {cfg['admin_email']}")

    idx = idx_start
    trainers, staff, miembros_pg = [], [], []

    for _ in range(cfg["n_trainers"]):
        idx += 1
        nombre = nombre_aleatorio(RNG.random() > 0.5)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Entrenador"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(f"Train{idx}!")
        db.session.add(u); db.session.flush()
        trainers.append(u)

    for _ in range(cfg["n_staff"]):
        idx += 1
        nombre = nombre_aleatorio(RNG.random() > 0.4)
        u = Usuario(nombre=nombre, email=email_slug(nombre, idx),
                    id_rol=roles["Recepcionista"].id, id_gimnasio=gym.id, activo=True)
        u.set_password(f"Recep{idx}!")
        db.session.add(u); db.session.flush()
        staff.append(u)

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
                                 series=RNG.choice([3, 4, 5]),
                                 repeticiones=RNG.choice(["5", "8", "10", "10-12", "12-15", "AMRAP"])))
    db.session.flush()

    for (nombre_cl, desc, dur, cap) in cfg["clases"]:
        db.session.add(TipoClase(id_gimnasio=gym.id, nombre=nombre_cl,
                                 descripcion=desc, duracion_minutos=dur, capacidad_max=cap))
    db.session.flush()
    db.session.commit()

    print(f"  Miembros: {cfg['n_miembros']}  Entrenadores: {cfg['n_trainers']}  Staff: {cfg['n_staff']}")
    print(f"  TipoMem: {len(tm_map)}  Ejercicios: {len(cfg['ejercicios'])}  Clases: {len(cfg['clases'])}")
    return gym, trainers, staff, miembros_pg, tm_map, idx


def _crear_rutina_catalogo(mdb, trainer, gym_id, cfg):
    """
    Una rutina de catálogo del entrenador (mdb.rutinas con id_entrenador_pg,
    sin id_miembro — sigue exactamente el esquema que usa POST
    /api/trainer/routines en trainer_routes.py) con 3 días y 4 ejercicios
    por día tomados del catálogo de ejercicios del gimnasio.
    """
    cat = RNG.choice(cfg["rutina_categorias"])
    ejercicios_list = [e[0] for e in cfg["ejercicios"]]
    ahora = datetime.now(timezone.utc)

    rutina_doc = {
        "id_entrenador_pg": trainer.id,
        "id_gimnasio_pg":   gym_id,
        "id_miembro":       None,
        "nombre":           f"{cat} — Programa de {trainer.nombre.split()[0]}",
        "categoria":        cat,
        "dificultad":       RNG.choice(["Principiante", "Intermedio", "Avanzado"]),
        "duracion_minutos": RNG.choice([45, 60, 75, 90]),
        "descripcion":      f"Rutina de {cat.lower()} del catálogo de {trainer.nombre}.",
        "objetivo":         RNG.choice(cfg["objetivos"]),
        "activa":           True,
        "fecha_creacion":   ahora,
        "fecha_actualizacion": ahora,
    }
    rutina_id = mdb.rutinas.insert_one(rutina_doc).inserted_id

    dias_nombres = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes"]
    for orden_d, dia_nombre in enumerate(RNG.sample(dias_nombres, 3)):
        dia_id = mdb.rutina_dias.insert_one({
            "id_rutina":      rutina_id,
            "dia_semana":     dia_nombre,
            "grupo_muscular": "",
            "orden":          orden_d,
        }).inserted_id

        ejercicios_dia = RNG.sample(ejercicios_list, min(4, len(ejercicios_list)))
        mdb.rutina_ejercicios.insert_many([
            {
                "id_rutina_dia":    dia_id,
                "nombre_ejercicio": ej,
                "series":           str(RNG.choice([3, 4, 5])),
                "repeticiones":     str(RNG.choice([6, 8, 10, 12, 15])),
                "peso":             "",
                "grupo_muscular":   "",
                "unidad":           "kg",
                "notas":            "",
                "orden":            orden_e,
            }
            for orden_e, ej in enumerate(ejercicios_dia)
        ])

    return rutina_id, rutina_doc


def _crear_dieta_asignada(mdb, trainer, gym_id, miembro_uid, miembro_pg_id, objetivo):
    """
    Plan alimenticio del entrenador asignado directamente a un miembro —
    esquema v2 de mdb.dietas (id_entrenador_pg + id_miembro_pg + id_miembro +
    semanas), el mismo que usa POST /api/trainer/diets en diet_routes.py.
    No el esquema plano y sin dueño que genera seed_mongo_gym() para cada
    miembro (ese sigue existiendo aparte, como plan "propio" del miembro).
    """
    comidas = ["Desayuno", "Colación", "Comida", "Cena"]
    dias = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    semana = {
        "numero": 1,
        "notas": "",
        "dias": [
            {
                "dia": dia,
                "comidas": [
                    {
                        "nombre": comida,
                        "hora": f"{RNG.randint(6, 20):02d}:00",
                        "tiempo_desde_anterior_min": RNG.choice([120, 180, 240]),
                        "items": [{
                            "nombre_alimento": RNG.choice([
                                "Pechuga de pollo", "Arroz integral", "Avena", "Claras de huevo",
                                "Verduras al vapor", "Atun", "Batata", "Almendras",
                            ]),
                            "cantidad": RNG.choice([100, 150, 200]),
                            "unidad": "g",
                            "calorias": RNG.randint(80, 350),
                            "proteinas_g": RNG.randint(5, 40),
                            "carbohidratos_g": RNG.randint(0, 50),
                            "grasas_g": RNG.randint(0, 20),
                        }],
                    }
                    for comida in comidas
                ],
            }
            for dia in dias
        ],
    }

    doc = {
        "id_entrenador_pg":      trainer.id,
        "id_gimnasio_pg":        gym_id,
        "id_miembro_pg":         miembro_pg_id,
        "id_miembro":            miembro_uid,
        "nombre":                f"Plan {objetivo.lower()} — {trainer.nombre.split()[0]}",
        "objetivo":              objetivo,
        "calorias_meta":         RNG.randint(1800, 2800),
        "proteinas_meta_g":      RNG.randint(120, 220),
        "carbohidratos_meta_g":  RNG.randint(150, 320),
        "grasas_meta_g":         RNG.randint(50, 100),
        "duracion_semanas":      4,
        "notas":                 "Plan generado como parte de los datos de demostración.",
        "semanas":               [semana],
        "comidas":               [],
        "fuente":                "manual",
        "archivo_fuente":        None,
        "eliminada":             False,
        "fecha_creacion":        datetime.now(timezone.utc),
    }
    mdb.dietas.insert_one(doc)


def asignar_contenido_entrenadores(gym, trainers, miembros_pg, cfg):
    """
    Vincula a cada entrenador un subconjunto de los miembros del gimnasio
    (solicitud PT aceptada), con una rutina de catálogo y un plan alimenticio
    ya asignados — así "Mis clientes" del entrenador no aparece vacío en la
    demo, y esos miembros ven contenido real en "Mi rutina"/"Mi nutrición".
    """
    mdb = get_db()
    gym_id = gym.id
    disponibles = list(miembros_pg)
    RNG.shuffle(disponibles)

    total_rutinas = total_dietas = total_solicitudes = 0

    for trainer in trainers:
        rutina_id, rutina_doc = _crear_rutina_catalogo(mdb, trainer, gym_id, cfg)
        total_rutinas += 1

        clientes = [disponibles.pop() for _ in range(min(CLIENTES_POR_TRAINER, len(disponibles)))]
        for cliente in clientes:
            miembro_doc = mdb.miembros.find_one({"id_usuario_pg": cliente.id, "id_gimnasio_pg": gym_id})
            if not miembro_doc:
                continue
            uid = miembro_doc["_id"]
            objetivo = miembro_doc.get("objetivo") or RNG.choice(cfg["objetivos"])
            ahora = datetime.now(timezone.utc)

            mdb.pt_solicitudes.insert_one({
                "id_miembro_pg":     cliente.id,
                "nombre_miembro":    cliente.nombre,
                "id_entrenador_pg":  trainer.id,
                "nombre_entrenador": trainer.nombre,
                "id_gimnasio_pg":    gym_id,
                "estado":            "aceptada",
                "notas_miembro":     "",
                "tipo_sesion":       "individual",
                "notas_entrenador":  "",
                "fecha_solicitud":   ahora - timedelta(days=RNG.randint(30, 300)),
                "fecha_respuesta":   ahora - timedelta(days=RNG.randint(28, 299)),
            })
            total_solicitudes += 1

            mdb.miembros.update_one(
                {"_id": uid},
                {"$set": {"id_entrenador_pg": trainer.id}},
            )

            mdb.rutinas_asignadas.insert_one({
                "id_miembro_pg":     cliente.id,
                "id_entrenador_pg":  trainer.id,
                "id_gimnasio_pg":    gym_id,
                "id_rutina":         rutina_id,
                "nombre":            rutina_doc["nombre"],
                "descripcion":       rutina_doc["descripcion"],
                "categoria":         rutina_doc["categoria"],
                "dificultad":        rutina_doc["dificultad"],
                "duracion_minutos":  rutina_doc["duracion_minutos"],
                "nombre_entrenador": trainer.nombre,
                "notas_entrenador":  "",
                "activa":            True,
                "fecha_asignacion":  ahora - timedelta(days=RNG.randint(1, 60)),
            })

            _crear_dieta_asignada(mdb, trainer, gym_id, uid, cliente.id, objetivo)
            total_dietas += 1

    print(f"  Contenido de entrenadores: {total_rutinas} rutinas de catálogo, "
          f"{total_solicitudes} clientes vinculados, {total_dietas} planes alimenticios asignados")


def seed_servidor():
    app = create_app()
    with app.app_context():
        mdb = get_db()
        reset_all(mdb)
        roles, planes_map = seed_pg_base()

        grand_total = {}
        idx = 0
        credentials = []

        for cfg in _gimnasios_servidor_cfg():
            gym, trainers, staff, miembros_pg, tm_map, idx = seed_gimnasio_servidor(cfg, roles, planes_map, idx)
            credentials.append((cfg["nombre"], cfg["admin_email"], "Admin1234!"))

            totales = seed_mongo_gym(gym, miembros_pg, tm_map, cfg)
            print(f"  MongoDB docs: ", end="")
            for col, n in totales.items():
                print(f"{col}={n}", end="  ")
                grand_total[col] = grand_total.get(col, 0) + n
            print()

            asignar_contenido_entrenadores(gym, trainers, miembros_pg, cfg)

        print(f"\n{'='*56}")
        print("SEED DE SERVIDOR COMPLETADO")
        print(f"{'='*56}")
        print(f"  PG: {Gimnasio.query.count()} gimnasios | "
              f"{Usuario.query.count()} usuarios | {TipoMembresia.query.count()} membresias PG")
        print(f"  MongoDB total: {sum(grand_total.values())} documentos")
        print()
        print(f"  {'Gimnasio':<30} {'Email owner':<36} Password")
        print(f"  {'-'*30} {'-'*36} {'-'*12}")
        for nombre, email, pwd in credentials:
            print(f"  {nombre:<30} {email:<36} {pwd}")
        print()
        print(f"  Superadmin: superadmin@gymprodev.com / SuperAdmin1234!")
        print()


if __name__ == "__main__":
    seed_servidor()
