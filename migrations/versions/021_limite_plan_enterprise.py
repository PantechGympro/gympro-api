"""021 — Corrige el tope "ilimitado" del plan enterprise (SCRUM-222, AC6)

La tarjeta de precios del plan enterprise anunciaba "Miembros y staff
ilimitados" (max_miembros=NULL, limites.max_staff=NULL en
planes_suscripcion). SCRUM-222 exige que los topes que anuncia cada plan
se apliquen de verdad -- y "ilimitado" no es un tope que un VPS
compartido (Postgres + Mongo + Redis + Ollama en la misma máquina) pueda
respaldar de forma creíble.

Esta migración solo corrige el DATO de una fila que ya pudo haber sido
sembrada (seed_pg.py) en un ambiente que corrió antes de este ticket --
seed_pg.py ya trae los valores correctos para un ambiente nuevo, pero no
se re-ejecuta sobre uno que ya tiene su catálogo de planes poblado.

500 miembros / 20 staff por sucursal son valores PROVISIONALES -- una
extrapolación directa del salto básico(50)->pro(200) de la misma tabla,
no el resultado de una prueba de carga real. Hay que confirmarlos con el
equipo antes de anunciárselos a un cliente real (ver nota en seed_pg.py).

No toca filas que ya tengan un valor explícito distinto de NULL -- si
alguien ya personalizó el plan enterprise desde el panel de superadmin
(routes/superadmin/planes.py), esa personalización se respeta.

Revision ID: 021
Revises: 020
Create Date: 2026-10-14
"""
from alembic import op

revision      = "021"
down_revision = "020"
branch_labels = None
depends_on    = None


def upgrade():
    # La columna `limites` es JSON (no JSONB, ver migración 012) -- se
    # castea a jsonb solo para poder usar jsonb_set(), y se castea de
    # vuelta a json al guardar, sin cambiar el tipo de la columna.
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 500,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '20'::jsonb))::json
        WHERE nombre = 'enterprise' AND max_miembros IS NULL
    """)


def downgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = NULL,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', 'null'::jsonb))::json
        WHERE nombre = 'enterprise' AND max_miembros = 500
    """)
