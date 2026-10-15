"""025 -- Forzar cambio de contraseña en el primer login (SCRUM-224)

Contexto: primer_login (migracion anterior) ya fuerza al Propietario a
completar el wizard de onboarding, pero esa bandera no tiene nada que ver
con la SEGURIDAD de la contraseña -- la elige el propio Propietario al
registrarse. Las cuentas que SI tienen este problema son las que crea un
Propietario/Administrador para otra persona (miembro, entrenador,
recepcionista, administrador de otra sucursal): hasta esta migracion, esa
contraseña la escribia (o la dejaba vacia, cayendo a un fallback fijo
"gym123") quien da de alta la cuenta -- ver crear_miembro() /
crear_staff(), ambos reescritos en el mismo cambio que esta migracion para
generar la contraseña y mandarla por correo en vez de capturarla en el
formulario.

debe_cambiar_password es una bandera DISTINTA de primer_login a proposito:
mezclarlas haria que, por ejemplo, un Administrador (que no pasa por el
wizard de onboarding del Propietario) quedara sin forma de disparar el
cambio de contraseña, o que el Propietario -- que SI elige su propia
contraseña al registrarse -- tuviera que cambiarla otra vez sin motivo.

Backfill: todas las filas existentes nacen en False. Ya iniciaron sesion
alguna vez con la contraseña que tienen (propia o ya cambiada), asi que no
hay nada que forzar retroactivamente; la bandera solo se activa a partir de
ahora, cuando una cuenta nueva recibe una contraseña generada o un
Propietario/Administrador le resetea la contraseña a otra persona (ver
actualizar_staff()).

Riesgo (mismo patron que 017/021/022/023/024): sin Postgres real accesible
desde el entorno donde se escribio esta migracion -- validar antes de
aplicar contra una base real o de respaldo (docker compose up -d postgres;
alembic upgrade head), y correr el downgrade tambien antes de confiar en
el.

Revision ID: 025
Revises: 024
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision      = "025"
down_revision = "024"
branch_labels = None
depends_on    = None


def upgrade():
    op.add_column(
        "usuarios",
        sa.Column(
            "debe_cambiar_password", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("usuarios", "debe_cambiar_password")
