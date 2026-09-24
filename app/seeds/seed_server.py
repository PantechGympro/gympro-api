"""
seeds/seed_server.py — Seed reducido para el despliegue de demostración en el
servidor (SCRUM-163).

seed_pg.py genera 5 gimnasios pensados para desarrollo local. Este seed es
distinto: genera exactamente lo que necesita la imagen que se publica en el
VPS para que cualquier módulo del sistema tenga algo que mostrar desde el
primer login, sin arrastrar el volumen de datos (ni el tiempo de build) del
seed de desarrollo.

Contenido generado:
  - 1 superadmin de plataforma + los 4 planes de suscripción SaaS de la
    plataforma (starter/basico/pro/enterprise, heredados de seed_pg_base())
  - 3 gimnasios: CrossFit Titan, Iron Temple Gym, Elite Performance Center
    — los tres, de los 5 de seed_pg.py, que ya traen configuradas exactamente
    5 membresías, cada una con beneficios, tipo (estandar/promocion) y datos
    completos (heredado de seed_pg.py)
  - Por gimnasio: 1 owner_gym, 2 entrenadores, 1 recepcionista, 64 miembros
  - Cada miembro con historial completo en MongoDB (pagos, asistencias,
    progreso físico, sesiones, rutina y dieta propias con todos sus campos
    llenos) para que las métricas de cada módulo tengan datos reales
  - Catálogo de ejercicios INDIVIDUAL por entrenador (no solo por gimnasio):
    cada entrenador tiene su propia biblioteca en PostgreSQL (Ejercicio con
    id_entrenador propio, con descripción y duración para cardio), que es
    justo lo que exige /api/trainer/exercises al armar una rutina.
  - Biblioteca de RECETAS individuales por entrenador (mdb.recetas, con
    macros, ingredientes e instrucciones) para poder armar dietas a partir
    de recetas reales en vez de solo texto libre.
  - Un subconjunto de miembros de cada gimnasio vinculado a uno de sus dos
    entrenadores (pt_solicitudes en estado "aceptada"), con una rutina del
    catálogo del entrenador asignada y un plan alimenticio armado con
    recetas de su propia biblioteca — visible tanto en la vista del
    entrenador (semanas/dias/comidas) como en "Plan Asignado" del miembro
    (comidas planas + creado_por="entrenador", igual que ahora genera
    POST /api/trainer/diets tras el fix de esta misma sesión).
  - Catálogo de productos del Punto de Venta por gimnasio (suplementos,
    bebidas, ropa, accesorios, snacks) con precio, stock y descripción —
    las imágenes se dejan vacías a propósito para que el usuario las cargue.

Uso:
  docker compose exec api python -m app.seeds.seed_server
"""
import os, sys
from datetime import date, datetime, timedelta, timezone

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

N_TRAINERS_SERVIDOR   = 2
N_STAFF_SERVIDOR      = 1
N_MIEMBROS_SERVIDOR   = 64
CLIENTES_POR_TRAINER  = 8   # cuántos miembros quedan vinculados a cada entrenador con contenido asignado

