"""010_suscripcion_auto_renovar — Agrega columna auto_renovar a suscripciones

Permite marcar una suscripcion de gimnasio como de cargo recurrente
(auto-renovacion). En el flujo demo se simula el cobro automatico.

Revision ID: 010
Revises: 009
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision      = "010"
down_revision = "009"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "suscripciones",
        sa.Column(
            "auto_renovar",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade():
    op.drop_column("suscripciones", "auto_renovar")
