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


def _aware(valor):
    """
    Fecha con zona, siempre. Postgres devuelve las columnas `timezone=True` ya
    con zona, pero SQLite (pruebas) y los seeds antiguos las entregan sin ella,
    y restar naive y aware revienta. Se asume UTC, que es como se guardan.
    """
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


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

    # ── Baja de cuenta pedida por el propietario (migración 037) ──────────────
    # La eliminación no es inmediata: se programa con DIAS_GRACIA_BAJA de
    # margen para que el arrepentimiento sea posible (ver utils/baja_gimnasio.py
    # y routes/owner_gym/cuenta.py). Los tres en NULL = sin baja pendiente.
    baja_solicitada_en   = db.Column(db.DateTime(timezone=True), nullable=True)
    baja_programada_para = db.Column(db.DateTime(timezone=True), nullable=True)
    baja_motivo          = db.Column(db.Text, nullable=True)
    # Entero sin FK: una clave ajena de vuelta a usuarios cerraría un ciclo
    # entre las dos tablas (ver migración 037). Quién pidió la baja queda
    # además en bitacora_eventos, que es el registro con valor probatorio.
    baja_solicitada_por  = db.Column(db.Integer, nullable=True)

    # Relación inversa: gimnasio.usuarios → lista de usuarios del gimnasio
    usuarios = db.relationship("Usuario", back_populates="gimnasio", lazy="dynamic")

    def __repr__(self):
        return f"<Gimnasio {self.nombre} [{self.plan_vigente}]>"

    @property
    def plan_vigente(self) -> str:
        """
        Plan SaaS que rige a este gimnasio: el de su ORGANIZACIÓN (SCRUM-223).

        La columna `gimnasios.plan` quedó como legado y ya no se mantenía al
        contratar/cambiar de plan, así que todo lo que la serializaba (listado
        y detalle del superadmin, perfil del propietario, app móvil) mostraba
        "Básico" mientras la suscripción y los permisos eran "Enterprise".
        Se cae a la columna solo si el gimnasio aún no tiene organización
        cargada.
        """
        org = self.organizacion
        plan = org.plan if org is not None else self.plan
        return plan.value if hasattr(plan, "value") else plan

    @property
    def baja(self) -> dict | None:
        """Estado de la baja pendiente, o None si no hay ninguna programada."""
        programada = _aware(self.baja_programada_para)
        if not programada:
            return None
        return {
            "solicitada_en":   self.baja_solicitada_en.isoformat() if self.baja_solicitada_en else None,
            "programada_para": programada.isoformat(),
            "motivo":          self.baja_motivo,
            "dias_restantes":  max(0, (programada - datetime.now(timezone.utc)).days),
        }

    def to_dict(self):
        return {
            "id":                    self.id,
            "nombre":                self.nombre,
            "plan":                  self.plan_vigente,
            "activo":                self.activo,
            "baja":                  self.baja,
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
