"""023 — Ciclo de facturación anual (SCRUM-224)

Hasta esta migración, todo plan se facturaba mensual: PlanSuscripcion solo
tenía precio_mensual_mxn, y Suscripcion no registraba ningún "ciclo" --
cada renovación sumaba 30 días y cobraba precio_mensual_mxn, sin excepción
(ver routes/owner_gym/billing.py::renovar_suscripcion_demo y
routes/owner_gym/suscripcion_recurrente.py::crear_recurrente, que llamaban
a la pasarela con dias_periodo=30 fijo).

Cambios:
  planes_suscripcion +precio_anual_mxn (centavos, nullable -- None = el
                      plan no ofrece anual, hoy ningún caso real pero deja
                      la puerta abierta sin forzar un valor).

  suscripciones       +ciclo_facturacion ('mensual' | 'anual', NOT NULL,
                      default 'mensual'). Mismo patrón que
                      pasarela_recurrente/estado_recurrente (ver migración
                      010/015): un VARCHAR validado en Python, no un ENUM
                      de Postgres -- no se justifica un tipo nuevo para dos
                      valores que ya se validan en el blueprint.

Backfill: toda Suscripcion existente se marca 'mensual' -- es el único
ciclo que existió hasta ahora, así que no hay ambigüedad que resolver.

Precio anual PROVISIONAL (mismo disclaimer que las migraciones 021/022):
10 meses de precio mensual -- "2 meses gratis" al pagar el año completo,
la convención más común en SaaS y fácil de explicar en una tarjeta de
precios. Confirmar con el equipo antes de anunciarlo a un cliente real.
Starter se deja en 0 (plan gratuito, no hay nada que descontar).

Riesgo (igual que en 017/021/022): sin Postgres real accesible desde el
sandbox donde se escribió esta migración -- validar antes de aplicar
contra una base real o de respaldo, y correr el downgrade también antes
de confiar en él.

Revision ID: 023
Revises: 022
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision      = "023"
down_revision = "022"
branch_labels = None
depends_on    = None

# nombre_plan -> precio_anual_mxn (centavos) = 10x el mensual ("2 meses gratis")
_PRECIOS_ANUALES = {
    "starter":    0,
    "basico":     499000,    # 10 x $499.00
    "pro":        999000,    # 10 x $999.00
    "enterprise": 1999000,   # 10 x $1,999.00
}


def upgrade():
    # ── 1. planes_suscripcion.precio_anual_mxn ──────────────────────────
    op.add_column(
        "planes_suscripcion",
        sa.Column("precio_anual_mxn", sa.Integer(), nullable=True),
    )
    for nombre_plan, precio in _PRECIOS_ANUALES.items():
        op.execute(
            f"UPDATE planes_suscripcion SET precio_anual_mxn = {precio} "
            f"WHERE nombre = '{nombre_plan}' AND precio_anual_mxn IS NULL"
        )

    # ── 2. suscripciones.ciclo_facturacion ───────────────────────────────
    op.add_column(
        "suscripciones",
        sa.Column("ciclo_facturacion", sa.String(10), nullable=True),
    )
    op.execute("UPDATE suscripciones SET ciclo_facturacion = 'mensual' WHERE ciclo_facturacion IS NULL")
    op.alter_column(
        "suscripciones", "ciclo_facturacion",
        nullable=False, server_default="mensual",
    )
    op.create_check_constraint(
        "ck_suscripciones_ciclo_facturacion",
        "suscripciones",
        "ciclo_facturacion IN ('mensual', 'anual')",
    )


def downgrade():
    op.drop_constraint("ck_suscripciones_ciclo_facturacion", "suscripciones", type_="check")
    op.drop_column("suscripciones", "ciclo_facturacion")
    op.drop_column("planes_suscripcion", "precio_anual_mxn")
