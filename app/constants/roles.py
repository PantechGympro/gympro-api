"""
constants/roles.py — Catálogo único y canónico de roles del sistema (SCRUM-107).

Por qué existe
---------------
El código traía al menos tres representaciones distintas del mismo rol
convivientes sin un catálogo central:

  1. Identificador técnico en minúsculas/snake_case ("superadmin",
     "owner_gym"), usado por ROL_BADGE/ROUTE_FOR_ROLE en el frontend y por
     las comparaciones con .toLowerCase().
  2. Capitalizado ("Entrenador", "Recepcionista", "Miembro") — así es como
     seed_pg.py puebla la tabla `roles` de PostgreSQL, y por lo tanto el
     valor EXACTO que termina en el claim "role" del JWT y en la columna
     Rol.nombre contra la que compara `Rol.nombre == rol_param` en
     superadmin/usuarios.py. Normalizar estos tres a minúsculas en la base
     de datos exigiría una migración y tocar cada comparación exacta del
     backend; se documenta aquí como la convención real en vez de
     reescribirla a ciegas en un solo sprint.
  3. "administrador"/"admin" como sinónimo informal de "owner_gym" en
     comentarios y vistas antiguas — no existe un rol "administrador"
     independiente en la tabla `roles` (ver seed_pg.py: solo
     superadmin/owner_gym/Entrenador/Recepcionista/Miembro). Se trata aquí
     como alias, no como rol distinto.

Qué resuelve este módulo
------------------------
Un único lugar que documenta el identificador canónico EXACTO de cada rol
(el que realmente viaja en el JWT y en la base), más un mapa de alias para
aceptar las variantes legadas sin que cada vista tenga que mantener su
propia lista. `normalizar_rol()` es la función que cualquier comparación
de rol debería usar en vez de comparar cadenas crudas.
"""

# ── Catálogo canónico ─────────────────────────────────────────────────────
# El valor es EXACTAMENTE el que puebla seed_pg.py en la tabla `roles` y el
# que firma auth/routes.py en el claim "role" del JWT. No es una elección de
# estilo: es el dato real contra el que compara `Rol.nombre == rol_param`.
ROL_SUPERADMIN    = "superadmin"
ROL_OWNER_GYM     = "owner_gym"
ROL_ENTRENADOR    = "Entrenador"
ROL_RECEPCIONISTA = "Recepcionista"
ROL_MIEMBRO       = "Miembro"

ROLES_CANONICOS = (
    ROL_SUPERADMIN, ROL_OWNER_GYM, ROL_ENTRENADOR, ROL_RECEPCIONISTA, ROL_MIEMBRO,
)

# Rótulo de presentación por rol canónico.
ROL_LABEL = {
    ROL_SUPERADMIN:    "Superadmin",
    ROL_OWNER_GYM:     "Owner Gym",
    ROL_ENTRENADOR:    "Entrenador",
    ROL_RECEPCIONISTA: "Recepcionista",
    ROL_MIEMBRO:       "Miembro",
}

# ── Alias ─────────────────────────────────────────────────────────────────
# Variantes legadas/informales → identificador canónico. Todas las claves
# van en minúsculas porque normalizar_rol() minuscula antes de buscar.
#
# "administrador"/"admin"/"owner" son sinónimos de owner_gym, no un rol
# aparte: responde la pregunta abierta del ticket.
_ALIASES = {
    "superadmin":    ROL_SUPERADMIN,
    "owner_gym":     ROL_OWNER_GYM,
    "administrador": ROL_OWNER_GYM,
    "admin":         ROL_OWNER_GYM,
    "owner":         ROL_OWNER_GYM,
    "entrenador":    ROL_ENTRENADOR,
    "trainer":       ROL_ENTRENADOR,
    "recepcionista": ROL_RECEPCIONISTA,
    "receptionist":  ROL_RECEPCIONISTA,
    "miembro":       ROL_MIEMBRO,
    "user":          ROL_MIEMBRO,
    "member":        ROL_MIEMBRO,
}


def normalizar_rol(valor):
    """
    Resuelve cualquier variante conocida de un rol (alias, mayúsculas,
    minúsculas) a su identificador canónico.

    Si `valor` no coincide con ningún alias conocido, se devuelve tal cual
    vino (sin inventar un canónico para algo que no se reconoce). Úsese
    siempre que se compare un rol en vez de comparar cadenas crudas.
    """
    clave = str(valor or "").strip().lower()
    return _ALIASES.get(clave, valor)


def roles_equivalentes(a, b):
    """True si `a` y `b` se refieren al mismo rol, sin importar mayúsculas o alias."""
    return normalizar_rol(a) == normalizar_rol(b)
