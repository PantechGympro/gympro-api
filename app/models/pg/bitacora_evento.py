"""
models/pg/bitacora_evento.py — Auditoría genérica (HU-45/SCRUM-216, migración 020).

Una sola tabla para "quién hizo qué, cuándo, sobre qué recurso, desde
dónde" -- en vez de una tabla de bitácora por historia de usuario, porque
el patrón se repite igual en HU-45 (altas de sucursal/administrador,
cambios de asignación) y HU-50 (cambios de cuenta de cobro).

No se expone endpoint de escritura: solo se crea con registrar_evento()
desde el propio código del servidor, nunca a partir de un valor que
mande el cliente -- así "bitácora no editable desde el panel" (criterio 5
de HU-50) es verdad por construcción, no por una regla de permisos que
alguien podría olvidar.
"""
from datetime import datetime, timezone
from app.extensions import db


class BitacoraEvento(db.Model):
    __tablename__ = "bitacora_eventos"

    id               = db.Column(db.Integer, primary_key=True)
    id_usuario_actor = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    accion           = db.Column(db.String(80), nullable=False, index=True)
    id_organizacion  = db.Column(db.Integer, db.ForeignKey("organizaciones.id", ondelete="SET NULL"), nullable=True, index=True)
    id_gimnasio      = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="SET NULL"), nullable=True)
    detalle          = db.Column(db.JSON, nullable=True)
    ip_origen        = db.Column(db.String(45), nullable=True)
    created_at       = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    actor = db.relationship("Usuario")

    def to_dict(self):
        return {
            "id":               self.id,
            "id_usuario_actor": self.id_usuario_actor,
            "actor":            self.actor.nombre if self.actor else None,
            "accion":           self.accion,
            "id_organizacion":  self.id_organizacion,
            "id_gimnasio":      self.id_gimnasio,
            "detalle":          self.detalle,
            "ip_origen":        self.ip_origen,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<BitacoraEvento {self.accion} usuario={self.id_usuario_actor}>"
