"""009_ejercicio_id_entrenador — Agrega columna id_entrenador a ejercicios

Permite filtrar la biblioteca de ejercicios por entrenador creador,
de modo que cada entrenador sólo vea sus propios ejercicios.
nullable=True para preservar ejercicios existentes sin propietario.

Revision ID: 009
Revises: 008
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa

revision      = "009"
down_revision = "008"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "ejercicios",
        sa.Column(
            "id_entrenador",
            sa.Integer(),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_ejercicios_id_entrenador",
        "ejercicios",
        ["id_entrenador"],
    )
    # Reemplazar constraint por-gimnasio → por-gimnasio+entrenador
    # para permitir que dos entrenadores tengan ejercicios con el mismo nombre
    op.drop_constraint("uq_ejercicio_gym_nombre", "ejercicios", type_="unique")
    op.create_unique_constraint(
        "uq_ejercicio_gym_trainer_nombre",
        "ejercicios",
        ["id_gimnasio", "id_entrenador", "nombre"],
    )


def downgrade():
    op.drop_constraint("uq_ejercicio_gym_trainer_nombre", "ejercicios", type_="unique")
    op.create_unique_constraint(
        "uq_ejercicio_gym_nombre", "ejercicios", ["id_gimnasio", "nombre"]
    )
    op.drop_index("ix_ejercicios_id_entrenador", table_name="ejercicios")
    op.drop_column("ejercicios", "id_entrenador")
