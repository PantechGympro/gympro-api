"""
models/pg/bloqueo_chat.py — Modelo SQLAlchemy para Bloqueos de chat (PostgreSQL).

HU-12 / RF-16: un miembro puede bloquear a un entrenador (o viceversa) en
el chat 1:1. Se guarda una sola fila por quién bloqueó a quién -- la
regla de "corta el chat en ambos sentidos" (AC1) no vive aquí, sino en
utils/chat_moderacion.py::existe_bloqueo(), que consulta esta tabla en
ambas direcciones. Mantener esa regla fuera del modelo evita que alguien
la reimplemente distinto en un segundo call site.

Por qué PostgreSQL y no la misma colección Mongo que `mensajes_chat`:
un bloqueo es una relación estructurada entre dos usuarios (no un
documento de conversación) que se consulta en el camino crítico de cada
GET/POST de chat -- encaja con el resto del catálogo relacional
(Usuario, Gimnasio) y permite el UNIQUE/CHECK que la migración 031 ya
impone a nivel de base de datos, en vez de confiar solo en el código de
la aplicación para no duplicar o auto-bloquear.
"""
from datetime import datetime, timezone
from app.extensions import db


class BloqueoChat(db.Model):
    __tablename__ = "bloqueos_chat"

    id                    = db.Column(db.Integer, primary_key=True)
    id_gimnasio           = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    id_usuario_bloqueador = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    id_usuario_bloqueado  = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"),
                                       nullable=False, index=True)
    creado_en             = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    bloqueador = db.relationship("Usuario", foreign_keys=[id_usuario_bloqueador])
    bloqueado  = db.relationship("Usuario", foreign_keys=[id_usuario_bloqueado])

    __table_args__ = (
        db.UniqueConstraint("id_usuario_bloqueador", "id_usuario_bloqueado", name="uq_bloqueos_chat_par"),
        db.CheckConstraint("id_usuario_bloqueador <> id_usuario_bloqueado", name="ck_bloqueos_chat_distintos"),
    )

    def __repr__(self):
        return f"<BloqueoChat {self.id_usuario_bloqueador} -> {self.id_usuario_bloqueado}>"

    def to_dict(self):
        return {
            "id":                    self.id,
            "id_usuario_bloqueado":  self.id_usuario_bloqueado,
            "nombre_bloqueado":      self.bloqueado.nombre if self.bloqueado else None,
            "creado_en":             self.creado_en.isoformat() if self.creado_en else None,
        }
