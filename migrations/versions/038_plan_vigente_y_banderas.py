"""Normaliza las banderas de funcion de los planes y realinea las copias de plan.

Revision ID: 038
Revises: 037
Create Date: 2026-11-21

Dos arreglos a la misma causa raiz: "el gimnasio paga Enterprise y la pantalla
dice que mejore su plan".

1. BANDERAS AUSENTES. PlanSuscripcion.permite() trata una bandera que no esta
   en `limites` como NO incluida ("si no se sabe, no se concede"). Es el
   criterio correcto, pero convierte cualquier fila incompleta en un bloqueo
   silencioso: el plan cuesta $2,499, la tarjeta comercial promete el modulo,
   y la pantalla lo bloquea porque a la fila le falta una llave. Las filas se
   vuelven incompletas con facilidad -- seed_pg.py hace TRUNCATE + re-INSERT
   de planes_suscripcion, asi que lo que no este en ese script desaparece (su
   propio comentario documenta que a `rutinas_ia` ya le paso una vez).

   Esta migracion completa cada plan del catalogo contra la matriz canonica de
   abajo, que es la misma que publica la tarjeta de precios.

   ASIMETRIA DELIBERADA: solo ENCIENDE banderas y solo SUBE topes. Nunca apaga
   ni baja nada. Si alguien activo una funcion a mano desde el panel de
   superadmin (una promocion, una cortesia a un cliente), una migracion no
   tiene por que quitarsela sin que nadie lo decida; revocar es una accion
   deliberada en el panel, no un efecto secundario de un arreglo. Y los planes
   que NO son del catalogo (creados desde el panel con otro nombre) no se
   tocan en absoluto.

2. COPIAS DE PLAN DESALINEADAS. `organizaciones.plan` y `gimnasios.plan` son
   copias del plan contratado; la suscripcion es lo que se cobra. Varios
   caminos de alta dejaban las copias en "basico" mientras la suscripcion
   decia otra cosa. El codigo ya no decide permisos con esas columnas (ver
   utils/capacidad.py, resolutor unico), pero siguen alimentando reportes,
   consultas directas y el panel de superadmin, asi que se realinean aqui para
   que no contradigan a la factura.

Reversibilidad: downgrade() no restaura las banderas ausentes ni las copias
viejas a proposito -- eran datos equivocados, no una configuracion. Devolver
un gimnasio Enterprise a "basico" porque asi estaba antes seria reintroducir
el bug. La migracion es segura de re-aplicar (idempotente).
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


# Matriz canonica: lo que cada plan del catalogo promete en la tarjeta de
# precios (ver app/seeds/seed_pg.py y constants/catalogo_funciones.py).
# Solo se usa para COMPLETAR lo que falte, nunca para recortar.
CANONICO = {
    "starter": {
        "pos": False, "pasarelas_pago": False, "analiticas_ia": False,
        "rutinas_ia": False, "reportes_pdf": False, "app_movil": True,
        "max_staff": 1, "max_sucursales": 1,
    },
    "basico": {
        "pos": True, "pasarelas_pago": True, "analiticas_ia": False,
        "rutinas_ia": False, "reportes_pdf": True, "app_movil": True,
        "max_staff": 4, "max_sucursales": 1,
    },
    "pro": {
        "pos": True, "pasarelas_pago": True, "analiticas_ia": True,
        "rutinas_ia": True, "reportes_pdf": True, "app_movil": True,
        "backups_automaticos": True, "max_staff": 12, "max_sucursales": 2,
    },
    "enterprise": {
        "pos": True, "pasarelas_pago": True, "analiticas_ia": True,
        "rutinas_ia": True, "reportes_pdf": True, "app_movil": True,
        "backups_automaticos": True, "ml_lab": True, "soporte_sla": True,
        "max_staff": 30, "max_sucursales": 8,
    },
}

# Parejas equivalentes (ver PlanSuscripcion._ALIAS_FUNCIONES): si la fila ya
# trae el sinonimo encendido, la funcion YA esta concedida y no hace falta
# agregar la otra llave.
ALIAS = {
    "pasarelas_pago": "cobros_en_linea",
    "backups_automaticos": "respaldos",
    "soporte_sla": "soporte_prioritario",
}

TOPES = ("max_staff", "max_sucursales")


def _completar(limites, canon):
    """Devuelve (limites_nuevos, cambios) aplicando solo altas y subidas."""
    nuevos = dict(limites or {})
    cambios = []

    for llave, valor in canon.items():
        actual = nuevos.get(llave)

        if llave in TOPES:
            # Solo se sube un tope ausente o mas bajo que el canonico; jamas
            # se baja uno que alguien haya ampliado a proposito.
            if actual is None:
                nuevos[llave] = valor
                cambios.append(f"{llave}: (ausente) -> {valor}")
            else:
                try:
                    if int(actual) < int(valor):
                        nuevos[llave] = valor
                        cambios.append(f"{llave}: {actual} -> {valor}")
                except (TypeError, ValueError):
                    nuevos[llave] = valor
                    cambios.append(f"{llave}: {actual!r} (invalido) -> {valor}")
            continue

        if not valor:
            # El canonico dice que NO va incluida: se escribe explicitamente
            # solo si falta, para que la fila quede completa y se pueda
            # diagnosticar de un vistazo. No apaga nada ya encendido.
            if llave not in nuevos:
                nuevos[llave] = False
            continue

        # El canonico dice que SI va incluida.
        if bool(actual):
            continue
        if bool(nuevos.get(ALIAS.get(llave, ""), False)):
            continue          # ya concedida por el sinonimo
        nuevos[llave] = True
        cambios.append(f"{llave}: {actual!r} -> True")

    return nuevos, cambios


def upgrade():
    con = op.get_bind()

    # ── 1. Completar banderas de los planes del catalogo ─────────────────────
    filas = con.execute(sa.text(
        "SELECT id, nombre, limites FROM planes_suscripcion WHERE nombre = ANY(:nombres)"
    ), {"nombres": list(CANONICO)}).fetchall()

    for fila in filas:
        limites = fila.limites
        if isinstance(limites, str):          # por si la columna viaja como texto
            try:
                limites = json.loads(limites)
            except ValueError:
                limites = {}
        nuevos, cambios = _completar(limites, CANONICO[fila.nombre])
        if cambios:
            con.execute(
                sa.text("UPDATE planes_suscripcion SET limites = CAST(:l AS JSON) WHERE id = :id"),
                {"l": json.dumps(nuevos), "id": fila.id},
            )
            print(f"[038] plan {fila.nombre}: {', '.join(cambios)}")

    # ── 2. Realinear las copias de plan con la suscripcion vigente ───────────
    # Plan vigente de cada organizacion: la suscripcion trialing/active mas
    # reciente de CUALQUIERA de sus sucursales, prefiriendo la principal
    # (mismo criterio que utils/capacidad._plan_de_suscripcion).
    vigentes = sa.text("""
        SELECT DISTINCT ON (g.id_organizacion)
               g.id_organizacion AS id_org, p.nombre AS plan
          FROM suscripciones s
          JOIN gimnasios            g ON g.id = s.id_gimnasio
          JOIN planes_suscripcion   p ON p.id = s.id_plan
         WHERE s.estado IN ('trialing', 'active')
           AND g.id_organizacion IS NOT NULL
         ORDER BY g.id_organizacion,
                  g.es_sucursal_principal DESC,
                  s.created_at DESC
    """)

    for fila in con.execute(vigentes).fetchall():
        actualizadas = con.execute(sa.text(
            "UPDATE organizaciones SET plan = :plan WHERE id = :id AND plan IS DISTINCT FROM :plan"
        ), {"plan": fila.plan, "id": fila.id_org}).rowcount
        con.execute(sa.text(
            "UPDATE gimnasios SET plan = :plan WHERE id_organizacion = :id AND plan IS DISTINCT FROM :plan"
        ), {"plan": fila.plan, "id": fila.id_org})
        if actualizadas:
            print(f"[038] organizacion {fila.id_org}: plan realineado a '{fila.plan}'")


def downgrade():
    # Sin vuelta atras a proposito: lo que corrige upgrade() son datos
    # equivocados (banderas que faltaban, copias desactualizadas), no una
    # configuracion que alguien eligiera. Restaurarlas seria reintroducir el
    # bloqueo de funciones ya pagadas. Ver el docstring del modulo.
    pass
