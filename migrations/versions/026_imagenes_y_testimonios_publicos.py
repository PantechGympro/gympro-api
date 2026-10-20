"""026 -- Fotos publicas del gimnasio y testimonios (pagina comercial)

Contexto: la nueva pagina comercial de GymPro (landing publica, sin login)
necesita mostrar un carrusel de "gimnasios que confian en nosotros" y una
seccion de testimonios. Para no fabricar clientes o citas falsas en el
marketing del propio producto, ambas secciones se alimentan de datos
reales que el propio Propietario/Administrador de cada gimnasio sube
desde el panel (ver api/app/routes/owner_gym/perfil_publico.py).

Dos tablas nuevas, independientes entre si:

  gimnasio_imagenes -- fotos del local subidas por el gimnasio. Se guardan
  como archivos en disco (igual que fotos de perfil/certificados, ver
  user_profile.py y trainer_routes.py), no como base64 en la fila como el
  logo (Gimnasio.logo): aqui puede haber varias por gimnasio y pesan mas,
  meter eso en Postgres como texto infla la tabla sin necesidad.

  testimonios -- texto corto + calificacion opcional (1-5), con
  id_usuario nullable (quien lo escribio puede dejar de existir sin que el
  testimonio desaparezca -- ON DELETE SET NULL; el testimonio en si
  pertenece al GIMNASIO, no a la persona).

Ambas con ON DELETE CASCADE desde gimnasios: si se borra un gimnasio (hoy
no hay endpoint que lo haga, pero por si acaso) sus fotos/testimonios
publicos no deben quedar huerfanos.

Riesgo (mismo patron que 017/021/022/023/024/025): sin Postgres real
accesible desde el entorno donde se escribio esta migracion -- validar
antes de aplicar contra una base real o de respaldo (docker compose up -d
postgres; alembic upgrade head), y correr el downgrade tambien antes de
confiar en el.

Revision ID: 026
Revises: 025
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision      = "026"
down_revision = "025"
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        "gimnasio_imagenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "id_gimnasio", sa.Integer(),
            sa.ForeignKey("gimnasios.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("archivo", sa.String(length=255), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "testimonios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "id_gimnasio", sa.Integer(),
            sa.ForeignKey("gimnasios.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "id_usuario", sa.Integer(),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("autor_nombre", sa.String(length=150), nullable=False),
        sa.Column("autor_cargo", sa.String(length=150), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("calificacion", sa.Integer(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "calificacion IS NULL OR (calificacion >= 1 AND calificacion <= 5)",
            name="ck_testimonios_calificacion_rango",
        ),
    )


def downgrade():
    op.drop_table("testimonios")
    op.drop_table("gimnasio_imagenes")
