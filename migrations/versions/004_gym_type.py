"""Agregar tipo_gimnasio y configuracion a gimnasios

Revision ID: 004
Revises: 003
Create Date: 2026-05-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "gimnasios",
        sa.Column("tipo_gimnasio", sa.String(50), nullable=True),
    )
    op.add_column(
        "gimnasios",
        sa.Column("configuracion", JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("gimnasios", "configuracion")
    op.drop_column("gimnasios", "tipo_gimnasio")
