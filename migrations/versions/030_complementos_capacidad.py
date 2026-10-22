"""030 -- Lotes de capacidad extra de miembros (comprar mas espacio)

Peticion explicita del usuario: que el Propietario pueda comprar espacio
adicional de miembros en lotes de tamano fijo (10/50/100/200/500) desde
"Mi suscripcion" (ver web/src/pages/owner_gym/OwnerSubscription.jsx y
models/pg/complemento_capacidad.py para la decision completa de arquitectura).

Dos cambios:

  1. contexto_pago_enum +'lote_capacidad' -- el checkout de un solo pago
     (routes/pagos_online.py) necesita un cuarto contexto para que un
     lote se cobre con las credenciales de la PLATAFORMA (igual que
     'suscripcion'), no las del gimnasio. ALTER TYPE ... ADD VALUE es
     seguro dentro de una transaccion normal desde Postgres 12 (el
     proyecto corre Postgres 16) -- mismo razonamiento que la migracion
     016.

  2. complementos_capacidad (tabla nueva) -- un lote comprado por una
     sucursal, con su propia vigencia (fecha_fin) y snapshot del precio
     pagado. Ver ese modelo para por que la capacidad extra es por
     sucursal (id_gimnasio), no por organizacion.

Revision ID: 030
Revises: 029
"""
from alembic import op
import sqlalchemy as sa

revision      = "030"
down_revision = "029"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("ALTER TYPE contexto_pago_enum ADD VALUE IF NOT EXISTS 'lote_capacidad'")

    op.create_table(
        "complementos_capacidad",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_gimnasio", sa.Integer(),
                  sa.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cantidad_miembros", sa.Integer(), nullable=False),
        sa.Column("ciclo", sa.String(length=10), nullable=False, server_default="mensual"),
        sa.Column("precio_mxn", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(length=12), nullable=False, server_default="activo"),
        sa.Column("fecha_inicio", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("fecha_fin", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id_transaccion", sa.Integer(),
                  sa.ForeignKey("transacciones_pago.id", ondelete="SET NULL"), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_complementos_capacidad_id_gimnasio", "complementos_capacidad", ["id_gimnasio"]
    )
    op.create_check_constraint(
        "ck_complementos_capacidad_cantidad", "complementos_capacidad",
        "cantidad_miembros IN (10, 50, 100, 200, 500)",
    )
    op.create_check_constraint(
        "ck_complementos_capacidad_ciclo", "complementos_capacidad",
        "ciclo IN ('mensual', 'anual')",
    )
    op.create_check_constraint(
        "ck_complementos_capacidad_estado", "complementos_capacidad",
        "estado IN ('activo', 'cancelado')",
    )


def downgrade():
    op.drop_constraint("ck_complementos_capacidad_estado", "complementos_capacidad", type_="check")
    op.drop_constraint("ck_complementos_capacidad_ciclo", "complementos_capacidad", type_="check")
    op.drop_constraint("ck_complementos_capacidad_cantidad", "complementos_capacidad", type_="check")
    op.drop_index("ix_complementos_capacidad_id_gimnasio", table_name="complementos_capacidad")
    op.drop_table("complementos_capacidad")
    # Postgres no permite quitar un valor de un ENUM sin recrear el tipo
    # completo y todas sus dependencias -- igual que en la migracion 016,
    # se deja como no-op mientras ninguna transaccion quede con
    # contexto='lote_capacidad' antes de bajar la version.