# ── Catálogo de productos del POS ─────────────────────────────────────────────
# Compartido por los 3 gimnasios (con variación aleatoria de precio/stock) —
# categorías tal como las define el selector del frontend (POSProductoModal.jsx):
# General, Suplementos, Accesorios, Snacks, Bebidas, Ropa, Combos.
PRODUCTOS_POS = [
    ("Proteína Whey 1kg — Vainilla",    "Suplementos", 850.0, 24, "Whey concentrada 24g de proteína por porción, sabor vainilla."),
    ("Proteína Whey 1kg — Chocolate",   "Suplementos", 850.0, 18, "Whey concentrada 24g de proteína por porción, sabor chocolate."),
    ("Creatina Monohidratada 300g",     "Suplementos", 480.0, 30, "Creatina micronizada sin sabor, 5g por porción."),
    ("Pre-entreno Explosivo 300g",      "Suplementos", 620.0, 15, "Pre-entreno con cafeína, beta-alanina y citrulina."),
    ("BCAA 2:1:1 200 caps",             "Suplementos", 390.0, 20, "Aminoácidos de cadena ramificada para recuperación muscular."),
    ("Multivitamínico Deportivo 60 caps","Suplementos", 280.0, 25, "Complejo vitamínico formulado para alta demanda física."),
    ("Barra de proteína (unidad)",      "Snacks",       45.0, 80, "20g de proteína, bajo en azúcar, sabor chocolate y cacahuate."),
    ("Barra energética (unidad)",       "Snacks",       35.0, 60, "Avena, miel y frutos secos, ideal antes de entrenar."),
    ("Mix de nueces 50g",               "Snacks",       55.0, 40, "Mezcla de almendra, nuez y arándano deshidratado."),
    ("Agua embotellada 600ml",          "Bebidas",      20.0, 150,"Agua purificada, presentación individual."),
    ("Bebida electrolitos 500ml",       "Bebidas",      35.0, 90, "Repone electrolitos perdidos durante el entrenamiento."),
    ("Café americano",                  "Bebidas",      30.0, 999,"Preparado en el momento en la barra del gimnasio."),
    ("Playera GymPro (unisex)",         "Ropa",        280.0, 35, "Playera deportiva transpirable con logo bordado."),
    ("Toalla deportiva GymPro",         "Ropa",        190.0, 45, "Toalla de microfibra de secado rápido, tamaño mediano."),
    ("Guantes de entrenamiento",        "Accesorios",  220.0, 28, "Guantes acolchados con soporte de muñeca."),
    ("Cinturón de levantamiento",       "Accesorios",  650.0, 12, "Cinturón de cuero para sentadilla y peso muerto."),
    ("Straps de agarre (par)",          "Accesorios",  180.0, 22, "Correas de agarre para jalones y peso muerto."),
    ("Shaker GymPro 700ml",             "Accesorios",  150.0, 50, "Shaker con malla mezcladora, libre de BPA."),
    ("Candado de casillero",            "General",      95.0, 60, "Candado de combinación para casilleros del gimnasio."),
    ("Cuerda para saltar (speed rope)", "Accesorios",  160.0, 18, "Cuerda de velocidad ajustable con rodamientos."),
]

