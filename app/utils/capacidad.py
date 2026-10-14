"""
utils/capacidad.py — Verificación de los topes de plan (SCRUM-222).

El problema que resuelve: la tarjeta de precios anuncia topes de miembros
y de staff por plan (ver seed_pg.py), pero hasta este ticket nada los
aplicaba -- cualquier gimnasio podía dar de alta a quien quisiera sin
importar lo que contrató.

Decisión del ticket (no es una elección de implementación, viene dada):
el tope de miembros es de 200 (hoy, el del plan "pro" -- ver
PlanSuscripcion.max_miembros) POR SUCURSAL, no por organización. "Un plan
con varias sucursales multiplica la capacidad por sucursal, no la
reparte." Por eso toda esta verificación cuenta sobre UN Gimnasio
(id_gimnasio), nunca sobre la organización completa -- independiente de
si ese Gimnasio es o no una "sucursal" en el sentido de HU-45: la unidad
de capacidad ya era el Gimnasio desde antes de esa épica.

Comportamiento al exceder el tope (AC4, decisión explícita requerida por
la historia): SE BLOQUEA el alta (quien llama traduce
LimiteDePlanExcedido a 409). Se descartaron las otras dos opciones que
planteaba el ticket:
  - "solo advertir" -- un tope que no bloquea no es un tope; no cumple
    el objetivo de la historia ("lo que se cobra y lo que se entrega
    coincidan").
  - "periodo de gracia" -- introduce un estado temporal sin que el
    ticket defina cuánto dura, qué pasa si se repite, ni quién lo
    administra; hoy no hay una política de soporte para resolver esas
    preguntas, así que implementarlo sería inventar una.
Bloquear es la única opción determinista y auditable con la información
disponible. Queda documentado aquí porque es, literalmente, un requisito
de la historia -- no un detalle de implementación a ocultar en un commit.
"""
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.usuario import Usuario
from app.models.pg.rol import Rol
from app.constants.roles import ROL_ENTRENADOR, ROL_RECEPCIONISTA, ROL_ADMINISTRADOR


class LimiteDePlanExcedido(Exception):
    """
    El alta no procede: la sucursal ya está en (o rebasaría) su tope de
    plan. Quien la captura decide la respuesta HTTP -- normalmente 409,
    ver owner_gym/miembros.py, owner_gym/owner_trainers.py y
    owner_gym/sucursales.py.
    """
    def __init__(self, mensaje, *, tope, actual):
        super().__init__(mensaje)
        self.tope = tope
        self.actual = actual


def _plan_de(id_gimnasio):
    gym = Gimnasio.query.get(id_gimnasio)
    if gym is None:
        return None
    nombre_plan = gym.plan.value if hasattr(gym.plan, "value") else gym.plan
    return PlanSuscripcion.query.filter_by(nombre=nombre_plan).first()


def contar_miembros_activos(id_gimnasio):
    """
    Nº de miembros activos de ESTA sucursal -- nunca de toda la
    organización (ver decisión del ticket en el docstring del módulo).
    Fuente: MongoDB, colección miembros (estado="Activo", id_gimnasio_pg),
    la misma que ya usa el dashboard de KPIs.
    """
    from app.mongo import get_db
    mdb = get_db()
    return mdb.miembros.count_documents({"id_gimnasio_pg": id_gimnasio, "estado": "Activo"})


def contar_staff_activo(id_gimnasio):
    """
    Nº de staff activo de ESTA sucursal: Entrenador + Recepcionista +
    Administrador (HU-45). Deliberadamente NO cuenta al Propietario -- su
    cupo no lo limita el tope de staff del plan, es el dueño.
    """
    role_ids = [
        r.id for r in Rol.query.filter(
            Rol.nombre.in_([ROL_ENTRENADOR, ROL_RECEPCIONISTA, ROL_ADMINISTRADOR])
        ).all()
    ]
    if not role_ids:
        return 0
    return Usuario.query.filter(
        Usuario.id_gimnasio == id_gimnasio,
        Usuario.id_rol.in_(role_ids),
        Usuario.activo == True,  # noqa: E712 (estilo del resto del proyecto)
    ).count()


def verificar_capacidad_miembro(id_gimnasio, *, incremento=1):
    """
    AC1: antes de dar de alta `incremento` miembro(s) nuevos en
    `id_gimnasio`, confirma que no se exceda max_miembros de su plan.
    No hace nada si el plan no define tope (None = sin límite -- hoy
    ningún plan vigente lo deja así tras el AC6 de este mismo ticket).

    Debe llamarse ANTES de crear el registro -- la nota del ticket es
    explícita: "la verificación ocurre en el alta, nunca en una consulta
    de lectura".
    """
    plan = _plan_de(id_gimnasio)
    tope = plan.max_miembros if plan else None
    if tope is None:
        return
    actual = contar_miembros_activos(id_gimnasio)
    if actual + incremento > tope:
        raise LimiteDePlanExcedido(
            f"Esta sucursal ya tiene {actual} miembro(s) activo(s) de un máximo de {tope} en su plan.",
            tope=tope, actual=actual,
        )


def verificar_capacidad_staff(id_gimnasio, *, incremento=1):
    """
    AC2: igual que verificar_capacidad_miembro() pero para staff
    (Entrenador/Recepcionista/Administrador), leyendo el tope de
    limites.max_staff del plan (ver seed_pg.py -- ya existía en el
    catálogo, solo que nada lo hacía cumplir).
    """
    plan = _plan_de(id_gimnasio)
    tope = (plan.limites or {}).get("max_staff") if plan else None
    if tope is None:
        return
    actual = contar_staff_activo(id_gimnasio)
    if actual + incremento > tope:
        raise LimiteDePlanExcedido(
            f"Esta sucursal ya tiene {actual} usuario(s) de staff de un máximo de {tope} en su plan.",
            tope=tope, actual=actual,
        )


def estado_capacidad(id_gimnasio):
    """
    AC3: estado de ocupación de una sucursal para que el panel del
    propietario pueda avisar ANTES de topar, no solo cuando una alta ya
    fue rechazada. "cerca_del_tope" se activa al 90% de ocupación --
    umbral de alerta temprana, no una regla de negocio del ticket.
    """
    plan = _plan_de(id_gimnasio)
    tope_miembros   = plan.max_miembros if plan else None
    tope_staff      = (plan.limites or {}).get("max_staff") if plan else None
    actual_miembros = contar_miembros_activos(id_gimnasio)
    actual_staff    = contar_staff_activo(id_gimnasio)

    def _bloque(actual, tope):
        return {
            "actual":         actual,
            "tope":           tope,
            "cerca_del_tope": tope is not None and actual >= tope * 0.9,
            "en_el_tope":     tope is not None and actual >= tope,
        }

    return {
        "miembros": _bloque(actual_miembros, tope_miembros),
        "staff":    _bloque(actual_staff, tope_staff),
    }
