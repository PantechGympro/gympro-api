"""022 — Plan de la organización + tope de sucursales (SCRUM-223)

Hasta esta migración, el plan SaaS era un dato DEL GIMNASIO
(gimnasios.plan) y la facturación también (suscripciones.id_gimnasio):
dos sucursales de la misma organización podían estar en planes distintos
y cada una pagaba su propia mensualidad. routes/owner_gym/sucursales.py
lo dejaba escrito como una decisión de producto deliberadamente diferida
("si el negocio decide que cada sucursal debe pagar su propia
suscripción SaaS... se deja para una actividad aparte").

Esa decisión ya se tomó: la organización completa contrata UN plan, que
cubre hasta N sucursales (N = limites.max_sucursales del plan), cada una
con su propio tope de miembros/staff -- el mismo criterio por-sucursal
que ya aplicaba utils/capacidad.py, sin cambiarlo. Lo que cambia es DE
DÓNDE sale ese plan.

Cambios:
  organizaciones +plan (mismo plan_enum de gimnasios, reutilizado -- ver
                  migración 016). Se vuelve la fuente de verdad; se
                  sincroniza desde app/routes/owner_gym/billing.py cada
                  vez que el Propietario cambia de plan (su suscripción
                  vive en la sede principal, g.tenant_id == sede
                  principal para todo Propietario -- ver docstring de
                  sucursales.py).

  gimnasios.plan  se queda en el esquema (no se elimina la columna: lo
                  siguen leyendo reportes/paneles viejos y bajarla ahora
                  obligaría a tocar todo en la misma migración) pero deja
                  de ser la fuente de verdad -- utils/capacidad.py y el
                  resto del backend leen organizaciones.plan desde ahora.
                  No se backfillea con un valor por sucursal porque ya no
                  se usa para decidir límites.

  planes_suscripcion.limites  +max_sucursales (nueva clave, mismo patrón
                  JSON que max_staff -- ver migración 012). Valores
                  iniciales PROVISIONALES, igual de "a confirmar con el
                  equipo" que los de la migración 021:
                    starter=1, basico=1, pro=2, enterprise=5.
                  Solo se agrega si la clave no existe ya (jsonb ? ) --
                  respeta cualquier personalización hecha desde el panel
                  de superadmin, mismo criterio que 021.

  Enterprise, además, corrige max_miembros/max_staff de 500/20 a 200/10:
  con tope de sucursales, la diferencia entre "pro" y "enterprise" deja
  de ser cuánto cabe en UNA sucursal (ya se saturaba en pro) y pasa a ser
  cuántas sucursales cubre un mismo contrato -- ver bitácora técnica del
  proyecto para la discusión completa. Solo se aplica si la fila sigue
  en los valores que dejó la migración 021 (500/20), para no pisar una
  personalización posterior hecha desde el panel.

Backfill determinista de organizaciones.plan: cada organización toma el
plan de su sucursal marcada es_sucursal_principal=True -- invariante
garantizado desde la migración 017 (toda organización tiene exactamente
una sede principal). Si alguna organización quedara sin sede principal
(no debería poder pasar), se le asigna 'basico' igual que el default de
gimnasios.plan.

Riesgo (igual que en 017/021): sin Postgres real accesible desde el
sandbox donde se escribió esta migración -- validar antes de aplicar
contra una base real o de respaldo, y correr el downgrade también antes
de confiar en él.

Revision ID: 022
Revises: 021
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

revision      = "022"
down_revision = "021"
branch_labels = None
depends_on    = None

_plan_enum_type = PGEnum(
    "starter", "basico", "pro", "enterprise",
    name="plan_enum", create_type=False,
)


def upgrade():
    # ── 1. organizaciones.plan ──────────────────────────────────────────
    op.add_column(
        "organizaciones",
        sa.Column("plan", _plan_enum_type, nullable=True),
    )
    op.execute("""
        UPDATE organizaciones o
        SET plan = g.plan
        FROM gimnasios g
        WHERE g.id_organizacion = o.id AND g.es_sucursal_principal = true
    """)
    op.execute("UPDATE organizaciones SET plan = 'basico' WHERE plan IS NULL")
    op.alter_column("organizaciones", "plan", nullable=False, server_default="basico")

    # ── 2. planes_suscripcion.limites.max_sucursales ────────────────────
    # La columna `limites` es JSON (no JSONB, ver migración 012) -- se
    # castea a jsonb solo para jsonb_set()/el operador `?`, sin cambiar
    # el tipo de columna (mismo patrón que la migración 021).
    for nombre_plan, tope in (("starter", 1), ("basico", 1), ("pro", 2), ("enterprise", 5)):
        op.execute(f"""
            UPDATE planes_suscripcion
            SET limites = (jsonb_set(
                coalesce(limites::jsonb, '{{}}'::jsonb), '{{max_sucursales}}', '{tope}'::jsonb
            ))::json
            WHERE nombre = '{nombre_plan}'
              AND NOT (coalesce(limites::jsonb, '{{}}'::jsonb) ? 'max_sucursales')
        """)

    # ── 3. enterprise: 500/20 por sucursal -> 200/10 por sucursal ───────
    # El tope de sucursales (arriba) es lo que ahora diferencia a
    # enterprise de pro, no un límite por-sucursal más alto que, en la
    # práctica, nunca se había validado contra una prueba de carga real
    # (ver nota de la migración 021).
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 200,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '10'::jsonb))::json,
            caracteristicas = (
                SELECT jsonb_agg(
                    CASE
                        WHEN elem = 'Miembros y staff ilimitados'
                          OR elem ILIKE 'Hasta 500 miembros%'
                        THEN '"Hasta 200 miembros y 10 staff por sucursal, hasta 5 sucursales"'::jsonb
                        ELSE to_jsonb(elem)
                    END
                )
                FROM jsonb_array_elements_text(coalesce(caracteristicas::jsonb, '[]'::jsonb)) AS elem
            )::json
        WHERE nombre = 'enterprise' AND max_miembros = 500
    """)


def downgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 500,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '20'::jsonb))::json
        WHERE nombre = 'enterprise' AND max_miembros = 200
    """)
    for nombre_plan in ("starter", "basico", "pro", "enterprise"):
        op.execute(f"""
            UPDATE planes_suscripcion
            SET limites = (limites::jsonb - 'max_sucursales')::json
            WHERE nombre = '{nombre_plan}'
        """)

    op.drop_column("organizaciones", "plan")
