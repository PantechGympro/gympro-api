"""016 — Agrega 'starter' al enum de planes de Gimnasio

El catálogo de planes de suscripción (planes_suscripcion, ver seed_pg.py)
ya incluye "starter" -- el plan gratuito de entrada -- desde SCRUM-163, pero
la columna gimnasios.plan sigue siendo un ENUM de Postgres limitado a
('basico', 'pro', 'enterprise') (ver 001_initial_schema). Cualquier intento
de registrar un gimnasio nuevo con el plan starter fallaba con una violación
de constraint al insertar, porque el valor ni siquiera existía en el tipo.

ALTER TYPE ... ADD VALUE es seguro dentro de una transacción normal desde
Postgres 12 (el proyecto corre Postgres 16), así que no se necesita
autocommit especial aqui.

Revision ID: 016
Revises: 09bcc26b2064
"""
from alembic import op

revision      = "016"
down_revision = "09bcc26b2064"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("ALTER TYPE plan_enum ADD VALUE IF NOT EXISTS 'starter' BEFORE 'basico'")


def downgrade():
    # Postgres no soporta eliminar un valor de un ENUM directamente (requeriria
    # recrear el tipo completo y todas sus dependencias). Dado que 'starter'
    # es solo un valor adicional -- no reemplaza a los existentes -- se deja
    # como no-op: revertir esta migracion no rompe nada mientras ningun
    # gimnasio quede con plan='starter' antes de bajar la version.
    pass
