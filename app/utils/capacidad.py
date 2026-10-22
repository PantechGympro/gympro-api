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

SCRUM-223 (actualización de este mismo módulo): el plan ya no se lee de
Gimnasio.plan sino de Organizacion.plan -- la organización completa
contrata un plan que cubre hasta limites.max_sucursales sucursales, y se
sincroniza desde routes/owner_gym/billing.py cuando el Propietario
cambia de plan. El criterio de CONTEO no cambia: miembros y staff se
siguen contando por Gimnasio (ver la decisión de arriba), y ahora se
suma un tercer conteo, de sucursales, que es el único que se hace a
nivel Organizacion en vez de Gimnasio -- por definición, "cuántas
sucursales tiene esta organización" no es una pregunta que tenga
sentido hacerle a una sola sucursal.

SCRUM-224 (añadido a este mismo módulo): además de los TOPES numéricos
de arriba, el plan también trae banderas de función SÍ/NO en
`limites` -- analiticas_ia, rutinas_ia, reportes_pdf, app_movil, etc.
Esas banderas ya existían desde la migración 012 (sembradas para los 4
planes reales, no solo en seed_pg.py) y PlanSuscripcion.permite() ya
sabía leerlas -- lo que faltaba, y es lo que agrega este ticket, es que
algo del backend las hiciera cumplir. permite_funcion() de abajo es ese
punto único: cualquier endpoint que dependa de una función de plan lo
usa (vía @require_funcion_plan en utils/security.py) en vez de leer
`limites` a mano.
"""
from datetime import datetime, timezone

from app.models.pg.gimnasio import Gimnasio
from app.models.pg.organizacion import Organizacion
from app.models.pg.plan_suscripcion import PlanSuscripcion
from app.models.pg.complemento_capacidad import ComplementoCapacidad
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
    """
    Plan vigente de la sucursal `id_gimnasio` -- SCRUM-223: se resuelve
    por la ORGANIZACIÓN a la que pertenece (organizacion.plan), no por
    Gimnasio.plan (que se queda en el esquema pero ya no es la fuente de
    verdad -- ver docstring del módulo y de models/pg/organizacion.py).
    """
    gym = Gimnasio.query.get(id_gimnasio)
    if gym is None or gym.organizacion is None:
        return None
    nombre_plan = gym.organizacion.plan
    nombre_plan = nombre_plan.value if hasattr(nombre_plan, "value") else nombre_plan
    return PlanSuscripcion.query.filter_by(nombre=nombre_plan).first()


def _plan_de_organizacion(id_organizacion):
    """Igual que _plan_de() pero a partir del id de Organizacion directamente."""
    org = Organizacion.query.get(id_organizacion)
    if org is None:
        return None
    nombre_plan = org.plan.value if hasattr(org.plan, "value") else org.plan
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


def extra_miembros_de(id_gimnasio):
    """
    Capacidad extra VIGENTE comprada en lotes (ver
    models/pg/complemento_capacidad.py) para sumar al tope base del plan
    de `id_gimnasio`. "Vigente" se decide por fecha_fin en el momento de
    esta consulta -- no hay un job que marque lotes vencidos, ver el
    docstring de ese modelo.
    """
    ahora = datetime.now(timezone.utc)
    lotes = ComplementoCapacidad.query.filter_by(id_gimnasio=id_gimnasio, estado="activo").all()
    return sum(l.cantidad_miembros for l in lotes if l.vigente(ahora))


def verificar_capacidad_miembro(id_gimnasio, *, incremento=1):
    """
    AC1: antes de dar de alta `incremento` miembro(s) nuevos en
    `id_gimnasio`, confirma que no se exceda max_miembros de su plan más
    la capacidad extra que haya comprado en lotes (extra_miembros_de()).
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
    tope += extra_miembros_de(id_gimnasio)
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


def permite_funcion(id_gimnasio, nombre_funcion: str) -> bool:
    """
    SCRUM-224: True si el plan de la organización de `id_gimnasio` habilita
    la función indicada (ver PlanSuscripcion.permite() y las banderas de
    la migración 012: analiticas_ia, rutinas_ia, reportes_pdf, app_movil,
    pos, cobros_en_linea, respaldos, soporte_prioritario, ...).

    Se apoya en _plan_de(), el mismo resolutor que ya usan los topes
    numéricos de arriba (Gimnasio -> Organizacion -> PlanSuscripcion) --
    el plan sigue siendo de la ORGANIZACIÓN completa (SCRUM-223), así que
    da igual qué sucursal de esa organización se pase aquí.

    Si no se puede resolver el plan (gimnasio inexistente, sin
    organización) se niega por seguridad -- mismo criterio que
    PlanSuscripcion.permite() con una bandera ausente: "si no se sabe,
    no se concede".
    """
    plan = _plan_de(id_gimnasio)
    return bool(plan and plan.permite(nombre_funcion))


def contar_sucursales(id_organizacion):
    """Nº de sucursales (Gimnasio) que ya tiene esta organización."""
    return Gimnasio.query.filter_by(id_organizacion=id_organizacion).count()


def verificar_capacidad_sucursal(id_organizacion, *, incremento=1):
    """
    Antes de dar de alta `incremento` sucursal(es) nueva(s) en
    `id_organizacion`, confirma que no se exceda limites.max_sucursales
    del plan contratado por la organización. Mismo criterio que
    verificar_capacidad_miembro()/verificar_capacidad_staff(), pero a
    nivel Organizacion en vez de Gimnasio -- ver docstring del módulo.
    """
    plan = _plan_de_organizacion(id_organizacion)
    tope = (plan.limites or {}).get("max_sucursales") if plan else None
    if tope is None:
        return
    actual = contar_sucursales(id_organizacion)
    if actual + incremento > tope:
        raise LimiteDePlanExcedido(
            f"Tu organización ya tiene {actual} sucursal(es) de un máximo de {tope} en su plan.",
            tope=tope, actual=actual,
        )


def estado_capacidad_organizacion(id_organizacion):
    """Estado de ocupación de sucursales de la organización completa (análogo a estado_capacidad())."""
    plan  = _plan_de_organizacion(id_organizacion)
    tope  = (plan.limites or {}).get("max_sucursales") if plan else None
    actual = contar_sucursales(id_organizacion)
    return {
        "plan":           (plan.nombre if plan else None),
        "actual":         actual,
        "tope":           tope,
        "cerca_del_tope": tope is not None and actual >= tope * 0.9,
        "en_el_tope":     tope is not None and actual >= tope,
    }


def estado_capacidad(id_gimnasio):
    """
    AC3: estado de ocupación de una sucursal para que el panel del
    propietario pueda avisar ANTES de topar, no solo cuando una alta ya
    fue rechazada. "cerca_del_tope" se activa al 90% de ocupación --
    umbral de alerta temprana, no una regla de negocio del ticket.

    tope_miembros ya incluye la capacidad extra comprada en lotes (ver
    extra_miembros_de()) -- "extra_miembros" se devuelve aparte solo para
    que el frontend pueda mostrar "tu plan te da X, más Y por lotes".
    """
    plan = _plan_de(id_gimnasio)
    tope_base       = plan.max_miembros if plan else None
    extra           = extra_miembros_de(id_gimnasio)
    tope_miembros   = (tope_base + extra) if tope_base is not None else None
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

    bloque_miembros = _bloque(actual_miembros, tope_miembros)
    bloque_miembros["extra_miembros"] = extra

    return {
        "miembros": bloque_miembros,
        "staff":    _bloque(actual_staff, tope_staff),
    }
