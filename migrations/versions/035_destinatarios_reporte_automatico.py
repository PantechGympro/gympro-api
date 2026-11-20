"""035 -- Destinatarios configurables en los reportes automaticos

Contexto: hasta la 034 el correo del reporte automatico iba SIEMPRE al
Propietario de la organizacion (regla fija, sin columna). El usuario pidio
poder elegir: Propietario, Administrador, ambos, u otros correos.

Tres columnas, sin tabla aparte (a lo mucho 5 correos extra por receta):

    enviar_propietario    bool, por omision TRUE  -- el Propietario de la
                          organizacion (usuario de la sede principal).
    enviar_administrador  bool, por omision FALSE -- los Administradores
                          activos asignados a la sucursal del reporte (o a
                          toda su organizacion).
    correos_extra         CSV en minusculas, NULL si no hay -- cualquier
                          otro correo (contador, socio, etc.).

Los server_default dejan las filas ya existentes exactamente como
funcionaban: solo al Propietario. Nadie ve cambiar a donde le llega un
reporte sin haberlo tocado.

El CHECK impide dejar una receta sin ningun destinatario (se manda a
nadie, en silencio, cada dia). La validacion amigable vive en la API
(reportes_automaticos.py::_validar_payload); esto es la red de seguridad.

Revision ID: 035
Revises: 034
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision      = "035"
down_revision = "034"
branch_labels = None
depends_on    = None

TABLA = "configuracion_reporte_automatico"


def upgrade():
    op.add_column(TABLA, sa.Column("enviar_propietario", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
    op.add_column(TABLA, sa.Column("enviar_administrador", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    op.add_column(TABLA, sa.Column("correos_extra", sa.String(length=500), nullable=True))
    op.create_check_constraint(
        "ck_reporte_auto_destinatario",
        TABLA,
        "enviar_propietario OR enviar_administrador "
        "OR (correos_extra IS NOT NULL AND correos_extra <> '')",
    )


def downgrade():
    op.drop_constraint("ck_reporte_auto_destinatario", TABLA, type_="check")
    op.drop_column(TABLA, "correos_extra")
    op.drop_column(TABLA, "enviar_administrador")
    op.drop_column(TABLA, "enviar_propietario")
