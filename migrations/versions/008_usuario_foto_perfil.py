"""008_usuario_foto_perfil — Agrega columna foto_perfil (Text) a usuarios

Revision ID: 008
Revises: 007_tipo_membresia_tipo
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa

revision   = "008"
down_revision = "007"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "usuarios",
        sa.Column("foto_perfil", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("usuarios", "foto_perfil")
