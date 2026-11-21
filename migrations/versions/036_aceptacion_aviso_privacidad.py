"""036 -- Registro de la aceptacion del aviso de privacidad

La LFPDPPP exige poder acreditar QUE texto acepto cada persona y CUANDO; la
tabla 3.2.3 del marco normativo del proyecto lo daba por hecho ("se registra su
aceptacion, con fecha y version del texto aceptado") pero no existia la columna:
el portal solo enlazaba el aviso.

    aviso_version      version del aviso aceptada (ver constants/legal.py).
                       NULL = nunca lo acepto -> el portal muestra la pasarela.
    aviso_aceptado_en  momento exacto de la aceptacion.

Se dejan NULL en las filas existentes a proposito: nadie "acepto" algo que no
se le pidio, asi que a todos se les pedira una vez. No se inventa una fecha.

Revision ID: 036
Revises: 035
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usuarios", sa.Column("aviso_version", sa.String(length=10), nullable=True))
    op.add_column("usuarios", sa.Column("aviso_aceptado_en", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("usuarios", "aviso_aceptado_en")
    op.drop_column("usuarios", "aviso_version")
