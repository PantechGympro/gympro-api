"""Recorta el plan Starter: 20 miembros y sin app movil.

Revision ID: 039
Revises: 038
Create Date: 2026-11-21

El Starter es una VITRINA, no un sistema gratuito permanente. Con 30 miembros
y la app movil incluida, un gimnasio chico podia quedarse ahi indefinidamente
y nunca tener una razon para pagar: la app movil con check-in por QR es el
gancho principal de la oferta y se estaba regalando.

Cambios:
  - max_miembros: 30 -> 20
  - limites.app_movil: true -> false   (empieza en Basico)
  - `caracteristicas` reescritas para que la tarjeta de precios diga la verdad

Lo que NO cambia, a proposito: el registro de entrada manual desde la web no
esta gateado por plan, asi que un gimnasio en Starter sigue pudiendo operar la
puerta todos los dias. Pierde el QR desde el celular del socio, no el control
de acceso.

Sobre los gimnasios que YA estan en Starter:
  - El tope de miembros se verifica en el ALTA, nunca en una lectura (ver
    utils/capacidad.verificar_capacidad_miembro). Nadie pierde miembros ya
    registrados: un gimnasio con 28 activos los conserva y simplemente no
    puede agregar mas hasta mejorar de plan o dar de baja a alguien.
  - La app movil SI se corta de inmediato para sus usuarios. Por eso el
    upgrade() imprime la lista de organizaciones afectadas: si hay alguna, es
    una decision de negocio avisarles o dejarlas con el plan anterior, y esa
    decision la toma una persona, no esta migracion.

Esta revision manda sobre la matriz canonica de la 038 para el Starter: la
038 refleja el catalogo tal como estaba al corregir el gateo, esta lo cambia.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None

MAX_MIEMBROS_NUEVO = 20
MAX_MIEMBROS_ANTERIOR = 30

CARACTERISTICAS_NUEVAS = [
    "Hasta 20 miembros activos",
    "Registro de asistencias y pagos desde la web",
    "Control de entrada manual en recepcion",
    "1 usuario administrador",
    "Soporte por correo electronico",
    "La app movil para tus miembros se incluye desde el plan Basico",
]

CARACTERISTICAS_ANTERIORES = [
    "Hasta 30 miembros activos",
    "App movil para tus miembros con check-in por QR",
    "Registro de asistencias y pagos",
    "1 usuario administrador",
    "Soporte por correo electronico",
]


def _starter(con):
    return con.execute(sa.text(
        "SELECT id, limites FROM planes_suscripcion WHERE nombre = 'starter'"
    )).fetchone()


def _limites(fila):
    limites = fila.limites
    if isinstance(limites, str):
        try:
            limites = json.loads(limites)
        except ValueError:
            limites = {}
    return dict(limites or {})


def _avisar_afectados(con):
    """Imprime quien esta hoy en Starter, para que la perdida de la app movil
    no le caiga encima a un cliente real sin que nadie se entere."""
    filas = con.execute(sa.text("""
        SELECT o.id, o.nombre,
               (SELECT COUNT(*) FROM gimnasios g WHERE g.id_organizacion = o.id) AS sucursales
          FROM organizaciones o
         WHERE o.plan = 'starter'
         ORDER BY o.id
    """)).fetchall()
    if not filas:
        print("[039] Ninguna organizacion esta en Starter: nadie pierde la app movil.")
        return
    print(f"[039] AVISO: {len(filas)} organizacion(es) en Starter pierden la app movil ahora:")
    for f in filas:
        print(f"[039]   - org {f.id} '{f.nombre}' ({f.sucursales} sucursal/es)")
    print("[039] El tope de 20 miembros NO expulsa a nadie ya registrado (se valida en el alta).")


def upgrade():
    con = op.get_bind()
    fila = _starter(con)
    if fila is None:
        print("[039] No existe el plan 'starter' en esta base: nada que recortar.")
        return

    _avisar_afectados(con)

    limites = _limites(fila)
    limites["app_movil"] = False
    limites.setdefault("max_staff", 1)
    limites.setdefault("max_sucursales", 1)

    con.execute(sa.text("""
        UPDATE planes_suscripcion
           SET max_miembros    = :max_miembros,
               limites         = CAST(:limites AS JSON),
               caracteristicas = CAST(:caracteristicas AS JSON)
         WHERE id = :id
    """), {
        "max_miembros": MAX_MIEMBROS_NUEVO,
        "limites": json.dumps(limites),
        "caracteristicas": json.dumps(CARACTERISTICAS_NUEVAS),
        "id": fila.id,
    })
    print(f"[039] Starter: {MAX_MIEMBROS_ANTERIOR} -> {MAX_MIEMBROS_NUEVO} miembros, app_movil apagada.")


def downgrade():
    con = op.get_bind()
    fila = _starter(con)
    if fila is None:
        return
    limites = _limites(fila)
    limites["app_movil"] = True
    con.execute(sa.text("""
        UPDATE planes_suscripcion
           SET max_miembros    = :max_miembros,
               limites         = CAST(:limites AS JSON),
               caracteristicas = CAST(:caracteristicas AS JSON)
         WHERE id = :id
    """), {
        "max_miembros": MAX_MIEMBROS_ANTERIOR,
        "limites": json.dumps(limites),
        "caracteristicas": json.dumps(CARACTERISTICAS_ANTERIORES),
        "id": fila.id,
    })
