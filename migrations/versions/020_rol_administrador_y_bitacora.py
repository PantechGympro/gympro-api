"""020 — Rol "Administrador" de sucursal + tabla bitacora_eventos (HU-45/SCRUM-216)

Dos cosas nuevas, sin relación de esquema entre sí pero del mismo ticket:

1. Rol "Administrador": no es un alias de "Propietario" (ver constants/roles.py
   -- hasta ahora "administrador"/"admin" eran sinónimos informales de
   Propietario, porque no existía nada más). Es un rol NUEVO y DISTINTO,
   con alcance limitado a las sucursales que el propietario le asigne via
   asignacion_usuario -- nunca a toda la organización. Un Administrador
   puede tener más de una sucursal asignada (criterio 4 de HU-45); su
   usuarios.id_gimnasio guarda cuál es la "sucursal activa" del momento
   (ver utils/sucursal_activa.py), conmutable entre las que tenga
   asignadas -- así el resto de los 267 endpoints que ya filtran por
   g.tenant_id (claim del JWT) siguen funcionando sin que nadie los toque.

2. bitacora_eventos: registro genérico de auditoría (quién hizo qué,
   cuándo, sobre qué recurso, desde qué IP). HU-45 lo exige para altas de
   sucursal/administrador y cambios de asignación (criterio 7); HU-50 lo
   reutilizará para cambios de cuenta de cobro -- una sola tabla en vez de
   una por historia, ya que el patrón ("quién, qué, cuándo, desde dónde")
   es idéntico.

Revision ID: 020
Revises: 019
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision      = "020"
down_revision = "019"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute(
        "INSERT INTO roles (nombre, created_at) VALUES ('Administrador', now())"
    )

    op.create_table(
        "bitacora_eventos",
        sa.Column("id",              sa.Integer(),               nullable=False),
        sa.Column("id_usuario_actor", sa.Integer(),               nullable=True),
        sa.Column("accion",          sa.String(80),               nullable=False),
        sa.Column("id_organizacion", sa.Integer(),               nullable=True),
        sa.Column("id_gimnasio",     sa.Integer(),               nullable=True),
        sa.Column("detalle",         sa.JSON(),                  nullable=True),
        sa.Column("ip_origen",       sa.String(45),              nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_usuario_actor"], ["usuarios.id"],       ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["id_organizacion"],  ["organizaciones.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["id_gimnasio"],      ["gimnasios.id"],      ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bitacora_eventos_id_organizacion", "bitacora_eventos", ["id_organizacion"])
    op.create_index("ix_bitacora_eventos_accion",          "bitacora_eventos", ["accion"])
    op.create_index("ix_bitacora_eventos_created_at",      "bitacora_eventos", ["created_at"])


def downgrade():
    op.drop_index("ix_bitacora_eventos_created_at",      table_name="bitacora_eventos")
    op.drop_index("ix_bitacora_eventos_accion",           table_name="bitacora_eventos")
    op.drop_index("ix_bitacora_eventos_id_organizacion",  table_name="bitacora_eventos")
    op.drop_table("bitacora_eventos")

    op.execute("DELETE FROM roles WHERE nombre = 'Administrador'")
