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
     superadmin/usuarios.py.
  3. "administrador"/"admin" como sinónimo informal de "owner_gym" en
     comentarios y vistas antiguas de antes de la migración 020 -- desde
     HU-45/SCRUM-216 SÍ existe un rol "Administrador" independiente en la
     tabla `roles` (alcance de sucursal, no de organización; lo asigna el
     Propietario), y estos alias ya apuntan a ese rol nuevo, no a
     Propietario.

Migración 019 (2026-10-14): "owner_gym" se renombró a "Propietario" en
la tabla `roles` -- HU-45/HU-50 introducen un rol NUEVO y distinto,
"Administrador" de sucursal (alcance limitado, lo asigna el propietario),
y mantener al dueño real con el nombre "owner_gym" (ni rol nuevo ni
etiqueta en español) iba a confundir justo donde más importa claridad.
"owner_gym" queda como ALIAS permanente de ROL_PROPIETARIO en vez de
retirarse: hay refresh tokens de hasta 90 días en circulación (Actividad
09 PR-02) que pueden traer el claim viejo, y el alias los sigue
resolviendo sin forzar un logout masivo en el despliegue.

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
ROL_PROPIETARIO   = "Propietario"   # antes "owner_gym" (ver migración 019)
ROL_ADMINISTRADOR = "Administrador" # NUEVO (migración 020, HU-45) -- alcance de
                                     # sucursal, no de organización; ver utils/alcance.py
ROL_ENTRENADOR    = "Entrenador"
ROL_RECEPCIONISTA = "Recepcionista"
ROL_MIEMBRO       = "Miembro"

ROLES_CANONICOS = (
    ROL_SUPERADMIN, ROL_PROPIETARIO, ROL_ADMINISTRADOR,
    ROL_ENTRENADOR, ROL_RECEPCIONISTA, ROL_MIEMBRO,
)

# Rótulo de presentación por rol canónico.
ROL_LABEL = {
    ROL_SUPERADMIN:    "Superadmin",
    ROL_PROPIETARIO:   "Propietario",
    ROL_ADMINISTRADOR: "Administrador",
    ROL_ENTRENADOR:    "Entrenador",
    ROL_RECEPCIONISTA: "Recepcionista",
    ROL_MIEMBRO:       "Miembro",
}

# ── Alias ─────────────────────────────────────────────────────────────────
# Variantes legadas/informales → identificador canónico. Todas las claves
# van en minúsculas porque normalizar_rol() minuscula antes de buscar.
#
# Migración 020 (HU-45): "Administrador" ya es un rol real y distinto de
# Propietario, así que "administrador"/"admin" dejan de ser sinónimos de
# Propietario -- apuntan al rol nuevo. "owner" sigue siendo Propietario sin
# ambigüedad (nadie dice "owner" para referirse al admin de una sucursal).
#
# "owner_gym" se queda como alias PERMANENTE (no legado a limpiar):
# cualquier JWT emitido antes de la migración 019 todavía trae ese
# valor en el claim "role" hasta que expire (refresh token, 90 días).
_ALIASES = {
    "superadmin":    ROL_SUPERADMIN,
    "owner_gym":     ROL_PROPIETARIO,
    "propietario":   ROL_PROPIETARIO,
    "owner":         ROL_PROPIETARIO,
    "administrador": ROL_ADMINISTRADOR,
    "admin":         ROL_ADMINISTRADOR,
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
