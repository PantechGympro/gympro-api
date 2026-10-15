"""
utils/alcance.py — Helpers de alcance organizacional (HU-41 / SCRUM-212).

Introduce el modelo Organizacion + AsignacionUsuario por encima del
esquema Gimnasio/Usuario existente, SIN cambiar el comportamiento actual:
el permiso real de un usuario sigue resolviéndose por Usuario.id_rol +
Usuario.id_gimnasio (ver utils/tenant.py) -- HU-42 migrará esa resolución
a AsignacionUsuario más adelante.

Estos helpers solo garantizan que, desde HU-41, todo Gimnasio nuevo tenga
su Organizacion y que todo Usuario nuevo quede reflejado también en
asignacion_usuario, para que ese backfill no tenga que repetirse a mano
cuando llegue HU-42.

Uso típico en un endpoint que crea un Gimnasio y luego su Usuario dueño:

    nuevo_gym = Gimnasio(...)
    crear_organizacion_para_gimnasio(nuevo_gym)   # ANTES de add()/flush() --
                                                   # ver nota en la función

    db.session.add(nuevo_gym)
    db.session.flush()                        # necesita nuevo_gym.id

    nuevo_admin = Usuario(..., id_gimnasio=nuevo_gym.id)
    db.session.add(nuevo_admin)
    db.session.flush()                        # necesita nuevo_admin.id
    crear_asignacion(nuevo_admin, gimnasio=nuevo_gym)

    db.session.commit()
"""
from app.extensions import db
from app.models.pg.organizacion import Organizacion
from app.models.pg.asignacion_usuario import AsignacionUsuario
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.rol import Rol
from app.constants.roles import normalizar_rol
from app.models.pg.bitacora_evento import BitacoraEvento


def crear_organizacion_para_gimnasio(gimnasio, plan=None) -> Organizacion:
    """
    Crea la Organizacion 1:1 de un Gimnasio recién creado y la deja
    asignada en gimnasio.id_organizacion (más es_sucursal_principal=True).

    `plan` (SCRUM-223): plan SaaS con el que arranca la organización --
    lo fija quien da de alta al gimnasio (ver onboarding.py, que ya
    resolvió el PlanSuscripcion elegido antes de llegar aquí). Si se
    omite, queda en el default del modelo ("basico").

    IMPORTANTE -- orden de llamada (bug real corregido en los 5 sitios que
    crean un Gimnasio, ver bitácora técnica): debe llamarse ANTES de
    db.session.add(gimnasio)/flush(), no después. Esta función no toca
    gimnasio.id para nada, solo gimnasio.nombre (ya disponible desde el
    constructor) -- pero sí hace su propio db.session.flush() interno
    (para obtener organizacion.id), y un flush() arrastra TODOS los
    objetos pendientes de la sesión, no solo el que uno tiene en mente. Si
    el gimnasio ya estaba agregado a la sesión en ese momento, ese flush
    intenta insertarlo con id_organizacion todavía en None -- y
    gimnasios.id_organizacion es NOT NULL (migración 017), así que la
    inserción revienta antes de que esta función alcance a resolverlo.
    """
    organizacion = Organizacion(nombre=gimnasio.nombre, activo=True)
    if plan:
        organizacion.plan = plan
    db.session.add(organizacion)
    db.session.flush()  # obtener organizacion.id

    gimnasio.id_organizacion = organizacion.id
    gimnasio.es_sucursal_principal = True
    return organizacion


def crear_asignacion(usuario, gimnasio=None):
    """
    Crea la fila de asignacion_usuario correspondiente a un Usuario recién
    creado, replicando su alcance actual (id_rol + id_gimnasio) para que
    quede sincronizada desde el día uno.

    - usuario.id_gimnasio es None (p. ej. superadmin, o un registro sin
      gimnasio asignado) → asignación sin organización ni sucursal.
    - gimnasio no se pasa pero usuario.id_gimnasio sí → se resuelve la
      organización de ese gimnasio con una consulta.
    - gimnasio se pasa explícitamente (el mismo objeto recién creado en
      esta misma transacción) → se usa directamente, sin consulta extra.

    Debe llamarse DESPUÉS de db.session.flush() sobre el usuario (se
    necesita usuario.id ya asignado).
    """
    if gimnasio is None and usuario.id_gimnasio:
        from app.models.pg.gimnasio import Gimnasio
        gimnasio = Gimnasio.query.get(usuario.id_gimnasio)

    asignacion = AsignacionUsuario(
        id_usuario      = usuario.id,
        id_organizacion = gimnasio.id_organizacion if gimnasio else None,
        id_gimnasio     = gimnasio.id if gimnasio else None,
        id_rol          = usuario.id_rol,
    )
    db.session.add(asignacion)
    return asignacion


