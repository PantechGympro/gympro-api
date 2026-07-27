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

    # ── Comercialización y control de acceso por plan ────────────────────────
    # Etiqueta comercial mostrada en la web ("Ideal para gimnasios en crecimiento")
    titulo_comercial   = db.Column(db.String(120), nullable=True)
    # Lista de textos que se muestran como beneficios incluidos en el plan
    caracteristicas    = db.Column(db.JSON, nullable=True, default=list)
    # Banderas de funciones habilitadas: {"analiticas_ia": true, "pos": true, ...}
    # El sistema consultará estas banderas para bloquear módulos por plan.
    limites            = db.Column(db.JSON, nullable=True, default=dict)
    # Orden de aparición en la página de planes y plan resaltado
    orden              = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    destacado          = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
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
            "titulo_comercial":    self.titulo_comercial,
            "caracteristicas":     self.caracteristicas or [],
            "limites":             self.limites or {},
            "orden":               self.orden,
            "destacado":           self.destacado,
            "precio_mxn":          round((self.precio_mensual_mxn or 0) / 100, 2),
        }

    def permite(self, funcion: str) -> bool:
        """
        True si el plan habilita la función indicada. Se usará para bloquear
        módulos según la suscripción del gimnasio (por ejemplo 'analiticas_ia').
        Si la bandera no está definida, se considera NO incluida.
        """
        return bool((self.limites or {}).get(funcion, False))
