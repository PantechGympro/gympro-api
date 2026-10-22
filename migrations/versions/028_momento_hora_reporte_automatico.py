"""028 -- Momento y hora de envio configurables para reportes automaticos

Contexto: el usuario pidio poder elegir si el reporte automatico le llega
"al final del dia en el que esta" (mismo_dia -- el ultimo dia del periodo
que se esta cerrando, con los datos acumulados hasta ese momento) o "al
dia siguiente" (dia_siguiente -- el dia despues de que el periodo cerro,
el comportamiento original, antes fijo a las 06:00 MX), y a que hora
exacta del dia.

server_default en ambas columnas para que las filas ya existentes (creadas
antes de esta migracion) queden con el comportamiento de antes: dia
siguiente a las 06:00 -- nadie ve su automatizacion cambiar de horario sin
haberlo tocado.

Revision ID: 028
Revises: 027
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision      = "028"
down_revision = "027"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "configuracion_reporte_automatico",
        sa.Column("momento_envio", sa.String(length=12), nullable=False,
                  server_default="dia_siguiente"),
    )
    op.add_column(
        "configuracion_reporte_automatico",
        sa.Column("hora_envio", sa.Integer(), nullable=False, server_default="6"),
    )
    op.create_check_constraint(
        "ck_reporte_auto_momento_envio",
        "configuracion_reporte_automatico",
        "momento_envio IN ('mismo_dia','dia_siguiente')",
    )
    op.create_check_constraint(
        "ck_reporte_auto_hora_envio",
        "configuracion_reporte_automatico",
        "hora_envio >= 0 AND hora_envio <= 23",
    )


def downgrade():
    op.drop_constraint("ck_reporte_auto_hora_envio", "configuracion_reporte_automatico", type_="check")
    op.drop_constraint("ck_reporte_auto_momento_envio", "configuracion_reporte_automatico", type_="check")
    op.drop_column("configuracion_reporte_automatico", "hora_envio")
    op.drop_column("configuracion_reporte_automatico", "momento_envio")
