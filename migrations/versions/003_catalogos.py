"""Catalogos a PostgreSQL -- Sprint 3 / US14

Tablas: tipos_membresia, ejercicios, tipos_clase
Todas son tenant-scoped via FK a gimnasios(id).
No se usan ENUM types (columnas tipo VARCHAR restringidas en capa de aplicacion).

Revision ID: 003
Revises: 002
Create Date: 2026-05-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # tipos_membresia: reemplaza coleccion Mongo 'membresias'
    op.create_table(
        "tipos_membresia",
        sa.Column("id",             sa.Integer(),               nullable=False),
        sa.Column("id_gimnasio",    sa.Integer(),               nullable=False),
        sa.Column("nombre",         sa.String(100),             nullable=False),
        sa.Column("duracion_meses", sa.Integer(),               nullable=False, server_default=sa.text("1")),
        sa.Column("precio",         sa.Numeric(10, 2),          nullable=False),
        sa.Column("descripcion",    sa.Text(),                  nullable=True),
        sa.Column("activo",         sa.Boolean(),               nullable=False, server_default=sa.text("true")),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_gimnasio"], ["gimnasios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_gimnasio", "nombre", name="uq_tipo_membresia_gym_nombre"),
    )
    op.create_index("ix_tipos_membresia_id_gimnasio", "tipos_membresia", ["id_gimnasio"])

    # ejercicios: reemplaza coleccion Mongo 'ejercicios'
    op.create_table(
        "ejercicios",
        sa.Column("id",             sa.Integer(),               nullable=False),
        sa.Column("id_gimnasio",    sa.Integer(),               nullable=False),
        sa.Column("nombre",         sa.String(150),             nullable=False),
        sa.Column("descripcion",    sa.Text(),                  nullable=True),
        sa.Column("grupo_muscular", sa.String(80),              nullable=True),
        sa.Column("tipo",           sa.String(50),              nullable=True),
        sa.Column("series",         sa.Integer(),               nullable=True),
        sa.Column("repeticiones",   sa.String(20),              nullable=True),
        sa.Column("duracion_min",   sa.Integer(),               nullable=True),
        sa.Column("activo",         sa.Boolean(),               nullable=False, server_default=sa.text("true")),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_gimnasio"], ["gimnasios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_gimnasio", "nombre", name="uq_ejercicio_gym_nombre"),
    )
    op.create_index("ix_ejercicios_id_gimnasio", "ejercicios", ["id_gimnasio"])

    # tipos_clase: reemplaza coleccion Mongo 'tipo_clases' / 'actividades'
    op.create_table(
        "tipos_clase",
        sa.Column("id",               sa.Integer(),               nullable=False),
        sa.Column("id_gimnasio",      sa.Integer(),               nullable=False),
        sa.Column("nombre",           sa.String(100),             nullable=False),
        sa.Column("descripcion",      sa.Text(),                  nullable=True),
        sa.Column("duracion_minutos", sa.Integer(),               nullable=True, server_default=sa.text("60")),
        sa.Column("capacidad_max",    sa.Integer(),               nullable=True),
        sa.Column("activo",           sa.Boolean(),               nullable=False, server_default=sa.text("true")),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_gimnasio"], ["gimnasios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_gimnasio", "nombre", name="uq_tipo_clase_gym_nombre"),
    )
    op.create_index("ix_tipos_clase_id_gimnasio", "tipos_clase", ["id_gimnasio"])


def downgrade() -> None:
    op.drop_index("ix_tipos_clase_id_gimnasio",     table_name="tipos_clase")
    op.drop_index("ix_ejercicios_id_gimnasio",       table_name="ejercicios")
    op.drop_index("ix_tipos_membresia_id_gimnasio",  table_name="tipos_membresia")
    op.drop_table("tipos_clase")
    op.drop_table("ejercicios")
    op.drop_table("tipos_membresia")
