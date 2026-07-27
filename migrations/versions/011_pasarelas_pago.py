"""011_pasarelas_pago — Pasarelas de pago por gimnasio y transacciones

Agrega el soporte de cobros en linea con PayPal y Mercado Pago:

  configuracion_pasarela  credenciales (cifradas) de cada gimnasio por proveedor.
                          El dinero de membresias y productos cae directo en la
                          cuenta del gimnasio; la plataforma no lo custodia.

  transacciones_pago      un registro por intento de cobro, en cualquiera de los
                          tres contextos: membresia, producto o suscripcion SaaS.

Revision ID: 011
Revises: 010
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "011"
down_revision = "010"
branch_labels = None
depends_on    = None


def upgrade():
    bind = op.get_bind()

    proveedor_enum = postgresql.ENUM(
        "paypal", "mercadopago", name="proveedor_pago_enum")
    modo_enum = postgresql.ENUM(
        "sandbox", "live", name="modo_pasarela_enum")
    contexto_enum = postgresql.ENUM(
        "membresia", "producto", "suscripcion", name="contexto_pago_enum")
    estado_tx_enum = postgresql.ENUM(
        "pendiente", "aprobado", "rechazado", "cancelado", "reembolsado",
        name="estado_transaccion_enum")

    for tipo in (proveedor_enum, modo_enum, contexto_enum, estado_tx_enum):
        tipo.create(bind, checkfirst=True)

    op.create_table(
        "configuracion_pasarela",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_gimnasio", sa.Integer(),
                  sa.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proveedor", proveedor_enum, nullable=False),
        sa.Column("modo", modo_enum, nullable=False, server_default="sandbox"),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("credenciales", sa.Text(), nullable=True),
        sa.Column("moneda", sa.String(length=3), nullable=False, server_default="MXN"),
        sa.Column("titular_cuenta", sa.String(length=150), nullable=True),
        sa.Column("verificado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_error", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("id_gimnasio", "proveedor", name="uq_pasarela_gym_proveedor"),
    )
    op.create_index("ix_configuracion_pasarela_id_gimnasio",
                    "configuracion_pasarela", ["id_gimnasio"])

    op.create_table(
        "transacciones_pago",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_gimnasio", sa.Integer(),
                  sa.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proveedor", proveedor_enum, nullable=False),
        sa.Column("contexto", contexto_enum, nullable=False),
        sa.Column("estado", estado_tx_enum, nullable=False, server_default="pendiente"),
        sa.Column("referencia_externa", sa.String(length=120), nullable=True),
        sa.Column("referencia_pago", sa.String(length=120), nullable=True),
        sa.Column("monto", sa.Numeric(10, 2), nullable=False),
        sa.Column("moneda", sa.String(length=3), nullable=False, server_default="MXN"),
        sa.Column("descripcion", sa.String(length=255), nullable=True),
        sa.Column("referencia_local", sa.String(length=80), nullable=True),
        sa.Column("id_usuario", sa.Integer(), nullable=True),
        sa.Column("metadatos", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("fecha_pago", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_transacciones_pago_id_gimnasio", "transacciones_pago", ["id_gimnasio"])
    op.create_index("ix_transacciones_pago_estado", "transacciones_pago", ["estado"])
    op.create_index("ix_transacciones_pago_referencia_externa",
                    "transacciones_pago", ["referencia_externa"])
    op.create_index("ix_transacciones_pago_referencia_local",
                    "transacciones_pago", ["referencia_local"])
    op.create_index("ix_transacciones_pago_created_at", "transacciones_pago", ["created_at"])


def downgrade():
    bind = op.get_bind()

    op.drop_index("ix_transacciones_pago_created_at", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_referencia_local", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_referencia_externa", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_estado", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_id_gimnasio", table_name="transacciones_pago")
    op.drop_table("transacciones_pago")

    op.drop_index("ix_configuracion_pasarela_id_gimnasio", table_name="configuracion_pasarela")
    op.drop_table("configuracion_pasarela")

    for nombre in ("estado_transaccion_enum", "contexto_pago_enum",
                   "modo_pasarela_enum", "proveedor_pago_enum"):
        postgresql.ENUM(name=nombre).drop(bind, checkfirst=True)
