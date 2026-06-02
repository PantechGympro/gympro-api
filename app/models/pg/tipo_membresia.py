"""
models/pg/tipo_membresia.py -- Catalogo de tipos de membresia por gimnasio.

Reemplaza la coleccion MongoDB 'membresias'. Al ser tenant-scoped
(id_gimnasio FK) cada gimnasio mantiene su propio catalogo de planes.

La relacion MiembroMembresia -> TipoMembresia se resolvera en un sprint
posterior cuando ese modelo sea migrado a PG.
"""
from datetime import datetime, timezone
from app.extensions import db


class TipoMembresia(db.Model):
    __tablename__ = "tipos_membresia"

    id              = db.Column(db.Integer, primary_key=True)
    id_gimnasio     = db.Column(
        db.Integer,
        db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nombre          = db.Column(db.String(100), nullable=False)
    # estandar | promocion
    tipo            = db.Column(db.String(20), nullable=False, server_default="estandar")
    duracion_meses  = db.Column(db.Integer, nullable=False, default=1)
    precio          = db.Column(db.Numeric(10, 2), nullable=False)
    descripcion     = db.Column(db.Text, nullable=True)
    activo          = db.Column(db.Boolean, default=True, nullable=False)
    created_at      = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Constraint: nombre unico por gimnasio
    __table_args__ = (
        db.UniqueConstraint("id_gimnasio", "nombre", name="uq_tipo_membresia_gym_nombre"),
    )

    gimnasio = db.relationship("Gimnasio", backref=db.backref("tipos_membresia", lazy="dynamic"))

    def __repr__(self):
        return f"<TipoMembresia {self.nombre} gym={self.id_gimnasio}>"

    def to_dict(self):
        return {
            "id":             self.id,
            "id_gimnasio":    self.id_gimnasio,
            "nombre":         self.nombre,
            "tipo":           self.tipo or "estandar",
            "duracion_meses": self.duracion_meses,
            "precio":         float(self.precio),
            "descripcion":    self.descripcion,
            "activo":         self.activo,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
        }
