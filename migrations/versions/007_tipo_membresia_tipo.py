"""Agregar columna tipo a tipos_membresia

Revision ID: 007_tipo_membresia_tipo
Revises: 006_primer_login
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tipos_membresia",
        sa.Column("tipo", sa.String(20), nullable=False, server_default="estandar"),
    )


def downgrade():
    op.drop_column("tipos_membresia", "tipo")
