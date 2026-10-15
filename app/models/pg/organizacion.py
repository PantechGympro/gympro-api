"""
models/pg/organizacion.py — Modelo SQLAlchemy para Organizaciones (PostgreSQL).

HU-41 (SCRUM-212): introduce un nivel de agrupación por encima de Gimnasio
("sucursal"). Hoy, con la migración 017, cada gimnasio existente recibe su
propia Organizacion 1:1 (mismo id) -- el modelo no cambia ningún
comportamiento actual, es la base sobre la que HU-42 construirá permisos
multi-sucursal (una Organizacion podrá agrupar varias sucursales).

SCRUM-223: la Organizacion pasa a ser la unidad de FACTURACIÓN del plan
SaaS -- antes vivía en Gimnasio.plan (una sucursal podía tener un plan
distinto al de sus hermanas y pagar su propia mensualidad). `plan` se
sincroniza desde routes/owner_gym/billing.py cuando el Propietario
cambia de plan (su suscripción siempre vive en la sede principal de su
organización). utils/capacidad.py lee este campo -- no Gimnasio.plan --
para resolver los topes de miembros/staff/sucursales; el conteo en sí
sigue siendo POR SUCURSAL, eso no cambió (ver docstring de capacidad.py).

Relación:
  Organizacion 1─N Gimnasio       (gimnasios.id_organizacion)
  Organizacion 1─N AsignacionUsuario
"""
import enum
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


# Mismo tipo Postgres que gimnasios.plan (ver models/pg/gimnasio.py) --
# se reutiliza, no se crea uno nuevo, para que un solo ALTER TYPE (ya
# aplicado en la migración 016) sirva para las dos tablas.
class PlanEnum(enum.Enum):
    starter    = "starter"
    basico     = "basico"
    pro        = "pro"
    enterprise = "enterprise"


_plan_enum_type = PGEnum("starter", "basico", "pro", "enterprise", name="plan_enum", create_type=False)


class Organizacion(db.Model):
    __tablename__ = "organizaciones"

    id         = db.Column(db.Integer, primary_key=True)
    nombre     = db.Column(db.String(150), nullable=False)
    activo     = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Plan SaaS contratado por TODA la organización (SCRUM-223) -- gobierna
    # los topes de miembros/staff/sucursales de todas sus sucursales.
    plan = db.Column(_plan_enum_type, default="basico", nullable=False, server_default="basico")

    # Relación inversa: organizacion.gimnasios → sucursales de esta organización
    gimnasios = db.relationship("Gimnasio", back_populates="organizacion", lazy="dynamic")

    def __repr__(self):
        return f"<Organizacion {self.nombre} [{self.plan.value if hasattr(self.plan, 'value') else self.plan}]>"

    def to_dict(self):
        return {
            "id":         self.id,
            "nombre":     self.nombre,
            "plan":       self.plan.value if hasattr(self.plan, "value") else self.plan,
            "activo":     self.activo,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
