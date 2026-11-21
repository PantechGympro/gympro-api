"""037 -- Baja de cuenta solicitada por el propietario

Hasta ahora eliminar un gimnasio solo era posible con el script de
mantenimiento (scripts/maintenance/borrar_gimnasios.py), es decir, pidiendoselo
al equipo. El propietario debe poder hacerlo por su cuenta, y la eliminacion
debe ser reversible durante un plazo para que un arrepentimiento no cueste
todos sus datos.

    baja_solicitada_en    cuando la confirmo el propietario.
    baja_programada_para  fecha en que se ejecuta la purga definitiva
                          (solicitud + DIAS_GRACIA_BAJA, ver constants/legal.py).
    baja_motivo           lo que el propietario quiso decir al irse (opcional,
                          sirve para saber por que se van).
    baja_solicitada_por   id del usuario que la pidio. Entero SIN clave ajena a
                          proposito: usuarios.id_gimnasio ya apunta a gimnasios,
                          y una FK de vuelta cerraria un ciclo entre las dos
                          tablas (SQLAlchemy no podria ordenarlas y la relacion
                          Gimnasio.usuarios quedaria ambigua). La trazabilidad
                          real de quien pidio la baja vive en bitacora_eventos.

Las tres primeras en NULL significan "sin baja pendiente", que es el estado de
todas las filas existentes.

Revision ID: 037
Revises: 036
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gimnasios", sa.Column("baja_solicitada_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gimnasios", sa.Column("baja_programada_para", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gimnasios", sa.Column("baja_motivo", sa.Text(), nullable=True))
    op.add_column("gimnasios", sa.Column("baja_solicitada_por", sa.Integer(), nullable=True))
    # Las bajas se buscan por fecha en el proceso programado: indice parcial,
    # porque lo normal es que casi ninguna fila tenga baja pendiente.
    op.create_index(
        "ix_gimnasios_baja_programada", "gimnasios", ["baja_programada_para"],
        postgresql_where=sa.text("baja_programada_para IS NOT NULL"),
    )


def downgrade():
    op.drop_index("ix_gimnasios_baja_programada", table_name="gimnasios")
    op.drop_column("gimnasios", "baja_solicitada_por")
    op.drop_column("gimnasios", "baja_motivo")
    op.drop_column("gimnasios", "baja_programada_para")
    op.drop_column("gimnasios", "baja_solicitada_en")
