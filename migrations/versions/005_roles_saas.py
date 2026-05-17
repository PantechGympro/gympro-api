"""Roles SaaS: rename Administrador → owner_gym, add superadmin

Revision ID: 005
Revises: 004
Create Date: 2026-05-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Renombrar "Administrador" → "owner_gym" (gym-level tenant admin)
    op.execute("UPDATE roles SET nombre = 'owner_gym' WHERE nombre = 'Administrador'")

    # Insertar rol de plataforma solo si no existe
    op.execute("""
        INSERT INTO roles (nombre)
        SELECT 'superadmin'
        WHERE NOT EXISTS (SELECT 1 FROM roles WHERE nombre = 'superadmin')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE nombre = 'superadmin'")
    op.execute("UPDATE roles SET nombre = 'Administrador' WHERE nombre = 'owner_gym'")
