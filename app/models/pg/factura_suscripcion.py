"""
models/pg/factura_suscripcion.py — Factura por período de suscripción de la plataforma.

Cada factura representa un cargo mensual (o el intento de cobrarlo) asociado
a una Suscripcion. El diseño es agnóstico de pasarela: stripe_invoice_id queda
nullable para el modo local/test sin Stripe real.

Relación: FacturaSuscripcion N─1 Suscripcion
"""
import enum
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


class EstadoFacturaEnum(enum.Enum):
    pendiente = "pendiente"   # emitida, aún no vence
    pagada    = "pagada"      # cobrada con éxito
    vencida   = "vencida"     # fecha_vencimiento superada sin pago
    fallida   = "fallida"     # intento de cobro rechazado


_estado_factura_enum_type = PGEnum(
    "pendiente", "pagada", "vencida", "fallida",
    name="estado_factura_enum",
    create_type=False,  # Alembic lo crea en la migración 002
)


class FacturaSuscripcion(db.Model):
    __tablename__ = "facturas_suscripcion"

    id                = db.Column(db.Integer, primary_key=True)

    # FK
    id_suscripcion    = db.Column(db.Integer, db.ForeignKey("suscripciones.id", ondelete="CASCADE"), nullable=False, index=True)

    # Importe
    monto             = db.Column(db.Integer, nullable=False)           # centavos MXN
    moneda            = db.Column(db.String(3), default="MXN", nullable=False)

    # Estado y fechas
    estado            = db.Column(_estado_factura_enum_type, default="pendiente", nullable=False)
    fecha_emision     = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    fecha_vencimiento = db.Column(db.DateTime(timezone=True), nullable=True)
    fecha_pago        = db.Column(db.DateTime(timezone=True), nullable=True)

    # Stripe (nullable para modo local)
    stripe_invoice_id = db.Column(db.String(100), nullable=True, unique=True)

    # Auditoría
    created_at        = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relación
    suscripcion       = db.relationship("Suscripcion", back_populates="facturas")

    def __repr__(self):
        monto_display = f"${self.monto / 100:,.2f}" if self.monto else "?"
        return f"<FacturaSuscripcion sub={self.id_suscripcion} {monto_display} {self.estado}>"

    def to_dict(self):
        estado_val = self.estado if isinstance(self.estado, str) else self.estado.value
        return {
            "id":                 self.id,
            "id_suscripcion":     self.id_suscripcion,
            "monto":              self.monto,
            "monto_display":      f"${self.monto / 100:,.2f} {self.moneda}" if self.monto else None,
            "moneda":             self.moneda,
            "estado":             estado_val,
            "fecha_emision":      self.fecha_emision.isoformat() if self.fecha_emision else None,
            "fecha_vencimiento":  self.fecha_vencimiento.isoformat() if self.fecha_vencimiento else None,
            "fecha_pago":         self.fecha_pago.isoformat() if self.fecha_pago else None,
            "stripe_invoice_id":  self.stripe_invoice_id,
            "created_at":         self.created_at.isoformat() if self.created_at else None,
        }