# ── Biblioteca de recetas por perfil nutricional ──────────────────────────────
# Solo se definen las 3 usadas por los gimnasios del seed de servidor
# (alta_proteina_carbos, alta_proteina_calorias, personalizada) — las otras
# dos (plant_based, deficit_calorico) no aplican a CrossFit Titan, Iron Temple
# Gym ni Elite Performance Center.
RECETAS_POR_DIETA = {
    "alta_proteina_carbos": [
        {"nombre": "Bowl de pollo y arroz post-WOD", "calorias": 620, "proteinas_g": 48, "carbohidratos_g": 70, "grasas_g": 14,
         "tiempo_preparacion_min": 20, "instrucciones": "Cocina el arroz, sella la pechuga de pollo en cubos y mezcla con verduras salteadas.",
         "ingredientes": [{"nombre": "Pechuga de pollo", "cantidad": 200, "unidad": "g"}, {"nombre": "Arroz blanco", "cantidad": 200, "unidad": "g"},
                          {"nombre": "Brócoli", "cantidad": 100, "unidad": "g"}, {"nombre": "Aceite de oliva", "cantidad": 1, "unidad": "cda"}]},
        {"nombre": "Omelette de claras con avena", "calorias": 480, "proteinas_g": 38, "carbohidratos_g": 52, "grasas_g": 10,
         "tiempo_preparacion_min": 12, "instrucciones": "Bate las claras y cocina en sartén antiadherente; sirve con avena cocida y plátano.",
         "ingredientes": [{"nombre": "Claras de huevo", "cantidad": 6, "unidad": "pza"}, {"nombre": "Avena", "cantidad": 80, "unidad": "g"},
                          {"nombre": "Plátano", "cantidad": 1, "unidad": "pza"}]},
        {"nombre": "Batata al horno con atún", "calorias": 540, "proteinas_g": 42, "carbohidratos_g": 58, "grasas_g": 12,
         "tiempo_preparacion_min": 35, "instrucciones": "Hornea la batata a 200°C por 30 min y sirve con atún y un toque de aceite de oliva.",
         "ingredientes": [{"nombre": "Batata", "cantidad": 250, "unidad": "g"}, {"nombre": "Atún en agua", "cantidad": 160, "unidad": "g"}]},
        {"nombre": "Wrap de pollo y vegetales", "calorias": 500, "proteinas_g": 40, "carbohidratos_g": 48, "grasas_g": 15,
         "tiempo_preparacion_min": 15, "instrucciones": "Rellena una tortilla integral con pollo desmenuzado, lechuga y pico de gallo.",
         "ingredientes": [{"nombre": "Tortilla integral", "cantidad": 1, "unidad": "pza"}, {"nombre": "Pechuga de pollo", "cantidad": 150, "unidad": "g"},
                          {"nombre": "Lechuga", "cantidad": 30, "unidad": "g"}]},
        {"nombre": "Batido de proteína y avena", "calorias": 420, "proteinas_g": 35, "carbohidratos_g": 45, "grasas_g": 8,
         "tiempo_preparacion_min": 5, "instrucciones": "Licúa todos los ingredientes con hielo hasta obtener una mezcla homogénea.",
         "ingredientes": [{"nombre": "Proteína whey", "cantidad": 1, "unidad": "scoop"}, {"nombre": "Avena", "cantidad": 40, "unidad": "g"},
                          {"nombre": "Leche descremada", "cantidad": 300, "unidad": "ml"}]},
        {"nombre": "Pechuga a la plancha con quinoa", "calorias": 580, "proteinas_g": 50, "carbohidratos_g": 55, "grasas_g": 13,
         "tiempo_preparacion_min": 25, "instrucciones": "Cocina la quinoa y sella la pechuga sazonada; acompaña con verduras al vapor.",
         "ingredientes": [{"nombre": "Pechuga de pollo", "cantidad": 200, "unidad": "g"}, {"nombre": "Quinoa", "cantidad": 150, "unidad": "g"},
                          {"nombre": "Calabacita", "cantidad": 100, "unidad": "g"}]},
        {"nombre": "Tacos de carne magra", "calorias": 610, "proteinas_g": 46, "carbohidratos_g": 60, "grasas_g": 16,
         "tiempo_preparacion_min": 20, "instrucciones": "Cocina la carne en cubos con especias y sirve en tortillas de maíz con cebolla asada.",
         "ingredientes": [{"nombre": "Arrachera", "cantidad": 180, "unidad": "g"}, {"nombre": "Tortilla de maíz", "cantidad": 4, "unidad": "pza"}]},
        {"nombre": "Ensalada de atún y garbanzo", "calorias": 460, "proteinas_g": 36, "carbohidratos_g": 42, "grasas_g": 14,
         "tiempo_preparacion_min": 10, "instrucciones": "Mezcla el atún escurrido con garbanzo, jitomate y aderezo ligero de limón.",
         "ingredientes": [{"nombre": "Atún en agua", "cantidad": 140, "unidad": "g"}, {"nombre": "Garbanzo cocido", "cantidad": 150, "unidad": "g"}]},
    ],
    "alta_proteina_calorias": [
        {"nombre": "Bistec con papa y huevo", "calorias": 780, "proteinas_g": 55, "carbohidratos_g": 60, "grasas_g": 30,
         "tiempo_preparacion_min": 25, "instrucciones": "Sella el bistec a fuego alto, fríe la papa en gajos y añade huevo estrellado.",
         "ingredientes": [{"nombre": "Bistec de res", "cantidad": 220, "unidad": "g"}, {"nombre": "Papa", "cantidad": 200, "unidad": "g"},
                          {"nombre": "Huevo", "cantidad": 2, "unidad": "pza"}]},
        {"nombre": "Pasta con pollo y crema ligera", "calorias": 820, "proteinas_g": 52, "carbohidratos_g": 85, "grasas_g": 25,
         "tiempo_preparacion_min": 22, "instrucciones": "Cocina la pasta al dente, saltea el pollo y mezcla con crema ligera y champiñones.",
         "ingredientes": [{"nombre": "Pasta", "cantidad": 150, "unidad": "g"}, {"nombre": "Pechuga de pollo", "cantidad": 200, "unidad": "g"},
                          {"nombre": "Champiñones", "cantidad": 100, "unidad": "g"}]},
        {"nombre": "Batido de volumen (mass gainer)", "calorias": 650, "proteinas_g": 45, "carbohidratos_g": 80, "grasas_g": 15,
         "tiempo_preparacion_min": 5, "instrucciones": "Licúa la proteína con leche entera, avena, plátano y crema de cacahuate.",
         "ingredientes": [{"nombre": "Proteína whey", "cantidad": 2, "unidad": "scoop"}, {"nombre": "Leche entera", "cantidad": 400, "unidad": "ml"},
                          {"nombre": "Crema de cacahuate", "cantidad": 1, "unidad": "cda"}]},
        {"nombre": "Salmón con arroz y aguacate", "calorias": 700, "proteinas_g": 48, "carbohidratos_g": 60, "grasas_g": 28,
         "tiempo_preparacion_min": 20, "instrucciones": "Hornea el salmón con limón y hierbas, sirve sobre arroz con aguacate en láminas.",
         "ingredientes": [{"nombre": "Salmón", "cantidad": 200, "unidad": "g"}, {"nombre": "Arroz blanco", "cantidad": 180, "unidad": "g"},
                          {"nombre": "Aguacate", "cantidad": 0.5, "unidad": "pza"}]},
        {"nombre": "Huevos revueltos con tocino y pan integral", "calorias": 640, "proteinas_g": 38, "carbohidratos_g": 45, "grasas_g": 32,
         "tiempo_preparacion_min": 15, "instrucciones": "Fríe el tocino, revuelve los huevos en la misma sartén y sirve con pan tostado.",
         "ingredientes": [{"nombre": "Huevo", "cantidad": 3, "unidad": "pza"}, {"nombre": "Tocino", "cantidad": 3, "unidad": "rebanadas"},
                          {"nombre": "Pan integral", "cantidad": 2, "unidad": "rebanadas"}]},
        {"nombre": "Carne molida con puré de papa", "calorias": 760, "proteinas_g": 50, "carbohidratos_g": 55, "grasas_g": 34,
         "tiempo_preparacion_min": 30, "instrucciones": "Dora la carne molida con cebolla y ajo; sirve sobre puré de papa cremoso.",
         "ingredientes": [{"nombre": "Carne molida de res", "cantidad": 220, "unidad": "g"}, {"nombre": "Papa", "cantidad": 250, "unidad": "g"},
                          {"nombre": "Mantequilla", "cantidad": 1, "unidad": "cda"}]},
        {"nombre": "Yogurt griego con granola y miel", "calorias": 520, "proteinas_g": 30, "carbohidratos_g": 65, "grasas_g": 16,
         "tiempo_preparacion_min": 5, "instrucciones": "Sirve el yogurt en un bowl y añade granola, miel y fruta picada.",
         "ingredientes": [{"nombre": "Yogurt griego", "cantidad": 250, "unidad": "g"}, {"nombre": "Granola", "cantidad": 60, "unidad": "g"},
                          {"nombre": "Miel", "cantidad": 1, "unidad": "cda"}]},
        {"nombre": "Sandwich de pavo y queso", "calorias": 590, "proteinas_g": 40, "carbohidratos_g": 55, "grasas_g": 20,
         "tiempo_preparacion_min": 10, "instrucciones": "Arma el sandwich con pan integral, pechuga de pavo, queso y verduras frescas.",
         "ingredientes": [{"nombre": "Pan integral", "cantidad": 2, "unidad": "rebanadas"}, {"nombre": "Pechuga de pavo", "cantidad": 120, "unidad": "g"},
                          {"nombre": "Queso panela", "cantidad": 40, "unidad": "g"}]},
    ],
    "personalizada": [
        {"nombre": "Salmón al horno con espárragos", "calorias": 560, "proteinas_g": 44, "carbohidratos_g": 20, "grasas_g": 30,
         "tiempo_preparacion_min": 25, "instrucciones": "Hornea el salmón con limón y eneldo junto con los espárragos a 200°C por 18 min.",
         "ingredientes": [{"nombre": "Salmón", "cantidad": 200, "unidad": "g"}, {"nombre": "Espárragos", "cantidad": 120, "unidad": "g"}]},
        {"nombre": "Bowl de pavo y quinoa con nueces", "calorias": 540, "proteinas_g": 40, "carbohidratos_g": 45, "grasas_g": 20,
         "tiempo_preparacion_min": 20, "instrucciones": "Cocina la quinoa, saltea el pavo en cubos y mezcla con nueces y arándanos deshidratados.",
         "ingredientes": [{"nombre": "Pechuga de pavo", "cantidad": 180, "unidad": "g"}, {"nombre": "Quinoa", "cantidad": 120, "unidad": "g"},
                          {"nombre": "Nueces", "cantidad": 20, "unidad": "g"}]},
        {"nombre": "Omelette de claras con espinaca y champiñón", "calorias": 380, "proteinas_g": 32, "carbohidratos_g": 12, "grasas_g": 18,
         "tiempo_preparacion_min": 12, "instrucciones": "Saltea la espinaca y el champiñón, agrega las claras batidas y cocina a fuego medio.",
         "ingredientes": [{"nombre": "Claras de huevo", "cantidad": 5, "unidad": "pza"}, {"nombre": "Espinaca", "cantidad": 50, "unidad": "g"},
                          {"nombre": "Champiñones", "cantidad": 60, "unidad": "g"}]},
        {"nombre": "Ensalada ejecutiva de pollo y aguacate", "calorias": 500, "proteinas_g": 38, "carbohidratos_g": 22, "grasas_g": 28,
         "tiempo_preparacion_min": 15, "instrucciones": "Mezcla la pechuga a la plancha en cubos con lechuga, aguacate, jitomate cherry y aderezo ligero.",
         "ingredientes": [{"nombre": "Pechuga de pollo", "cantidad": 180, "unidad": "g"}, {"nombre": "Aguacate", "cantidad": 0.5, "unidad": "pza"},
                          {"nombre": "Lechuga", "cantidad": 60, "unidad": "g"}]},
        {"nombre": "Bacalao con vegetales al vapor", "calorias": 420, "proteinas_g": 40, "carbohidratos_g": 18, "grasas_g": 15,
         "tiempo_preparacion_min": 20, "instrucciones": "Cocina el bacalao al vapor con hierbas y acompaña con brócoli y zanahoria al vapor.",
         "ingredientes": [{"nombre": "Bacalao", "cantidad": 200, "unidad": "g"}, {"nombre": "Brócoli", "cantidad": 80, "unidad": "g"},
                          {"nombre": "Zanahoria", "cantidad": 60, "unidad": "g"}]},
        {"nombre": "Batido verde de proteína y frutos rojos", "calorias": 360, "proteinas_g": 30, "carbohidratos_g": 35, "grasas_g": 8,
         "tiempo_preparacion_min": 5, "instrucciones": "Licúa la proteína con espinaca, frutos rojos congelados y agua de coco.",
         "ingredientes": [{"nombre": "Proteína whey", "cantidad": 1, "unidad": "scoop"}, {"nombre": "Frutos rojos", "cantidad": 100, "unidad": "g"},
                          {"nombre": "Espinaca", "cantidad": 30, "unidad": "g"}]},
        {"nombre": "Filete de res con ensalada mediterránea", "calorias": 600, "proteinas_g": 48, "carbohidratos_g": 25, "grasas_g": 32,
         "tiempo_preparacion_min": 22, "instrucciones": "Sella el filete a término medio y sirve con ensalada de pepino, jitomate, aceituna y queso feta.",
         "ingredientes": [{"nombre": "Filete de res", "cantidad": 200, "unidad": "g"}, {"nombre": "Queso feta", "cantidad": 30, "unidad": "g"}]},
        {"nombre": "Tazón de yogurt, chía y fruta de temporada", "calorias": 400, "proteinas_g": 22, "carbohidratos_g": 48, "grasas_g": 12,
         "tiempo_preparacion_min": 8, "instrucciones": "Mezcla el yogurt con chía, deja reposar 10 minutos y agrega fruta picada al servir.",
         "ingredientes": [{"nombre": "Yogurt natural", "cantidad": 200, "unidad": "g"}, {"nombre": "Semillas de chía", "cantidad": 15, "unidad": "g"},
                          {"nombre": "Fruta de temporada", "cantidad": 100, "unidad": "g"}]},
    ],
}


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
    necesitan para construir después la biblioteca individual de ejercicios y
    recetas de cada entrenador, y para vincularles clientes.
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
    for tm_data_raw in cfg["membresias"]:
        tm_data = dict(tm_data_raw)
        dias_promo = tm_data.pop("fecha_fin_promo_dias", None)
        if dias_promo is not None:
            tm_data["fecha_fin_promo"] = date.today() + timedelta(days=dias_promo)
        tm = TipoMembresia(id_gimnasio=gym.id, activo=True, **tm_data)
        db.session.add(tm); db.session.flush()
        tm_map[tm_data["nombre"]] = tm

    for (nombre_cl, desc, dur, cap) in cfg["clases"]:
        db.session.add(TipoClase(id_gimnasio=gym.id, nombre=nombre_cl,
                                 descripcion=desc, duracion_minutos=dur, capacidad_max=cap))
    db.session.flush()
    db.session.commit()

    print(f"  Miembros: {cfg['n_miembros']}  Entrenadores: {cfg['n_trainers']}  Staff: {cfg['n_staff']}")
    print(f"  TipoMem: {len(tm_map)}  Clases: {len(cfg['clases'])}")
    return gym, trainers, staff, miembros_pg, tm_map, idx


