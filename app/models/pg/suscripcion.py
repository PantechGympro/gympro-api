"""
models/pg/suscripcion.py — Suscripción activa de un Gimnasio a la plataforma.

Un gimnasio tiene como máximo una suscripción activa en cualquier momento.
El estado sigue el ciclo de vida de Stripe (trialing → active → past_due →
cancelled), lo que facilita la sincronización vía webhook en Sprint 3/US13.

Relaciones:
  Suscripcion N─1 Gimnasio
  Suscripcion N─1 PlanSuscripcion
  Suscripcion 1─N FacturaSuscripcion
"""
import enum
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


class EstadoSuscripcionEnum(enum.Enum):
    trialing  = "trialing"    # período de prueba gratuito
    active    = "active"      # suscripción al corriente
    past_due  = "past_due"    # pago fallido, en período de gracia
    unpaid    = "unpaid"      # no se pudo cobrar, acceso suspendido
    cancelled = "cancelled"   # cancelada por el gym o la plataforma
    paused    = "paused"      # pausada temporalmente


_estado_enum_type = PGEnum(
    "trialing", "active", "past_due", "unpaid", "cancelled", "paused",
    name="estado_suscripcion_enum",
    create_type=False,  # Alembic lo crea en la migración 002
)


class Suscripcion(db.Model):
    __tablename__ = "suscripciones"

    id                      = db.Column(db.Integer, primary_key=True)

    # FKs
    id_gimnasio             = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=False, index=True)
    id_plan                 = db.Column(db.Integer, db.ForeignKey("planes_suscripcion.id", ondelete="RESTRICT"), nullable=False)

    # Estado y fechas de ciclo
    estado                  = db.Column(_estado_enum_type, default="trialing", nullable=False)
    fecha_inicio            = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    fecha_fin               = db.Column(db.DateTime(timezone=True), nullable=True)     # None = sin vencimiento fijo
    fecha_proximo_cobro     = db.Column(db.DateTime(timezone=True), nullable=True)

    # Stripe (nullable mientras se trabaja en local)
    stripe_subscription_id  = db.Column(db.String(100), nullable=True, unique=True)

    # Auditoría
    created_at              = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at              = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relaciones
    gimnasio  = db.relationship("Gimnasio",        backref=db.backref("suscripciones", lazy="dynamic"))
    plan      = db.relationship("PlanSuscripcion",  back_populates="suscripciones")
    facturas  = db.relationship("FacturaSuscripcion", back_populates="suscripcion", lazy="dynamic", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Suscripcion gym={self.id_gimnasio} plan={self.id_plan} estado={self.estado}>"

    @property
    def activa(self) -> bool:
        return self.estado in ("trialing", "active")

    def to_dict(self):
        return {
            "id":                     self.id,
            "id_gimnasio":            self.id_gimnasio,
            "id_plan":                self.id_plan,
            "plan":                   self.plan.to_dict() if self.plan else None,
            "estado":                 self.estado if isinstance(self.estado, str) else self.estado.value,
            "activa":                 self.activa,
            "fecha_inicio":           self.fecha_inicio.isoformat() if self.fecha_inicio else None,
            "fecha_fin":              self.fecha_fin.isoformat() if self.fecha_fin else None,
            "fecha_proximo_cobro":    self.fecha_proximo_cobro.isoformat() if self.fecha_proximo_cobro else None,
            "stripe_subscription_id": self.stripe_subscription_id,
            "created_at":             self.created_at.isoformat() if self.created_at else None,
            "updated_at":             self.updated_at.isoformat() if self.updated_at else None,
        }
