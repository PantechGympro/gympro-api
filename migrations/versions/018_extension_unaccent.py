"""018 — Extensión unaccent de PostgreSQL (SCRUM-105)

Habilita la extensión `unaccent` (contrib oficial de PostgreSQL, incluida
en la imagen `postgres` que usa el proyecto) para que las comparaciones
de texto del lado del servidor puedan ignorar acentos además de
mayúsculas/minúsculas, SIN modificar nunca el valor guardado -- decisión
de producto confirmada con el usuario: "normalizar solo para comparar",
nunca al guardar.

Ver api/app/utils/busqueda.py -- el único punto de normalización que usa
esta extensión (normalizar_columna()).

Riesgo (igual que en migraciones anteriores de este entorno): no hay
Postgres accesible desde el sandbox donde se escribió esta migración.
CREATE EXTENSION unaccent no requiere privilegios de superusuario en
PostgreSQL 13+ (la imagen oficial del proyecto es más nueva) siempre que
la extensión esté instalada en el servidor -- viene en el paquete
`postgresql-contrib`, presente en la imagen oficial `postgres:*` que usa
`docker-compose.yml`/`docker-compose.server.yml`. Si el Postgres real del
VPS no la tiene instalada, esta migración fallará explícitamente al
correrla (no en silencio) y habrá que instalar `postgresql-contrib` en
esa imagen antes de reintentar.

Revision ID: 018
Revises: 017
Create Date: 2026-10-13
"""
from alembic import op

revision      = "018"
down_revision = "017"
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade():
    # Nada más que las queries de la aplicación (ver busqueda.py) depende
    # de esta extensión -- ningún índice ni vista la referencia -- así que
    # es seguro quitarla al revertir.
    op.execute("DROP EXTENSION IF EXISTS unaccent")
