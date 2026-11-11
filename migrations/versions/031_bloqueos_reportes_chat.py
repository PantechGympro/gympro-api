"""031 -- Bloqueo y denuncia de usuarios en el chat miembro <-> entrenador (HU-12)

Soporta el cuestionario de clasificación de contenido de Play Console:
GymPro declara mensajería entre usuarios (HU-12), así que debe ofrecer
bloquear, denunciar y moderar -- declararlo sin tenerlo es motivo de
retiro de la app; no declararlo teniendo chat, también.

Tres cambios:

  1. usuarios.chat_suspendido(_motivo/_en/_por) -- AC3: el Propietario
     puede suspender el acceso al CHAT de un miembro de su personal SIN
     desactivar su cuenta (Usuario.activo no se toca). Vive en `usuarios`
     en vez de una tabla aparte porque es un flag de estado binario con
     metadata de auditoria simple, igual que `activo` o
     `debe_cambiar_password` -- no necesita historial de reaperturas,
     solo el estado vigente (bitacora_eventos ya registra cada cambio).

  2. bloqueos_chat (tabla nueva) -- AC1: bloqueo bidireccional entre dos
     usuarios del mismo gimnasio. Se guarda solo la direccion de quien
     bloqueo a quien (no hace falta una fila por cada sentido), pero la
     regla de negocio corta el chat en AMBOS sentidos sin importar quien
     bloqueo (ver utils/chat_moderacion.py::existe_bloqueo).

  3. reportes_chat (tabla nueva) -- RF-22 / RNF-22: cada denuncia sobre
     un mensaje o un usuario, con motivo catalogado, el mensaje de Mongo
     referenciado (si aplica) y el flujo de resolucion del propietario
     (desestimar / advertir / suspender). FKs a usuarios con
     ondelete=SET NULL: un reporte sobrevive aunque superadmin purgue mas
     tarde la cuenta de alguna de las partes (DELETE /superadmin/usuarios
     ya hace purga total) -- la trazabilidad (RNF-22) no puede depender
     de que esa cuenta siga existiendo.

Revision ID: 031
Revises: 030
"""
from alembic import op
import sqlalchemy as sa

revision      = "031"
down_revision = "030"
branch_labels = None
depends_on    = None


def upgrade():
    # ── 1. Suspension de chat por usuario (independiente de Usuario.activo) ──
    op.add_column("usuarios", sa.Column("chat_suspendido", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("usuarios", sa.Column("chat_suspendido_motivo", sa.Text(), nullable=True))
    op.add_column("usuarios", sa.Column("chat_suspendido_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("usuarios", sa.Column("chat_suspendido_por", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_usuarios_chat_suspendido_por", "usuarios", "usuarios",
        ["chat_suspendido_por"], ["id"], ondelete="SET NULL",
    )

    # ── 2. Bloqueos entre usuarios del chat ──────────────────────────────────
    op.create_table(
        "bloqueos_chat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_gimnasio", sa.Integer(),
                  sa.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("id_usuario_bloqueador", sa.Integer(),
                  sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("id_usuario_bloqueado", sa.Integer(),
                  sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_bloqueos_chat_id_gimnasio", "bloqueos_chat", ["id_gimnasio"])
    op.create_index("ix_bloqueos_chat_bloqueador", "bloqueos_chat", ["id_usuario_bloqueador"])
    op.create_index("ix_bloqueos_chat_bloqueado", "bloqueos_chat", ["id_usuario_bloqueado"])
    op.create_unique_constraint(
        "uq_bloqueos_chat_par", "bloqueos_chat",
        ["id_usuario_bloqueador", "id_usuario_bloqueado"],
    )
    op.create_check_constraint(
        "ck_bloqueos_chat_distintos", "bloqueos_chat",
        "id_usuario_bloqueador <> id_usuario_bloqueado",
    )

    # ── 3. Reportes de chat (denuncia + resolucion del propietario) ──────────
    op.create_table(
        "reportes_chat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("id_gimnasio", sa.Integer(),
                  sa.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("id_usuario_reportante", sa.Integer(),
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id_usuario_reportado", sa.Integer(),
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("id_mensaje_mongo", sa.String(length=24), nullable=True),
        sa.Column("motivo", sa.String(length=30), nullable=False),
        sa.Column("comentario", sa.Text(), nullable=True),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="pendiente"),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resuelto_por", sa.Integer(),
                  sa.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resuelto_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nota_resolucion", sa.Text(), nullable=True),
    )
    op.create_index("ix_reportes_chat_gimnasio_estado", "reportes_chat", ["id_gimnasio", "estado"])
    op.create_index("ix_reportes_chat_reportado", "reportes_chat", ["id_usuario_reportado"])
    op.create_check_constraint(
        "ck_reportes_chat_motivo", "reportes_chat",
        "motivo IN ('acoso', 'lenguaje_inapropiado', 'spam_publicidad', "
        "'suplantacion', 'contenido_inapropiado', 'otro')",
    )
    op.create_check_constraint(
        "ck_reportes_chat_estado", "reportes_chat",
        "estado IN ('pendiente', 'desestimado', 'advertencia', 'suspendido')",
    )


def downgrade():
    op.drop_table("reportes_chat")
    op.drop_table("bloqueos_chat")
    op.drop_constraint("fk_usuarios_chat_suspendido_por", "usuarios", type_="foreignkey")
    op.drop_column("usuarios", "chat_suspendido_por")
    op.drop_column("usuarios", "chat_suspendido_en")
    op.drop_column("usuarios", "chat_suspendido_motivo")
    op.drop_column("usuarios", "chat_suspendido")
