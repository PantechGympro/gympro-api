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

    # ── Beneficios que el dueño define para el plan ──────────────────────────
    # Lista de textos mostrados como incluidos: ["Acceso 24/7", "1 clase grupal"]
    beneficios      = db.Column(db.JSON, nullable=True, default=list)

    # ── Combos: agrupan varios conceptos en un solo precio ───────────────────
    # items_combo: [{"nombre": "Mensualidad", "cantidad": 1},
    #               {"nombre": "Proteína 1kg", "cantidad": 1, "id_producto": 4}]
    es_combo        = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    items_combo     = db.Column(db.JSON, nullable=True, default=list)

    # ── Vigencia de promociones ──────────────────────────────────────────────
    # Cuando 'tipo' es 'promocion', el dueño puede fijar hasta cuándo se ofrece.
    # Al pasar esa fecha, la membresía se desactiva automáticamente.
    fecha_fin_promo = db.Column(db.Date, nullable=True)
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
            "beneficios":     self.beneficios or [],
            "es_combo":       bool(self.es_combo),
            "items_combo":    self.items_combo or [],
            "fecha_fin_promo": self.fecha_fin_promo.isoformat() if self.fecha_fin_promo else None,
            "vigente":        self.vigente,
            "dias_restantes_promo": self.dias_restantes_promo,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
        }

    # ── Vigencia de promociones ──────────────────────────────────────────────

    @property
    def caducada(self) -> bool:
        """True si es una promoción cuya fecha de fin ya pasó."""
        if not self.fecha_fin_promo:
            return False
        from datetime import date
        return date.today() > self.fecha_fin_promo

    @property
    def vigente(self) -> bool:
        """Se puede ofrecer: está activa y no ha caducado."""
        return bool(self.activo) and not self.caducada

    @property
    def dias_restantes_promo(self):
        """Días que faltan para que caduque la promoción (None si no aplica)."""
        if not self.fecha_fin_promo:
            return None
        from datetime import date
        return (self.fecha_fin_promo - date.today()).days
