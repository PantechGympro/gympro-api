"""
models/pg/gimnasio.py — Modelo SQLAlchemy para Gimnasios (PostgreSQL).

El Gimnasio es el tenant anchor de la arquitectura multi-tenant.
Todo dato operacional en MongoDB y financiero en PostgreSQL referencia
a un id_gimnasio que se propaga desde el JWT via tenant middleware.

Planes:
  starter     → gratuito, hasta 25 miembros activos (SCRUM-163)
  basico      → hasta 50 miembros activos
  pro         → hasta 200 miembros + analytics
  enterprise  → ilimitado + SLA + soporte dedicado

HU-41 (SCRUM-212): todo Gimnasio pertenece a una Organizacion
(id_organizacion, NOT NULL). Hoy esa relación es 1:1 -- cada gimnasio
existente recibió su propia Organizacion vía la migración 017 -- pero el
campo ya admite que una Organizacion agrupe varias sucursales cuando
HU-42 habilite ese flujo. es_sucursal_principal marca cuál de las
sucursales de una organización es la "sede" cuando hay más de una.
"""
import enum
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


class PlanEnum(enum.Enum):
    starter    = "starter"
    basico     = "basico"
    pro        = "pro"
    enterprise = "enterprise"


# create_type=False: SQLAlchemy no intenta CREATE TYPE en db.create_all().
# El tipo lo gestiona Alembic (migraciones 001 y 016). Esto hace create_all idempotente.
_plan_enum_type = PGEnum("starter", "basico", "pro", "enterprise", name="plan_enum", create_type=False)


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
    # Tipo de establecimiento — usado para personalizar la experiencia
    tipo_gimnasio      = db.Column(db.String(50), nullable=True)
    # Logotipo del gimnasio como data URL base64 (nullable)
    logo               = db.Column(db.Text, nullable=True)
    # Config libre por tipo: etiquetas, módulos activos, etc.
    configuracion      = db.Column(db.JSON, nullable=True, default=dict)
    # Stripe Customer ID — se completa al integrar billing
    stripe_customer_id = db.Column(db.String(100))
    created_at         = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # FK hacia Organizacion (organizaciones.id) — HU-41 / SCRUM-212.
    # NOT NULL: la migración 017 garantiza que todo gimnasio existente
    # quede con una organización antes de aplicar la restricción.
    id_organizacion = db.Column(
        db.Integer, db.ForeignKey("organizaciones.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    organizacion    = db.relationship("Organizacion", back_populates="gimnasios")

    # True si esta sucursal es la "sede principal" de su organización.
    # Hoy, con la relación 1:1, toda sucursal existente es su propia sede.
    es_sucursal_principal = db.Column(db.Boolean, default=True, nullable=False, server_default="true")

    # Relación inversa: gimnasio.usuarios → lista de usuarios del gimnasio
    usuarios = db.relationship("Usuario", back_populates="gimnasio", lazy="dynamic")

    def __repr__(self):
        return f"<Gimnasio {self.nombre} [{self.plan.value}]>"

    def to_dict(self):
        return {
            "id":                    self.id,
            "nombre":                self.nombre,
            "plan":                  self.plan.value if hasattr(self.plan, "value") else self.plan,
            "activo":                self.activo,
            "email_contacto":        self.email_contacto,
            "telefono":              self.telefono,
            "tipo_gimnasio":         self.tipo_gimnasio,
            # Solo se expone si es una data URL válida
            "logo":                  self.logo if (self.logo or "").startswith("data:image") else None,
            "configuracion":         self.configuracion or {},
            "id_organizacion":       self.id_organizacion,
            "es_sucursal_principal": self.es_sucursal_principal,
            "created_at":            self.created_at.isoformat() if self.created_at else None,
        }
