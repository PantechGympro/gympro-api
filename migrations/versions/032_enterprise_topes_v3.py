"""032 -- Nuevos topes del plan Enterprise: 600 miembros / 30 staff / 8 sucursales

Antes (migración 029 y seed_pg.py): 400 miembros, 20 staff por sucursal y
hasta 5 sucursales. Ahora: 600 / 30 / 8. El precio no cambia.

Mismo criterio que 021/023/029: el UPDATE está GUARDADO por el valor
anterior (max_miembros = 400), así que si un superadmin ya personalizó el
plan desde el panel, su cambio se respeta y esta migración no lo pisa.
`jsonb_set` solo modifica max_staff y max_sucursales dentro de `limites`;
las demás banderas del plan (analiticas_ia, pos...) quedan intactas.

Revision ID: 032
Revises: 031
"""
from alembic import op

revision      = "032"
down_revision = "031"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 600,
            limites = (
                jsonb_set(
                    jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '30'::jsonb),
                    '{max_sucursales}', '8'::jsonb)
            )::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 600 miembros y 30 staff por sucursal, hasta 8 sucursales',
                'Todo lo del plan Pro',
                'Laboratorio de Machine Learning completo',
                'Soporte prioritario con SLA',
                'Acompanamiento en la implementacion',
                'Amplia tu limite comprando lotes de miembros adicionales'
            ]::text[]))::json
        WHERE nombre = 'enterprise' AND max_miembros = 400
    """)


def downgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 400,
            limites = (
                jsonb_set(
                    jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '20'::jsonb),
                    '{max_sucursales}', '5'::jsonb)
            )::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 400 miembros y 20 staff por sucursal, hasta 5 sucursales',
                'Todo lo del plan Pro',
                'Laboratorio de Machine Learning completo',
                'Soporte prioritario con SLA',
                'Acompanamiento en la implementacion',
                'Amplia tu limite comprando lotes de miembros adicionales'
            ]::text[]))::json
        WHERE nombre = 'enterprise' AND max_miembros = 600
    """)