# ─────────────────────────────────────────────────────────────────────────────
# SCRUM-213 (HU-42) — resolución real de permisos sobre AsignacionUsuario
# ─────────────────────────────────────────────────────────────────────────────
#
# Por qué esto no podía esperar a HU-45: un Administrador de sucursal (el
# rol que introduce HU-45) puede tener MÁS de una sucursal asignada, y el
# propietario puede reasignarlo en cualquier momento sin forzarlo a volver
# a iniciar sesión. El JWT se firma una sola vez en login a partir de
# usuarios.id_rol + usuarios.id_gimnasio -- es un snapshot fijo, no puede
# representar "este administrador hoy gestiona la sucursal B y C" ni
# reflejar un cambio de asignación hecho después de que el token se firmó.
# AsignacionUsuario, consultada en cada petición, sí puede.
#
# Lo que SÍ sigue igual (decisión deliberada, no una pendiente): Propietario,
# Entrenador, Recepcionista y Miembro siguen teniendo una sola sucursal de
# por vida hoy -- para ellos usuarios.id_rol/id_gimnasio y sus claims del
# JWT (ver require_tenant/require_role en utils/tenant.py y
# utils/security.py) siguen siendo correctos y se quedan sin tocar: no hay
# ninguna razón de negocio para pagar una consulta extra por petición donde
# el alcance nunca puede volverse obsoleto en medio de una sesión. Por eso
# usuario_tiene_alcance() y @requiere() (utils/security.py) son una vía
# NUEVA que se usa donde hace falta -- HU-45 en adelante, cualquier
# operación que pueda tocar una sucursal distinta de la propia del
# usuario -- y no un reemplazo de require_tenant/require_role en los 267
# endpoints que ya los usan (ahí cambiar de mecanismo sin necesidad real
# es el riesgo, no la solución).


def usuario_tiene_alcance(id_usuario, roles_permitidos, id_gimnasio_objetivo=None):
    """
    Responde la pregunta única de HU-42: ¿existe una asignación que le dé
    a `id_usuario` alguno de `roles_permitidos` sobre `id_gimnasio_objetivo`?

    roles_permitidos: iterable de nombres/alias de rol (se normalizan aquí,
        no hace falta que el llamador use el valor canónico exacto).
    id_gimnasio_objetivo:
        - None  → no se exige una sucursal concreta; basta con que exista
          CUALQUIER asignación del usuario con alguno de los roles pedidos
          (uso típico: "¿es Propietario de algo?", sin importar cuál).
        - int   → exige alcance sobre ESA sucursal puntual: una asignación
          directa a ella, o una asignación de organización completa
          (id_gimnasio NULL) cuya organización sea la de esa sucursal.

    No hace nada con superadmin -- eso se resuelve antes, por rama
    explícita (g.is_superadmin), igual que en get_tenant_filter().
    """
    roles_norm = {normalizar_rol(r) for r in roles_permitidos}

    organizacion_objetivo = None
    if id_gimnasio_objetivo is not None:
        gym = Gimnasio.query.get(id_gimnasio_objetivo)
        if gym is None:
            return False
        organizacion_objetivo = gym.id_organizacion

    asignaciones = (
        AsignacionUsuario.query
        .filter_by(id_usuario=id_usuario)
        .join(Rol, AsignacionUsuario.id_rol == Rol.id)
        .all()
    )

    for a in asignaciones:
        if normalizar_rol(a.rol.nombre) not in roles_norm:
            continue
        if id_gimnasio_objetivo is None:
            return True
        if a.id_gimnasio == id_gimnasio_objetivo:
            return True
        if a.id_gimnasio is None and a.id_organizacion == organizacion_objetivo:
            return True

    return False



# ─────────────────────────────────────────────────────────────────────────────
# SCRUM-216 (HU-45) — bitácora genérica
# ─────────────────────────────────────────────────────────────────────────────

def sucursales_de_administrador(id_usuario):
    """
    Lista los ids de Gimnasio sobre los que `id_usuario` tiene una
    asignación como Administrador (HU-45) -- directa a esa sucursal, o
    de organización completa (se expande a todas las sucursales de esa
    organización).

    Usado para poblar el claim "sucursales" del JWT (ver auth/routes.py)
    y para que el selector de sucursal activa del frontend sepa qué
    ofrecer sin una llamada aparte.
    """
    from app.constants.roles import ROL_ADMINISTRADOR

    asignaciones = (
        AsignacionUsuario.query
        .filter_by(id_usuario=id_usuario)
        .join(Rol, AsignacionUsuario.id_rol == Rol.id)
        .filter(Rol.nombre == ROL_ADMINISTRADOR)
        .all()
    )

    ids_gimnasio = set()
    ids_organizacion_completa = set()
    for a in asignaciones:
        if a.id_gimnasio is not None:
            ids_gimnasio.add(a.id_gimnasio)
        elif a.id_organizacion is not None:
            ids_organizacion_completa.add(a.id_organizacion)

    if ids_organizacion_completa:
        extra = (
            Gimnasio.query
            .filter(Gimnasio.id_organizacion.in_(ids_organizacion_completa))
            .with_entities(Gimnasio.id)
            .all()
        )
        ids_gimnasio.update(g.id for g in extra)

    return sorted(ids_gimnasio)


def registrar_evento(id_usuario_actor, accion, *, id_organizacion=None, id_gimnasio=None, detalle=None, ip_origen=None):
    """
    Crea una fila de bitacora_eventos. No hace commit -- se llama dentro de
    la misma transacción del endpoint que ya va a hacer db.session.commit(),
    para que el evento quede o no quede junto con el cambio que describe
    (nunca un evento huérfano de una operación que falló a medio camino).
    """
    evento = BitacoraEvento(
        id_usuario_actor=id_usuario_actor,
        accion=accion,
        id_organizacion=id_organizacion,
        id_gimnasio=id_gimnasio,
        detalle=detalle,
        ip_origen=ip_origen,
    )
    db.session.add(evento)
    return evento
