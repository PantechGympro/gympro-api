"""027 -- Automatizar el reporte ejecutivo del gimnasio por correo

Contexto: pedido explicito del Propietario (corte de caja) -- poder elegir
que tipo de reporte (secciones), con que filtros (comparativa, graficas,
sucursal) y cada cuanto (diaria, semanal, mensual, anual) se genera y envia
por correo al propietario, sin tener que entrar a Reportes y descargarlo a
mano cada vez.

Una sola tabla, 1 fila por configuracion. El job programado (ver
routes/owner_gym/reportes_automaticos.py, tick_reportes_automaticos) la lee
una vez al dia y decide, por fila, si hoy toca generarla -- ultima_ejecucion
es lo que evita reenviar dos veces el mismo periodo.

id_usuario_creador es solo auditoria (quien la configuro, Propietario o
Administrador); el correo SIEMPRE se manda al Propietario de la
organizacion (usuario_propietario_de), sin importar quien la configuro --
asi lo pidio el usuario ("que se envie al correo del propietario").

Riesgo (mismo patron que 017/021/022/023/024/025/026): sin Postgres real
accesible desde el entorno donde se escribio esta migracion -- validar
antes de aplicar contra una base real o de respaldo (docker compose up -d
postgres; alembic upgrade head), y correr el downgrade tambien antes de
confiar en el.

Revision ID: 027
Revises: 026
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision      = "027"
down_revision = "026"
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        "configuracion_reporte_automatico",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "id_gimnasio", sa.Integer(),
            sa.ForeignKey("gimnasios.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        # Quien la configuro -- solo auditoria, nunca el destinatario del
        # correo (ver nota arriba). Nullable: si el usuario se da de baja,
        # la configuracion sigue corriendo.
        sa.Column(
            "id_usuario_creador", sa.Integer(),
            sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("nombre", sa.String(length=120), nullable=False),
        sa.Column("frecuencia", sa.String(length=10), nullable=False),
        # Lista separada por comas de ids de SECCIONES_VALIDAS
        # (reportes_negocio.py) -- mismo formato que ya manda el frontend
        # al generar un reporte manual.
        sa.Column("secciones", sa.String(length=200), nullable=False,
                   server_default="resumen,ingresos,membresias,pos,asistencias,miembros"),
        sa.Column("comparar", sa.Boolean(), nullable=False, default=True, server_default="true"),
        sa.Column("graficas", sa.Boolean(), nullable=False, default=True, server_default="true"),
        sa.Column("activo", sa.Boolean(), nullable=False, default=True, server_default="true"),
        sa.Column("ultima_ejecucion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ultimo_error", sa.String(length=300), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "actualizado_en", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "frecuencia IN ('diaria','semanal','mensual','anual')",
            name="ck_reporte_auto_frecuencia",
        ),
    )


def downgrade():
    op.drop_table("configuracion_reporte_automatico")
