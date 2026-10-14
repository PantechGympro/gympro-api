"""019 — Rol "owner_gym" renombrado a "Propietario"

El catálogo de roles usaba el identificador técnico "owner_gym" para el
dueño de gimnasio desde la migración 005 (que a su vez lo renombró desde
"Administrador"). Con HU-45/HU-50 llega un rol NUEVO y distinto --
"Administrador" de sucursal, con alcance limitado a una sucursal -- y
dejar al dueño real con un nombre que ya no describe lo que es (ni es
"Administrador" en el sentido nuevo, ni es una etiqueta en español como
el resto del catálogo) iba a confundir exactamente donde más importa
tener claridad: la matriz de permisos de HU-42/HU-45.

Esta migración solo renombra el VALOR almacenado en `roles.nombre`. No
toca la tabla `usuarios` (sigue apuntando por `id_rol`, un entero, así
que ninguna fila de usuario se mueve) ni ningún blueprint, ruta o
carpeta -- `api/app/routes/owner_gym/`, `/api/owner_gym/*` y
`web/src/pages/owner_gym/` son namespaces de código, no el nombre del
rol, y se quedan igual a propósito.

Compatibilidad con JWT ya emitidos: el refresh token vive hasta 90 días
(ver auth/routes.py, Actividad 09 PR-02), así que puede haber tokens en
circulación con el claim "role": "owner_gym" después de este deploy.
app/constants/roles.py (y su espejo web/src/constants/roles.js) agregan
"owner_gym" como ALIAS permanente de "Propietario" en normalizar_rol(),
así que esos tokens viejos siguen autorizando correctamente sin forzar
un logout masivo -- ver la bitácora técnica para el detalle completo.

Revision ID: 019
Revises: 018
Create Date: 2026-10-14
"""
from alembic import op

revision      = "019"
down_revision = "018"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("UPDATE roles SET nombre = 'Propietario' WHERE nombre = 'owner_gym'")


def downgrade():
    op.execute("UPDATE roles SET nombre = 'owner_gym' WHERE nombre = 'Propietario'")
