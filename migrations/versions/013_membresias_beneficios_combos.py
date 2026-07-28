"""013_membresias_beneficios_combos — Beneficios, combos y vigencia de promociones

Amplia tipos_membresia para que el dueno del gimnasio pueda:

    beneficios       listar que incluye el plan (JSON de textos)
    es_combo         marcar el plan como combo (varios conceptos, un precio)
    items_combo      detallar los conceptos del combo (JSON)
    fecha_fin_promo  fijar hasta cuando se ofrece una promocion; al pasar esa
                     fecha el sistema la desactiva automaticamente

Revision ID: 013
Revises: 012
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa

revision      = "013"
down_revision = "012"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column("tipos_membresia", sa.Column("beneficios", sa.JSON(), nullable=True))
    op.add_column("tipos_membresia", sa.Column("es_combo", sa.Boolean(), nullable=False,
                                               server_default=sa.text("false")))
    op.add_column("tipos_membresia", sa.Column("items_combo", sa.JSON(), nullable=True))
    op.add_column("tipos_membresia", sa.Column("fecha_fin_promo", sa.Date(), nullable=True))

    # Indice para localizar rapido las promociones por vencer
    op.create_index("ix_tipos_membresia_fecha_fin_promo",
                    "tipos_membresia", ["fecha_fin_promo"])


def downgrade():
    op.drop_index("ix_tipos_membresia_fecha_fin_promo", table_name="tipos_membresia")
    op.drop_column("tipos_membresia", "fecha_fin_promo")
    op.drop_column("tipos_membresia", "items_combo")
    op.drop_column("tipos_membresia", "es_combo")
    op.drop_column("tipos_membresia", "beneficios")
