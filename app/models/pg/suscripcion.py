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

    # SCRUM-224: ciclo de facturación contratado -- 'mensual' | 'anual'
    # (CHECK en BD, ver migración 023). Mismo patrón de VARCHAR validado en
    # Python que pasarela_recurrente/estado_recurrente más abajo, no un ENUM
    # de Postgres nuevo para solo dos valores.
    ciclo_facturacion       = db.Column(db.String(10), default="mensual", nullable=False, server_default="mensual")

    # Estado y fechas de ciclo
    estado                  = db.Column(_estado_enum_type, default="trialing", nullable=False)
    fecha_inicio            = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    fecha_fin               = db.Column(db.DateTime(timezone=True), nullable=True)     # None = sin vencimiento fijo
    fecha_proximo_cobro     = db.Column(db.DateTime(timezone=True), nullable=True)

    # Fin del periodo de prueba gratuito. None = esta suscripción no es una
    # prueba (o es anterior a la migración 040).
    #
    # Es una columna propia y no `fecha_proximo_cobro` reutilizada porque las
    # dos responden preguntas distintas: "hasta cuándo es gratis" no es lo
    # mismo que "cuándo toca el siguiente cargo", y confundirlas es lo que
    # dejaba pruebas abiertas para siempre. Además sobrevive a la conversión
    # a plan de pago, así que queda el registro de que esta suscripción nació
    # de una prueba.
    prueba_fin              = db.Column(db.DateTime(timezone=True), nullable=True, index=True)

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
        """
        Vigente para efectos de servicio. Una prueba VENCIDA no lo está,
        aunque el estado siga diciendo "trialing": que el trabajo nocturno
        todavía no la haya marcado no significa que siga siendo gratis.
        Ésa era la puerta abierta -- `trialing` contaba como vigente sin que
        nada mirara la fecha, así que quien se registraba eligiendo Enterprise
        se quedaba con Enterprise gratis indefinidamente.
        """
        if self.estado not in ("trialing", "active"):
            return False
        return not self.prueba_vencida

    @property
    def en_prueba(self) -> bool:
        """True si hoy está dentro de un periodo de prueba gratuito."""
        return (self.estado == "trialing"
                and self.prueba_fin is not None
                and not self.prueba_vencida)

    @property
    def prueba_vencida(self) -> bool:
        """True solo si ES una prueba y su fecha ya pasó."""
        if self.estado != "trialing" or self.prueba_fin is None:
            return False
        fin = self.prueba_fin
        if fin.tzinfo is None:          # SQLite devuelve datetimes sin zona
            fin = fin.replace(tzinfo=timezone.utc)
        return fin <= datetime.now(timezone.utc)

    @property
    def dias_restantes_prueba(self) -> int | None:
        """Días completos que le quedan a la prueba, o None si no está en una."""
        if not self.en_prueba:
            return None
        fin = self.prueba_fin
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)
        restantes = (fin - datetime.now(timezone.utc)).total_seconds() / 86400
        return max(0, int(-(-restantes // 1)))   # redondeo hacia arriba

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
            "ciclo_facturacion":      self.ciclo_facturacion,
            "estado":                 self.estado if isinstance(self.estado, str) else self.estado.value,
            "prueba_fin":             self.prueba_fin.isoformat() if self.prueba_fin else None,
            "en_prueba":              self.en_prueba,
            "prueba_vencida":         self.prueba_vencida,
            "dias_restantes_prueba":  self.dias_restantes_prueba,
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
