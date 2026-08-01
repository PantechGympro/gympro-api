"""014 — Teléfono del usuario y logotipo del gimnasio

Dos campos que faltaban para completar los perfiles:

  usuarios.telefono   El propietario y el staff necesitan un teléfono propio,
                      distinto del de contacto del gimnasio: uno es la persona
                      y otro el negocio.
  gimnasios.logo      Imagen del gimnasio, guardada como data URL base64 igual
                      que las fotos de perfil de los usuarios.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision      = "014"
down_revision = "013"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column("usuarios",  sa.Column("telefono", sa.String(length=30), nullable=True))
    # Text y no String: una imagen en base64 supera cualquier longitud fija.
    op.add_column("gimnasios", sa.Column("logo", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("gimnasios", "logo")
    op.drop_column("usuarios",  "telefono")
