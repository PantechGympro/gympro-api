"""017 — Modelo de alcance y migración de usuarios (HU-41 / SCRUM-212)

Introduce un nivel de agrupación por encima de Gimnasio ("sucursal"):

  organizaciones      nueva tabla, tenant anchor de nivel superior. Hoy
                      cada Gimnasio existente recibe su propia
                      Organizacion 1:1 (mismo id, por diseño -- ver más
                      abajo), pero una Organizacion podrá agrupar varias
                      sucursales cuando HU-42 habilite ese flujo.

  gimnasios           +id_organizacion (NOT NULL, FK → organizaciones.id)
                      +es_sucursal_principal (bandera para cuando una
                      organización tenga más de una sucursal)

  asignacion_usuario  nueva tabla: alcance de un usuario, scoped a una
                      organización y, opcionalmente, a una sucursal
                      específica. Combinaciones válidas:
                        (organización, sucursal)  → una sucursal puntual
                        (organización, NULL)      → toda la organización
                        (NULL, NULL)              → superadmin de plataforma
                        (NULL, sucursal)          → INVÁLIDA
                                                     (ck_asignacion_alcance_valido)

IMPORTANTE -- alcance de esta migración (ver bitácora técnica para más
detalle): el sistema debe comportarse EXACTAMENTE igual que antes. El
permiso real de cada usuario sigue resolviéndose por
usuarios.id_rol + usuarios.id_gimnasio (ver app/utils/tenant.py);
asignacion_usuario queda poblada y sincronizada desde ahora (ver
app/utils/alcance.py), pero todavía no la lee ningún endpoint para
decidir permisos -- eso es HU-42.

Backfill determinista (sin depender del orden de INSERT...SELECT):
  - organizaciones reutiliza el mismo id de cada gimnasio existente (1:1),
    así que gimnasios.id_organizacion se completa con una correlación
    directa por id, sin JOIN.
  - asignacion_usuario se llena con un LEFT JOIN (no INNER): los usuarios
    sin gimnasio hoy (NULL) -- es decir, superadmin -- quedan con
    id_organizacion = NULL e id_gimnasio = NULL, que es exactamente la
    combinación reservada para ellos (criterio 4 del ticket), satisfaciendo
    a la vez que TODO usuario existente termine con exactamente una fila
    (criterio 2).

Riesgo (igual que en migraciones anteriores de este entorno): no hay un
Postgres real accesible desde el sandbox donde se escribió esta
migración, así que el upgrade/downgrade se validó por lectura cuidadosa
contra el estilo de 002/005/011, no ejecutándola. Debe correrse (y su
downgrade probarse) contra una base real o de respaldo antes de
aplicarla en producción.

Revision ID: 017
Revises: 016
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa

revision      = "017"
down_revision = "016"
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. organizaciones ───────────────────────────────────────────────
    op.create_table(
        "organizaciones",
        sa.Column("id",         sa.Integer(),               nullable=False),
        sa.Column("nombre",     sa.String(150),              nullable=False),
        sa.Column("activo",     sa.Boolean(),                nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True),  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Backfill 1:1 -- cada gimnasio existente obtiene su propia
    # organización, reutilizando el mismo id (evita depender del orden de
    # un INSERT...SELECT para correlacionar filas despues).
    op.execute("""
        INSERT INTO organizaciones (id, nombre, activo, created_at)
        SELECT id, nombre, activo, now() FROM gimnasios
    """)
    # El id se insertó a mano -- resincronizar la secuencia para que el
    # próximo INSERT sin id explícito no choque con uno ya existente.
    op.execute("""
        SELECT setval(
            pg_get_serial_sequence('organizaciones', 'id'),
            COALESCE((SELECT MAX(id) FROM organizaciones), 1)
        )
    """)

    # ── 2. gimnasios.id_organizacion / es_sucursal_principal ────────────
    op.add_column("gimnasios", sa.Column("id_organizacion", sa.Integer(), nullable=True))
    op.add_column(
        "gimnasios",
        sa.Column("es_sucursal_principal", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE gimnasios SET id_organizacion = id")
    op.alter_column("gimnasios", "id_organizacion", nullable=False)
    op.create_foreign_key(
        "fk_gimnasios_id_organizacion", "gimnasios", "organizaciones",
        ["id_organizacion"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_gimnasios_id_organizacion", "gimnasios", ["id_organizacion"])

    # ── 3. asignacion_usuario ────────────────────────────────────────────
    op.create_table(
        "asignacion_usuario",
        sa.Column("id",              sa.Integer(),               nullable=False),
        sa.Column("id_usuario",      sa.Integer(),               nullable=False),
        sa.Column("id_organizacion", sa.Integer(),               nullable=True),
        sa.Column("id_gimnasio",     sa.Integer(),               nullable=True),
        sa.Column("id_rol",          sa.Integer(),               nullable=False),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["id_usuario"],      ["usuarios.id"],       ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_organizacion"], ["organizaciones.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_gimnasio"],     ["gimnasios.id"],      ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["id_rol"],          ["roles.id"],          ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id_usuario", "id_gimnasio", name="uq_asignacion_usuario_gimnasio"),
        sa.CheckConstraint(
            "id_organizacion IS NOT NULL OR id_gimnasio IS NULL",
            name="ck_asignacion_alcance_valido",
        ),
    )
    op.create_index("ix_asignacion_usuario_id_usuario",      "asignacion_usuario", ["id_usuario"])
    op.create_index("ix_asignacion_usuario_id_organizacion", "asignacion_usuario", ["id_organizacion"])
    op.create_index("ix_asignacion_usuario_id_gimnasio",     "asignacion_usuario", ["id_gimnasio"])

    # Backfill: el LEFT JOIN garantiza una fila por usuario incluso para
    # los que hoy no tienen gimnasio (superadmin) -- quedan con
    # id_organizacion = NULL e id_gimnasio = NULL, la combinación reservada.
    op.execute("""
        INSERT INTO asignacion_usuario (id_usuario, id_organizacion, id_gimnasio, id_rol, created_at)
        SELECT u.id, g.id_organizacion, u.id_gimnasio, u.id_rol, now()
        FROM usuarios u
        LEFT JOIN gimnasios g ON g.id = u.id_gimnasio
    """)


def downgrade():
    op.drop_index("ix_asignacion_usuario_id_gimnasio",     table_name="asignacion_usuario")
    op.drop_index("ix_asignacion_usuario_id_organizacion", table_name="asignacion_usuario")
    op.drop_index("ix_asignacion_usuario_id_usuario",      table_name="asignacion_usuario")
    op.drop_table("asignacion_usuario")

    op.drop_index("ix_gimnasios_id_organizacion", table_name="gimnasios")
    op.drop_constraint("fk_gimnasios_id_organizacion", "gimnasios", type_="foreignkey")
    op.drop_column("gimnasios", "es_sucursal_principal")
    op.drop_column("gimnasios", "id_organizacion")

    op.drop_table("organizaciones")
