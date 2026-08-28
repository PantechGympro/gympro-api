"""
models/pg/usuario.py — Modelo SQLAlchemy para Usuarios (PostgreSQL).

Reemplaza progresivamente el modelo Mongo User (app.models.user).
Durante Sprint 2 el auth opera en modo dual:
  1. Busca el usuario en PostgreSQL (nuevos registros).
  2. Si no existe, busca en MongoDB (usuarios legacy).

La migración completa de usuarios Mongo → PG se realiza mediante el
script seeds/migrate_users.py cuando el equipo decida cortar el fallback.

Relaciones:
  Usuario N─1 Rol
  Usuario N─1 Gimnasio
"""
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id            = db.Column(db.Integer, primary_key=True)
    nombre        = db.Column(db.String(150), nullable=False)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    activo        = db.Column(db.Boolean, default=True, nullable=False)
    created_at    = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # FK hacia Rol (roles.id)
    id_rol = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)
    rol    = db.relationship("Rol", back_populates="usuarios")

    # FK hacia Gimnasio (gimnasios.id)
    # nullable=True durante Sprint 2 para compatibilidad con usuarios sin gimnasio asignado
    id_gimnasio = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="SET NULL"), nullable=True, index=True)
    gimnasio    = db.relationship("Gimnasio", back_populates="usuarios")

    # Onboarding: True = primer login, debe completar configuración del gimnasio
    primer_login = db.Column(db.Boolean, default=False, nullable=False, server_default="false")

    # Foto de perfil almacenada como data URL base64 (nullable)
    foto_perfil  = db.Column(db.Text, nullable=True)

    # Teléfono de la PERSONA, distinto del teléfono de contacto del gimnasio.
    telefono     = db.Column(db.String(30), nullable=True)

    # ── Autenticación ─────────────────────────────────────────────────────────

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # ── Serialización ─────────────────────────────────────────────────────────

    def __repr__(self):
        return f"<Usuario {self.email}>"

    def to_dict(self):
        fp = self.foto_perfil
        return {
            "id":          self.id,
            "nombre":      self.nombre,
            "email":       self.email,
            "activo":      self.activo,
            "id_rol":      self.id_rol,
            "rol":         self.rol.nombre if self.rol else None,
            "id_gimnasio": self.id_gimnasio,
            "telefono":    self.telefono,
            "created_at":  self.created_at.isoformat() if self.created_at else None,
            # Solo exponer si es base64 valida
            "foto_perfil": fp if (fp and fp.startswith("data:image")) else None,
        }
