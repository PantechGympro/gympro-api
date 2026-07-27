"""012_planes_caracteristicas — Planes SaaS con beneficios y limites por plan

Amplia planes_suscripcion con la informacion comercial y las banderas de
funciones que mas adelante permitiran bloquear modulos segun la suscripcion:

    titulo_comercial  etiqueta mostrada en la pagina de planes
    caracteristicas   lista de beneficios incluidos (JSON)
    limites           banderas de funciones habilitadas (JSON)
    orden / destacado presentacion en la web

Ademas siembra/actualiza cuatro planes: Starter, Basico, Pro y Enterprise.

Revision ID: 012
Revises: 011
Create Date: 2026-07-27
"""
import json
from alembic import op
import sqlalchemy as sa

revision      = "012"
down_revision = "011"
branch_labels = None
depends_on    = None


# Catalogo comercial. Precios en centavos MXN.
PLANES = [
    {
        "nombre": "starter",
        "titulo_comercial": "Para arrancar sin costo",
        "precio_mensual_mxn": 0,
        "max_miembros": 25,
        "orden": 1,
        "destacado": False,
        "descripcion": "Plan gratuito para probar GymPro con un grupo pequeno de miembros.",
        "caracteristicas": [
            "Hasta 25 miembros activos",
            "Registro de asistencias y pagos",
            "1 usuario administrador",
            "Soporte por correo electronico",
        ],
        "limites": {
            "max_miembros": 25, "max_staff": 1,
            "pos": False, "analiticas_basicas": True, "analiticas_ia": False,
            "rutinas_ia": False, "app_movil": False, "reportes_pdf": False,
            "respaldos": False, "cobros_en_linea": False, "soporte_prioritario": False,
        },
    },
    {
        "nombre": "basico",
        "titulo_comercial": "Ideal para gimnasios pequenos",
        "precio_mensual_mxn": 49900,
        "max_miembros": 50,
        "orden": 2,
        "destacado": False,
        "descripcion": "Gestion completa del dia a dia para gimnasios en operacion.",
        "caracteristicas": [
            "Hasta 50 miembros activos",
            "Punto de venta y control de productos",
            "Cobros en linea con PayPal y Mercado Pago",
            "Hasta 3 usuarios de staff",
            "Reportes en PDF",
            "App movil para tus miembros",
        ],
        "limites": {
            "max_miembros": 50, "max_staff": 3,
            "pos": True, "analiticas_basicas": True, "analiticas_ia": False,
            "rutinas_ia": False, "app_movil": True, "reportes_pdf": True,
            "respaldos": False, "cobros_en_linea": True, "soporte_prioritario": False,
        },
    },
    {
        "nombre": "pro",
        "titulo_comercial": "El mas elegido por gimnasios en crecimiento",
        "precio_mensual_mxn": 99900,
        "max_miembros": 200,
        "orden": 3,
        "destacado": True,
        "descripcion": "Suma inteligencia de negocio y automatizacion con IA.",
        "caracteristicas": [
            "Hasta 200 miembros activos",
            "Todo lo del plan Basico",
            "Analiticas con IA: riesgo de abandono y clientes por valor",
            "Generacion de rutinas y dietas asistida por IA",
            "Respaldos automaticos de tu informacion",
            "Hasta 10 usuarios de staff",
        ],
        "limites": {
            "max_miembros": 200, "max_staff": 10,
            "pos": True, "analiticas_basicas": True, "analiticas_ia": True,
            "rutinas_ia": True, "app_movil": True, "reportes_pdf": True,
            "respaldos": True, "cobros_en_linea": True, "soporte_prioritario": False,
        },
    },
    {
        "nombre": "enterprise",
        "titulo_comercial": "Para cadenas y multiples sucursales",
        "precio_mensual_mxn": 199900,
        "max_miembros": None,
        "orden": 4,
        "destacado": False,
        "descripcion": "Sin limites, con soporte dedicado y acuerdos de nivel de servicio.",
        "caracteristicas": [
            "Miembros y staff ilimitados",
            "Todo lo del plan Pro",
            "Laboratorio de Machine Learning completo",
            "Soporte prioritario con SLA",
            "Acompanamiento en la implementacion",
        ],
        "limites": {
            "max_miembros": None, "max_staff": None,
            "pos": True, "analiticas_basicas": True, "analiticas_ia": True,
            "rutinas_ia": True, "app_movil": True, "reportes_pdf": True,
            "respaldos": True, "cobros_en_linea": True, "soporte_prioritario": True,
        },
    },
]


def upgrade():
    op.add_column("planes_suscripcion", sa.Column("titulo_comercial", sa.String(length=120), nullable=True))
    op.add_column("planes_suscripcion", sa.Column("caracteristicas", sa.JSON(), nullable=True))
    op.add_column("planes_suscripcion", sa.Column("limites", sa.JSON(), nullable=True))
    op.add_column("planes_suscripcion", sa.Column("orden", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("planes_suscripcion", sa.Column("destacado", sa.Boolean(), nullable=False,
                                                  server_default=sa.text("false")))

    conn = op.get_bind()
    for p in PLANES:
        existe = conn.execute(
            sa.text("SELECT id FROM planes_suscripcion WHERE nombre = :n"), {"n": p["nombre"]}
        ).fetchone()

        params = {
            "n": p["nombre"],
            "t": p["titulo_comercial"],
            "pr": p["precio_mensual_mxn"],
            "mm": p["max_miembros"],
            "d": p["descripcion"],
            "c": json.dumps(p["caracteristicas"], ensure_ascii=False),
            "l": json.dumps(p["limites"], ensure_ascii=False),
            "o": p["orden"],
            "de": p["destacado"],
        }

        if existe:
            conn.execute(sa.text("""
                UPDATE planes_suscripcion
                   SET titulo_comercial = :t,
                       precio_mensual_mxn = :pr,
                       max_miembros = :mm,
                       descripcion = :d,
                       caracteristicas = CAST(:c AS JSON),
                       limites = CAST(:l AS JSON),
                       orden = :o,
                       destacado = :de,
                       activo = true
                 WHERE nombre = :n
            """), params)
        else:
            conn.execute(sa.text("""
                INSERT INTO planes_suscripcion
                    (nombre, precio_mensual_mxn, max_miembros, descripcion, activo,
                     titulo_comercial, caracteristicas, limites, orden, destacado, created_at)
                VALUES
                    (:n, :pr, :mm, :d, true,
                     :t, CAST(:c AS JSON), CAST(:l AS JSON), :o, :de, now())
            """), params)


def downgrade():
    op.drop_column("planes_suscripcion", "destacado")
    op.drop_column("planes_suscripcion", "orden")
    op.drop_column("planes_suscripcion", "limites")
    op.drop_column("planes_suscripcion", "caracteristicas")
    op.drop_column("planes_suscripcion", "titulo_comercial")
