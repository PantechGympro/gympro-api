"""033 -- Sincroniza Organizacion.plan con la suscripcion vigente

Bug real: en el gimnasio de demostracion (plan Enterprise activo en
facturacion) algunas funciones aparecian bloqueadas. Causa: el gating
(utils/capacidad.py) leia `organizaciones.plan`, que es una copia del plan
contratado; `seed_pg.py` y el webhook de Stripe creaban la Suscripcion con
el plan correcto pero dejaban la organizacion en el default 'basico'.

Esta migracion corrige los datos ya existentes: para cada organizacion con
una suscripcion vigente (trialing/active) en su sede principal, copia el
plan de esa suscripcion a `organizaciones.plan`. El codigo ya no depende de
la copia (lee la suscripcion primero) y los caminos de alta la mantienen
sincronizada, asi que esto es una limpieza unica.

Solo toca planes del enum estandar (starter/basico/pro/enterprise); un plan
personalizado creado desde el panel de superadmin no cabe en el enum y se
deja como esta. Es idempotente.

Revision ID: 033
Revises: 032
"""
from alembic import op

revision      = "033"
down_revision = "032"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        UPDATE organizaciones o
        SET plan = x.nombre::plan_enum
        FROM (
            SELECT DISTINCT ON (g.id_organizacion)
                   g.id_organizacion AS org_id, p.nombre AS nombre
            FROM gimnasios g
            JOIN suscripciones s
              ON s.id_gimnasio = g.id AND s.estado IN ('trialing', 'active')
            JOIN planes_suscripcion p ON p.id = s.id_plan
            WHERE g.es_sucursal_principal = true
              AND p.nombre IN ('starter', 'basico', 'pro', 'enterprise')
            ORDER BY g.id_organizacion, s.created_at DESC
        ) x
        WHERE o.id = x.org_id AND o.plan::text <> x.nombre
    """)


def downgrade():
    # Correccion de datos: no hay estado anterior que restaurar.
    pass
