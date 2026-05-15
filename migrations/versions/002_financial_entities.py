"""Entidades financieras -- Sprint 3 / US12

Tablas: planes_suscripcion, suscripciones, facturas_suscripcion
Tipos ENUM: estado_suscripcion_enum, estado_factura_enum

Los tipos ENUM se crean via .create(conn, checkfirst=True) antes de las tablas,
lo que evita el doble-CREATE que dispara el evento before_create de SQLAlchemy
cuando se usa sa.Enum(...) inline en op.create_table.

Revision ID: 002
Revises: 001
Create Date: 2026-05-14
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGENUM

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Crear ENUM types antes que las tablas.
    # create_type=False en el objeto + .create(checkfirst=True) es el patron
    # oficial de Alembic para tipos PostgreSQL reutilizados en modelos
    # (evita que op.create_table vuelva a emitir CREATE TYPE via before_create).
    estado_sub_t = PGENUM(
        "trialing", "active", "past_due", "unpaid", "cancelled", "paused",
        name="estado_suscripcion_enum", create_type=False,
    )
    estado_fac_t = PGENUM(
        "pendiente", "pagada", "vencida", "fallida",
        name="estado_factura_enum", create_type=False,
    )
    estado_sub_t.create(conn, checkfirst=True)
    estado_fac_t.create(conn, checkfirst=True)

    # planes_suscripcion (sin enum -- sin riesgo de doble CREATE)
    op.create_table(
        "planes_suscripcion",
        sa.Column("id",                  sa.Integer(),               nullable=False),
        sa.Column("nombre",              sa.String(50),              nullable=False),
        sa.Column("precio_mensual_mxn",  sa.Integer(),               nullable=False),
        sa.Column("max_miembros",        sa.Integer(),               nullable=True),
        sa.Column("descripcion",         sa.Text(),                  nullable=True),
        sa.Column("activo",              sa.Boolean(),               nullable=False, server_default=sa.text("true")),
        sa.Column("stripe_price_id",     sa.String(100),             nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nombre"),
    )

    # suscripciones -- usa el objeto PGENUM preexistente (create_type=False)
    op.create_table(
        "suscripciones",
        sa.Column("id",                     sa.Integer(),               nullable=False),
        sa.Column("id_gimnasio",            sa.Integer(),               nullable=False),
        sa.Column("id_plan",                sa.Integer(),               nullable=False),
        sa.Column("estado",                 estado_sub_t,               nullable=False, server_default="trialing"),
        sa.Column("fecha_inicio",           sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("fecha_fin",              sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_proximo_cobro",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(100),             nullable=True),
        sa.Column("created_at",             sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at",             sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_gimnasio"], ["gimnasios.id"],           ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_plan"],     ["planes_suscripcion.id"],  ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_subscription_id"),
    )
    op.create_index("ix_suscripciones_id_gimnasio", "suscripciones", ["id_gimnasio"])
    op.create_index("ix_suscripciones_estado",      "suscripciones", ["estado"])

    # facturas_suscripcion
    op.create_table(
        "facturas_suscripcion",
        sa.Column("id",                sa.Integer(),               nullable=False),
        sa.Column("id_suscripcion",    sa.Integer(),               nullable=False),
        sa.Column("monto",             sa.Integer(),               nullable=False),
        sa.Column("moneda",            sa.String(3),               nullable=False, server_default="MXN"),
        sa.Column("estado",            estado_fac_t,               nullable=False, server_default="pendiente"),
        sa.Column("fecha_emision",     sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("fecha_vencimiento", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_pago",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(100),             nullable=True),
        sa.Column("created_at",        sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_suscripcion"], ["suscripciones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_invoice_id"),
    )
    op.create_index("ix_facturas_id_suscripcion", "facturas_suscripcion", ["id_suscripcion"])
    op.create_index("ix_facturas_estado",         "facturas_suscripcion", ["estado"])

    # Seed inicial de planes
    op.execute("""
        INSERT INTO planes_suscripcion (nombre, precio_mensual_mxn, max_miembros, descripcion, activo)
        VALUES
            ('basico',      49900,  50,   'Hasta 50 miembros activos. Funciones esenciales de gestion.', true),
            ('pro',        149900, 200,   'Hasta 200 miembros + Analytics avanzados (Spark).',           true),
            ('enterprise', 399900, NULL,  'Miembros ilimitados + SLA + soporte dedicado.',               true)
        ON CONFLICT (nombre) DO NOTHING;
    """)


def downgrade() -> None:
    conn = op.get_bind()

    op.drop_index("ix_facturas_estado",         table_name="facturas_suscripcion")
    op.drop_index("ix_facturas_id_suscripcion", table_name="facturas_suscripcion")
    op.drop_table("facturas_suscripcion")

    op.drop_index("ix_suscripciones_estado",      table_name="suscripciones")
    op.drop_index("ix_suscripciones_id_gimnasio", table_name="suscripciones")
    op.drop_table("suscripciones")

    op.drop_table("planes_suscripcion")

    PGENUM(name="estado_factura_enum",      create_type=False).drop(conn, checkfirst=True)
    PGENUM(name="estado_suscripcion_enum",  create_type=False).drop(conn, checkfirst=True)
