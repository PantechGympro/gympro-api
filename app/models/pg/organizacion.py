"""
models/pg/organizacion.py — Modelo SQLAlchemy para Organizaciones (PostgreSQL).

HU-41 (SCRUM-212): introduce un nivel de agrupación por encima de Gimnasio
("sucursal"). Hoy, con la migración 017, cada gimnasio existente recibe su
propia Organizacion 1:1 (mismo id) -- el modelo no cambia ningún
comportamiento actual, es la base sobre la que HU-42 construirá permisos
multi-sucursal (una Organizacion podrá agrupar varias sucursales).

Relación:
  Organizacion 1─N Gimnasio       (gimnasios.id_organizacion)
  Organizacion 1─N AsignacionUsuario
"""
from datetime import datetime, timezone
from app.extensions import db


class Organizacion(db.Model):
    __tablename__ = "organizaciones"

    id         = db.Column(db.Integer, primary_key=True)
    nombre     = db.Column(db.String(150), nullable=False)
    activo     = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relación inversa: organizacion.gimnasios → sucursales de esta organización
    gimnasios = db.relationship("Gimnasio", back_populates="organizacion", lazy="dynamic")

    def __repr__(self):
        return f"<Organizacion {self.nombre}>"

    def to_dict(self):
        return {
            "id":         self.id,
            "nombre":     self.nombre,
            "activo":     self.activo,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
