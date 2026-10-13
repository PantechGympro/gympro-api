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
    db.session.add(nuevo_gym)
    db.session.flush()                        # necesita nuevo_gym.id
    crear_organizacion_para_gimnasio(nuevo_gym)

    nuevo_admin = Usuario(..., id_gimnasio=nuevo_gym.id)
    db.session.add(nuevo_admin)
    db.session.flush()                        # necesita nuevo_admin.id
    crear_asignacion(nuevo_admin, gimnasio=nuevo_gym)

    db.session.commit()
"""
from app.extensions import db
from app.models.pg.organizacion import Organizacion
from app.models.pg.asignacion_usuario import AsignacionUsuario


def crear_organizacion_para_gimnasio(gimnasio) -> Organizacion:
    """
    Crea la Organizacion 1:1 de un Gimnasio recién creado y la deja
    asignada en gimnasio.id_organizacion (más es_sucursal_principal=True).

    Debe llamarse DESPUÉS de db.session.flush() sobre el gimnasio (se
    necesita gimnasio.id ya asignado) y ANTES del commit.
    """
    organizacion = Organizacion(nombre=gimnasio.nombre, activo=True)
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
