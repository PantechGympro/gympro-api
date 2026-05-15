"""
models/pg/tipo_clase.py -- Catalogo de tipos/actividades de clase por gimnasio.

Reemplaza la coleccion MongoDB 'tipo_clases' o 'actividades'.
Cada gimnasio define sus propias categorias de clase grupal.
"""
from datetime import datetime, timezone
from app.extensions import db


class TipoClase(db.Model):
    __tablename__ = "tipos_clase"

    id              = db.Column(db.Integer, primary_key=True)
    id_gimnasio     = db.Column(
        db.Integer,
        db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nombre          = db.Column(db.String(100), nullable=False)
    descripcion     = db.Column(db.Text, nullable=True)
    duracion_minutos = db.Column(db.Integer, nullable=True, default=60)
    capacidad_max   = db.Column(db.Integer, nullable=True)  # None = sin limite
    activo          = db.Column(db.Boolean, default=True, nullable=False)
    created_at      = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint("id_gimnasio", "nombre", name="uq_tipo_clase_gym_nombre"),
    )

    gimnasio = db.relationship("Gimnasio", backref=db.backref("tipos_clase", lazy="dynamic"))

    def __repr__(self):
        return f"<TipoClase {self.nombre} gym={self.id_gimnasio}>"

    def to_dict(self):
        return {
            "id":               self.id,
            "id_gimnasio":      self.id_gimnasio,
            "nombre":           self.nombre,
            "descripcion":      self.descripcion,
            "duracion_minutos": self.duracion_minutos,
            "capacidad_max":    self.capacidad_max,
            "activo":           self.activo,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
        }