def _crear_ejercicios_individuales(gym, trainers, cfg):
    """
    Biblioteca de ejercicios INDIVIDUAL por entrenador (Ejercicio.id_entrenador),
    con descripción y duración para los de tipo cardio — es justo lo que lee
    GET /api/trainer/exercises (filtra por id_gimnasio + id_entrenador propio),
    usado al armar rutinas. Cada entrenador recibe su propia copia del catálogo
    del gimnasio: el constraint único es (gimnasio, entrenador, nombre), así que
    no hay colisión entre las copias de distintos entrenadores.
    """
    total = 0
    for trainer in trainers:
        for (nombre_ej, grupo, tipo_ej) in cfg["ejercicios"]:
            es_cardio = tipo_ej == "cardio"
            db.session.add(Ejercicio(
                id_gimnasio=gym.id, id_entrenador=trainer.id, nombre=nombre_ej,
                descripcion=f"Ejercicio de {tipo_ej} enfocado en {grupo.lower()}. "
                            f"Realiza cada repeticion con control y buena tecnica postural, "
                            f"priorizando la calidad del movimiento sobre la velocidad.",
                grupo_muscular=grupo, tipo=tipo_ej,
                series=RNG.choice([3, 4, 5]),
                repeticiones=RNG.choice(["5", "8", "10", "10-12", "12-15", "AMRAP"]),
                duracion_min=RNG.choice([5, 10, 15, 20]) if es_cardio else None,
            ))
            total += 1
    db.session.commit()
    print(f"  Ejercicios individuales: {total} ({len(cfg['ejercicios'])} x {len(trainers)} entrenadores)")


