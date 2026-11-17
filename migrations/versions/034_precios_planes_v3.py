"""034 -- Reestructura de precios y beneficios (v3, competitiva)

Referencia de mercado (Mexico, nov/2026, fuentes publicas de cada proveedor,
ver bitacora del proyecto): Vekio gratis<=30 / $398, GoGym $499 IVA incl.,
Koreo $499/$999/$1,899, Gym&i $799/$1,299/$1,899 + IVA (80/500/ilimitado),
Fitco $999/$1,999/$3,299, Gymforce $1,508.

Matriz aplicada (MXN/mes, anual = mensual x 10 como en la migracion 023):

    Plan        Precio            Miembros   Staff   Sucursales
    starter     $0                30         1       1        (+ app movil)
    basico      $649 -> $549      80 -> 100  3 -> 4  1
    pro         $1,399 -> $1,199  300 -> 400 10 -> 12 2
    enterprise  $2,999 -> $2,499  600 (sin cambio, decision del usuario) 30 / 8

Criterios: (1) el costo por miembro a tope ya no sube de un escalon al
siguiente (basico $5.5, pro $3.0, enterprise $4.2 por sucursal, y esta
ultima cubre hasta 8); (2) el salto basico -> pro sigue en ~2.2x de precio pero
ahora trae 4x los miembros (100 -> 400); (3) la app movil se abre tambien al
plan gratuito (tope de 30 miembros = costo marginal casi nulo) porque es el
diferenciador frente a los gratuitos de la competencia (Vekio, Connect Gym).

Cada UPDATE esta GUARDADO por el precio anterior: si un superadmin ya
personalizo un plan desde el panel, esta migracion no lo pisa.

Revision ID: 034
Revises: 033
"""
from alembic import op

revision      = "034"
down_revision = "033"
branch_labels = None
depends_on    = None

_LOTES = "Amplia tu limite comprando lotes de miembros adicionales"


def upgrade():
    op.execute(f"""
        UPDATE planes_suscripcion
        SET limites = (jsonb_set(coalesce(limites::jsonb, '{{}}'::jsonb), '{{app_movil}}', 'true'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 30 miembros activos',
                'App movil para tus miembros con check-in por QR',
                'Registro de asistencias y pagos',
                '1 usuario administrador',
                'Soporte por correo electronico'
            ]::text[]))::json
        WHERE nombre = 'starter' AND max_miembros = 30
          AND coalesce(limites::jsonb ->> 'app_movil', 'false') <> 'true'
    """)

    op.execute(f"""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 54900, precio_anual_mxn = 549000, max_miembros = 100,
            limites = (jsonb_set(coalesce(limites::jsonb, '{{}}'::jsonb), '{{max_staff}}', '4'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 100 miembros activos',
                'Punto de venta y control de productos',
                'Cobros en linea con PayPal y Mercado Pago',
                'Hasta 4 usuarios de staff',
                'Reportes en PDF',
                'App movil para tus miembros',
                '{_LOTES}'
            ]::text[]))::json
        WHERE nombre = 'basico' AND precio_mensual_mxn = 64900
    """)

    op.execute(f"""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 119900, precio_anual_mxn = 1199000, max_miembros = 400,
            limites = (jsonb_set(coalesce(limites::jsonb, '{{}}'::jsonb), '{{max_staff}}', '12'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 400 miembros activos',
                'Todo lo del plan Basico',
                'Analiticas con IA: riesgo de abandono y clientes por valor',
                'Generacion de rutinas y dietas asistida por IA',
                'Respaldos automaticos de tu informacion',
                'Hasta 12 usuarios de staff',
                'Hasta 2 sucursales con un solo contrato',
                '{_LOTES}'
            ]::text[]))::json
        WHERE nombre = 'pro' AND precio_mensual_mxn = 139900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 249900, precio_anual_mxn = 2499000
        WHERE nombre = 'enterprise' AND precio_mensual_mxn = 299900
    """)


def downgrade():
    op.execute(f"""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 64900, precio_anual_mxn = 649000, max_miembros = 80,
            limites = (jsonb_set(coalesce(limites::jsonb, '{{}}'::jsonb), '{{max_staff}}', '3'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 80 miembros activos',
                'Punto de venta y control de productos',
                'Cobros en linea con PayPal y Mercado Pago',
                'Hasta 3 usuarios de staff',
                'Reportes en PDF',
                'App movil para tus miembros',
                '{_LOTES}'
            ]::text[]))::json
        WHERE nombre = 'basico' AND precio_mensual_mxn = 54900
    """)

    op.execute(f"""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 139900, precio_anual_mxn = 1399000, max_miembros = 300,
            limites = (jsonb_set(coalesce(limites::jsonb, '{{}}'::jsonb), '{{max_staff}}', '10'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 300 miembros activos',
                'Todo lo del plan Basico',
                'Analiticas con IA: riesgo de abandono y clientes por valor',
                'Generacion de rutinas y dietas asistida por IA',
                'Respaldos automaticos de tu informacion',
                'Hasta 10 usuarios de staff',
                '{_LOTES}'
            ]::text[]))::json
        WHERE nombre = 'pro' AND precio_mensual_mxn = 119900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 299900, precio_anual_mxn = 2999000
        WHERE nombre = 'enterprise' AND precio_mensual_mxn = 249900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET limites = (coalesce(limites::jsonb, '{}'::jsonb) - 'app_movil')::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 30 miembros activos',
                'Registro de asistencias y pagos',
                '1 usuario administrador',
                'Soporte por correo electronico'
            ]::text[]))::json
        WHERE nombre = 'starter' AND max_miembros = 30
    """)
