"""
models/pg/plan_suscripcion.py — Catálogo de planes de suscripción de la plataforma.

Tabla de referencia estática que define los tres niveles de servicio.
Los precios se expresan en centavos MXN para evitar aritmética de punto flotante
(convención estándar de Stripe y pasarelas de pago).

Relación: PlanSuscripcion 1─N Suscripcion
"""
from datetime import datetime, timezone
from app.extensions import db


class PlanSuscripcion(db.Model):
    __tablename__ = "planes_suscripcion"

    id                 = db.Column(db.Integer, primary_key=True)
    nombre             = db.Column(db.String(50), unique=True, nullable=False)   # basico | pro | enterprise
    precio_mensual_mxn = db.Column(db.Integer, nullable=False)                   # centavos: 49900 = $499.00 MXN
    max_miembros       = db.Column(db.Integer, nullable=True)                    # None = ilimitado
    descripcion        = db.Column(db.Text, nullable=True)
    activo             = db.Column(db.Boolean, default=True, nullable=False)
    stripe_price_id    = db.Column(db.String(100), nullable=True)                # se completa al configurar Stripe
    created_at         = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relación inversa
    suscripciones = db.relationship("Suscripcion", back_populates="plan", lazy="dynamic")

    def __repr__(self):
        return f"<PlanSuscripcion {self.nombre} ${self.precio_mensual_mxn / 100:.2f} MXN>"

    def to_dict(self):
        return {
            "id":                  self.id,
            "nombre":              self.nombre,
            "precio_mensual_mxn":  self.precio_mensual_mxn,
            "precio_display":      f"${self.precio_mensual_mxn / 100:,.2f} MXN/mes",
            "max_miembros":        self.max_miembros,
            "descripcion":         self.descripcion,
            "activo":              self.activo,
            "stripe_price_id":     self.stripe_price_id,
        }