def _crear_recetas_entrenador(mdb, trainer, gym_id, dieta_tipo):
    """
    Biblioteca privada de recetas del entrenador (mdb.recetas, mismo esquema
    que POST /api/trainer/recipes) — con macros, ingredientes e instrucciones
    completos. Devuelve la lista de documentos insertados (con _id) para poder
    referenciarlos luego como id_receta en los planes alimenticios asignados.
    """
    plantillas = RECETAS_POR_DIETA.get(dieta_tipo, [])
    ahora = datetime.now(timezone.utc)
    docs = []
    for plantilla in plantillas:
        doc = {
            "id_entrenador_pg":       trainer.id,
            "id_gimnasio_pg":         gym_id,
            "nombre":                 plantilla["nombre"],
            "descripcion":            f"Receta de {trainer.nombre.split()[0]} — "
                                      f"{plantilla['calorias']} kcal, {plantilla['proteinas_g']}g de proteina.",
            "imagen":                 None,
            "calorias":               plantilla["calorias"],
            "proteinas_g":            plantilla["proteinas_g"],
            "carbohidratos_g":        plantilla["carbohidratos_g"],
            "grasas_g":               plantilla["grasas_g"],
            "tiempo_preparacion_min": plantilla["tiempo_preparacion_min"],
            "ingredientes":           plantilla["ingredientes"],
            "instrucciones":          plantilla["instrucciones"],
            "activo":                 True,
            "created_at":             ahora,
        }
        result = mdb.recetas.insert_one(doc)
        doc["_id"] = result.inserted_id
        docs.append(doc)
    return docs


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


