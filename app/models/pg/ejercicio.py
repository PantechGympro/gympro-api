"""
models/pg/ejercicio.py -- Catalogo de ejercicios por gimnasio.

Reemplaza la coleccion MongoDB 'ejercicios'.
Tenant-scoped: cada gimnasio mantiene su propia biblioteca de ejercicios.
Los ejercicios se referencian desde las rutinas (coleccion Mongo).
"""
from datetime import datetime, timezone
from app.extensions import db


class Ejercicio(db.Model):
    __tablename__ = "ejercicios"

    id              = db.Column(db.Integer, primary_key=True)
    id_gimnasio     = db.Column(
        db.Integer,
        db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nombre          = db.Column(db.String(150), nullable=False)
    descripcion     = db.Column(db.Text, nullable=True)
    grupo_muscular  = db.Column(db.String(80), nullable=True)   # pecho, espalda, pierna...
    tipo            = db.Column(db.String(50), nullable=True)   # fuerza, cardio, flexibilidad, funcional
    series          = db.Column(db.Integer, nullable=True)
    repeticiones    = db.Column(db.String(20), nullable=True)   # "10-12" o "al fallo"
    duracion_min    = db.Column(db.Integer, nullable=True)      # para cardio (minutos)
    imagenes        = db.Column(db.JSON,    nullable=True)      # lista de base64 JPEG (max 3)
    video           = db.Column(db.Text,    nullable=True)      # base64 video comprimido (max 15 s)
    activo          = db.Column(db.Boolean, default=True, nullable=False)
    created_at      = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint("id_gimnasio", "nombre", name="uq_ejercicio_gym_nombre"),
    )

    gimnasio = db.relationship("Gimnasio", backref=db.backref("ejercicios", lazy="dynamic"))

    def __repr__(self):
        return f"<Ejercicio {self.nombre} [{self.tipo}] gym={self.id_gimnasio}>"

    def to_dict(self):
        return {
            "id":             self.id,
            "id_gimnasio":    self.id_gimnasio,
            "nombre":         self.nombre,
            "descripcion":    self.descripcion,
            "grupo_muscular": self.grupo_muscular,
            "tipo":           self.tipo,
            "series":         self.series,
            "repeticiones":   self.repeticiones,
            "duracion_min":   self.duracion_min,
            "imagenes":       self.imagenes or [],
            "video":          self.video,
            "activo":         self.activo,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
        }
