"""
models/pg/asignacion_usuario.py — Modelo SQLAlchemy para AsignacionUsuario
(PostgreSQL). HU-41 (SCRUM-212).

Representa el alcance de un usuario, independiente de su fila en
`usuarios`: a qué Organizacion y, opcionalmente, a qué Gimnasio (sucursal)
específico queda asignado, con qué rol.

Combinaciones válidas de alcance (reforzadas por ck_asignacion_alcance_valido):
  (organización, sucursal)  → el usuario opera en esa sucursal puntual.
  (organización, NULL)      → el usuario opera en toda la organización.
  (NULL, NULL)               → alcance de plataforma (superadmin).
  (NULL, sucursal)           → INVÁLIDA, no debería poder insertarse.

Nota de diseño (desviación deliberada frente al texto literal del ticket):
el ticket describe un campo "rol" como string suelto; aquí se usa
`id_rol` -- FK hacia la tabla `roles` ya existente -- para no introducir
una segunda fuente de nombres de rol en el sistema. Documentado en la
bitácora técnica.

IMPORTANTE: esta tabla NO es todavía la fuente de verdad de permisos. El
alcance real de un usuario sigue resolviéndose por
`usuarios.id_rol` + `usuarios.id_gimnasio` (ver utils/tenant.py) -- eso
cambia en HU-42. Por ahora, asignacion_usuario se mantiene sincronizada
desde los puntos donde se crean Usuarios (ver utils/alcance.py) para que
esa migración futura no tenga que reconstruir el backfill desde cero.
"""
from datetime import datetime, timezone
from app.extensions import db


class AsignacionUsuario(db.Model):
    __tablename__ = "asignacion_usuario"

    id = db.Column(db.Integer, primary_key=True)

    id_usuario = db.Column(
        db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # NULL = alcance de plataforma (superadmin, sin organización ni sucursal).
    id_organizacion = db.Column(
        db.Integer, db.ForeignKey("organizaciones.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # NULL = alcance de toda la organización (todas sus sucursales).
    id_gimnasio = db.Column(
        db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"), nullable=True, index=True
    )
    id_rol = db.Column(db.Integer, db.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relaciones de solo lectura -- unidireccionales a propósito: ni Usuario,
    # ni Gimnasio, ni Rol necesitan hoy una colección inversa de asignaciones
    # (nada las consume todavía), así que no se tocan esos modelos.
    usuario      = db.relationship("Usuario")
    organizacion = db.relationship("Organizacion")
    gimnasio     = db.relationship("Gimnasio")
    rol          = db.relationship("Rol")

    __table_args__ = (
        # Una sucursal puntual no se asigna dos veces al mismo usuario.
        # (Postgres no deduplica combinaciones con id_gimnasio NULL -- ese
        # caso, alcance de organización completa o de plataforma, no está
        # cubierto por este UNIQUE; no hace falta hoy porque nada más que
        # el backfill de esta migración inserta filas todavía.)
        db.UniqueConstraint("id_usuario", "id_gimnasio", name="uq_asignacion_usuario_gimnasio"),
        db.CheckConstraint(
            "id_organizacion IS NOT NULL OR id_gimnasio IS NULL",
            name="ck_asignacion_alcance_valido",
        ),
    )

    def __repr__(self):
        return f"<AsignacionUsuario usuario={self.id_usuario} org={self.id_organizacion} gimnasio={self.id_gimnasio}>"

    def to_dict(self):
        return {
            "id":              self.id,
            "id_usuario":      self.id_usuario,
            "id_organizacion": self.id_organizacion,
            "id_gimnasio":     self.id_gimnasio,
            "id_rol":          self.id_rol,
            "created_at":      self.created_at.isoformat() if self.created_at else None,
        }