def _crear_dieta_asignada(mdb, trainer, gym_id, miembro_uid, miembro_pg_id, objetivo, recetas):
    """
    Plan alimenticio del entrenador asignado directamente a un miembro,
    construido a partir de recetas reales de la biblioteca del entrenador
    (esquema v2 de mdb.dietas: id_entrenador_pg + id_miembro_pg + semanas,
    el mismo que usa POST /api/trainer/diets en diet_routes.py) — cada item
    de comida referencia su receta vía id_receta, tal como espera
    TrainerDiets.jsx para mostrar imagen/macros al hacer click.

    Además incluye el arreglo plano "comidas" (día 1 de la semana 1) y
    "creado_por": "entrenador", igual que ahora genera POST /api/trainer/diets
    tras el fix aplicado en esta misma sesión — así el plan se ve completo
    tanto en la vista del entrenador como en "Plan Asignado" del miembro.
    """
    comidas_nombres = ["Desayuno", "Comida", "Cena"]
    horas           = ["07:30", "14:00", "20:00"]
    dias_semana     = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    ahora           = datetime.now(timezone.utc)

    recetas_pool = list(recetas) if recetas else []

    def _comidas_del_dia():
        comidas = []
        for nombre_comida, hora in zip(comidas_nombres, horas):
            receta = RNG.choice(recetas_pool) if recetas_pool else None
            if receta:
                item = {
                    "id_receta":         str(receta["_id"]),
                    "nombre_alimento":   receta["nombre"],
                    "cantidad":          1,
                    "unidad":            "porcion",
                    "calorias":          receta["calorias"],
                    "proteinas_g":       receta["proteinas_g"],
                    "carbohidratos_g":   receta["carbohidratos_g"],
                    "grasas_g":          receta["grasas_g"],
                }
            else:
                item = {"nombre_alimento": nombre_comida, "cantidad": 1, "unidad": "porcion",
                        "calorias": 400, "proteinas_g": 25, "carbohidratos_g": 40, "grasas_g": 10}
            comidas.append({
                "nombre":                     nombre_comida,
                "hora":                       hora,
                "tiempo_desde_anterior_min":  RNG.choice([120, 180, 240]),
                "items":                      [item],
            })
        return comidas

    semana = {
        "numero": 1,
        "notas":  "",
        "dias":   [{"dia": dia, "comidas": _comidas_del_dia()} for dia in dias_semana],
    }

    dia1_comidas = semana["dias"][0]["comidas"]
    comidas_plano = [
        {
            "nombre":    c["nombre"],
            "hora":      c["hora"],
            "calorias":  sum(it.get("calorias", 0) for it in c["items"]),
            "alimentos": [it["nombre_alimento"] for it in c["items"]],
        }
        for c in dia1_comidas
    ]
    cal_meta = sum(c["calorias"] for c in comidas_plano) or RNG.randint(1800, 2800)

    doc = {
        "id_entrenador_pg":      trainer.id,
        "id_gimnasio_pg":        gym_id,
        "id_miembro_pg":         miembro_pg_id,
        "id_miembro":            miembro_uid,
        "nombre":                f"Plan {objetivo.lower()} — {trainer.nombre.split()[0]}",
        "objetivo":              objetivo,
        "calorias_meta":         cal_meta,
        "proteinas_meta_g":      RNG.randint(120, 220),
        "carbohidratos_meta_g":  RNG.randint(150, 320),
        "grasas_meta_g":         RNG.randint(50, 100),
        "duracion_semanas":      4,
        "notas":                 "Plan generado como parte de los datos de demostración, con recetas de la biblioteca del entrenador.",
        "semanas":               [semana],
        "comidas":               comidas_plano,
        "creado_por":            "entrenador",
        "fuente":                "manual",
        "archivo_fuente":        None,
        "eliminada":             False,
        "fecha_creacion":        ahora,
    }
    mdb.dietas.insert_one(doc)


