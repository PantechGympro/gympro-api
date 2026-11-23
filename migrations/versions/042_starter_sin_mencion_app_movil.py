"""042 — Quita la mención de app móvil de las características del Starter.

Revision ID: 042
Revises: 041
Create Date: 2026-11-22

La 039 reescribió `caracteristicas` del plan Starter para que la tarjeta de
precios dijera la verdad (app_movil=False desde ese plan), pero dejó una
última línea: "La app movil para tus miembros se incluye desde el plan
Basico". El problema no es el contenido -- es correcto -- sino cómo lo pinta
el frontend: `caracteristicas` es una lista plana de strings y Landing.jsx le
pone un ✓ verde a CADA línea sin distinguir "lo que el plan sí tiene" de "una
nota sobre lo que no tiene" (ver web/src/pages/publico/Landing.jsx, el .map
de `p.caracteristicas`). Resultado: un check verde al lado de una frase que
dice "no la tiene hasta Basico" -- se lee como que el Starter SÍ trae la app
móvil, justo lo que se quería dejar de transmitir.

La forma correcta de comunicar "no incluido, disponible desde X" ya existe y
es correcta: el panel "Ver todo lo que incluye" (FuncionesPorRol.jsx, datos de
constants/catalogo_funciones.py) marca esa misma función con 🔒 y el texto
"Disponible desde el plan Basico". Esta revision solo quita la línea
duplicada y mal representada de la lista superior; el panel detallado
no cambia (sigue leyendo `funciones_por_rol`, no `caracteristicas`).

No toca `limites.app_movil` (ya en False desde la 039) ni `max_miembros`.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None

CARACTERISTICAS_039 = [
    "Hasta 20 miembros activos",
    "Registro de asistencias y pagos desde la web",
    "Control de entrada manual en recepcion",
    "1 usuario administrador",
    "Soporte por correo electronico",
    "La app movil para tus miembros se incluye desde el plan Basico",
]

CARACTERISTICAS_SIN_MENCION = CARACTERISTICAS_039[:-1]  # las primeras 5, sin la última línea


def _starter(con):
    return con.execute(sa.text(
        "SELECT id, caracteristicas FROM planes_suscripcion WHERE nombre = 'starter'"
    )).fetchone()


def upgrade():
    con = op.get_bind()
    fila = _starter(con)
    if fila is None:
        print("[042] No existe el plan 'starter' en esta base: nada que corregir.")
        return

    con.execute(sa.text("""
        UPDATE planes_suscripcion
           SET caracteristicas = CAST(:caracteristicas AS JSON)
         WHERE id = :id
    """), {"caracteristicas": json.dumps(CARACTERISTICAS_SIN_MENCION), "id": fila.id})
    print("[042] Starter: quitada la línea de app móvil de la lista superior "
          "(el panel 'Ver todo lo que incluye' ya la explica correctamente).")


def downgrade():
    con = op.get_bind()
    fila = _starter(con)
    if fila is None:
        return
    con.execute(sa.text("""
        UPDATE planes_suscripcion
           SET caracteristicas = CAST(:caracteristicas AS JSON)
         WHERE id = :id
    """), {"caracteristicas": json.dumps(CARACTERISTICAS_039), "id": fila.id})
