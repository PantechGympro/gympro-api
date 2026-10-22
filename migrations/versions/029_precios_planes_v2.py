"""029 -- Ajuste de precios y topes de miembros de los 4 planes

Peticion explicita del usuario: subir Enterprise a 400 miembros / 20
staff, y ajustar el resto de los planes (precio Y tope de miembros) de
forma que la subida se justifique con lo que el sistema ya ofrece hoy --
"hay muchas funciones ya y entre mas costoso el plan le ofrece mas
herramientas". Los precios originales (ver seed_pg.py / migraciones
012/023) se fijaron antes de la automatizacion de reportes, el calendario
propio, los lotes de capacidad (migracion 030) y el resto de lo agregado
desde entonces.

Matriz aplicada (mensual MXN / tope de miembros; staff y sucursales solo
cambian en enterprise, unico plan donde el usuario lo pidio explicito):

    Plan        Precio antes -> ahora   Miembros antes -> ahora
    starter     $0        -> $0         25  -> 30
    basico      $499.00   -> $649.00    50  -> 80
    pro         $999.00   -> $1,399.00  200 -> 300
    enterprise  $1,999.00 -> $2,999.00  200 -> 400  (+staff 10 -> 20)

precio_anual_mxn sigue la convencion ya establecida en la migracion 023
(mensual x 10, "2 meses gratis") -- no se vuelve a definir aparte.

Como en la migracion 021/023, cada UPDATE esta GUARDADO por el precio (o
tope, en starter) ANTERIOR: si un superadmin ya personalizo un plan desde
el panel (routes/superadmin/planes.py) desde que se fijaron esos valores,
esa personalizacion se respeta y esta migracion no la pisa.

Revision ID: 029
Revises: 028
"""
from alembic import op

revision      = "029"
down_revision = "028"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 30,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 30 miembros activos',
                'Registro de asistencias y pagos',
                '1 usuario administrador',
                'Soporte por correo electronico'
            ]::text[]))::json
        WHERE nombre = 'starter' AND max_miembros = 25
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 64900,
            precio_anual_mxn   = 649000,
            max_miembros       = 80,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 80 miembros activos',
                'Punto de venta y control de productos',
                'Cobros en linea con PayPal y Mercado Pago',
                'Hasta 3 usuarios de staff',
                'Reportes en PDF',
                'App movil para tus miembros',
                'Amplia tu limite comprando lotes de miembros adicionales'
            ]::text[]))::json
        WHERE nombre = 'basico' AND precio_mensual_mxn = 49900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 139900,
            precio_anual_mxn   = 1399000,
            max_miembros       = 300,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 300 miembros activos',
                'Todo lo del plan Basico',
                'Analiticas con IA: riesgo de abandono y clientes por valor',
                'Generacion de rutinas y dietas asistida por IA',
                'Respaldos automaticos de tu informacion',
                'Hasta 10 usuarios de staff',
                'Amplia tu limite comprando lotes de miembros adicionales'
            ]::text[]))::json
        WHERE nombre = 'pro' AND precio_mensual_mxn = 99900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 299900,
            precio_anual_mxn   = 2999000,
            max_miembros       = 400,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '20'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 400 miembros y 20 staff por sucursal, hasta 5 sucursales',
                'Todo lo del plan Pro',
                'Laboratorio de Machine Learning completo',
                'Soporte prioritario con SLA',
                'Acompanamiento en la implementacion',
                'Amplia tu limite comprando lotes de miembros adicionales'
            ]::text[]))::json
        WHERE nombre = 'enterprise' AND precio_mensual_mxn = 199900
    """)


def downgrade():
    op.execute("""
        UPDATE planes_suscripcion
        SET max_miembros = 25,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 25 miembros activos',
                'Registro de asistencias y pagos',
                '1 usuario administrador',
                'Soporte por correo electronico'
            ]::text[]))::json
        WHERE nombre = 'starter' AND max_miembros = 30
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 49900,
            precio_anual_mxn   = 499000,
            max_miembros       = 50,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 50 miembros activos',
                'Punto de venta y control de productos',
                'Cobros en linea con PayPal y Mercado Pago',
                'Hasta 3 usuarios de staff',
                'Reportes en PDF',
                'App movil para tus miembros'
            ]::text[]))::json
        WHERE nombre = 'basico' AND precio_mensual_mxn = 64900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 99900,
            precio_anual_mxn   = 999000,
            max_miembros       = 200,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 200 miembros activos',
                'Todo lo del plan Basico',
                'Analiticas con IA: riesgo de abandono y clientes por valor',
                'Generacion de rutinas y dietas asistida por IA',
                'Respaldos automaticos de tu informacion',
                'Hasta 10 usuarios de staff'
            ]::text[]))::json
        WHERE nombre = 'pro' AND precio_mensual_mxn = 139900
    """)

    op.execute("""
        UPDATE planes_suscripcion
        SET precio_mensual_mxn = 199900,
            precio_anual_mxn   = 1999000,
            max_miembros       = 200,
            limites = (jsonb_set(coalesce(limites::jsonb, '{}'::jsonb), '{max_staff}', '10'::jsonb))::json,
            caracteristicas = (to_jsonb(ARRAY[
                'Hasta 200 miembros y 10 staff por sucursal, hasta 5 sucursales',
                'Todo lo del plan Pro',
                'Laboratorio de Machine Learning completo',
                'Soporte prioritario con SLA',
                'Acompanamiento en la implementacion'
            ]::text[]))::json
        WHERE nombre = 'enterprise' AND precio_mensual_mxn = 299900
    """)