def asignar_contenido_entrenadores(gym, trainers, miembros_pg, cfg):
    """
    Vincula a cada entrenador un subconjunto de los miembros del gimnasio
    (solicitud PT aceptada), con una rutina de catálogo y un plan alimenticio
    armado a partir de su propia biblioteca de recetas — así "Mis clientes"
    del entrenador no aparece vacío en la demo, y esos miembros ven contenido
    real en "Mi rutina"/"Mi nutrición".
    """
    mdb = get_db()
    gym_id = gym.id
    disponibles = list(miembros_pg)
    RNG.shuffle(disponibles)

    total_rutinas = total_dietas = total_solicitudes = total_recetas = 0

    for trainer in trainers:
        recetas = _crear_recetas_entrenador(mdb, trainer, gym_id, cfg["dieta_tipo"])
        total_recetas += len(recetas)

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

            _crear_dieta_asignada(mdb, trainer, gym_id, uid, cliente.id, objetivo, recetas)
            total_dietas += 1

    print(f"  Contenido de entrenadores: {total_recetas} recetas, {total_rutinas} rutinas de catálogo, "
          f"{total_solicitudes} clientes vinculados, {total_dietas} planes alimenticios asignados")


def seed_productos_pos(mdb, gym_id):
    """Catálogo de productos del punto de venta — sin imágenes (el usuario las carga después)."""
    ahora = datetime.now(timezone.utc)
    docs = []
    for nombre, categoria, precio_base, stock_base, descripcion in PRODUCTOS_POS:
        variacion_precio = RNG.uniform(0.92, 1.08)
        docs.append({
            "id_gimnasio":  gym_id,
            "nombre":       nombre,
            "precio":       round(precio_base * variacion_precio, 2),
            "stock":        max(0, stock_base + RNG.randint(-5, 10)),
            "categoria":    categoria,
            "descripcion":  descripcion,
            "imagenes":     [],
            "activo":       True,
            "es_combo":     False,
            "items_combo":  [],
            "created_at":   ahora,
        })
    mdb.productos.insert_many(docs)
    print(f"  Productos POS: {len(docs)}")


