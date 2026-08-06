"""015 — Acuerdo de cobro recurrente en la suscripción

Hasta ahora `auto_renovar` era una casilla que se guardaba y nadie leía: el
gimnasio la activaba, llegaba la fecha de cobro y la suscripción vencía igual.
Estas tres columnas guardan el acuerdo real con la pasarela, que es quien cobra.

  pasarela_recurrente     'paypal' o 'mercadopago'
  referencia_recurrente   id del acuerdo (subscription en PayPal,
                          preapproval en Mercado Pago)
  estado_recurrente       pendiente | activo | pausado | cancelado | vencido

Se separan de `auto_renovar` a propósito. Esa columna expresa lo que el dueño
pidió; estas, lo que la pasarela realmente está haciendo. Un dueño puede haber
activado el cargo recurrente y tener el acuerdo sin autorizar, o autorizado y
luego suspendido por pagos fallidos. Con una sola columna el panel afirmaría
que se cobra solo cuando no es cierto.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa

revision      = "015"
down_revision = "014"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column("suscripciones",
                  sa.Column("pasarela_recurrente", sa.String(length=30), nullable=True))
    op.add_column("suscripciones",
                  sa.Column("referencia_recurrente", sa.String(length=120), nullable=True))
    op.add_column("suscripciones",
                  sa.Column("estado_recurrente", sa.String(length=20), nullable=True))

    # El webhook de la pasarela llega con la referencia del acuerdo y sin saber
    # a qué gimnasio pertenece: se busca por esta columna en cada notificación,
    # así que conviene indexarla.
    op.create_index("ix_suscripciones_referencia_recurrente",
                    "suscripciones", ["referencia_recurrente"])

    # Identificador del cargo en la pasarela. Único a propósito: PayPal y
    # Mercado Pago reenvían la misma notificación si no reciben respuesta, y sin
    # esta restricción un reintento crearía una factura duplicada, de modo que
    # el historial mostraría el doble de lo realmente cobrado.
    op.add_column("facturas_suscripcion",
                  sa.Column("referencia_externa", sa.String(length=120), nullable=True))
    op.create_unique_constraint("uq_facturas_referencia_externa",
                                "facturas_suscripcion", ["referencia_externa"])
    op.create_index("ix_facturas_referencia_externa",
                    "facturas_suscripcion", ["referencia_externa"])


def downgrade():
    op.drop_index("ix_facturas_referencia_externa", table_name="facturas_suscripcion")
    op.drop_constraint("uq_facturas_referencia_externa",
                       "facturas_suscripcion", type_="unique")
    op.drop_column("facturas_suscripcion", "referencia_externa")

    op.drop_index("ix_suscripciones_referencia_recurrente", table_name="suscripciones")
    op.drop_column("suscripciones", "estado_recurrente")
    op.drop_column("suscripciones", "referencia_recurrente")
    op.drop_column("suscripciones", "pasarela_recurrente")
