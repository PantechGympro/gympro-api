"""
models/pg/testimonio.py -- Testimonios de clientes para la pagina comercial.

Lo escribe el Propietario/Administrador de un gimnasio sobre SU propio
gimnasio (routes/owner_gym/perfil_publico.py) y se muestra sin
autenticacion en la landing publica (routes/publico/landing.py). No hay
paso de moderacion por superadmin todavia -- aparece en cuanto se guarda,
igual que las fotos -- queda anotado como seguimiento si en el futuro se
necesita revisar contenido antes de publicarlo.

id_usuario es nullable con ON DELETE SET NULL a proposito: el testimonio
pertenece al GIMNASIO que lo publico, no a la cuenta de quien lo escribio
-- si esa cuenta se elimina mas adelante, el testimonio se queda (ya trae
autor_nombre/autor_cargo guardados aparte, no se recalculan desde Usuario).
"""
from datetime import datetime, timezone
from app.extensions import db


class Testimonio(db.Model):
    __tablename__ = "testimonios"

    id           = db.Column(db.Integer, primary_key=True)
    id_gimnasio  = db.Column(
        db.Integer,
        db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    id_usuario   = db.Column(
        db.Integer,
        db.ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    autor_nombre = db.Column(db.String(150), nullable=False)
    autor_cargo  = db.Column(db.String(150), nullable=True)
    texto        = db.Column(db.Text, nullable=False)
    calificacion = db.Column(db.Integer, nullable=True)
    creado_en    = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Solo lectura (sin back_populates en Gimnasio -- nadie mas necesita
    # navegar de gimnasio a sus testimonios hoy, landing.py ya hace su
    # propio query directo a Testimonio). Se usa para traer el nombre del
    # gimnasio en /api/publico/testimonios sin un segundo query manual.
    gimnasio = db.relationship("Gimnasio")

    def to_dict(self):
        return {
            "id":            self.id,
            "autor_nombre":  self.autor_nombre,
            "autor_cargo":   self.autor_cargo,
            "texto":         self.texto,
            "calificacion":  self.calificacion,
            "creado_en":     self.creado_en.isoformat() if self.creado_en else None,
        }
