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

    # ── Cargo recurrente ─────────────────────────────────────────────────────
    #
    # `auto_renovar` es la intención del dueño; los tres campos siguientes son
    # el acuerdo real con la pasarela, que es quien cobra. Están separados a
    # propósito: el dueño puede haber pedido el cargo recurrente y que el
    # acuerdo todavía esté sin autorizar, o que la pasarela lo haya suspendido
    # tras varios intentos fallidos. Guardar solo la casilla ocultaría esa
    # diferencia y el panel afirmaría que se cobra solo cuando no es cierto.
    auto_renovar            = db.Column(db.Boolean, nullable=False, default=False, server_default="false")

    # 'paypal' | 'mercadopago'. Null mientras no haya acuerdo.
    pasarela_recurrente     = db.Column(db.String(30), nullable=True)
    # Id del acuerdo en la pasarela (subscription de PayPal, preapproval de MP).
    referencia_recurrente   = db.Column(db.String(120), nullable=True, index=True)
    # Último estado conocido: pendiente | activo | pausado | cancelado | vencido.
    estado_recurrente       = db.Column(db.String(20), nullable=True)

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

    @property
    def cobro_automatico(self) -> bool:
        """
        True solo si el cobro recurrente va a ocurrir de verdad.

        Requiere las dos cosas: que el dueño lo haya pedido y que la pasarela
        confirme el acuerdo como activo. Un acuerdo pendiente de autorizar, o
        suspendido por pagos fallidos, no cobra nada.
        """
        return bool(self.auto_renovar) and self.estado_recurrente == "activo"

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
            "auto_renovar":           bool(self.auto_renovar),
            "pasarela_recurrente":    self.pasarela_recurrente,
            "estado_recurrente":      self.estado_recurrente,
            # Lo que el panel debe creer: solo hay cobro automático si el dueño
            # lo pidió Y la pasarela confirma que el acuerdo está activo.
            "cobro_automatico":       self.cobro_automatico,
            "created_at":             self.created_at.isoformat() if self.created_at else None,
            "updated_at":             self.updated_at.isoformat() if self.updated_at else None,
        }