def seed_servidor():
    app = create_app()
    with app.app_context():
        mdb = get_db()
        reset_all(mdb)
        # Reset adicional de colecciones que reset_all() (compartido con seed_pg.py)
        # no limpia porque no las usa el seed de desarrollo.
        for col in ["recetas", "productos", "pt_solicitudes", "rutinas_asignadas",
                    "rutina_dias", "rutina_ejercicios", "consumo_recetas"]:
            mdb[col].drop()

        roles, planes_map = seed_pg_base()

        grand_total = {}
        idx = 0
        credentials = []

        for cfg in _gimnasios_servidor_cfg():
            gym, trainers, staff, miembros_pg, tm_map, idx = seed_gimnasio_servidor(cfg, roles, planes_map, idx)
            credentials.append((cfg["nombre"], cfg["admin_email"], "Admin1234!"))

            _crear_ejercicios_individuales(gym, trainers, cfg)

            totales = seed_mongo_gym(gym, miembros_pg, tm_map, cfg)
            print(f"  MongoDB docs: ", end="")
            for col, n in totales.items():
                print(f"{col}={n}", end="  ")
                grand_total[col] = grand_total.get(col, 0) + n
            print()

            asignar_contenido_entrenadores(gym, trainers, miembros_pg, cfg)
            seed_productos_pos(mdb, gym.id)

        print(f"\n{'='*56}")
        print("SEED DE SERVIDOR COMPLETADO")
        print(f"{'='*56}")
        print(f"  PG: {Gimnasio.query.count()} gimnasios | "
              f"{Usuario.query.count()} usuarios | {TipoMembresia.query.count()} membresias PG | "
              f"{Ejercicio.query.count()} ejercicios individuales")
        print(f"  MongoDB total: {sum(grand_total.values())} documentos de historial")
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
