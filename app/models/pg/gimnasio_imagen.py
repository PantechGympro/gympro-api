"""
models/pg/gimnasio_imagen.py -- Fotos publicas de un gimnasio.

Subidas por el Propietario/Administrador desde
routes/owner_gym/perfil_publico.py, para el carrusel de "gimnasios que
confian en nosotros" de la pagina comercial (routes/publico/landing.py).
El archivo en si vive en disco (/app/storage/uploads/gimnasios_publico/,
servido sin autenticacion via /api/uploads/publico/<archivo> -- son fotos
pensadas para verse sin sesion); esta fila solo guarda el nombre.
"""
from datetime import datetime, timezone
from app.extensions import db


class GimnasioImagen(db.Model):
    __tablename__ = "gimnasio_imagenes"

    id          = db.Column(db.Integer, primary_key=True)
    id_gimnasio = db.Column(
        db.Integer,
        db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    archivo     = db.Column(db.String(255), nullable=False)
    orden       = db.Column(db.Integer, nullable=False, default=0)
    creado_en   = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_dict(self):
        return {
            "id":       self.id,
            "url":      f"/api/uploads/publico/{self.archivo}",
            "orden":    self.orden,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
        }
