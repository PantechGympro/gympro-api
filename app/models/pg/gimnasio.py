"""
models/pg/gimnasio.py — Modelo SQLAlchemy para Gimnasios (PostgreSQL).

El Gimnasio es el tenant anchor de la arquitectura multi-tenant.
Todo dato operacional en MongoDB y financiero en PostgreSQL referencia
a un id_gimnasio que se propaga desde el JWT via tenant middleware.

Planes:
  basico      → hasta 50 miembros activos
  pro         → hasta 200 miembros + analytics
  enterprise  → ilimitado + SLA + soporte dedicado
"""
import enum
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


class PlanEnum(enum.Enum):
    basico     = "basico"
    pro        = "pro"
    enterprise = "enterprise"


# create_type=False: SQLAlchemy no intenta CREATE TYPE en db.create_all().
# El tipo lo gestiona Alembic en la migración 001. Esto hace create_all idempotente.
_plan_enum_type = PGEnum("basico", "pro", "enterprise", name="plan_enum", create_type=False)


class Gimnasio(db.Model):
    __tablename__ = "gimnasios"

    id                 = db.Column(db.Integer, primary_key=True)
    nombre             = db.Column(db.String(150), nullable=False)
    plan               = db.Column(
        _plan_enum_type,
        default="basico",
        nullable=False,
    )
    activo             = db.Column(db.Boolean, default=True, nullable=False)
    email_contacto     = db.Column(db.String(255))
    telefono           = db.Column(db.String(20))
    # Stripe Customer ID — se completa en Sprint 3 al integrar billing
    stripe_customer_id = db.Column(db.String(100))
    created_at         = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relación inversa: gimnasio.usuarios → lista de usuarios del gimnasio
    usuarios = db.relationship("Usuario", back_populates="gimnasio", lazy="dynamic")

    def __repr__(self):
        return f"<Gimnasio {self.nombre} [{self.plan.value}]>"

    def to_dict(self):
        return {
            "id":             self.id,
            "nombre":         self.nombre,
            "plan":           self.plan.value,
            "activo":         self.activo,
            "email_contacto": self.email_contacto,
            "telefono":       self.telefono,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
        }
