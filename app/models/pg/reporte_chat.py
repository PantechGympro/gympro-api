"""
models/pg/reporte_chat.py — Modelo SQLAlchemy para Reportes de chat (PostgreSQL).

HU-12 / RF-22, RNF-22: denuncia de un mensaje o de un usuario dentro del
chat miembro <-> entrenador, con el flujo de resolución que queda en
manos del Propietario (o un Administrador de sucursal): desestimar,
advertir o suspender el acceso al chat del reportado.

Por qué PostgreSQL y no Mongo (donde vive `mensajes_chat`): el reporte
es un caso de workflow estructurado con estado, resolución y auditoría
-- exactamente lo que ya resuelve el resto del catálogo relacional
(BitacoraEvento, Usuario) -- y necesita FKs reales a `usuarios` para que
RNF-22 (observabilidad) sobreviva incluso si una de las cuentas se borra
después (ondelete=SET NULL en vez de CASCADE: el reporte no desaparece
con la cuenta).

`id_mensaje_mongo` es un string suelto (no FK) a propósito: Mongo no
participa en integridad referencial de Postgres. Queda nullable porque
el ticket permite denunciar a un usuario sin señalar un mensaje puntual
("reportar un mensaje O a un usuario").
"""
from datetime import datetime, timezone
from app.extensions import db

# Catálogo cerrado de motivos -- el mismo que valida la migración 031 con
# un CHECK a nivel de base de datos (ck_reportes_chat_motivo). Vive aquí,
# no repetido en cada endpoint, para que frontend y backend compartan una
# sola fuente si en algún momento se expone vía un endpoint de catálogos.
MOTIVOS_REPORTE_CHAT = (
    "acoso",
    "lenguaje_inapropiado",
    "spam_publicidad",
    "suplantacion",
    "contenido_inapropiado",
    "otro",
)

# pendiente -> (desestimado | advertencia | suspendido). Transición única
# y sin vuelta atrás desde el endpoint de resolución (routes/owner_gym/
# moderacion_chat.py) -- revertir una suspensión es una acción aparte
# (reactivar_chat), no una reapertura del reporte.
ESTADOS_REPORTE_CHAT = ("pendiente", "desestimado", "advertencia", "suspendido")


class ReporteChat(db.Model):
    __tablename__ = "reportes_chat"

    id                     = db.Column(db.Integer, primary_key=True)
    id_gimnasio            = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                                        nullable=False, index=True)
    id_usuario_reportante  = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"),
                                        nullable=True)
    id_usuario_reportado   = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"),
                                        nullable=True, index=True)
    id_mensaje_mongo       = db.Column(db.String(24), nullable=True)
    motivo                 = db.Column(db.String(30), nullable=False)
    comentario             = db.Column(db.Text, nullable=True)
    estado                 = db.Column(db.String(20), nullable=False, default="pendiente",
                                        server_default="pendiente", index=True)
    creado_en              = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resuelto_por           = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"),
                                        nullable=True)
    resuelto_en            = db.Column(db.DateTime(timezone=True), nullable=True)
    nota_resolucion        = db.Column(db.Text, nullable=True)

    reportante = db.relationship("Usuario", foreign_keys=[id_usuario_reportante])
    reportado  = db.relationship("Usuario", foreign_keys=[id_usuario_reportado])
    resolutor  = db.relationship("Usuario", foreign_keys=[resuelto_por])

    def __repr__(self):
        return f"<ReporteChat {self.id} reportado={self.id_usuario_reportado} estado={self.estado}>"

    def to_dict(self):
        return {
            "id":                     self.id,
            "id_gimnasio":            self.id_gimnasio,
            "id_usuario_reportante":  self.id_usuario_reportante,
            "nombre_reportante":      self.reportante.nombre if self.reportante else None,
            "id_usuario_reportado":   self.id_usuario_reportado,
            "nombre_reportado":       self.reportado.nombre if self.reportado else None,
            "rol_reportado":          self.reportado.rol.nombre if (self.reportado and self.reportado.rol) else None,
            "id_mensaje_mongo":       self.id_mensaje_mongo,
            "motivo":                 self.motivo,
            "comentario":             self.comentario,
            "estado":                 self.estado,
            "creado_en":              self.creado_en.isoformat() if self.creado_en else None,
            "resuelto_por":           self.resuelto_por,
            "nombre_resolutor":       self.resolutor.nombre if self.resolutor else None,
            "resuelto_en":            self.resuelto_en.isoformat() if self.resuelto_en else None,
            "nota_resolucion":        self.nota_resolucion,
        }
