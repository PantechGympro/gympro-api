"""Schema inicial PostgreSQL — Sprint 2

Tablas: roles, gimnasios, usuarios
Incluye índices y FK con ON DELETE RESTRICT.

Revision ID: 001
Revises:
Create Date: 2026-05-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── roles ─────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id",         sa.Integer(),                  nullable=False),
        sa.Column("nombre",     sa.String(50),                 nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),    server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nombre"),
    )

    # ── gimnasios ──────────────────────────────────────────────────────────────
    op.create_table(
        "gimnasios",
        sa.Column("id",                 sa.Integer(),                nullable=False),
        sa.Column("nombre",             sa.String(150),              nullable=False),
        sa.Column("plan",               sa.Enum("basico", "pro", "enterprise", name="plan_enum"), nullable=False, server_default="basico"),
        sa.Column("activo",             sa.Boolean(),                nullable=False, server_default=sa.text("true")),
        sa.Column("email_contacto",     sa.String(255),              nullable=True),
        sa.Column("telefono",           sa.String(20),               nullable=True),
        sa.Column("stripe_customer_id", sa.String(100),              nullable=True),
        sa.Column("created_at",         sa.DateTime(timezone=True),  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── usuarios ───────────────────────────────────────────────────────────────
    op.create_table(
        "usuarios",
        sa.Column("id",            sa.Integer(),               nullable=False),
        sa.Column("nombre",        sa.String(150),             nullable=False),
        sa.Column("email",         sa.String(255),             nullable=False),
        sa.Column("password_hash", sa.String(255),             nullable=False),
        sa.Column("activo",        sa.Boolean(),               nullable=False, server_default=sa.text("true")),
        sa.Column("id_rol",        sa.Integer(),               nullable=False),
        sa.Column("id_gimnasio",   sa.Integer(),               nullable=True),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_rol"],      ["roles.id"],      ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["id_gimnasio"], ["gimnasios.id"],  ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usuarios_email",       "usuarios", ["email"],       unique=True)
    op.create_index("ix_usuarios_id_gimnasio", "usuarios", ["id_gimnasio"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_usuarios_id_gimnasio", table_name="usuarios")
    op.drop_index("ix_usuarios_email",       table_name="usuarios")
    op.drop_table("usuarios")
    op.drop_table("gimnasios")
    op.drop_table("roles")
    # Eliminar el tipo enum que PostgreSQL registra por separado
    op.execute("DROP TYPE IF EXISTS plan_enum")
