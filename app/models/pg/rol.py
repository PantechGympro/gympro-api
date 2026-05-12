"""
models/pg/rol.py — Modelo SQLAlchemy para Roles (PostgreSQL).

Catálogo estático de roles del sistema:
  - Administrador
  - Entrenador
  - Recepcionista
  - Miembro

Relación: Rol 1─N Usuario
"""
from datetime import datetime, timezone
from app.extensions import db


class Rol(db.Model):
    __tablename__ = "roles"

    id         = db.Column(db.Integer, primary_key=True)
    nombre     = db.Column(db.String(50), unique=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relación inversa: rol.usuarios → lista de usuarios con este rol
    usuarios = db.relationship("Usuario", back_populates="rol", lazy="dynamic")

    def __repr__(self):
        return f"<Rol {self.nombre}>"

    def to_dict(self):
        return {"id": self.id, "nombre": self.nombre}
